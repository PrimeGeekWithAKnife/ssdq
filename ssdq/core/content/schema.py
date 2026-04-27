"""Typed shapes for the YAML content tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ───────── ships / weapons ─────────


@dataclass(frozen=True, slots=True)
class FirePattern:
    """One bullet origin in a multi-bullet weapon pattern."""

    angle_deg: float
    offset_x: float
    offset_y: float


@dataclass(frozen=True, slots=True)
class WeaponDef:
    name: str
    type: str
    sprite: str
    damage: int
    speed: float
    fire_rate: float  # shots/sec
    pattern: tuple[FirePattern, ...]


@dataclass(frozen=True, slots=True)
class BombDef:
    name: str
    sprite: str
    damage: int
    radius: float
    duration: float
    clears_bullets: bool


@dataclass(frozen=True, slots=True)
class ShipDef:
    name: str
    sprite: str
    sprite_hit_flash: str
    max_speed: float
    accel: float
    hitbox_radius: float
    starting_lives: int
    starting_bombs: int
    respawn_invulnerability: float
    respawn_clearing_radius: float
    primary_weapon: str
    bomb: str


# ───────── enemies / pickups / boss ─────────


@dataclass(frozen=True, slots=True)
class EnemyWeaponDef:
    name: str
    sprite: str
    damage: int
    speed: float
    pattern: str  # "aimed", "fan", "aimed_fan"
    bullets_per_beat: int
    fan_arc_deg: float = 0.0


@dataclass(frozen=True, slots=True)
class EnemyDef:
    name: str
    sprite: str
    hitbox_radius: float
    hp: int
    speed_along_path: float
    weapon: str | None
    fire_beats: tuple[float, ...]
    score: int
    drop_chance: float
    drop_pool: tuple[str, ...]


class PickupEffect(Enum):
    WEAPON_UPGRADE = "weapon_upgrade"
    SPEED_UP = "speed_up"
    EXTRA_BOMB = "extra_bomb"
    EXTRA_LIFE = "extra_life"
    SHIELD = "shield"


@dataclass(frozen=True, slots=True)
class PickupDef:
    name: str
    sprite: str
    hitbox_radius: float
    fall_speed: float
    effect: PickupEffect
    speed_multiplier: float = 1.0
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class BossPhaseDef:
    hp: int
    formation: str
    weapon: str
    fire_beats: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class BossDef:
    name: str
    sprite: str
    hitbox_radius: float
    score: int
    intro_telegraph_seconds: float
    phases: tuple[BossPhaseDef, ...]


# ───────── formations ─────────


class FormationKind(Enum):
    CATMULL_ROM = "catmull_rom"
    BEZIER = "bezier"


@dataclass(frozen=True, slots=True)
class FormationDef:
    name: str
    kind: FormationKind
    duration: float
    control_points: tuple[tuple[float, float], ...]
    loop: bool = False


# ───────── levels / waves ─────────


@dataclass(frozen=True, slots=True)
class SpawnDef:
    enemy: str
    count: int
    formation: str
    spacing: float
    delay: float = 0.0
    mirrored: bool = False  # set if formation arrived as mirror(name)
    # Survivors of this spawn re-enter the formation this many extra
    # times. 0 = current behaviour (single pass, despawn off-screen);
    # 1 = one extra pass for survivors; 2 = two extra passes; etc.
    # Deterministic — every survivor returns; no randomness (spec §6.3).
    return_passes: int = 0


@dataclass(frozen=True, slots=True)
class WaveDef:
    at: float  # seconds from level start
    stage: str  # "1" | "2" | "3" | "boss" — display only
    spawns: tuple[SpawnDef, ...] = field(default_factory=tuple)
    boss: str | None = None  # boss key, if this wave is a boss wave


@dataclass(frozen=True, slots=True)
class LevelDef:
    level: int
    title: str
    codename: str
    music: str
    boss_music: str
    background: str
    length_seconds: float
    waves: tuple[WaveDef, ...]


# ───────── coop ─────────


@dataclass(frozen=True, slots=True)
class CoopConfig:
    starting_lives: int
    continues_per_session: int
    respawn_delay: float
    respawn_invulnerability: float
    respawn_clearing_radius: float
    friendly_fire: bool
    ship_on_ship_collision: bool
    proximity_bonus_radius: float
    proximity_bonus_multiplier: float
    proximity_bonus_edge_zone: float
    pause_dim_alpha: int
