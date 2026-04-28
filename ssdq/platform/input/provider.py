"""Input provider protocol + selection.

The rest of the engine sees only ``InputProvider.poll() -> (PlayerInput, PlayerInput)``;
which concrete provider answers the call depends on the host environment.

Selection rules (in order):

* If ``SSDQ_KEYBOARD=1``, return :class:`KeyboardProvider`.
* Otherwise return :class:`GamepadProvider` regardless of whether any pads are
  presently connected — pads can be attached at any point and assignment is
  driven by START presses (per spec §8.2). The provider yields neutral input
  for unbound slots until a pad presses START.
"""

from __future__ import annotations

import os
from typing import Callable, Protocol, runtime_checkable

from ssdq.core.types import PlayerInput, PlayerSlot
from ssdq.platform.input.bindings import BindingsStore
from ssdq.platform.input.gamepad import GamepadProvider
from ssdq.platform.input.keyboard import KeyboardProvider

KEYBOARD_ENV_VAR = "SSDQ_KEYBOARD"


@runtime_checkable
class InputProvider(Protocol):
    """Per-tick source of two ``PlayerInput`` values.

    Concrete providers (gamepad, keyboard, replay) all conform to this shape so
    the scene layer never branches on input source.
    """

    def poll(self) -> tuple[PlayerInput, PlayerInput]:
        """Sample input for the current tick. Must be called exactly once per
        tick — providers maintain edge-detection state internally."""
        ...

    @property
    def disconnected(self) -> PlayerSlot | None:
        """If a previously-bound device has gone away, the slot it was bound
        to. The pause-overlay scene consults this each tick."""
        ...


def select_provider(
    *,
    bindings: BindingsStore | None = None,
    on_pad_bound: Callable[[str, str], None] | None = None,
) -> InputProvider:
    """Pick the right provider for this environment. Pumps pygame events
    once during construction so the chosen provider can settle its initial
    button state without an external init step.

    ``bindings`` is forwarded to the gamepad provider so per-pad rebinds
    saved by the SettingsScene take effect. ``on_pad_bound`` is fired with
    ``(guid, name)`` when the gamepad provider claims a slot — main.py uses
    it to populate ``AppState.last_active_pad_*`` so the SettingsScene knows
    which pad to configure. Keyboard provider ignores both (keyboard
    rebinding is dev-only and behind ``SSDQ_KEYBOARD``)."""
    if os.environ.get(KEYBOARD_ENV_VAR) == "1":
        return KeyboardProvider()
    return GamepadProvider(bindings=bindings, on_pad_bound=on_pad_bound)
