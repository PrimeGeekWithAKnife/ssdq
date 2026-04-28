"""Settings scene — gamepad button rebinding (kid playtest 2026-04-28 #1, #2).

Action-list-and-edit flow:

* Up/down on left stick cycles the selected action.
* FIRE/CONFIRM enters capture mode for the selected action.
* In capture mode, the next ``pygame.JOYBUTTONDOWN`` becomes the new
  binding. CANCEL exits capture mode without saving.
* CANCEL outside capture mode pops the scene back to where we came from
  (Title or in-game pause).

Capture mode reads raw pygame events directly so the rebind flow works
even if the player's *current* mapping is broken — exactly the case we're
fixing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Pop, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.platform.input.bindings import BindingAction
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (8, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_ROW_COLOUR = (200, 200, 200)
_ROW_SELECTED_COLOUR = (255, 240, 120)
_HINT_COLOUR = (130, 130, 150)
_CAPTURING_COLOUR = (255, 80, 80)

# Stick-Y → menu navigation: trigger only on rising edge crossing this
# magnitude so a held stick doesn't autoscroll past every row.
_NAV_THRESHOLD = 0.5


@dataclass(slots=True)
class _NavState:
    prev_y: float = 0.0


class SettingsScene(Scene):
    """Action list + edit; pop on CANCEL."""

    __slots__ = (
        "_app",
        "_pad_guid",
        "_pad_name",
        "_on_exit",
        "_actions",
        "selected_index",
        "capturing",
        "_capture_target",
        "_nav",
        "_title_font",
        "_row_font",
        "_hint_font",
    )

    def __init__(
        self,
        *,
        app: AppState,
        pad_guid: str,
        pad_name: str,
        on_exit: Callable[[], None] | None = None,
    ) -> None:
        self._app = app
        self._pad_guid = pad_guid
        self._pad_name = pad_name
        self._on_exit = on_exit
        self._actions: list[BindingAction] = list(BindingAction)
        self.selected_index: int = 0
        self.capturing: bool = False
        self._capture_target: BindingAction | None = None
        self._nav = _NavState()
        self._title_font: pygame.font.Font | None = None
        self._row_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None

    def selected_action(self) -> BindingAction:
        return self._actions[self.selected_index]

    # -- Scene API ------------------------------------------------------

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
        if self.capturing:
            # CANCEL exits capture mode without saving — handled here so the
            # PlayerInput-derived cancel still works even if the bound CANCEL
            # button is the one being rebound (capture mode listens to raw
            # JOYBUTTONDOWN, but PlayerInput is built from the *previous*
            # binding which is still valid).
            if inputs[0].cancel or inputs[1].cancel:
                self.capturing = False
                self._capture_target = None
                return None
            return self._tick_capture()
        return self._tick_navigate(inputs)

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._row_font is None or self._hint_font is None:
            return
        w, _h = surface.get_size()
        title = self._title_font.render("REBIND PAD", True, _TITLE_COLOUR)
        surface.blit(title, title.get_rect(center=(w // 2, 60)))
        sub = self._hint_font.render(
            f"Pad: {self._pad_name or self._pad_guid or 'default'}",
            True,
            _HINT_COLOUR,
        )
        surface.blit(sub, sub.get_rect(center=(w // 2, 100)))
        if self._app.bindings is None:
            return
        binding = self._app.bindings.get(self._pad_guid, pad_name=self._pad_name)
        y = 160
        for i, action in enumerate(self._actions):
            colour = _ROW_SELECTED_COLOUR if i == self.selected_index else _ROW_COLOUR
            label = action.value.upper().replace("_", " ")
            idx = binding.button_for(action)
            row = self._row_font.render(f"{label:<14}  button {idx}", True, colour)
            surface.blit(row, row.get_rect(center=(w // 2, y)))
            y += 36
        if self.capturing:
            cap = self._row_font.render(
                f"Press a button for {self.selected_action().value.upper()}"
                "  (cancel to skip)",
                True,
                _CAPTURING_COLOUR,
            )
            surface.blit(cap, cap.get_rect(center=(w // 2, y + 30)))
        else:
            hint = self._hint_font.render(
                "Up/Down: select   FIRE: rebind   CANCEL: back",
                True,
                _HINT_COLOUR,
            )
            surface.blit(hint, hint.get_rect(center=(w // 2, y + 30)))

    def exit(self, world: World) -> None:
        # Persist on the way out — any rebinds applied in capture mode
        # already mutated the in-memory store via .set().
        if self._app.bindings is not None:
            self._app.bindings.save()
        if self._on_exit is not None:
            self._on_exit()

    # -- internals ------------------------------------------------------

    def _tick_navigate(
        self, inputs: tuple[PlayerInput, PlayerInput]
    ) -> SceneTransition | None:
        i = inputs[0]  # P1 only — settings is single-player
        # Stick-Y rising-edge detection (down = +Y in pygame convention).
        y = i.move.y
        if y > _NAV_THRESHOLD and self._nav.prev_y <= _NAV_THRESHOLD:
            self.selected_index = (self.selected_index + 1) % len(self._actions)
        elif y < -_NAV_THRESHOLD and self._nav.prev_y >= -_NAV_THRESHOLD:
            self.selected_index = (self.selected_index - 1) % len(self._actions)
        self._nav.prev_y = y
        if i.confirm:
            self.capturing = True
            self._capture_target = self.selected_action()
            # Drain any pending JOYBUTTONDOWN so the FIRE press that
            # entered capture mode doesn't immediately re-bind.
            pygame.event.get(eventtype=pygame.JOYBUTTONDOWN)
            return None
        if i.cancel:
            return Pop()
        return None

    def _tick_capture(self) -> SceneTransition | None:
        # Read raw pygame button events directly — we deliberately do NOT use
        # PlayerInput here because the bindings being captured are exactly
        # what generates that. Tolerate any pad: rebinding on the wrong pad
        # is harmless because each pad has its own GUID-keyed entry, and
        # single-pad-per-slot is the common case.
        for event in pygame.event.get(eventtype=pygame.JOYBUTTONDOWN):
            idx = int(getattr(event, "button", -1))
            if idx < 0 or self._capture_target is None:
                continue
            if self._app.bindings is not None:
                cur = self._app.bindings.get(self._pad_guid, pad_name=self._pad_name)
                self._app.bindings.set(
                    self._pad_guid,
                    pad_name=self._pad_name,
                    binding=cur.with_button(self._capture_target, idx),
                )
            self.capturing = False
            self._capture_target = None
            return None
        return None
