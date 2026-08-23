"""Decaying screen shake — sim-owned magnitude, render-applied offset.

Fun review 2026-06-12 R7: the game had literally zero kinetic feedback. A bomb,
a death and a boss dying all looked exactly as calm as an empty screen.

Lives in ``core`` so the level scene (which raises shakes) and the Renderer
(which reads them) can share it without either importing the other —
``ssdq/platform/render`` must never import ``ssdq.scenes``, which is the whole
reason the ``_optional_component_type`` duck-typing exists in ``renderer.py``.

Stored as a World **resource**, not a component: it is one global scalar per
world, and ``LevelScene.exit()`` despawns every entity, which would silently
take a component carrier with it.

Determinism: the per-tick jitter is a pure function of ``(tick, amplitude)``
via :mod:`ssdq.core.rng` — no stdlib ``random``, no stateful stream. Nothing in
the tick pipeline ever reads ``ScreenShake`` back; the sim writes it and
forgets it, which is what keeps replays bit-identical with shake in either
position (pinned by ``tests/unit/test_shake_triggers.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from ssdq.core.ecs import World
from ssdq.core.rng import tick_range

# Fresh RNG channels. Existing allocations: background.py uses literal bases
# across 10_000..97_000, hyperspace.py 61_001..61_031, level.py 70_001..70_006.
# 71_000+ sits in the free gap, adjacent to level.py's block because raising a
# shake is a level-scene concern.
_CH_SHAKE_X = 71_000
_CH_SHAKE_Y = 71_001

# (peak px, ticks). Magnitudes are the fun review's R7 table; durations are
# tuned so the heavier the hit, the longer the ring-out.
SHAKE_BOMB = (8.0, 18)  # 0.30s — the biggest thing the kid does on purpose
SHAKE_PLAYER_DEATH = (6.0, 24)  # 0.40s
SHAKE_BOSS_PHASE = (5.0, 15)  # 0.25s
SHAKE_BIG_KILL = (3.0, 10)  # 0.17s
SHAKE_BOSS_KILL = (12.0, 45)  # 0.75s — the loudest punctuation in the game

# Hard ceiling. Also the largest designed magnitude, so this only ever bites a
# future mis-tuned caller.
MAX_SHAKE_PX = 12.0
# Below this the offset would round to nothing anyway, so we skip the scroll
# entirely and the frame stays byte-identical to an unshaken one.
_MIN_VISIBLE_PX = 0.5


@dataclass(slots=True)
class ScreenShake:
    """Current shake envelope. Mutated ONLY by the sim tick."""

    magnitude: float = 0.0
    ticks_remaining: int = 0
    ticks_total: int = 0

    def amplitude(self) -> float:
        """Linearly-decayed peak amplitude in px for the current tick."""
        if self.ticks_remaining <= 0 or self.ticks_total <= 0:
            return 0.0
        return self.magnitude * (self.ticks_remaining / self.ticks_total)

    def add(self, magnitude: float, ticks: int) -> None:
        """Combine a new event with whatever is already decaying.

        MAX-with-clamp, not SUM. A bomb detonating on the same tick as a big
        kill should read as one 8px jolt, not an 11px lurch, and two players
        bombing together must not stack to 16px.

        The comparison is against the CURRENT DECAYED amplitude rather than the
        original magnitude, so a small event landing in the tail of a big one
        still re-kicks the screen instead of being silently swallowed.
        """
        magnitude = min(MAX_SHAKE_PX, max(0.0, magnitude))
        if magnitude <= 0.0 or ticks <= 0:
            return
        if magnitude < self.amplitude():
            return
        self.magnitude = magnitude
        self.ticks_total = ticks
        self.ticks_remaining = ticks

    def decay(self) -> None:
        """Advance one sim tick. Called exactly once per ``LevelScene.tick``."""
        if self.ticks_remaining > 0:
            self.ticks_remaining -= 1
            if self.ticks_remaining == 0:
                self.magnitude = 0.0
                self.ticks_total = 0


def trigger(world: World, spec: tuple[float, int]) -> None:
    """Raise a shake on ``world``, creating the resource on first use."""
    state = world.try_resource(ScreenShake)
    if state is None:
        state = ScreenShake()
        world.insert_resource(state)
    state.add(spec[0], spec[1])


def offset_for(tick: int, amplitude: float) -> tuple[int, int]:
    """Deterministic integer ``(dx, dy)`` jitter for ``tick`` at ``amplitude``.

    Independent per-axis white noise from two tick-derived channels. Box rather
    than polar distribution: two mixer calls instead of three plus a sin/cos
    pair, and at <= 12px the distribution shape is imperceptible.

    Purely a function of its arguments — stateless, process-stable, no stdlib
    ``random``.
    """
    if amplitude < _MIN_VISIBLE_PX:
        return (0, 0)
    dx = round(tick_range(tick, -amplitude, amplitude, channel=_CH_SHAKE_X))
    dy = round(tick_range(tick, -amplitude, amplitude, channel=_CH_SHAKE_Y))
    return (int(dx), int(dy))


def world_offset(world: World, tick: int) -> tuple[int, int]:
    """The renderer's single entry point. ``(0, 0)`` when no shake is active."""
    state = world.try_resource(ScreenShake)
    if state is None:
        return (0, 0)
    return offset_for(tick, state.amplitude())
