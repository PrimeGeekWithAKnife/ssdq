"""Fixed-timestep clock (60 Hz) with render decoupled.

Standard accumulator pattern: when the frame budget is overrun, logic ticks
multiple times to catch up; when under, render but don't tick. We cap catch-up
at MAX_TICKS_PER_FRAME to avoid the "spiral of death" if the host hangs.
"""

from __future__ import annotations

from ssdq.core.types import TickIndex

TICK_HZ: int = 60
TICK_DT: float = 1.0 / 60.0
MAX_TICKS_PER_FRAME: int = 5


class Clock:
    __slots__ = ("_accumulator", "_tick_count")

    def __init__(self) -> None:
        self._accumulator: float = 0.0
        self._tick_count: int = 0

    def advance(self, real_dt: float) -> int:
        """Add real_dt seconds to the accumulator, return number of fixed ticks
        to step. Capped at MAX_TICKS_PER_FRAME — leftover time is discarded to
        keep the sim from spiralling on a stalled host."""
        if real_dt < 0.0:
            real_dt = 0.0
        self._accumulator += real_dt
        ticks = 0
        while self._accumulator >= TICK_DT and ticks < MAX_TICKS_PER_FRAME:
            self._accumulator -= TICK_DT
            ticks += 1
            self._tick_count += 1
        if self._accumulator >= TICK_DT:
            # Hit the cap; throw away the excess so we don't keep falling further behind.
            self._accumulator = 0.0
        return ticks

    @property
    def tick(self) -> TickIndex:
        return TickIndex(self._tick_count)

    @property
    def alpha(self) -> float:
        """Render interpolation factor in [0, 1)."""
        return self._accumulator / TICK_DT
