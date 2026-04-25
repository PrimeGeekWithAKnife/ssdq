"""Deterministic per-tick noise. The slice has no real RNG.

Cosmetic-only randomness (particle jitter, debris angles) is derived from the
tick index plus an integer channel, via a SplitMix64-style mixer. This means
replays reproduce particle effects bit-for-bit and we don't need to seed
or save RNG state anywhere.

If post-slice features need true randomness (e.g. shop drops), they get a
named, replay-recorded RNG stream at that point, not a global seed.
"""

from __future__ import annotations

import math

_M1 = 0xBF58476D1CE4E5B9
_M2 = 0x94D049BB133111EB
_C1 = 0x9E3779B97F4A7C15
_MASK64 = 0xFFFFFFFFFFFFFFFF


def _mix(value: int) -> int:
    x = value & _MASK64
    x ^= x >> 30
    x = (x * _M1) & _MASK64
    x ^= x >> 27
    x = (x * _M2) & _MASK64
    x ^= x >> 31
    return x


def tick_u64(tick: int, channel: int = 0) -> int:
    """Stable 64-bit unsigned mix of (tick, channel)."""
    return _mix((tick * _C1 + channel * _M1) & _MASK64)


def tick_unit(tick: int, channel: int = 0) -> float:
    """Deterministic value in [0.0, 1.0). Stable across processes (no PYTHONHASHSEED)."""
    return (tick_u64(tick, channel) & 0x1FFFFFFFFFFFFF) / float(1 << 53)


def tick_range(tick: int, lo: float, hi: float, channel: int = 0) -> float:
    """Deterministic value in [lo, hi)."""
    return lo + (hi - lo) * tick_unit(tick, channel)


def tick_angle(tick: int, channel: int = 0) -> float:
    """Deterministic angle in [0, 2π)."""
    return tick_unit(tick, channel) * math.tau


def tick_int(tick: int, lo: int, hi: int, channel: int = 0) -> int:
    """Deterministic integer in [lo, hi). Requires hi > lo."""
    if hi <= lo:
        raise ValueError(f"hi ({hi}) must be > lo ({lo})")
    return lo + (tick_u64(tick, channel) % (hi - lo))
