"""Intro scene: short multi-page story crawl shown after Title's PLAY action.

Sets up the SSDQ premise (galaxy in peril, two pilots boarding their ships).
The kid will redraw the pilot art later; for now each page renders a small
tableau combining the existing Kenney ship sprites with placeholder
"pilot" silhouettes drawn with ``pygame.draw``.

Flow (post-2026-05-02): Boot --> Title --> (PLAY) --> Intro --> Level. Any
input button advances one page (rising edge); on the final page that input
transitions to ``next_scene_factory()`` — defaulting to TitleScene if the
caller didn't supply one (legacy compatibility for tests / the dev skip).
After a per-page ceiling of ~9 sim seconds the scene auto-advances even
without input -- this is how the headless smoke playthrough (which holds
fire continuously) gets through the intro.

The scene plays the ``intro_epic`` music track on enter and lets the next
scene's own music cue take over on exit (LevelScene crossfades to its
level track in ``enter()``).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (4, 4, 16)
_STAR_COLOUR = (160, 160, 200)
_TEXT_COLOUR = (230, 230, 240)
_HINT_COLOUR = (130, 130, 150)
_PILOT_SUIT = (200, 200, 210)
_PILOT_VISOR = (60, 90, 160)
_PILOT_HEAD = (240, 210, 180)
_GROUND_COLOUR = (40, 40, 60)

# Auto-advance ceiling per page (in 60Hz sim ticks). Smoke runs hold inputs
# continuously, so without this they would only ever clear page 1.
_AUTO_ADVANCE_TICKS = 540  # 9 sim seconds at 60Hz — kid playtest #2: 4s
# wasn't long enough for an 8-year-old to read the page.

_SPRITES_DIR = Path("content") / "assets" / "sprites"

# Each page is (paragraph, tableau_kind). The tableau kind selects which
# placeholder vignette is drawn behind the text.
_Page = tuple[str, str]
_PAGES: tuple[_Page, ...] = (
    ("The galaxy is in peril.", "stars"),
    (
        "Alien fleets have breached the outer rim. Earth's colonies are falling.",
        "stars",
    ),
    (
        "From the dark side of the Moon, the Special Space Defence Quasar -- SSDQ "
        "-- is humanity's last line of defence.",
        "hangar",
    ),
    (
        "From Moon Base Delta Bravo, two pilots launch into the dark. "
        "The fight begins now.",
        "boarding",
    ),
    ("Press FIRE to begin.", "launch"),
)


class IntroScene(Scene):
    """Multi-page story crawl. Press any input button to advance."""

    __slots__ = (
        "_app",
        "_body_font",
        "_hint_font",
        "_next_scene_factory",
        "_page",
        "_page_tick",
        "_prev_input",
        "_ship_blue",
        "_ship_red",
        "_title_font",
    )

    def __init__(
        self,
        app: AppState,
        *,
        next_scene_factory: Callable[[], Scene] | None = None,
    ) -> None:
        self._app = app
        # ``next_scene_factory`` builds the scene to transition to once the
        # intro is done. Default = TitleScene, which keeps the legacy
        # path (Boot → Intro → Title) working when older callers haven't
        # been updated. Title.PLAY now passes a factory that builds the
        # appropriate LevelScene so the new flow lands on level 1.
        self._next_scene_factory: Callable[[], Scene] | None = next_scene_factory
        self._page: int = 0
        self._page_tick: int = 0
        # _prev_input gates rising-edge detection. Start True so that an
        # input held over from Boot does not auto-skip page 1.
        self._prev_input: bool = True
        self._title_font: pygame.font.Font | None = None
        self._body_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None
        self._ship_blue: pygame.Surface | None = None
        self._ship_red: pygame.Surface | None = None

    # ------------------------------------------------------------------ lifecycle

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 48, bold=True)
        self._body_font = pygame.font.SysFont(None, 32)
        self._hint_font = pygame.font.SysFont(None, 22)
        self._ship_blue = _try_load_sprite(_SPRITES_DIR / "ships" / "player_blue.png")
        self._ship_red = _try_load_sprite(_SPRITES_DIR / "ships" / "player_red.png")
        # Cinematic music for the story crawl. The next scene's enter()
        # is responsible for crossfading to its own track.
        self._app.audio.crossfade_to("intro_epic", ms=400)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        # Dev shortcut: SSDQ_SKIP_INTRO=1 jumps straight past the intro
        # so testing iterations don't sit through the prologue every run.
        # Checked on tick 0 so the skip is instant. Kid playtest 2026-04-28.
        import os as _os

        if _os.environ.get("SSDQ_SKIP_INTRO") == "1":
            return Replace(scene=self._build_next_scene())

        self._page_tick += 1

        any_now = _any_advance_input(inputs)
        rising = any_now and not self._prev_input
        self._prev_input = any_now

        # Advance on a button press (rising edge) or when the page has been
        # on screen long enough for the auto-advance ceiling.
        should_advance = rising or self._page_tick >= _AUTO_ADVANCE_TICKS
        if not should_advance:
            return None

        if self._page >= len(_PAGES) - 1:
            return Replace(scene=self._build_next_scene())

        self._page += 1
        self._page_tick = 0
        return None

    def _build_next_scene(self) -> Scene:
        if self._next_scene_factory is not None:
            return self._next_scene_factory()
        # Legacy default — the pre-2026-05-02 flow ended at TitleScene.
        # Importing here avoids a circular import with title.py.
        from ssdq.scenes.title import TitleScene

        return TitleScene(self._app)

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._body_font is None or self._hint_font is None:
            return

        w, h = surface.get_size()
        text, tableau = _PAGES[self._page]

        _draw_starfield(surface)
        if tableau == "hangar":
            _draw_hangar(surface, self._ship_blue, self._ship_red)
        elif tableau == "boarding":
            _draw_boarding(surface, self._ship_blue, self._ship_red)
        elif tableau == "launch":
            _draw_launch(surface, self._ship_blue, self._ship_red)

        # SSDQ banner at the top of every page so the kid always sees the title.
        banner = self._title_font.render("SSDQ", True, (255, 240, 120))
        surface.blit(banner, banner.get_rect(center=(w // 2, 40)))

        # Wrap the body text into screen-fit lines and centre-stack them.
        lines = _wrap_text(text, self._body_font, max_width=int(w * 0.85))
        line_height = self._body_font.get_linesize()
        block_height = line_height * len(lines)
        baseline_y = h - 110 - block_height // 2
        for i, line in enumerate(lines):
            surf = self._body_font.render(line, True, _TEXT_COLOUR)
            surface.blit(surf, surf.get_rect(center=(w // 2, baseline_y + i * line_height)))

        page_label = f"{self._page + 1} / {len(_PAGES)}"
        hint_text = "press FIRE to continue" if self._page < len(_PAGES) - 1 else "press FIRE"
        hint = self._hint_font.render(f"{hint_text}    {page_label}", True, _HINT_COLOUR)
        surface.blit(hint, hint.get_rect(center=(w // 2, h - 30)))

    def exit(self, world: World) -> None:
        return None


# --------------------------------------------------------------------- helpers


def _any_advance_input(inputs: tuple[PlayerInput, PlayerInput]) -> bool:
    """Return True if either player is pressing any 'do something' button.

    We accept fire / confirm / cancel / bomb -- a kid is going to mash, and
    we want every reasonable mash to advance.
    """
    p1, p2 = inputs
    return bool(
        p1.fire
        or p1.confirm
        or p1.cancel
        or p1.bomb
        or p2.fire
        or p2.confirm
        or p2.cancel
        or p2.bomb
    )


def _try_load_sprite(path: Path) -> pygame.Surface | None:
    """Best-effort sprite load. Returns None on failure (we'll just skip it)."""
    if not path.is_file():
        return None
    try:
        surf = pygame.image.load(str(path))
        if pygame.display.get_init() and pygame.display.get_surface() is not None:
            surf = surf.convert_alpha()
    except pygame.error:
        return None
    return surf


def _wrap_text(text: str, font: pygame.font.Font, max_width: int) -> list[str]:
    """Greedy word-wrap on whitespace; degrades gracefully on a single long word."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if font.size(candidate)[0] <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_starfield(surface: pygame.Surface) -> None:
    """Sparse deterministic star pattern -- no RNG, screenshot-stable."""
    w, h = surface.get_size()
    # Hand-picked offsets so the same backdrop renders identically every time.
    for sx, sy in (
        (40, 60),
        (110, 30),
        (220, 90),
        (340, 50),
        (470, 110),
        (560, 40),
        (640, 80),
        (760, 30),
        (860, 100),
        (980, 60),
        (90, 200),
        (310, 250),
        (590, 220),
        (820, 270),
        (1010, 230),
    ):
        if sx < w and sy < h:
            surface.set_at((sx, sy), _STAR_COLOUR)


def _draw_pilot(surface: pygame.Surface, cx: int, cy: int, suit: tuple[int, int, int]) -> None:
    """Tiny placeholder pilot silhouette: helmet + suit body. Stand-in art."""
    # Body -- rounded suit
    pygame.draw.rect(surface, suit, pygame.Rect(cx - 9, cy - 4, 18, 26), border_radius=4)
    # Arms
    pygame.draw.rect(surface, suit, pygame.Rect(cx - 14, cy + 2, 5, 18), border_radius=2)
    pygame.draw.rect(surface, suit, pygame.Rect(cx + 9, cy + 2, 5, 18), border_radius=2)
    # Head (skin) + helmet visor
    pygame.draw.circle(surface, _PILOT_HEAD, (cx, cy - 10), 9)
    pygame.draw.circle(surface, suit, (cx, cy - 10), 11, width=2)
    pygame.draw.rect(surface, _PILOT_VISOR, pygame.Rect(cx - 6, cy - 13, 12, 5), border_radius=2)
    # Legs
    pygame.draw.rect(surface, suit, pygame.Rect(cx - 8, cy + 22, 6, 14), border_radius=2)
    pygame.draw.rect(surface, suit, pygame.Rect(cx + 2, cy + 22, 6, 14), border_radius=2)


def _draw_ground_strip(surface: pygame.Surface, y: int) -> None:
    w, _ = surface.get_size()
    pygame.draw.rect(surface, _GROUND_COLOUR, pygame.Rect(0, y, w, 4))


def _blit_centred(surface: pygame.Surface, sprite: pygame.Surface | None, cx: int, cy: int) -> None:
    if sprite is None:
        return
    surface.blit(sprite, sprite.get_rect(center=(cx, cy)))


def _draw_hangar(
    surface: pygame.Surface,
    ship_blue: pygame.Surface | None,
    ship_red: pygame.Surface | None,
) -> None:
    """Page-3 tableau: two ships parked, two pilots standing beside them."""
    w, h = surface.get_size()
    ground_y = int(h * 0.62)
    _draw_ground_strip(surface, ground_y)
    blue_x = w // 2 - 140
    red_x = w // 2 + 140
    ship_y = ground_y - 30
    _blit_centred(surface, ship_blue, blue_x, ship_y)
    _blit_centred(surface, ship_red, red_x, ship_y)
    _draw_pilot(surface, blue_x - 50, ground_y - 18, _PILOT_SUIT)
    _draw_pilot(surface, red_x + 50, ground_y - 18, _PILOT_SUIT)


def _draw_boarding(
    surface: pygame.Surface,
    ship_blue: pygame.Surface | None,
    ship_red: pygame.Surface | None,
) -> None:
    """Page-4 tableau: pilots have climbed into the cockpit -- silhouettes
    drawn slightly behind the ship (small heads peeking)."""
    w, h = surface.get_size()
    ground_y = int(h * 0.62)
    _draw_ground_strip(surface, ground_y)
    blue_x = w // 2 - 130
    red_x = w // 2 + 130
    ship_y = ground_y - 30
    # Heads first (behind the hull)
    pygame.draw.circle(surface, _PILOT_HEAD, (blue_x, ship_y - 6), 7)
    pygame.draw.circle(surface, _PILOT_HEAD, (red_x, ship_y - 6), 7)
    pygame.draw.rect(
        surface, _PILOT_VISOR, pygame.Rect(blue_x - 5, ship_y - 9, 10, 4), border_radius=1
    )
    pygame.draw.rect(
        surface, _PILOT_VISOR, pygame.Rect(red_x - 5, ship_y - 9, 10, 4), border_radius=1
    )
    _blit_centred(surface, ship_blue, blue_x, ship_y)
    _blit_centred(surface, ship_red, red_x, ship_y)


def _draw_launch(
    surface: pygame.Surface,
    ship_blue: pygame.Surface | None,
    ship_red: pygame.Surface | None,
) -> None:
    """Page-5 tableau: ships lifting off, exhaust plumes underneath."""
    w, h = surface.get_size()
    blue_x = w // 2 - 100
    red_x = w // 2 + 100
    ship_y = int(h * 0.45)
    # Exhaust plume rectangles
    for x in (blue_x, red_x):
        pygame.draw.polygon(
            surface,
            (255, 180, 80),
            [
                (x - 8, ship_y + 22),
                (x + 8, ship_y + 22),
                (x + 4, ship_y + 60),
                (x - 4, ship_y + 60),
            ],
        )
        pygame.draw.polygon(
            surface,
            (255, 240, 180),
            [
                (x - 4, ship_y + 22),
                (x + 4, ship_y + 22),
                (x + 2, ship_y + 48),
                (x - 2, ship_y + 48),
            ],
        )
    _blit_centred(surface, ship_blue, blue_x, ship_y)
    _blit_centred(surface, ship_red, red_x, ship_y)
