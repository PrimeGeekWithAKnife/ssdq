"""Gamepad haptics — a null-object rumble bus.

Fun review 2026-06-12 R7. Mirrors :class:`ssdq.platform.audio.AudioBus`:
constructed once per session, hung on ``AppState``, reachable everywhere as
``self.app.rumble``. EVERY guard lives in here, so callers write
``self.app.rumble.pulse(slot, RumbleEvent.BOMB)`` unconditionally and never
branch on "is a pad even connected".

Degradation ladder — every rung is a silent no-op, never an exception:

* no pad resolver wired (keyboard provider, replay, smoke, unit tests)
* resolver returns None (slot unbound, or pad unplugged mid-level)
* pad object has no ``rumble`` attribute (the FakePad in
  tests/unit/test_input.py, or an SDL build without haptics)
* ``pad.rumble(...)`` returns False or raises — cheap HID pads whose driver
  advertises force-feedback it doesn't actually have, the same hardware class
  that shuffled button indices in kid playtest 2026-04-27
* the bus is disabled (global switch / ``SSDQ_NO_RUMBLE=1``)

A pad that fails once is LATCHED and never called again for the life of that
connection, so bomb-spam doesn't cost an ioctl per frame on a pad that will
never buzz. The latch keys on the pad's instance id (stable per connection,
exactly like ``GamepadProvider._pads``), so a replug gets a fresh chance.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from enum import Enum
from typing import Any

from ssdq.core.types import PlayerSlot

logger = logging.getLogger(__name__)

# Env kill-switch. Gives the Pi a zero-code escape hatch mid-playtest if a
# pad's motor turns out to be obnoxious, without a settings menu that does not
# exist yet.
NO_RUMBLE_ENV_VAR = "SSDQ_NO_RUMBLE"

# Hard ceiling so a future bad profile can never leave a motor spinning.
_MAX_DURATION_MS = 1000

PadResolver = Callable[[int], Any]


class RumbleEvent(Enum):
    """The four moments that earn a buzz.

    Deliberately few. Haptics desensitise fast, and a pad that rumbles on every
    kill reads as noise rather than feedback — which is why there is no
    per-kill event here even though the screen shake has one.
    """

    PLAYER_HIT = "player_hit"
    BOMB = "bomb"
    BOSS_KILL = "boss_kill"
    TIER_UP = "tier_up"


# (low_frequency, high_frequency, duration_ms). SDL exposes two motors: `low`
# is the big/heavy one (a deep thud), `high` the small one (a buzz). Both are
# 0..1 magnitudes.
#
#   PLAYER_HIT  the only NEGATIVE cue. Heavy-motor dominant so it reads as a
#               thud, and short enough to finish well inside the respawn
#               i-frames rather than bleeding into them.
#   BOMB        the biggest thing the kid does on purpose. Full heavy motor,
#               low buzz — a boom, not a rattle.
#   BOSS_KILL   longest, both motors, celebratory. Fires on BOTH pads.
#   TIER_UP     deliberately light and buzzy: a reward tick, not an impact.
_PROFILES: dict[RumbleEvent, tuple[float, float, int]] = {
    RumbleEvent.PLAYER_HIT: (0.85, 0.55, 260),
    RumbleEvent.BOMB: (1.00, 0.35, 400),
    RumbleEvent.BOSS_KILL: (0.90, 0.70, 650),
    RumbleEvent.TIER_UP: (0.35, 0.60, 150),
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class RumbleBus:
    """Slot-addressed haptics. Null-object: safe to call always."""

    __slots__ = ("_broken", "_enabled", "_resolver")

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled: bool = enabled and os.environ.get(NO_RUMBLE_ENV_VAR) != "1"
        self._resolver: PadResolver | None = None
        self._broken: set[int] = set()

    # -- wiring ---------------------------------------------------------

    def set_pad_resolver(self, resolver: PadResolver | None) -> None:
        """Install the slot-index → pad-handle lookup.

        ``main.py`` wires this with ``GamepadProvider.pad_for_slot`` on the
        real-gamepad path only. Left unset (the default) the bus is a total
        no-op — which is exactly what replay, smoke, keyboard-only and every
        unit test want, and it makes that suppression a property of the object
        graph rather than a flag someone can forget to check.
        """
        self._resolver = resolver

    # -- global switch --------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Global on/off. Public so a future pause-menu row is a 3-line change."""
        if not value:
            self.stop_all()
        self._enabled = value

    # -- playback -------------------------------------------------------

    def pulse(self, slot: PlayerSlot, event: RumbleEvent) -> None:
        """Fire ``event``'s profile on ``slot``'s pad. Never raises."""
        self._pulse_index(slot.index, event)

    def pulse_both(self, event: RumbleEvent) -> None:
        """Fire on P1 and P2 — for team-wide events like the boss dying."""
        for index in (0, 1):
            self._pulse_index(index, event)

    def stop_all(self) -> None:
        """Cut any in-flight effect on both slots.

        Called from main()'s finally-block: SDL usually stops effects on device
        close, but not reliably on the cheap pads this project has history
        with, and a SIGHUP over SSH mid-boss-kill is a routine event here.
        """
        for index in (0, 1):
            pad = self._pad(index)
            if pad is None:
                continue
            stop = getattr(pad, "stop_rumble", None)
            if not callable(stop):
                continue
            # A dying pad must not break teardown.
            with contextlib.suppress(Exception):
                stop()

    # -- internals ------------------------------------------------------

    def _pulse_index(self, index: int, event: RumbleEvent) -> None:
        profile = _PROFILES.get(event)
        if profile is None or not self._enabled:
            return
        pad = self._pad(index)
        if pad is None:
            return
        low, high, ms = profile
        self._fire(pad, low, high, min(ms, _MAX_DURATION_MS))

    def _pad(self, index: int) -> Any:
        if self._resolver is None or index not in (0, 1):
            return None
        try:
            return self._resolver(index)
        except Exception:
            return None

    def _fire(self, pad: Any, low: float, high: float, ms: int) -> None:
        key = self._pad_id(pad)
        if key in self._broken:
            return
        rumble = getattr(pad, "rumble", None)
        if not callable(rumble):
            # Test FakePad, or an SDL build without haptics.
            self._mark_broken(key, "pad exposes no rumble()")
            return
        # Deliberately broad: a badly-behaved pad binding can raise TypeError
        # or OSError out of SDL, not just pygame.error, and no haptic effect is
        # worth dropping a frame over.
        try:
            ok = rumble(_clamp01(low), _clamp01(high), ms)
        except Exception as exc:
            self._mark_broken(key, f"rumble raised: {exc}")
            return
        if ok is False:
            # pygame-ce returns False when SDL reports no force-feedback.
            # One-way latch: a pad that says no once keeps saying no.
            self._mark_broken(key, "pad reports no force-feedback support")

    @staticmethod
    def _pad_id(pad: Any) -> int:
        get_id = getattr(pad, "get_instance_id", None)
        if callable(get_id):
            try:
                return int(get_id())
            except Exception:
                pass
        return id(pad)

    def _mark_broken(self, key: int, reason: str) -> None:
        if key not in self._broken:
            logger.debug("rumble disabled for pad %s: %s", key, reason)
        self._broken.add(key)
