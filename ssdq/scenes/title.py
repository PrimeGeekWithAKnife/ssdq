"""Title scene: 'press START to play'. Doubles as Lobby per spec §4.4.

For the slice, both players are auto-bound — confirm-press from either
slot transitions to the Level scene. (Real lobby with START → P1/P2
ship select waits for the second-ship-type to exist; out-of-scope here.)
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (8, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_PROMPT_COLOUR = (200, 200, 200)
_HINT_COLOUR = (130, 130, 150)


class TitleScene(Scene):
    """One-screen splash. Press FIRE/CONFIRM (any pad) to start."""

    __slots__ = ("_app", "_hint_font", "_prev_fire", "_prompt_font", "_title_font")

    def __init__(self, app: AppState) -> None:
        self._app = app
        self._title_font: pygame.font.Font | None = None
        self._prompt_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None
        # Track previous-tick fire so we only transition on rising edge —
        # otherwise a player holding fire from the prior level would
        # immediately re-enter Level when GameOver bounces back to Title.
        self._prev_fire = True

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 96, bold=True)
        self._prompt_font = pygame.font.SysFont(None, 36)
        self._hint_font = pygame.font.SysFont(None, 24)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        fire_now = inputs[0].fire or inputs[1].fire
        confirm = inputs[0].confirm or inputs[1].confirm
        rising_fire = fire_now and not self._prev_fire
        self._prev_fire = fire_now
        if confirm or rising_fire:
            from ssdq.scenes.level import LevelScene

            return Replace(scene=LevelScene(self._app, level_index=self._app.current_level))
        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._prompt_font is None or self._hint_font is None:
            return
        w, h = surface.get_size()
        title = self._title_font.render("SSDQ", True, _TITLE_COLOUR)
        surface.blit(title, title.get_rect(center=(w // 2, h // 3)))
        prompt = self._prompt_font.render("Press FIRE to start", True, _PROMPT_COLOUR)
        surface.blit(prompt, prompt.get_rect(center=(w // 2, h // 2)))
        hint = self._hint_font.render(
            "2-player local co-op — gamepad or keyboard", True, _HINT_COLOUR
        )
        surface.blit(hint, hint.get_rect(center=(w // 2, h // 2 + 50)))

    def exit(self, world: World) -> None:
        return None
