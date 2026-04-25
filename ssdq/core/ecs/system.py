"""System base class and scheduler.

A System is a callable that runs once per fixed tick. The scheduler invokes
systems in registration order — phase ordering is the responsibility of the
caller (we don't auto-resolve dependencies because we don't need to).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ssdq.core.ecs.world import World
from ssdq.core.types import PlayerInput, TickIndex


@dataclass(frozen=True, slots=True)
class TickContext:
    """Read-only context handed to every system on each tick."""

    tick: TickIndex
    inputs: tuple[PlayerInput, PlayerInput]
    dt: float  # always 1/60 in fixed-timestep mode


# A System is just a callable with this signature. We don't force inheritance.
System = Callable[[World, TickContext], None]


class Scheduler:
    """Holds an ordered list of systems and runs them per tick."""

    __slots__ = ("_systems",)

    def __init__(self, systems: Sequence[System] | None = None) -> None:
        self._systems: list[System] = list(systems or [])

    def add(self, system: System) -> None:
        self._systems.append(system)

    def run(self, world: World, ctx: TickContext) -> None:
        for sys_ in self._systems:
            sys_(world, ctx)

    def __len__(self) -> int:
        return len(self._systems)
