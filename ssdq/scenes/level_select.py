"""Level-select dev menu — jump straight to any defined level.

Reachable from TitleScene's "LEVELS" option. CANCEL pops back to Title;
CONFIRM on a row replaces the stack with ``LevelScene(level_index=N)``.
No state seeding — the destination LevelScene initialises lives, weapon
tier, and charges from the same defaults as a normal Title→PLAY entry.
This is intentional: the menu is for testing level content, not for
testing arbitrary mid-game inventory states.

Menu contract: ``rows`` is a tuple of ``(kind, payload)`` —
``("level", index)`` for every level in the bundle (sorted), plus a
final ``("hyperspace", None)`` bonus row that launches the hyperspace
ride with ``exit_to="level_select"`` so it returns here when the run
ends (fun review 2026-06-12: the kid gets to replay the bonus run on
demand). Both launch paths clear progression first.
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Pop, Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (8, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_ROW_COLOUR = (200, 200, 200)
_ROW_SELECTED_COLOUR = (255, 240, 120)
_BONUS_ROW_COLOUR = (140, 220, 255)
_HINT_COLOUR = (130, 130, 150)

# Stick-Y rising-edge threshold — same convention as the other menus.
_NAV_THRESHOLD = 0.5


class LevelSelectScene(Scene):
    """Vertical list of every defined level + the hyperspace bonus row.
    CONFIRM launches the selection."""

    __slots__ = (
        "_app",
        "_hint_font",
        "_prev_y",
        "_row_font",
        "_title_font",
        "rows",
        "selected_index",
    )

    def __init__(self, app: AppState) -> None:
        self._app = app
        level_rows: list[tuple[str, int | None]] = [
            ("level", i) for i in sorted(app.content.levels.keys())
        ]
        # Bonus row sits last so the level list keeps its familiar order.
        self.rows: tuple[tuple[str, int | None], ...] = (
            *level_rows,
            ("hyperspace", None),
        )
        self.selected_index: int = 0
        self._prev_y: float = 0.0
        self._title_font: pygame.font.Font | None = None
        self._row_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 56, bold=True)
        self._row_font = pygame.font.SysFont(None, 32)
        self._hint_font = pygame.font.SysFont(None, 22)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        i = inputs[0]
        y = i.move.y if abs(i.move.y) >= abs(inputs[1].move.y) else inputs[1].move.y
        if y > _NAV_THRESHOLD and self._prev_y <= _NAV_THRESHOLD:
            self.selected_index = (self.selected_index + 1) % len(self.rows)
        elif y < -_NAV_THRESHOLD and self._prev_y >= -_NAV_THRESHOLD:
            self.selected_index = (self.selected_index - 1) % len(self.rows)
        self._prev_y = y

        if i.cancel or inputs[1].cancel:
            return Pop()
        if i.confirm or inputs[1].confirm:
            kind, payload = self.rows[self.selected_index]
            # Dev-jump should always begin with default state — don't leak
            # stockpile / score from whatever the user did before opening
            # the menu (kid playtest 2026-04-28 #4).
            self._app.clear_progression()
            if kind == "hyperspace":
                from ssdq.scenes.hyperspace import HyperspaceScene

                return Replace(scene=HyperspaceScene(self._app, exit_to="level_select"))
            from ssdq.scenes.level import LevelScene

            assert payload is not None
            self._app.current_level = payload
            return Replace(scene=LevelScene(self._app, level_index=payload))
        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._row_font is None or self._hint_font is None:
            return
        w, _h = surface.get_size()
        title = self._title_font.render("LEVEL SELECT", True, _TITLE_COLOUR)
        surface.blit(title, title.get_rect(center=(w // 2, 80)))

        y = 160
        for i, (kind, payload) in enumerate(self.rows):
            selected = i == self.selected_index
            if kind == "hyperspace":
                colour = _ROW_SELECTED_COLOUR if selected else _BONUS_ROW_COLOUR
                label = "HYPERSPACE RUN  - BONUS -"
            else:
                colour = _ROW_SELECTED_COLOUR if selected else _ROW_COLOUR
                level = self._app.content.levels[payload]
                label = f"{payload}  —  {level.title}"
            row = self._row_font.render(label, True, colour)
            surface.blit(row, row.get_rect(center=(w // 2, y)))
            y += 40

        hint = self._hint_font.render(
            "Up/Down: select   FIRE: launch   CANCEL: back", True, _HINT_COLOUR
        )
        surface.blit(hint, hint.get_rect(center=(w // 2, y + 30)))

    def exit(self, world: World) -> None:
        return None
