"""Collision detection: spatial grid + circle/AABB primitives.

The play field is partitioned into a uniform grid; each entity registers
its hitbox once per tick, then `pairs()` yields candidate (entity_a, entity_b)
collisions for narrow-phase resolution by callers.

All circle/AABB tests are pure functions on `Vec2` so they're trivial to
unit-test without a `World`.
"""

from ssdq.core.collision.grid import SpatialGrid
from ssdq.core.collision.shapes import (
    AABB,
    aabb_overlaps,
    circle_aabb_overlaps,
    circles_overlap,
)

__all__ = [
    "AABB",
    "SpatialGrid",
    "aabb_overlaps",
    "circle_aabb_overlaps",
    "circles_overlap",
]
