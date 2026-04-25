"""Engine-agnostic core. Has zero pygame imports — testable as plain Python."""

from ssdq.core.ecs import Scheduler, TickContext, World
from ssdq.core.scene import Pop, Push, Quit, Replace, Scene, SceneStack
from ssdq.core.types import (
    NEUTRAL_INPUT,
    P1,
    P2,
    VEC2_ZERO,
    Entity,
    PlayerInput,
    PlayerSlot,
    TickIndex,
    Vec2,
)

__all__ = [
    "NEUTRAL_INPUT",
    "P1",
    "P2",
    "VEC2_ZERO",
    "Entity",
    "PlayerInput",
    "PlayerSlot",
    "Pop",
    "Push",
    "Quit",
    "Replace",
    "Scene",
    "SceneStack",
    "Scheduler",
    "TickContext",
    "TickIndex",
    "Vec2",
    "World",
]
