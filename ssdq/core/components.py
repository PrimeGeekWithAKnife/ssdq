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
class MaxHealth:
    """Maximum HP at spawn. Read by the renderer for health-bar
    fill ratios; never mutated after spawn."""

    hp: int


@dataclass(frozen=True, slots=True)
class TimeToLive:
    """Despawn after this many ticks remain. Used for bullets, particles,
    expired pickups."""

    ticks: int


@dataclass(frozen=True, slots=True)
class Sprite:
    """Render hint — a path key into the loaded sprite atlas. Optional
    layer (lower draws first). `alpha` is 0..255; 255 = fully opaque.
    `scale` multiplies render size (1.0 = native)."""

    path: str
    layer: int = 0
    rotation_rad: float = 0.0
    alpha: int = 255
    scale: float = 1.0


@dataclass(frozen=True, slots=True)
class PickupHalo:
    """Tag: render a pulsing coloured halo behind this entity.

    Colour and base radius are read by the renderer; the pulse uses
    the global tick so all halos pulse in sync (deterministic and
    visually grouped).
    """

    radius: float
    colour: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class ShieldHalo:
    """Tag: render a pulsing translucent forcefield ring around this entity.

    Used by the SHIELD pickup — the level scene attaches one to the
    player ship while ``PlayerPowerupState.shield`` is active and
    removes it on expiry. Colour is cyan by convention; the renderer
    reads ``base_radius`` and pulses the ring radius + alpha.
    """

    base_radius: float
    colour: tuple[int, int, int] = (80, 220, 255)


@dataclass(frozen=True, slots=True)
class FloatingText:
    """A short-lived text label that drifts upward and fades out.

    Used for pickup-collection feedback ("WEAPON UP!" etc.).
    """

    text: str
    colour: tuple[int, int, int]
    ticks_remaining: int
    rise_speed: float = 30.0  # px/sec upward drift


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


# ───────── drones ─────────


@dataclass(frozen=True, slots=True)
class Drone:
    """Companion drone tag.

    A drone is a small ship that flies next to its owning player, copies
    movement (with a configurable formation offset) and fires whenever
    the player fires. It has independent HP — enemy bullets can kill the
    drone without ejecting the player from any power-up state.

    `slot_index` selects which of the up-to-2 drone slots this drone
    occupies — used purely so the formation system knows which side of
    the player to put it on (slot 0 → left flank, slot 1 → right flank).
    """

    slot: PlayerSlot
    slot_index: int  # 0 or 1 (which of the player's up-to-2 drones this is)
