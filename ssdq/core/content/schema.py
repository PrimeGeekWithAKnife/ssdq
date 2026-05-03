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
    # Optional spawn-shield window (kid playtest 2026-05-02 #3 — "I've yet
    # to spot a single enemy with shields"). When > 0, the enemy spawns
    # with a forcefield active for that many seconds; player bullets
    # bounce / are absorbed during the window. Bombs still penetrate
    # (a screen-clear should always read as decisive).
    shield_on_spawn_seconds: float = 0.0
    # Kid playtest 2026-05-03 #8: red kamikaze ships should "come back
    # and try again until they get the player or are destroyed".
    # Setting this overrides the per-spawn `return_passes` field with
    # a large value so survivors keep looping the formation until HP
    # hits zero. Distinct from `return_passes` so we don't have to
    # litter every wave spec with a sentinel.
    passes_unlimited: bool = False
    # Kid playtest 2026-05-03 #9 + #2: shields-on-first-hit. The enemy
    # spawns dormant; the FIRST damaging player bullet triggers a
    # forcefield that absorbs further fire for this many seconds, then
    # decays. One-shot — does not regenerate. Distinct from
    # ``shield_on_spawn_seconds`` (always-on at spawn): this one rewards
    # the kid for engaging at all without the spawn-shield's "you can't
    # touch me yet" frustration.
    shield_on_first_hit_seconds: float = 0.0


class PickupEffect(Enum):
    WEAPON_UPGRADE = "weapon_upgrade"
    SPEED_UP = "speed_up"  # legacy alias for ship_speed (timed boost)
    EXTRA_BOMB = "extra_bomb"
    EXTRA_LIFE = "extra_life"
    SHIELD = "shield"
    # Task #9: kid-playtest pool of 8 power-ups.
    SHIP_SPEED = "ship_speed"  # permanent +N% ship-speed bump, capped
    WEAPON_SPEED = "weapon_speed"  # timed rate-of-fire boost
    DRONE = "drone"  # inventory: queue +1 drone for the DRONE agent
    MISSILE = "missile"  # inventory: +N missile charges (equippable)


@dataclass(frozen=True, slots=True)
class PickupDef:
    name: str
    sprite: str
    hitbox_radius: float
    fall_speed: float
    effect: PickupEffect
    speed_multiplier: float = 1.0
    duration: float = 0.0
    # SHIP_SPEED: how much a single pickup adds to the ship-speed bonus
    # (additive, capped by PlayerPowerupState.SHIP_SPEED_BONUS_CAP).
    ship_speed_step: float = 0.15
    # WEAPON_SPEED: rate-of-fire multiplier while the boost is active.
    fire_rate_multiplier: float = 1.5
    # MISSILE: how many missile charges a single pickup grants.
    missile_count: int = 3


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
    # Boss shield mechanics (kid playtest 2026-05-02 #15 + #16). Both
    # default to "no shield" so existing bosses are unaffected.
    #
    # `shield_on_phase_start_seconds`: when phase 2 starts, open a shield
    # window of this many seconds (boss_03 — "shield at 50% HP for 10s").
    # `shield_cycle_seconds`: tuple (vulnerable, shielded). Once the boss
    # leaves the intro telegraph it cycles between the two — vulnerable
    # for `vulnerable` seconds, then shielded for `shielded` seconds,
    # repeating (boss_04/05 — "shield for 10s every 20s of fighting").
    shield_on_phase_start_seconds: float = 0.0
    shield_cycle_seconds: tuple[float, float] | None = None


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
