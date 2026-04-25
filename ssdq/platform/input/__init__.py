"""Platform input layer.

Pygame events live exclusively here; ``core/`` only ever sees ``PlayerInput``.
"""

from __future__ import annotations

from ssdq.platform.input.gamepad import GamepadProvider
from ssdq.platform.input.keyboard import KeyboardProvider
from ssdq.platform.input.provider import InputProvider, select_provider

__all__ = [
    "GamepadProvider",
    "InputProvider",
    "KeyboardProvider",
    "select_provider",
]
