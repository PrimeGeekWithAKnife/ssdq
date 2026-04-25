"""Game-over scene: shows final scores, prompts return to title."""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState
from ssdq.scenes.title import TitleScene

_BG_COLOUR = (16, 4, 8)
_TITLE_COLOUR = (255, 100, 100)
_TEXT_COLOUR = (220, 220, 220)


class GameOverScene(Scene):
    """Final scores + 'press FIRE to return to title'."""

    __slots__ = ("_app", "_body_font", "_title_font")

    def __init__(self, app: AppState) -> None:
        self._app = app
        self._title_font: pygame.font.Font | None = None
        self._body_font: pygame.font.Font | None = None

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 96, bold=True)
        self._body_font = pygame.font.SysFont(None, 32)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        if inputs[0].confirm or inputs[1].confirm or inputs[0].fire or inputs[1].fire:
            return Replace(scene=TitleScene(self._app))
        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._body_font is None:
            return
        w, h = surface.get_size()
        title = self._title_font.render("GAME OVER", True, _TITLE_COLOUR)
        surface.blit(title, title.get_rect(center=(w // 2, h // 3)))
        lines = [
            f"Team score: {self._app.last_team_score:08d}",
            f"P1: {self._app.last_p1_score:08d}    P2: {self._app.last_p2_score:08d}",
            "",
            "Press FIRE to return to title",
        ]
        y = h // 2
        for line in lines:
            r = self._body_font.render(line, True, _TEXT_COLOUR)
            surface.blit(r, r.get_rect(center=(w // 2, y)))
            y += r.get_height() + 8

    def exit(self, world: World) -> None:
        return None
