"""Gamepad input provider — wraps ``pygame.joystick``.

Only this module touches pygame joystick APIs; the rest of the engine consumes
``PlayerInput`` values produced by :meth:`GamepadProvider.poll`.

Behaviour summary (spec §8.1, §8.2):

* First pad whose START is pressed binds to P1; second to P2.
* Stick deadzone is radial 0.15, with the live region renormalised so 0.15..1.0
  maps to 0..1.0; magnitude is then clamped to 1.0.
* Edge-triggered: bomb, pause, confirm, cancel — true only on the tick the
  button transitions 0->1. Held: fire.
* Disconnect detection is per bound pad, exposed via :attr:`disconnected` for
  the pause-overlay scene to consult.

Button mapping is the Xbox-360 layout that pygame-CE reports for 8BitDo,
Steam Deck Controller (in XInput mode) and most generic XInput pads:

==================  =========================================
Button              Index
==================  =========================================
A (fire / confirm)   0
B (cancel)           1
X (bomb)             2
Y (also bomb)        3
LB / RB (bomb)       4 / 5
Back                 6
Start (pause)        7
==================  =========================================

**Bomb on multiple buttons** — kid playtest 2026-04-27 reported that no
button activated bombs on a Gamesir T4n Lite. Different controllers
sometimes shuffle the X/Y/LB/RB indices, so we accept any of those four
as a bomb press to maximise the chance the kid lands on something that
works.
"""

from __future__ import annotations

import math

import pygame

from ssdq.core.types import NEUTRAL_INPUT, PlayerInput, PlayerSlot, Vec2

# Stick deadzone — see ``_deadzone_stick``.
STICK_DEADZONE: float = 0.15

# Button indices on the canonical Xbox-360-style mapping.
BUTTON_A: int = 0
BUTTON_B: int = 1
BUTTON_X: int = 2
BUTTON_Y: int = 3
BUTTON_LB: int = 4
BUTTON_RB: int = 5
BUTTON_BACK: int = 6
BUTTON_START: int = 7

# Buttons that all map to "bomb" — see module docstring.
BOMB_BUTTONS: tuple[int, ...] = (BUTTON_X, BUTTON_Y, BUTTON_LB, BUTTON_RB)

# Left-stick axis indices.
AXIS_LX: int = 0
AXIS_LY: int = 1


def _deadzone_stick(x: float, y: float, deadzone: float = STICK_DEADZONE) -> Vec2:
    """Apply a radial deadzone, renormalise the live region, and clamp.

    A raw ``(x, y)`` whose magnitude is below ``deadzone`` returns the zero
    vector. Above it, the magnitude is rescaled so ``deadzone..1.0`` maps to
    ``0..1.0``; finally the result's magnitude is clamped to 1.0 to absorb any
    out-of-spec hardware values.
    """
    mag = math.sqrt(x * x + y * y)
    if mag <= deadzone:
        return Vec2(0.0, 0.0)
    # Renormalise: shift the live magnitude into [0, 1].
    live = (mag - deadzone) / (1.0 - deadzone)
    if live > 1.0:
        live = 1.0
    # Direction comes from the raw vector; magnitude from the renormalised value.
    inv = live / mag
    return Vec2(x * inv, y * inv)


class _PadState:
    """Per-pad edge-detection bookkeeping.

    We only remember the previous-tick value for buttons that need
    edge-detection; the rest are sampled live each tick. `prev_bomb`
    tracks the OR of all bomb-mapped buttons (X, Y, LB, RB).
    """

    __slots__ = ("prev_a", "prev_b", "prev_bomb", "prev_pause")

    def __init__(self) -> None:
        self.prev_a: bool = False
        self.prev_b: bool = False
        self.prev_bomb: bool = False
        self.prev_pause: bool = False


class GamepadProvider:
    """Polls pygame joysticks and emits ``(PlayerInput, PlayerInput)``.

    Construction initialises the joystick subsystem and opens every currently
    connected pad. New pads attached later are picked up automatically through
    pygame's ``JOYDEVICEADDED`` events on the next ``poll``.
    """

    def __init__(self) -> None:
        if not pygame.get_init():
            pygame.init()
        if not pygame.joystick.get_init():
            pygame.joystick.init()

        # All pads we know about, keyed by pygame instance id (stable across
        # the lifetime of one connection — unlike the dynamic device index).
        self._pads: dict[int, pygame.joystick.JoystickType] = {}
        # Edge-detection state, keyed the same way.
        self._states: dict[int, _PadState] = {}

        # Slot bindings: instance id of the pad bound to P1 / P2, or None.
        self._slot_pads: list[int | None] = [None, None]

        self._disconnected: PlayerSlot | None = None

        self._scan_initial_pads()

    # -- public API -----------------------------------------------------

    def poll(self) -> tuple[PlayerInput, PlayerInput]:
        """Return ``(P1 input, P2 input)`` for the current tick."""
        # Drain pygame events so joystick state is current. We deliberately
        # consume *all* events here — the scene layer doesn't watch raw pygame
        # events, only ``PlayerInput`` and our :attr:`disconnected` flag.
        for event in pygame.event.get():
            self._handle_event(event)

        # If a slot is bound but its pad has vanished, surface a disconnect.
        for idx, instance_id in enumerate(self._slot_pads):
            if instance_id is not None and instance_id not in self._pads:
                self._slot_pads[idx] = None
                self._disconnected = PlayerSlot(idx)

        # Try to bind any pad pressing START to the next free slot.
        self._assign_pads_pressing_start()

        return (self._read_slot(0), self._read_slot(1))

    @property
    def disconnected(self) -> PlayerSlot | None:
        """Slot whose bound pad most recently went away, else ``None``.

        The flag latches until :meth:`clear_disconnected` is called so a scene
        running its own poll cadence can't miss the transition.
        """
        return self._disconnected

    def clear_disconnected(self) -> None:
        """Acknowledge a disconnect notification (called by the pause scene
        once it has shown the reconnect overlay)."""
        self._disconnected = None

    # -- internals ------------------------------------------------------

    def _scan_initial_pads(self) -> None:
        for idx in range(pygame.joystick.get_count()):
            pad = pygame.joystick.Joystick(idx)
            pad.init()
            instance_id = pad.get_instance_id()
            self._pads[instance_id] = pad
            self._states[instance_id] = _PadState()

    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.JOYDEVICEADDED:
            # ``device_index`` here is the *device* index (passes to Joystick),
            # not the instance id.
            pad = pygame.joystick.Joystick(event.device_index)
            pad.init()
            instance_id = pad.get_instance_id()
            if instance_id not in self._pads:
                self._pads[instance_id] = pad
                self._states[instance_id] = _PadState()
        elif event.type == pygame.JOYDEVICEREMOVED:
            # Removed event carries the instance id directly.
            instance_id = int(event.instance_id)
            self._pads.pop(instance_id, None)
            self._states.pop(instance_id, None)

    def _assign_pads_pressing_start(self) -> None:
        # Skip if both slots are already bound.
        if self._slot_pads[0] is not None and self._slot_pads[1] is not None:
            return
        for instance_id, pad in self._pads.items():
            if instance_id in self._slot_pads:
                continue  # already bound to a slot
            if not _safe_button(pad, BUTTON_START):
                continue
            # Bind to first free slot.
            for slot_idx in (0, 1):
                if self._slot_pads[slot_idx] is None:
                    self._slot_pads[slot_idx] = instance_id
                    # Mark START as previously-pressed so we don't fire a
                    # spurious pause on the very next tick.
                    self._states[instance_id].prev_pause = True
                    break

    def _read_slot(self, slot_idx: int) -> PlayerInput:
        instance_id = self._slot_pads[slot_idx]
        if instance_id is None:
            return NEUTRAL_INPUT
        pad = self._pads.get(instance_id)
        if pad is None:
            return NEUTRAL_INPUT
        state = self._states[instance_id]

        move = _deadzone_stick(_safe_axis(pad, AXIS_LX), _safe_axis(pad, AXIS_LY))

        a = _safe_button(pad, BUTTON_A)
        b = _safe_button(pad, BUTTON_B)
        # Bomb = OR of every mapped bomb button (X / Y / LB / RB) — gamepad
        # button indices vary across vendors and we don't want the kid stuck
        # because their pad puts X at index 3 instead of 2.
        bomb_held = any(_safe_button(pad, btn) for btn in BOMB_BUTTONS)
        start = _safe_button(pad, BUTTON_START)

        # Edge-triggered: true only on 0 -> 1 transition.
        bomb = bomb_held and not state.prev_bomb
        pause = start and not state.prev_pause
        # A doubles as both held-fire and edge-triggered confirm in menus.
        confirm = a and not state.prev_a
        cancel = b and not state.prev_b

        # Update prev-tick state for next poll.
        state.prev_a = a
        state.prev_b = b
        state.prev_bomb = bomb_held
        state.prev_pause = start

        return PlayerInput(
            move=move,
            fire=a,
            bomb=bomb,
            pause=pause,
            confirm=confirm,
            cancel=cancel,
        )


def _safe_axis(pad: pygame.joystick.JoystickType, axis: int) -> float:
    """Read an axis, returning 0.0 if the pad doesn't expose it."""
    if axis >= pad.get_numaxes():
        return 0.0
    return float(pad.get_axis(axis))


def _safe_button(pad: pygame.joystick.JoystickType, button: int) -> bool:
    """Read a button, returning False if the pad doesn't expose it."""
    if button >= pad.get_numbuttons():
        return False
    return bool(pad.get_button(button))
