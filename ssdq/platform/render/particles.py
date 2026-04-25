"""Fixed-size particle pool.

Particles are real ECS entities (carrying Position/Velocity/TimeToLive +
Sprite) so they advance and despawn through the same systems as bullets.
The pool just caps the total number alive at any one time — when full,
new emissions are dropped (rather than allocating unbounded memory). This
keeps slice perf predictable on the Pi 5.
"""

from __future__ import annotations

from ssdq.core.components import Position, Sprite, TimeToLive, Velocity
from ssdq.core.ecs import World
from ssdq.core.types import Entity, Vec2

_DEFAULT_CAPACITY = 256


class ParticlePool:
    """Caps the number of live particle entities at ``capacity``.

    The pool tracks particles by entity id; expired ids (entities that have
    already been despawned) are pruned lazily on the next ``emit``.
    """

    __slots__ = ("_capacity", "_live")

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._live: list[Entity] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    def alive_count(self, world: World) -> int:
        return sum(1 for eid in self._live if world.is_alive(eid))

    def emit(
        self,
        world: World,
        pos: Vec2,
        velocity: Vec2,
        ttl_ticks: int,
        sprite_path: str,
        layer: int = 50,
    ) -> Entity | None:
        """Spawn a particle entity. Returns ``None`` if the pool is full."""
        # Prune dead refs first so the pool can recover capacity.
        self._live = [eid for eid in self._live if world.is_alive(eid)]
        if len(self._live) >= self._capacity:
            return None
        eid = world.spawn(
            Position(pos),
            Velocity(velocity),
            TimeToLive(ticks=ttl_ticks),
            Sprite(path=sprite_path, layer=layer),
        )
        self._live.append(eid)
        return eid
