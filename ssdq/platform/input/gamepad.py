"""Gamepad input provider — wraps ``pygame.joystick``.

Only this module touches pygame joystick APIs; the rest of the engine consumes
``PlayerInput`` values produced by :meth:`GamepadProvider.poll`.

Behaviour summary (spec §8.1, §8.2):

* First pad whose any-button is pressed binds to P1; second to P2.
* Stick deadzone is radial 0.15, with the live region renormalised so 0.15..1.0
  maps to 0..1.0; magnitude is then clamped to 1.0.
* Edge-triggered: bomb, pause, confirm, cancel, shield, missile, drone_cycle —
  true only on the tick the button transitions 0->1. Held: fire.
* Disconnect detection is per bound pad, exposed via :attr:`disconnected` for
  the pause-overlay scene to consult.

Button assignment is no longer hard-coded — each pad's mapping comes from a
:class:`BindingsStore` keyed by SDL pad GUID. Defaults match the canonical
Xbox-360 layout (FIRE=0, BOMB=2, SHIELD=4, MISSILE=5, DRONE_CYCLE=6, PAUSE=7,
CONFIRM=0, CANCEL=1) so working pads keep working without any setup.

The kid playtest 2026-04-28 found two pads (Zikway HID, Gamesir T4n Lite)
where these defaults didn't match the physical buttons; the SettingsScene
reachable from Title and Pause lets the player rebind any action.
"""

from __future__ import annotations

import math

import pygame

from ssdq.core.types import NEUTRAL_INPUT, PlayerInput, PlayerSlot, Vec2
from ssdq.platform.input.bindings import BindingAction, BindingsStore

# Stick deadzone — see ``_deadzone_stick``.
STICK_DEADZONE: float = 0.15

# Button indices on the canonical Xbox-360-style mapping. Kept as named
# constants for the keyboard layer's parity tests; the gamepad runtime
# itself reads indices from the active :class:`BindingsStore` entry, not
# from these.
BUTTON_A: int = 0
BUTTON_B: int = 1
BUTTON_X: int = 2
BUTTON_Y: int = 3
BUTTON_LB: int = 4
BUTTON_RB: int = 5
BUTTON_BACK: int = 6
BUTTON_START: int = 7

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
    edge-detection; the rest are sampled live each tick. ``prev_a`` tracks
    the previously-held state of the CONFIRM-bound button; ``prev_b`` the
    CANCEL-bound button.
    """

    __slots__ = (
        "prev_a",
        "prev_b",
        "prev_back",
        "prev_bomb",
        "prev_missile",
        "prev_pause",
        "prev_shield",
    )

    def __init__(self) -> None:
        self.prev_a: bool = False
        self.prev_b: bool = False
        self.prev_back: bool = False
        self.prev_bomb: bool = False
        self.prev_pause: bool = False
        self.prev_shield: bool = False
        self.prev_missile: bool = False


class GamepadProvider:
    """Polls pygame joysticks and emits ``(PlayerInput, PlayerInput)``.

    Construction initialises the joystick subsystem and opens every currently
    connected pad. New pads attached later are picked up automatically through
    pygame's ``JOYDEVICEADDED`` events on the next ``poll``.

    The ``bindings`` argument is the :class:`BindingsStore` consulted on every
    poll for each slot's bound pad. Pass ``None`` (default) to use a store
    backed by ``~/.config/ssdq/bindings.json``.
    """

    def __init__(self, *, bindings: BindingsStore | None = None) -> None:
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

        self._bindings: BindingsStore = bindings if bindings is not None else BindingsStore()

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

        # Try to bind any pad with a button held down to the next free slot.
        self._assign_pads_with_button_press()

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

    def _assign_pads_with_button_press(self) -> None:
        # Bind any unbound pad to the first free slot as soon as the player
        # presses *any* button on it. Generic / Chinese HID pads shuffle the
        # button indices unpredictably (kid playtest 2026-04-27: a Zikway pad
        # had no working "Start" at index 7, so the game wouldn't start). We
        # accept any button so whatever the kid mashes counts; they can then
        # rebind from the SettingsScene if defaults misfire.
        if self._slot_pads[0] is not None and self._slot_pads[1] is not None:
            return
        for instance_id, pad in self._pads.items():
            if instance_id in self._slot_pads:
                continue  # already bound to a slot
            if not _any_button_pressed(pad):
                continue
            # Bind to first free slot.
            for slot_idx in (0, 1):
                if self._slot_pads[slot_idx] is None:
                    self._slot_pads[slot_idx] = instance_id
                    # Mark every edge-tracked button as previously-pressed so
                    # the act of binding doesn't also fire spurious edges on
                    # the very next tick (the kid is holding *every* button
                    # to wake the pad up).
                    state = self._states[instance_id]
                    binding = self._bindings.get(pad.get_guid(), pad_name=pad.get_name())
                    state.prev_a = _safe_button(
                        pad, binding.button_for(BindingAction.CONFIRM)
                    )
                    state.prev_b = _safe_button(
                        pad, binding.button_for(BindingAction.CANCEL)
                    )
                    state.prev_bomb = _safe_button(
                        pad, binding.button_for(BindingAction.BOMB)
                    )
                    state.prev_pause = _safe_button(
                        pad, binding.button_for(BindingAction.PAUSE)
                    )
                    state.prev_shield = _safe_button(
                        pad, binding.button_for(BindingAction.SHIELD)
                    )
                    state.prev_missile = _safe_button(
                        pad, binding.button_for(BindingAction.MISSILE)
                    )
                    state.prev_back = _safe_button(
                        pad, binding.button_for(BindingAction.DRONE_CYCLE)
                    )
                    break

    def _read_slot(self, slot_idx: int) -> PlayerInput:
        instance_id = self._slot_pads[slot_idx]
        if instance_id is None:
            return NEUTRAL_INPUT
        pad = self._pads.get(instance_id)
        if pad is None:
            return NEUTRAL_INPUT
        state = self._states[instance_id]
        binding = self._bindings.get(pad.get_guid(), pad_name=pad.get_name())

        move = _deadzone_stick(_safe_axis(pad, AXIS_LX), _safe_axis(pad, AXIS_LY))

        fire_held = _safe_button(pad, binding.button_for(BindingAction.FIRE))
        confirm_held = _safe_button(pad, binding.button_for(BindingAction.CONFIRM))
        cancel_held = _safe_button(pad, binding.button_for(BindingAction.CANCEL))
        bomb_held = _safe_button(pad, binding.button_for(BindingAction.BOMB))
        pause_held = _safe_button(pad, binding.button_for(BindingAction.PAUSE))
        shield_held = _safe_button(pad, binding.button_for(BindingAction.SHIELD))
        missile_held = _safe_button(pad, binding.button_for(BindingAction.MISSILE))
        back_held = _safe_button(pad, binding.button_for(BindingAction.DRONE_CYCLE))

        # Edge-triggered: true only on 0 -> 1 transition.
        bomb = bomb_held and not state.prev_bomb
        pause = pause_held and not state.prev_pause
        confirm = confirm_held and not state.prev_a
        cancel = cancel_held and not state.prev_b
        shield = shield_held and not state.prev_shield
        missile = missile_held and not state.prev_missile
        drone_cycle = back_held and not state.prev_back

        # Update prev-tick state for next poll.
        state.prev_a = confirm_held
        state.prev_b = cancel_held
        state.prev_bomb = bomb_held
        state.prev_pause = pause_held
        state.prev_shield = shield_held
        state.prev_missile = missile_held
        state.prev_back = back_held

        return PlayerInput(
            move=move,
            fire=fire_held,
            bomb=bomb,
            pause=pause,
            confirm=confirm,
            cancel=cancel,
            shield=shield,
            missile=missile,
            drone_cycle=drone_cycle,
        )


def _safe_axis(pad: pygame.joystick.JoystickType, axis: int) -> float:
    """Read an axis, returning 0.0 if the pad doesn't expose it."""
    if axis >= pad.get_numaxes():
        return 0.0
    return float(pad.get_axis(axis))


def _safe_button(pad: pygame.joystick.JoystickType, button: int) -> bool:
    """Read a button, returning False if the pad doesn't expose it."""
    if button < 0 or button >= pad.get_numbuttons():
        return False
    return bool(pad.get_button(button))


def _any_button_pressed(pad: pygame.joystick.JoystickType) -> bool:
    """True if any button on the pad is currently held."""
    return any(pad.get_button(i) for i in range(pad.get_numbuttons()))
