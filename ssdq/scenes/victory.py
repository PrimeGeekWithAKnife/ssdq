"""Victory scene — final beat after Boss 5 dies.

Kid playtest 2026-04-28: "after the final boss the game does not end?
No victory text about how the players saved billions of lives."

Triggered by LevelCompleteScene when the cleared level has no successor
in the content bundle. CONFIRM/FIRE returns to Title with progression
reset so a follow-up run starts clean.
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (4, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_BODY_COLOUR = (220, 230, 255)
_HINT_COLOUR = (130, 150, 180)


class VictoryScene(Scene):
    """End-of-campaign celebration screen."""

    __slots__ = ("_app", "_title_font", "_body_font", "_hint_font")

    def __init__(self, app: AppState) -> None:
        self._app = app
        self._title_font: pygame.font.Font | None = None
        self._body_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 96, bold=True)
        self._body_font = pygame.font.SysFont(None, 36)
        self._hint_font = pygame.font.SysFont(None, 24)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        if not (
            inputs[0].confirm or inputs[1].confirm or inputs[0].fire or inputs[1].fire
        ):
            return None
        from ssdq.scenes.title import TitleScene

        # Reset campaign state so a fresh run after the credits begins
        # at level 1 with no carry-forward stockpile or score.
        self._app.clear_progression()
        self._app.current_level = 1
        self._app.completed_level = False
        return Replace(scene=TitleScene(self._app))

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._body_font is None or self._hint_font is None:
            return
        w, h = surface.get_size()
        banner = self._title_font.render("VICTORY", True, _TITLE_COLOUR)
        surface.blit(banner, banner.get_rect(center=(w // 2, h // 4)))
        lines = [
            "Earth's defence fleet is broken.",
            "The alien armada falls back.",
            "",
            "You saved billions of lives.",
            "",
            f"Final team score: {self._app.last_team_score:08d}",
            f"P1: {self._app.last_p1_score:08d}    P2: {self._app.last_p2_score:08d}",
        ]
        y = h // 2 - 80
        for line in lines:
            r = self._body_font.render(line, True, _BODY_COLOUR)
            surface.blit(r, r.get_rect(center=(w // 2, y)))
            y += r.get_height() + 8
        hint = self._hint_font.render("FIRE to return to title", True, _HINT_COLOUR)
        surface.blit(hint, hint.get_rect(center=(w // 2, h - 60)))

    def exit(self, world: World) -> None:
        return None
