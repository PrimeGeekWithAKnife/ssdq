"""Engine-agnostic core types. No pygame imports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NewType

# Discriminated integer types for safety against passing the wrong int around.
Entity = NewType("Entity", int)
TickIndex = NewType("TickIndex", int)


@dataclass(frozen=True, slots=True)
class Vec2:
    """2D vector. Immutable. All operations return a new Vec2."""

    x: float
    y: float

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> Vec2:
        return Vec2(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar: float) -> Vec2:
        return self.__mul__(scalar)

    def __truediv__(self, scalar: float) -> Vec2:
        return Vec2(self.x / scalar, self.y / scalar)

    def __neg__(self) -> Vec2:
        return Vec2(-self.x, -self.y)

    def dot(self, other: Vec2) -> float:
        return self.x * other.x + self.y * other.y

    def length_squared(self) -> float:
        return self.x * self.x + self.y * self.y

    def length(self) -> float:
        return math.sqrt(self.length_squared())

    def normalised(self) -> Vec2:
        n = self.length()
        if n == 0.0:
            return Vec2(0.0, 0.0)
        return Vec2(self.x / n, self.y / n)

    def with_x(self, x: float) -> Vec2:
        return Vec2(x, self.y)

    def with_y(self, y: float) -> Vec2:
        return Vec2(self.x, y)

    def clamped_magnitude(self, max_mag: float) -> Vec2:
        m2 = self.length_squared()
        if m2 <= max_mag * max_mag:
            return self
        m = math.sqrt(m2)
        return Vec2(self.x * max_mag / m, self.y * max_mag / m)


VEC2_ZERO = Vec2(0.0, 0.0)


@dataclass(frozen=True, slots=True)
class PlayerInput:
    """Per-tick input for a single player. Edge-triggered fields are True only on
    the tick where the press occurred. Held fields stay True for the duration."""

    move: Vec2 = VEC2_ZERO  # left stick, deadzoned, magnitude 0..1
    fire: bool = False  # held
    bomb: bool = False  # edge-triggered (just pressed)
    pause: bool = False  # edge-triggered
    confirm: bool = False  # menu A, edge-triggered
    cancel: bool = False  # menu B, edge-triggered
    # Drone-formation cycle: edge-triggered. Pads → BUTTON_BACK (6),
    # keyboard → F. The level scene advances the player's active drone
    # configuration (Tight → Spread → Trailing → Vanguard → loop) only
    # when at least one drone is alive.
    drone_cycle: bool = False  # edge-triggered


NEUTRAL_INPUT = PlayerInput()


@dataclass(frozen=True, slots=True)
class PlayerSlot:
    """Identifies which player a thing belongs to. P1=0, P2=1."""

    index: int

    def __post_init__(self) -> None:
        if self.index not in (0, 1):
            raise ValueError(f"PlayerSlot must be 0 or 1, got {self.index}")


P1 = PlayerSlot(0)
P2 = PlayerSlot(1)
