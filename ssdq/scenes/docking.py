"""Docking / supply-ship inter-level scene.

Plays AFTER LevelCompleteScene and BEFORE the next LevelScene. A scripted
~5 second cinematic in which a "Supply Ship" descends from the top of
the screen toward both player ships parked at the bottom, briefly docks,
and grants +2 bombs per player. Designed as the seam between cleared
levels in a longer campaign — the slice currently has only level 1, so
on completion this scene transitions back to TitleScene.

Future-self note: when level 2 lands, replace the TitleScene transition
with a NextLevelScene (or simply ``LevelScene(self._app, level_index=
self._app.current_level + 1)`` after bumping ``current_level`` here).
The bomb bonus is delivered via ``AppState.bomb_bonus_pending`` which
``LevelScene.enter`` consumes one-shot, so adding more campaign scenes
between docking and level start won't double-award.

Skippable with FIRE/CONFIRM after a 1-second minimum so a kid mashing
fire from LevelComplete doesn't accidentally skip the entire animation.
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.clock import TICK_DT
from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

# ---------------- design constants ----------------

# Total scripted duration in sim seconds. The supply ship slides down,
# pauses to "dock", then the bonus banner pops in.
_TOTAL_SECONDS = 5.0
# Earliest tick at which the player can skip with FIRE — short enough to
# not feel imprisoned, long enough to absorb a held-fire input from the
# LevelComplete → Docking transition.
_SKIP_AFTER_SECONDS = 1.0
# When the supply ship reaches y=_DOCK_Y, the dock-pause begins.
_DOCK_Y_FRAC = 0.42  # fraction of screen height
# Bombs to grant each player on completion.
_BOMB_BONUS = 2

_BG_COLOUR = (4, 6, 20)
_BANNER_COLOUR = (255, 220, 120)
_DOCKING_COLOUR = (180, 220, 255)
_HINT_COLOUR = (160, 160, 180)
_PLAYER_BLUE = (110, 170, 255)
_PLAYER_RED = (255, 110, 110)


class DockingScene(Scene):
    """Scripted supply-ship docking animation between levels.

    Fixed-step animation: the per-tick `_advance` updates ``_elapsed`` and
    derives positions/text alpha from it. Determinism is preserved because
    we drive everything off integer tick counts (TICK_DT * count), not
    wall-clock.
    """

    __slots__ = (
        "_app",
        "_banner_font",
        "_bonus_applied",
        "_elapsed",
        "_skipped",
        "_status_font",
        "_supply_sprite",
        "_title_font",
    )

    def __init__(self, app: AppState) -> None:
        self._app = app
        self._elapsed: float = 0.0
        self._skipped: bool = False
        self._bonus_applied: bool = False
        self._title_font: pygame.font.Font | None = None
        self._banner_font: pygame.font.Font | None = None
        self._status_font: pygame.font.Font | None = None
        self._supply_sprite: pygame.Surface | None = None

    # ---------------- lifecycle ----------------

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 64, bold=True)
        self._banner_font = pygame.font.SysFont(None, 44, bold=True)
        self._status_font = pygame.font.SysFont(None, 28)
        self._supply_sprite = _try_load_supply_sprite()
        # Stage the bonus immediately so even if the player skips before
        # the dock-pause we still credit the bombs (prevents a "skip too
        # early" foot-gun).
        self._stage_bonus()

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        self._elapsed += TICK_DT

        # Skippable after the minimum hold window.
        if self._elapsed >= _SKIP_AFTER_SECONDS:
            wants_skip = (
                inputs[0].fire or inputs[1].fire or inputs[0].confirm or inputs[1].confirm
            )
            if wants_skip:
                self._skipped = True

        if self._elapsed >= _TOTAL_SECONDS or self._skipped:
            return self._next_scene()
        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._banner_font is None or self._status_font is None:
            return
        w, h = surface.get_size()

        _draw_starfield(surface)

        # Player ships at the bottom (parked, awaiting supplies). Drawn as
        # tinted triangles so we don't depend on the SpriteAtlas being
        # available in this scene's draw context.
        ship_y = int(h * 0.86)
        _draw_player_marker(surface, w // 2 - 90, ship_y, _PLAYER_BLUE)
        _draw_player_marker(surface, w // 2 + 90, ship_y, _PLAYER_RED)

        # Supply ship descending from the top toward the dock-line, then
        # holding station while the bonus banner reads out.
        dock_y = int(h * _DOCK_Y_FRAC)
        approach_seconds = 2.5
        if self._elapsed <= approach_seconds:
            t = self._elapsed / approach_seconds
            supply_y = int(-80 + (dock_y + 80) * t)
        else:
            supply_y = dock_y
        _draw_supply_ship(surface, w // 2, supply_y, self._supply_sprite)

        # "DOCKING..." status while the supply ship is en route.
        if self._elapsed <= approach_seconds:
            status = self._status_font.render("DOCKING...", True, _DOCKING_COLOUR)
            surface.blit(status, status.get_rect(center=(w // 2, 60)))
        else:
            # Bonus banner — fade in over the first 0.4s after dock.
            banner_t = min(1.0, (self._elapsed - approach_seconds) / 0.4)
            alpha_v = int(255 * banner_t)
            banner = self._banner_font.render(
                "+2 BOMBS — SUPPLIES RECEIVED", True, _BANNER_COLOUR
            )
            banner.set_alpha(alpha_v)
            surface.blit(banner, banner.get_rect(center=(w // 2, h // 2 + 30)))

        # Skip hint once it's available.
        if self._elapsed >= _SKIP_AFTER_SECONDS:
            hint = self._status_font.render("Press FIRE to skip", True, _HINT_COLOUR)
            surface.blit(hint, hint.get_rect(center=(w // 2, h - 30)))

    def exit(self, world: World) -> None:
        return None

    # ---------------- internals ----------------

    def _stage_bonus(self) -> None:
        """Queue the bomb bonus for the next LevelScene to apply.

        We accumulate (rather than overwrite) so back-to-back dockings
        across multiple levels stack correctly. ``LevelScene.enter``
        consumes and zeroes the field one-shot.
        """
        if self._bonus_applied:
            return
        self._app.bomb_bonus_pending += _BOMB_BONUS
        self._bonus_applied = True

    def _next_scene(self) -> SceneTransition:
        # If there's a next level in the bundle (LevelCompleteScene
        # already advanced ``app.current_level`` to it), launch it.
        # Otherwise the campaign is over for now → Title.
        bundle = self._app.content
        if self._app.current_level in bundle.levels:
            from ssdq.scenes.level import LevelScene

            return Replace(scene=LevelScene(self._app, level_index=self._app.current_level))
        from ssdq.scenes.title import TitleScene

        return Replace(scene=TitleScene(self._app))


# ---------------- drawing helpers ----------------


def _try_load_supply_sprite() -> pygame.Surface | None:
    """Best-effort load of the bomber sprite as our supply ship.

    Scaled up and rotated 180° so it faces downward toward the players.
    Returns None on failure — the renderer will fall back to a solid
    polygon so the scene still works in headless smoke tests.
    """
    from pathlib import Path

    sprite_path = Path("content") / "assets" / "sprites" / "enemies" / "bomber.png"
    if not sprite_path.is_file():
        return None
    try:
        surf = pygame.image.load(str(sprite_path))
        if pygame.display.get_init() and pygame.display.get_surface() is not None:
            surf = surf.convert_alpha()
    except pygame.error:
        return None
    # Scale up ~1.6× to read as a "big" supply hauler vs a regular enemy.
    w, h = surf.get_size()
    surf = pygame.transform.scale(surf, (int(w * 1.6), int(h * 1.6)))
    # Rotate 180° so the ship is nose-down, descending toward the players.
    surf = pygame.transform.rotate(surf, 180)
    return surf


def _draw_supply_ship(
    surface: pygame.Surface,
    cx: int,
    cy: int,
    sprite: pygame.Surface | None,
) -> None:
    if sprite is not None:
        surface.blit(sprite, sprite.get_rect(center=(cx, cy)))
        return
    # Fallback: draw a chunky downward-pointing pentagon so the scene is
    # legible even without the sprite (e.g. in unit tests).
    pygame.draw.polygon(
        surface,
        (200, 210, 220),
        [
            (cx - 40, cy - 30),
            (cx + 40, cy - 30),
            (cx + 40, cy + 10),
            (cx, cy + 50),
            (cx - 40, cy + 10),
        ],
    )
    # A few "exhaust" rectangles on top to read as a ship's engines.
    pygame.draw.rect(surface, (140, 150, 160), pygame.Rect(cx - 30, cy - 38, 12, 8))
    pygame.draw.rect(surface, (140, 150, 160), pygame.Rect(cx + 18, cy - 38, 12, 8))


def _draw_player_marker(
    surface: pygame.Surface, cx: int, cy: int, colour: tuple[int, int, int]
) -> None:
    """Small upward-pointing triangle silhouette for a player ship."""
    pygame.draw.polygon(
        surface,
        colour,
        [(cx, cy - 22), (cx - 18, cy + 14), (cx + 18, cy + 14)],
    )


def _draw_starfield(surface: pygame.Surface) -> None:
    """Sparse deterministic star pattern — same approach as IntroScene."""
    w, h = surface.get_size()
    for sx, sy in (
        (60, 50),
        (180, 110),
        (300, 70),
        (440, 130),
        (560, 90),
        (700, 60),
        (820, 120),
        (940, 80),
        (1080, 140),
        (140, 250),
        (320, 290),
        (500, 230),
        (740, 280),
        (980, 240),
        (1180, 290),
    ):
        if sx < w and sy < h:
            surface.set_at((sx, sy), (160, 160, 200))
