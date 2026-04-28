"""Title scene: PLAY / SETTINGS menu. Doubles as Lobby per spec §4.4.

Default selection is PLAY so existing reflexes (mash FIRE → start playing)
keep working. Stick-Y (or pad-D-pad) cycles between PLAY and SETTINGS;
selecting SETTINGS pushes the gamepad-rebind scene (kid playtest 2026-04-28
#1+#2 — players need to remap buttons that don't match the canonical
Xbox layout on cheap HID pads).
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Push, Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (8, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_PROMPT_COLOUR = (200, 200, 200)
_PROMPT_SELECTED_COLOUR = (255, 240, 120)
_HINT_COLOUR = (130, 130, 150)

# Stick-Y rising-edge threshold for menu navigation.
_NAV_THRESHOLD = 0.5

_OPTION_PLAY = "PLAY"
_OPTION_SETTINGS = "SETTINGS"
_OPTIONS: tuple[str, ...] = (_OPTION_PLAY, _OPTION_SETTINGS)


class TitleScene(Scene):
    """Two-option menu (PLAY / SETTINGS). Confirm activates the highlighted row."""

    __slots__ = (
        "_app",
        "_hint_font",
        "_prev_fire",
        "_prev_y",
        "_prompt_font",
        "_selected_index",
        "_title_font",
    )

    def __init__(self, app: AppState) -> None:
        self._app = app
        self._title_font: pygame.font.Font | None = None
        self._prompt_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None
        # Track previous-tick fire so we only transition on rising edge —
        # otherwise a player holding fire from the prior level would
        # immediately re-enter Level when GameOver bounces back to Title.
        self._prev_fire = True
        self._prev_y: float = 0.0
        self._selected_index: int = 0

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
        # Stick-Y rising-edge detection — same convention as SettingsScene.
        # Either player's stick can navigate the menu.
        y = inputs[0].move.y if abs(inputs[0].move.y) > abs(inputs[1].move.y) else inputs[1].move.y
        if y > _NAV_THRESHOLD and self._prev_y <= _NAV_THRESHOLD:
            self._selected_index = (self._selected_index + 1) % len(_OPTIONS)
        elif y < -_NAV_THRESHOLD and self._prev_y >= -_NAV_THRESHOLD:
            self._selected_index = (self._selected_index - 1) % len(_OPTIONS)
        self._prev_y = y

        fire_now = inputs[0].fire or inputs[1].fire
        confirm = inputs[0].confirm or inputs[1].confirm
        rising_fire = fire_now and not self._prev_fire
        self._prev_fire = fire_now
        if not (confirm or rising_fire):
            return None
        chosen = _OPTIONS[self._selected_index]
        if chosen == _OPTION_PLAY:
            from ssdq.scenes.level import LevelScene

            return Replace(scene=LevelScene(self._app, level_index=self._app.current_level))
        if chosen == _OPTION_SETTINGS:
            from ssdq.scenes.settings import SettingsScene

            return Push(
                scene=SettingsScene(
                    app=self._app,
                    pad_guid=getattr(self._app, "last_active_pad_guid", "") or "",
                    pad_name=getattr(self._app, "last_active_pad_name", "") or "",
                )
            )
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
        for i, option in enumerate(_OPTIONS):
            colour = _PROMPT_SELECTED_COLOUR if i == self._selected_index else _PROMPT_COLOUR
            text = self._prompt_font.render(option, True, colour)
            surface.blit(text, text.get_rect(center=(w // 2, h // 2 + i * 50)))
        hint = self._hint_font.render(
            "Up/Down: select   FIRE: choose", True, _HINT_COLOUR
        )
        surface.blit(hint, hint.get_rect(center=(w // 2, h // 2 + len(_OPTIONS) * 50 + 30)))

    def exit(self, world: World) -> None:
        return None
