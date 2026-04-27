"""Shared ECS components used by multiple systems.

System-specific components (e.g. FormationFollow for the wave system,
Pickup for the powerup system) live alongside their system. Components
in this file are touched by ≥ 2 systems.

All components are frozen dataclasses; mutation is by `world.replace()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ssdq.core.types import PlayerSlot, Vec2

# ───────── motion ─────────


@dataclass(frozen=True, slots=True)
class Position:
    """World-space pixel coordinates. Top-left origin, +y down."""

    pos: Vec2


@dataclass(frozen=True, slots=True)
class Velocity:
    """Pixels per second in world space."""

    vel: Vec2


# ───────── identity ─────────


class Faction(Enum):
    """Whose side an entity is on. Drives collision rules."""

    PLAYER = "player"
    PLAYER_BULLET = "player_bullet"
    ENEMY = "enemy"
    ENEMY_BULLET = "enemy_bullet"
    PICKUP = "pickup"


@dataclass(frozen=True, slots=True)
class FactionTag:
    faction: Faction


@dataclass(frozen=True, slots=True)
class PlayerOwned:
    """Marker tying an entity to a specific player (P1/P2). Bullets the
    player fires get this; the score for that bullet's kills routes to
    the right personal score."""

    slot: PlayerSlot


# ───────── shape ─────────


@dataclass(frozen=True, slots=True)
class CircleHitbox:
    """Circle hitbox. radius pixels, centred on Position."""

    radius: float


# ───────── lifecycle ─────────


@dataclass(frozen=True, slots=True)
class Health:
    """Entity HP. 0 means schedule for despawn next tick. Player ships are
    1-hit-kill in the slice (spec §2 footnote) — they don't carry Health;
    the death is handled by the coop system on collision."""

    hp: int


@dataclass(frozen=True, slots=True)
class TimeToLive:
    """Despawn after this many ticks remain. Used for bullets, particles,
    expired pickups."""

    ticks: int


@dataclass(frozen=True, slots=True)
class Sprite:
    """Render hint — a path key into the loaded sprite atlas. Optional
    layer (lower draws first). `alpha` is 0..255; 255 = fully opaque."""

    path: str
    layer: int = 0
    rotation_rad: float = 0.0
    alpha: int = 255


@dataclass(frozen=True, slots=True)
class AnimatedSprite:
    """Frame sequence playing at `frame_ticks` ticks per frame.

    `frames` is a tuple of atlas paths (e.g. ('particles/explosion_00.png',
    ..., 'particles/explosion_03.png')). The renderer reads the current
    frame from `current_index` (mutated by a system, not the renderer).

    `loop=False` means the AnimationSystem despawns the entity on the
    final frame; `loop=True` cycles indefinitely.
    """

    frames: tuple[str, ...]
    frame_ticks: int = 4
    current_index: int = 0
    elapsed_ticks: int = 0
    loop: bool = False
    layer: int = 0


@dataclass(frozen=True, slots=True)
class HitFlash:
    """Tag: this entity should render with a brief white-tint flash.
    `ticks_remaining` counts down each frame; 0 = clear flash."""

    ticks_remaining: int


@dataclass(frozen=True, slots=True)
class InvulnerabilityBlink:
    """Tag: this entity is in i-frames; renderer pulses its alpha.

    Used on player ships during the LifecycleState.INVULNERABLE window
    so the player can SEE they're invulnerable and use the time to
    reposition before vulnerability resumes.
    """

    ticks_remaining: int


# ───────── damage routing ─────────


@dataclass(frozen=True, slots=True)
class Damage:
    """Damage dealt on a successful hit. Bullets carry one of these."""

    amount: int


@dataclass(frozen=True, slots=True)
class ScoreValue:
    """Score awarded when this entity is destroyed by a player."""

    points: int
