"""HUD overlay — lives, bombs, weapon level, scores.

Reads from a duck-typed ``CoopState`` resource on the world. Builder A owns
the canonical type; we only require the attribute shape:

  state.p1.lives           int
  state.p1.bombs           int
  state.p1.weapon_level    int
  state.p1.score           int
  state.p2.lives  /  bombs  /  weapon_level  /  score   (same)
  state.team_score         int

If the resource isn't present (e.g. on the title screen) :meth:`Hud.draw`
no-ops. Layout is fixed for the slice's 1280x720 playfield; we lay things
out relative to the surface size so nothing breaks if the window is sized
differently.
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World

# Layout constants. Centralised so screenshot tests have one place to look.
_PADDING = 16
_PANEL_LINE_GAP = 6
_TEAM_FONT_SIZE = 48
_PANEL_FONT_SIZE = 22
_SCORE_FONT_SIZE = 20

_TEXT_COLOUR = (240, 240, 240)
_TEAM_COLOUR = (255, 240, 120)
_P1_COLOUR = (120, 180, 255)
_P2_COLOUR = (255, 140, 140)
_HINT_COLOUR = (180, 180, 200)
# Task #9 — inventory rows (shields/missiles/drones) read distinctly
# from the lives/bombs row so the kid can tell at a glance which is
# auto-spent vs equippable.
_INVENTORY_COLOUR = (200, 220, 255)


class Hud:
    """Stateless HUD draw helper. Caches font handles so we only build them once."""

    __slots__ = ("_panel_font", "_score_font", "_team_font")

    def __init__(self) -> None:
        # SysFont with name=None falls back to pygame's bundled default font,
        # which is always available — even on a headless dummy driver.
        self._team_font = self._make_font(_TEAM_FONT_SIZE, bold=True)
        self._panel_font = self._make_font(_PANEL_FONT_SIZE, bold=False)
        self._score_font = self._make_font(_SCORE_FONT_SIZE, bold=False)

    @staticmethod
    def _make_font(size: int, *, bold: bool) -> pygame.font.Font:
        if not pygame.font.get_init():
            pygame.font.init()
        try:
            return pygame.font.SysFont(None, size, bold=bold)
        except pygame.error:  # pragma: no cover — extreme fallback
            return pygame.font.Font(None, size)

    # ---------------- public ----------------

    def draw(self, world: World, surface: pygame.Surface) -> None:
        """Draw the HUD onto ``surface``. No-op if no CoopState resource."""
        state = _try_coop_state(world)
        if state is None:
            return
        self._draw_team_score(surface, _attr_int(state, "team_score"))
        self._draw_panel(
            surface,
            label="P1",
            colour=_P1_COLOUR,
            stats=_player_stats(state, "p1"),
            x=_PADDING,
            anchor_left=True,
        )
        self._draw_panel(
            surface,
            label="P2",
            colour=_P2_COLOUR,
            stats=_player_stats(state, "p2"),
            x=surface.get_width() - _PADDING,
            anchor_left=False,
        )
        self._draw_controls_hint(surface)

    def _draw_controls_hint(self, surface: pygame.Surface) -> None:
        """Tiny bottom-centre reminder of the controls — kid playtest
        showed bombs weren't obvious."""
        text = "FIRE: A   BOMB: X/Y   SHIELD: LB   MISSILE: RB   PAUSE: START"
        rendered = self._score_font.render(text, True, _HINT_COLOUR)
        rect = rendered.get_rect(midbottom=(surface.get_width() // 2, surface.get_height() - 6))
        surface.blit(rendered, rect)

    # ---------------- internals ----------------

    def _draw_team_score(self, surface: pygame.Surface, team_score: int) -> None:
        text = f"TEAM  {team_score:08d}"
        rendered = self._team_font.render(text, True, _TEAM_COLOUR)
        rect = rendered.get_rect(midtop=(surface.get_width() // 2, _PADDING))
        surface.blit(rendered, rect)

    def _draw_panel(
        self,
        surface: pygame.Surface,
        *,
        label: str,
        colour: tuple[int, int, int],
        stats: _PlayerStats,
        x: int,
        anchor_left: bool,
    ) -> None:
        lines: list[tuple[str, tuple[int, int, int]]] = [
            (label, colour),
            (f"Lives: {stats.lives}", _TEXT_COLOUR),
            (f"Bombs: {stats.bombs}", _TEXT_COLOUR),
            (f"Weapon Lv {stats.weapon_level}", _TEXT_COLOUR),
        ]
        # Equippable / drone inventory rows — only render when non-zero so a
        # clean session doesn't clutter the panel with zeroed-out counters.
        if stats.shield_charges:
            lines.append((f"[S] Shield x{stats.shield_charges}", (140, 255, 240)))
        if stats.missile_level:
            lines.append((f"Missile Lv {stats.missile_level}", (255, 180, 100)))
        if stats.drones:
            lines.append((f"Drones: {stats.drones}", _INVENTORY_COLOUR))
        # Score is always last so its position relative to the bottom
        # of the panel is consistent regardless of inventory rows.
        score_idx = len(lines)
        lines.append((f"{stats.score:08d}", _TEXT_COLOUR))
        y = _PADDING
        # Layout: panel font for the label + stat lines, smaller score
        # font for the trailing 8-digit score number. The score line is
        # always the last entry (index == len(lines) - 1).
        last_idx = len(lines) - 1
        for i, (text, col) in enumerate(lines):
            font = self._score_font if i == last_idx else self._panel_font
            rendered = font.render(text, True, col)
            if anchor_left:
                rect = rendered.get_rect(topleft=(x, y))
            else:
                rect = rendered.get_rect(topright=(x, y))
            surface.blit(rendered, rect)
            y += rendered.get_height() + _PANEL_LINE_GAP


# ---------------- duck-typed coop state access ----------------


class _PlayerStats:
    __slots__ = (
        "bombs",
        "drones",
        "lives",
        "missile_level",
        "score",
        "shield_charges",
        "weapon_level",
    )

    def __init__(
        self,
        lives: int,
        bombs: int,
        weapon_level: int,
        score: int,
        *,
        shield_charges: int = 0,
        missile_level: int = 0,
        drones: int = 0,
    ) -> None:
        self.lives = lives
        self.bombs = bombs
        self.weapon_level = weapon_level
        self.score = score
        self.shield_charges = shield_charges
        self.missile_level = missile_level
        self.drones = drones


def _try_coop_state(world: World) -> Any | None:
    """Locate any resource exposing ``team_score``/``p1``/``p2`` attrs.

    We can't import the canonical type (Builder A owns it and we must not
    create a hard dependency), so iterate the resource map and pick by shape.
    """
    # World.try_resource needs a type; we don't have one here, so reach in.
    for resource in getattr(world, "_resources", {}).values():
        if hasattr(resource, "team_score") and hasattr(resource, "p1") and hasattr(resource, "p2"):
            return resource
    return None


def _attr_int(obj: Any, name: str) -> int:
    val = getattr(obj, name, 0)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _player_stats(state: Any, slot_attr: str) -> _PlayerStats:
    p = getattr(state, slot_attr, None)
    if p is None:
        return _PlayerStats(0, 0, 1, 0)
    return _PlayerStats(
        lives=_attr_int(p, "lives"),
        bombs=_attr_int(p, "bombs"),
        weapon_level=_attr_int(p, "weapon_level") or 1,
        score=_attr_int(p, "score"),
        shield_charges=_attr_int(p, "shield_charges"),
        missile_level=_attr_int(p, "missile_level"),
        drones=_attr_int(p, "drones"),
    )
