"""Level scene — the engine room. Wires every core module to the platform.

Per-tick pipeline:

  1. consume any one-shot triggers from previous tick (clearing shockwaves)
  2. tick coop session timers (DYING/INVULN countdown)
  3. tick speed-boost timers on each player's powerup state
  4. apply player inputs → move ships, fire bullets, drop bombs
  5. advance wave scheduler → spawn new enemies, transition to boss
  6. advance enemies along their formations; emit fire-beat events
  7. integrate movement (bullets, pickups, particles)
  8. populate spatial grid; resolve collisions (player vs *, bullets vs enemies,
     pickups vs player); apply DamageDecision routing
  9. process kill queue: award scores, roll drops, spawn pickups
 10. cull off-screen / dead entities
 11. update HUD-coop snapshot resource (used by the renderer's HUD module)
 12. check level completion / game-over transitions

The scene scrolls a virtual playfield 1280x720 (match formations.yaml).
Player ships are spawned bottom-centre on enter().
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from ssdq.core.ai import FreeRoamConfig, free_roam_step
from ssdq.core.clock import TICK_DT
from ssdq.core.collision import SpatialGrid, circles_overlap
from ssdq.core.components import (
    AnimatedSprite,
    BossTag,
    CircleHitbox,
    Damage,
    Drone,
    EnemyShield,
    Faction,
    FactionTag,
    FloatingText,
    FreeRoamAI,
    Health,
    HitFlash,
    InvulnerabilityBlink,
    MaxHealth,
    PendingFreeRoam,
    PickupHalo,
    PlayerOwned,
    Position,
    ScoreValue,
    ShieldHalo,
    ShieldOnHitConfig,
    ShieldOnHitConsumed,
    Sprite,
    TimeToLive,
    Velocity,
)
from ssdq.core.content.schema import (
    BossDef,
    BossPhaseDef,
    EnemyWeaponDef,
    PickupDef,
)
from ssdq.core.coop import (
    CoopSession,
    DamageDecision,
    LifecycleState,
    proximity_multiplier,
    should_apply_damage,
)
from ssdq.core.ecs import World
from ssdq.core.powerups import (
    MISSILE_LEVEL_CAP,
    SHIELD_CONSUME_DURATION,
    PlayerPowerupState,
    Shield,
    WeaponState,
    apply_pickup,
    roll_drop,
)
from ssdq.core.rng import tick_angle, tick_int, tick_range
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import P1, P2, Entity, PlayerInput, PlayerSlot, TickIndex, Vec2
from ssdq.core.waves import (
    EnemyShooter,
    SpawnEvent,
    WaveScheduler,
    evaluate_path,
)
from ssdq.scenes.app_state import AppState
from ssdq.scenes.hud_state import HudCoopState, HudPlayerStats
from ssdq.scenes.music_routing import boss_music_name, clamp_level, level_music_pool

logger = logging.getLogger(__name__)

PLAY_W = 1280.0
PLAY_H = 720.0


def _input_is_active(inp: PlayerInput) -> bool:
    """True if the input differs from the neutral default — i.e. the
    player has actually pressed or moved something this tick.

    Used to gate the LevelScene's auto-continue loop so an idle slot
    (e.g. an un-bound P2) is never auto-resurrected (kid playtest
    2026-05-02 #9).
    """
    if inp.fire or inp.bomb or inp.pause or inp.confirm or inp.cancel:
        return True
    if inp.shield or inp.drone_cycle:
        return True
    # Stick movement above a tiny noise floor counts as engagement.
    return abs(inp.move.x) > 0.05 or abs(inp.move.y) > 0.05
_PLAYER_SPAWN_Y = PLAY_H - 80.0
_OFF_SCREEN_MARGIN = 80.0  # cull entities this far past the play boundary
_BOSS_INTRO_TELEGRAPH_RADIUS = 80.0

# ───────── drones (task #10) ─────────
#
# Up to 2 drones per player slot. Each drone has independent HP and dies
# from enemy fire / collision without ejecting the player from any
# power-up state. The drone-formation cycle button (gamepad BACK / kbd F)
# walks through these symmetric offsets on the rising edge.
#
# Offsets are (dx, dy) relative to the player ship; slot_index 0 takes
# the negative-x flank and slot_index 1 mirrors to the positive-x flank.
_DRONE_MAX = 2
_DRONE_HP = 3
# Drone visual scale relative to the player ship sprite (which itself
# renders at scale 0.66 — see _spawn_player). 0.7 of the player size is
# "noticeably smaller, still readable" — matches the kid's brief.
_DRONE_SCALE_FACTOR = 0.7
# Drone hitbox is a touch smaller than the player's so duels-with-bullets
# don't feel cheap. Player hitbox = 8 px → drone = 6 px.
_DRONE_HITBOX_RADIUS = 6.0


@dataclass(frozen=True, slots=True)
class _DroneConfigDef:
    """One drone-formation preset. ``offset_x``/``offset_y`` is applied
    to the drone whose ``slot_index`` is 0; slot_index 1 mirrors X."""

    name: str
    offset_x: float
    offset_y: float


# Order matters — this is the cycle the BACK button walks through.
_DRONE_CONFIGS: tuple[_DroneConfigDef, ...] = (
    _DroneConfigDef(name="tight", offset_x=25.0, offset_y=0.0),
    _DroneConfigDef(name="spread", offset_x=60.0, offset_y=0.0),
    _DroneConfigDef(name="trailing", offset_x=30.0, offset_y=50.0),
    _DroneConfigDef(name="vanguard", offset_x=30.0, offset_y=-50.0),
)


def drone_offset(config_index: int, slot_index: int) -> Vec2:
    """Return the (dx, dy) the drone with ``slot_index`` should sit at
    under the given drone-formation preset.

    ``slot_index`` 0 takes the negative-x flank, 1 mirrors to +x. y is
    the same for both so paired drones always sit symmetric.
    """
    cfg = _DRONE_CONFIGS[config_index % len(_DRONE_CONFIGS)]
    sign = -1.0 if slot_index == 0 else 1.0
    return Vec2(sign * cfg.offset_x, cfg.offset_y)



# Boss-intro overlay (text banner) duration. Slightly longer than the
# default telegraph so the banner reads cleanly even if the boss data
# overrides intro_telegraph_seconds to something short.
_BOSS_INTRO_BANNER_SECONDS = 3.0
# Level-intro overlay shown the moment LevelScene.enter() lands. Long
# enough for an adult-or-kid to read the full sentence at kid-distance
# without gating gameplay (players can shoot through it).
_LEVEL_INTRO_BANNER_SECONDS = 6.0


# ───────── per-level narrative copy ─────────
#
# Verbatim user copy from kid-playtest feedback (levels 1–5). Levels
# 6–7 (fun review 2026-06-12 R5 — the boy asked for "more levels")
# keep the same short, punchy register and each names the level's
# mechanic so the banner doubles as a tutorial line.
_LEVEL_INTROS: dict[int, str] = {
    1: (
        "Before you can save Earth you must destroy the aliens "
        "preventing you from leaving the moon"
    ),
    2: (
        "You have left the moon but enemies are closing in to "
        "intercept you in space"
    ),
    3: (
        "Earth's last orbital defence station is under attack, "
        "without it all is lost. You must destroy the alien ships "
        "so it can repair its main weapon systems!"
    ),
    4: (
        "We have finally made it to Earth but aliens are attack "
        "from all directions you must defend space above the "
        "United Kingdom before the aliens attack the government "
        "and the King!"
    ),
    5: (
        "We have defeated all the aliens but they have called for "
        "reinforcements and a massive alien fleet has just come out "
        "of hyperspace near Earth. Let's hope they finished repairs "
        "on that space station!!"
    ),
    6: (
        "The asteroid graveyard! Hide behind the rocks - they eat "
        "alien lasers!"
    ),
    7: (
        "The mothership convoy! Blast the supply ships to power up!"
    ),
}

_LEVEL_BOSS_INTROS: dict[int, str] = {
    1: "MOON GUARDIAN approaches! Defend the lunar launch site!",
    2: "DEEP SPACE INTERCEPTOR closing in! Hold the line!",
    3: "ORBITAL SIEGE FLEET commander locks on - protect the station!",
    4: "ALIEN WARLORD attacks the United Kingdom! Defend the King!",
    5: (
        "HYPERSPACE ARMADA flagship engaged - destroy it before "
        "reinforcements land!"
    ),
    6: "THE CARRIER awakens! Smash it before it launches its swarm!",
    7: "THE MOTHERSHIP itself! End the invasion - fire everything!",
}

# Fallback used when an unknown level index is requested. Kept as a
# generic string so the renderer never has to guard against a missing
# banner — the show goes on even if content drifts.
_DEFAULT_LEVEL_INTRO = "Mission start"
_DEFAULT_BOSS_INTRO = "A LARGE ALIEN SHIP APPROACHES - IT FEELS ANGRY"

# High-weapon-tier shield sprinkle — kid playtest 2026-04-28 #7. When
# either player has reached weapon tier ≥ 3, a deterministic fraction
# of common enemies spawn with a 1s shield-on-first-hit so the kid
# meets a few "tougher" ships at high tier without needing a separate
# armoured-variant content pipeline. Skipped for chunky/already-shielded
# enemies (bomber, gunship, sentinel, marauder, resupply_ship).
_HIGH_TIER_SHIELD_TYPES = frozenset({"fighter", "interceptor", "kamikaze", "drone"})
_HIGH_TIER_SHIELD_THRESHOLD = 3        # min weapon tier that triggers any sprinkle
_HIGH_TIER_SHIELD_DENSE_THRESHOLD = 4  # at this tier+, sprinkle is denser
_HIGH_TIER_SHIELD_SPARSE_MOD = 6       # tier 3: every 6th qualifying enemy (~17%)
_HIGH_TIER_SHIELD_DENSE_MOD = 4        # tier 4+: every 4th qualifying enemy (25%)
_HIGH_TIER_SHIELD_SECONDS = 1.0

# Faction pair that can trigger bullet-blocking cover (level 6 asteroid
# hulks). Pre-built frozenset so the hot collision loop's IGNORE path
# pays one set compare, not two enum compares per orientation.
_BULLET_BLOCK_PAIR = frozenset({Faction.ENEMY_BULLET, Faction.ENEMY})

# Single-player campaign scaling. The boy reported (2026-05-23) that two
# kitted-out players melt bosses too fast — so when solo, also reduce
# the load relative to co-op. Density scales spawn counts per wave (each
# spawn keeps at least 1 member); boss HP scale multiplies every phase
# HP at boss-spawn time. Both are tunable here — playtest-driven.
_SP_ENEMY_DENSITY: float = 0.65   # 35% fewer common enemies per spawn
_SP_BOSS_HP_SCALE: float = 0.65   # bosses take 35% less to kill solo

# Stray-asteroid dodge-fest (Level 7; fun review 2026-06-12). Three
# tumbling-rock sizes — (sprite, collision radius). The kid reads the
# silhouette and judges the dodge; the radius is generous-but-fair so a
# clean weave threads the gap. Sizes are picked deterministically per
# burst from tick_int so replays stay bit-identical.
_STRAY_SIZES: tuple[tuple[str, float], ...] = (
    ("enemies/asteroid_hulk.png", 40.0),
    ("enemies/asteroid_med.png", 24.0),
    ("enemies/asteroid_small.png", 14.0),
)
# Base traverse speed = vanguard.max_speed; the config's speed_multiplier
# scales it (1.5 ⇒ 480 px/s — faster than the player, still dodgeable).
_STRAY_BASE_SPEED: float = 320.0
# Fresh RNG channels (70001..70010) so the spawner never collides with
# any other tick-derived stream. tick_range/tick_int/tick_angle.
_CH_STRAY_INTERVAL = 70001
_CH_STRAY_EDGE = 70002
_CH_STRAY_SIZE = 70003
_CH_STRAY_FROM = 70004
_CH_STRAY_TO = 70005
_CH_STRAY_SPIN = 70006


def level_intro_text(level: int) -> str:
    """Return the per-level intro banner copy. Falls back to a generic
    line for unknown indices so callers never crash on missing data."""
    return _LEVEL_INTROS.get(level, _DEFAULT_LEVEL_INTRO)


def boss_intro_text(level: int) -> str:
    """Return the per-level boss intro banner copy. Falls back to the
    legacy generic line so the banner machinery still has something to
    show on uncovered levels."""
    return _LEVEL_BOSS_INTROS.get(level, _DEFAULT_BOSS_INTRO)


# ───────── runtime components (Level-scene-internal) ─────────


@dataclass(frozen=True, slots=True)
class PlayerShip:
    """Marker + per-player primary-weapon fire cooldown."""

    slot: PlayerSlot
    weapon_cooldown: float  # seconds until next allowed shot


@dataclass(frozen=True, slots=True)
class FormationFollower:
    """Enemy following a named formation. `path_t0` is wall-clock spawn time.

    `passes_remaining` counts extra return passes for survivors: when the
    enemy reaches the end of the formation (t_norm ≥ 1.0) and still has
    passes left, `path_t0` resets to current sim time and `passes_remaining`
    decrements. Deterministic — no RNG (spec §6.3).
    """

    formation_name: str
    mirrored: bool
    path_t0: float
    speed: float  # speed_along_path in px/sec — divided by formation length
    formation_duration: float
    fire_beats: tuple[float, ...]
    weapon_name: str | None
    score: int
    drop_chance: float
    drop_pool: tuple[str, ...]
    passes_remaining: int = 0
    # Pickups always dropped on death in addition to the standard
    # roll_drop result (kid playtest 2026-05-03 #1 + #4 — supply ship
    # drops 1 guaranteed missile + 1 random).
    guaranteed_drops: tuple[str, ...] = ()
    # Pickups dropped on death, multiplied by current level_index
    # (kid playtest 2026-04-28 #5 — resupply scaling boost).
    level_scaled_drops: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EnemyShooterRef:
    """Wraps the mutable EnemyShooter so the World can store it."""

    shooter: EnemyShooter


@dataclass(frozen=True, slots=True)
class PickupTag:
    """Marker so the collision layer can identify pickups quickly."""

    pickup_name: str


@dataclass(frozen=True, slots=True)
class BulletBlocker:
    """Marker for enemies that absorb ENEMY bullets (level-6 asteroid
    hulks — "hide behind the rocks, they eat alien lasers"). Read by the
    ENEMY_BULLET × ENEMY branch of the collision IGNORE path; enemies
    without it keep the zero-cost ignore."""


@dataclass(frozen=True, slots=True)
class StrayAsteroid:
    """Marker for a Level-7 stray asteroid (fun review 2026-06-12). These
    are ENEMY-faction but spawned directly (no FormationFollower / EnemyDef),
    so the kill path special-cases this marker: award ScoreValue, spawn an
    explosion, and SKIP the FormationFollower/EnemyDef drop-roll machinery
    in _on_enemy_killed (which would otherwise have nothing to roll). They
    move via plain Velocity integration and self-cull on TTL / off-screen."""


@dataclass
class BombActive:
    """An active bomb shockwave — drawn by renderer, damages each enemy once.

    Geometry:
      * ``pos`` — detonation origin (player position at fire time).
      * ``aoe_radius`` — fixed damage radius for the whole lifetime. Every
        enemy whose centre lands inside this circle takes the bomb hit
        once. Half-screen (~360px on 1280×720) reads as a "screen-clear".
      * ``radius`` — animated visual outer ring radius. Expands from 0 to
        ``aoe_radius`` over ``duration_total`` so the player sees the
        shockwave race outwards. Duck-typed by the renderer (kept under
        the ``radius`` name so the legacy renderer path still draws it).
      * ``visual_progress`` — 0.0 at detonation, 1.0 at expiry. Drives the
        ring/flash animation in the renderer (no RNG, deterministic).

    ``_hit`` tracks already-damaged enemy entity IDs so the bomb deals
    its damage exactly once per enemy across its full duration (a single
    membership check; would otherwise become DPS).
    """

    pos: Vec2
    radius: float  # animated visual outer ring (px)
    duration_remaining: float
    aoe_radius: float = 0.0  # fixed damage radius (px); falls back to radius
    duration_total: float = 0.0  # original duration (s); falls back to duration_remaining
    visual_progress: float = 0.0  # 0..1, advanced each tick by _tick_bombs
    _hit: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        # Back-compat: if callers only set the (legacy) ``radius`` and
        # ``duration_remaining`` fields, mirror them onto the explicit
        # AoE / duration_total fields so the renderer animation works.
        if self.aoe_radius <= 0.0:
            self.aoe_radius = self.radius
        if self.duration_total <= 0.0:
            self.duration_total = max(self.duration_remaining, 0.001)


@dataclass(frozen=True, slots=True)
class BossTelegraph:
    """Boss-intro shockwave indicator. Drawn by renderer."""

    pos: Vec2
    radius: float
    colour: tuple[int, int, int] = (255, 80, 80)


@dataclass(frozen=True, slots=True)
class BossIntroBanner:
    """Big top-of-screen text overlay shown during the boss intro window.

    The renderer reads this duck-typed component (any entity that has it)
    and draws ``text`` near the top of the playfield, fading to invisible
    over its TimeToLive. Spawned alongside the BossTelegraph circle so the
    player gets BOTH a visual ping and a narrative hook before the boss
    starts shooting (kid playtest feedback: boss appearance was too sudden).
    """

    text: str
    total_ticks: int  # original lifetime, used by renderer to compute fade


# ───────── missile tunables ─────────
#
# Damage: a regular pulse_lvl1 shot deals 1; a bomb deals 8 (or 20% of a
# boss's current HP, whichever is greater). Per kid playtest the missile
# should sit between: "more than a shot but less than a bomb". We pick
# **3 damage** — exactly 3x a pulse_lvl1 hit and ~37.5% of a bomb's base
# damage. Documented here so future tuning has a single source of truth.
MISSILE_DAMAGE: int = 3
# Initial speed (px/s) — enough to read as "fired", but lets the missile
# curve toward its target instead of rocketing past on the first frame.
MISSILE_INITIAL_SPEED: float = 220.0
# Cap on the missile's speed (px/s) once it has built up acceleration.
MISSILE_MAX_SPEED: float = 520.0
# Acceleration along the heading (px/s^2) — reaches MAX_SPEED after ~0.6s.
MISSILE_ACCEL: float = 500.0
# Maximum turn rate (radians/sec) so a fast-moving target can outrun the
# missile's heading change. ~120 deg/sec — the kid wanted "homes in on a
# target" while still letting fast movers cause near-misses.
MISSILE_TURN_RATE: float = math.radians(120.0)
# Auto-cull lifetime (seconds) so a missile that never finds a target
# doesn't linger forever. 3s is enough to cross the playfield.
MISSILE_LIFETIME: float = 3.0
# Hitbox radius (px) — a touch wider than a pulse bullet so glancing
# blows still register against fast-moving formations.
MISSILE_HITBOX_RADIUS: float = 8.0
# Sprite path. Atlas auto-generates a placeholder if the file is absent
# (see SpriteAtlas) so the missile is always visible during dev.
MISSILE_SPRITE: str = "projectiles/missile.png"

# ───── auto-fire (post-2026-04-30 missile redesign) ─────
# Cadence: a slot's missile_level (0..MISSILE_LEVEL_CAP) determines how
# many missiles are spawned every MISSILE_AUTOFIRE_TICKS. Driven off the
# integer tick counter so replays stay deterministic. 120 ticks = 2.0s
# at 60Hz — matches the "fires every 2s" in the design doc.
MISSILE_AUTOFIRE_TICKS: int = 120

# Per-tier x-offsets relative to the firing ship's position. Shape per
# the design doc: L1 fires from one side, L2 from each side, L3 adds a
# centre shot, L4 doubles up each side, L5 adds a centre on top of L4.
# Offsets are first-pass numbers — adjust during Pi playtest if missiles
# overlap with the ship sprite or with each other.
_MISSILE_PATTERNS: dict[int, tuple[float, ...]] = {
    0: (),
    1: (-12.0,),
    2: (-12.0, 12.0),
    3: (-12.0, 0.0, 12.0),
    4: (-18.0, -6.0, 6.0, 18.0),
    5: (-18.0, -6.0, 0.0, 6.0, 18.0),
}


@dataclass
class Missile:
    """Heat-seeking missile state.

    Position + Velocity are stored as standard ECS components (so the
    motion integrator advances them); this component carries the
    homing-specific scratch fields.

    ``target`` is the entity id we're chasing. The missile latches onto
    its target at fire-time (kid spec: "no re-target") so a target dying
    mid-flight just lets the missile coast to the corpse's last known
    position before lifetime auto-cull.
    """

    target: Entity | None
    speed: float  # current speed magnitude (px/s)
    heading: float  # current heading in radians; 0 = straight up


@dataclass(frozen=True, slots=True)
class LevelIntroBanner:
    """Big centred text overlay shown at the start of every level.

    Spawned in :meth:`LevelScene.enter` so it appears on tick 0 — players
    can shoot through it (no input gating). Word-wrapped by the renderer
    to ~80% of the playfield width so the longer narrative lines on
    Levels 3-5 fit on a 1280x720 fullscreen at kid-distance.

    The renderer holds full alpha for most of the banner's life and
    fades the last ~30% so the banner retreats just before gameplay
    intensifies.
    """

    text: str
    total_ticks: int  # original lifetime, used by renderer to compute fade


# ───────── boss state ─────────


@dataclass
class BossState:
    boss: BossDef
    entity: Entity
    phase_index: int
    phase_hp_remaining: int
    shooter: EnemyShooter
    path_t0: float
    intro_remaining: float
    # Effective per-phase HPs after applying any session-level scaling
    # (e.g. _SP_BOSS_HP_SCALE in single-player). Populated at boss spawn;
    # all HP-threshold / phase-transition math reads from here rather
    # than ``boss.phases[*].hp`` so scaling stays consistent across the
    # whole fight. Defaults to empty for unit tests that construct
    # BossState directly without going through _maybe_spawn_boss.
    phase_hps: tuple[int, ...] = ()
    settled: bool = False
    # Wall-clock sim_time at which the boss intro completed — used by
    # the time-based bonus on death (kid playtest 2026-05-02 #12).
    fight_started_at: float = 0.0
    # Shield window state (kid playtest #15/#16). seconds_remaining > 0
    # means the boss is currently invulnerable. cycle_phase is "vuln"
    # or "shield" or "off"; cycle_t accumulates inside the active phase
    # so we can flip when the configured phase duration elapses.
    shield_remaining: float = 0.0
    cycle_phase: str = "off"  # "off" | "vuln" | "shield"
    cycle_t: float = 0.0
    # Total seconds the boss has spent shielded — subtracted from the
    # time-bonus elapsed so shield windows don't penalise the player.
    shielded_seconds_accumulated: float = 0.0
    # Total damage taken so far across all phases — kid playtest #13.
    # Replaces the old per-phase HP refill model: bar reads
    # (max_total - damage_taken) / max_total which decreases monotonically.
    damage_taken: int = 0
    # Previous wrapped path-time, used by _advance_boss to detect a
    # formation cycle boundary and reset the shooter exactly once per
    # loop. Naive reset-on-shooter-exhaust caused carpet-of-projectiles
    # firing between the last beat and the cycle wrap (kid playtest
    # 2026-05-03).
    last_path_t_wrap: float = 0.0
    # Homing-missile salvo cadence (kid playtest 2026-05-03 #3 —
    # final boss). Accumulates per tick post-intro; on crossing the
    # configured rate, fires `homing_missile_salvo` missiles.
    missile_clock: float = 0.0


# ───────── Level scene ─────────


@dataclass
class LevelScene(Scene):
    app: AppState
    level_index: int

    # All else populated in enter()
    _scheduler: WaveScheduler = field(init=False)
    _session: CoopSession = field(init=False)
    _powerup_states: dict[PlayerSlot, PlayerPowerupState] = field(init=False)
    _player_entities: dict[PlayerSlot, Entity | None] = field(init=False)
    _player_positions: dict[PlayerSlot, Vec2] = field(init=False)
    # Per-slot internal-tick at which the next missile auto-fire is due.
    # Initialised to 0 so the first auto-fire happens on enter; the gate
    # at the dispatch site checks current_tick >= next_fire_tick.
    _missile_next_fire_tick: dict[PlayerSlot, int] = field(init=False)
    _grid: SpatialGrid = field(init=False)
    _sim_time: float = field(default=0.0, init=False)
    _internal_tick: int = field(default=0, init=False)
    _boss: BossState | None = field(default=None, init=False)
    _boss_dispatched: bool = field(default=False, init=False)
    _level_completed: bool = field(default=False, init=False)
    _level_complete_grace: float = field(default=0.0, init=False)
    _music_playing: str | None = field(default=None, init=False)
    # Track which (wave_idx, spawn_idx, member_idx) tuples have had a
    # warning telegraph spawned, so we don't double-up.
    _telegraphed: set[tuple[int, int, int]] = field(default_factory=set, init=False)
    # Slots that have given any real input this level — used to gate
    # auto-continue (kid playtest 2026-05-02 #9). An idle P2 with no
    # plays should NEVER spend continues to come back from death; the
    # auto-spend was the cause of "P2's life count went 1 → 3 after
    # they died again." Populated lazily in _apply_player_input.
    _engaged: set[PlayerSlot] = field(default_factory=set, init=False)
    # True ship deaths this level (drone hits and shield-absorbed hits
    # excluded) — drives the resupply death catch-up bonus (fun review
    # 2026-06-12 R2): struggling players get extra powerups instead of
    # the old always-on level-index drop flood.
    _deaths_this_level: int = field(default=0, init=False)

    def __init__(self, app: AppState, level_index: int = 1) -> None:
        self.app = app
        self.level_index = level_index

    # Render-branch protocol (read by main.py): scenes whose entities
    # are drawn by the world Renderer set `world_rendered = True` and
    # expose the backdrop to draw under them. Plain class attr (no
    # annotation) so the dataclass machinery doesn't treat it as a field.
    world_rendered = True

    @property
    def background_name(self) -> str:
        """The active level's `background:` field, for the renderer.

        Unknown level indices fall back to the registry default name —
        the renderer degrades gracefully on unknown names anyway, this
        just keeps the property total.
        """
        level_def = self.app.content.levels.get(self.level_index)
        return level_def.background if level_def is not None else "bg_starfield_01"

    # ───────── lifecycle ─────────

    def enter(self, world: World) -> None:
        bundle = self.app.content
        if self.level_index not in bundle.levels:
            raise RuntimeError(f"level {self.level_index} not in content")
        level = bundle.levels[self.level_index]
        # Hold the LevelDef so per-tick systems (e.g. the stray-asteroid
        # hazard spawner) can read level-scoped config without re-fetching.
        self._level_def = level
        # Single-player runs ship at reduced wave density (boy playtest
        # 2026-05-23). Multiplier flows into WaveScheduler at build-time
        # so spawn counts are pre-scaled; boss HP scaling happens later
        # at boss-spawn time in _maybe_spawn_boss.
        density = _SP_ENEMY_DENSITY if self.app.single_player else 1.0
        self._scheduler = WaveScheduler(level, density_multiplier=density)
        # Seed score from any prior cleared-level totals (carry-forward).
        # Title→PLAY and LevelSelect both clear last_*_score before launching,
        # so a fresh session sees zeros here.
        from ssdq.core.coop.scoring import ScoreLedger as _ScoreLedger

        # Seed lives from the previous cleared level if we have it, else
        # fall back to options.starting_lives (fresh campaign / first level).
        # Kid playtest 2026-05-02: lives reset to 3 every level — P2 with
        # no movement could never go below 1 because each level rebooted
        # the lifecycle.
        seeded_p1_lives = self.app.last_lives.get(P1.index)
        seeded_p2_lives = self.app.last_lives.get(P2.index)
        self._session = CoopSession.initial(
            bundle.coop,
            self.app.options,
            scores=_ScoreLedger.with_seed(
                team=self.app.last_team_score,
                p1=self.app.last_p1_score,
                p2=self.app.last_p2_score,
            ),
            p1_lives=seeded_p1_lives,
            p2_lives=seeded_p2_lives,
        )
        # Solo-play mode (added 2026-05-08): force P2 OUT so
        # CoopSession.is_game_over fires on a P1 wipeout. The
        # _engaged gate in _apply_player_input skips continue
        # consumption for non-engaged-yet-OUT slots, so P2 stays out
        # cleanly without burning the player's continues.
        if self.app.single_player:
            self._session.mark_out(P2)
        # Powerup state per slot — one ship type for the slice; tree name
        # is parsed off the primary weapon name (e.g. "pulse_lvl1" → "pulse").
        ship = bundle.ships["vanguard"]
        tree = ship.primary_weapon.split("_lvl")[0]
        # Apply any pending bomb bonus from an inter-level scene (e.g.
        # DockingScene) on TOP of the ship's starting baseline. Consumed
        # one-shot so Title → Level without another docking doesn't
        # re-award.
        bomb_bonus = max(0, self.app.bomb_bonus_pending)
        self.app.bomb_bonus_pending = 0
        # Drain the docking shield-charge bonus into both slots' running
        # inventories — same one-shot pattern as bomb_bonus_pending above.
        # Fun review 2026-06-12 R2: DockingScene had been staging this
        # since the equippable-shield work but nothing ever consumed it,
        # so the promised resupply shield silently evaporated each dock.
        shield_bonus = max(0, self.app.shield_charge_pending)
        self.app.shield_charge_pending = 0
        if shield_bonus:
            self.app.add_shield_charge(P1, shield_bonus)
            self.app.add_shield_charge(P2, shield_bonus)
        # Carry weapon tier across LEVEL boundaries — kid playtest:
        # "If you were good and you got awesome weaponry then you keep
        # it." Look up each slot's last persisted level (defaults to 0
        # for a fresh campaign) and clamp to the tree's max so a stale
        # tier from a previous content-bundle layout can't crash here.
        max_tree_level = max(0, len(bundle.weapon_trees.get(tree, ())) - 1)
        p1_tier = self._seeded_tier(P1, max_tree_level)
        p2_tier = self._seeded_tier(P2, max_tree_level)
        # Kid playtest 2026-04-28 #4 — bombs/ship-speed used to reset every
        # level. Carry forward any stockpile written by the previous cleared
        # level (or from before death; reset_on_death already preserves
        # bombs / ship_speed_bonus per-life). Bombs are clamped to at least
        # ship.starting_bombs so an empty stockpile never starts below
        # baseline; the supply-ship bonus is added on TOP.
        p1_bombs = max(self.app.last_bombs.get(P1.index, 0), ship.starting_bombs) + bomb_bonus
        p2_bombs = max(self.app.last_bombs.get(P2.index, 0), ship.starting_bombs) + bomb_bonus
        p1_speed_bonus = self.app.last_ship_speed_bonus.get(P1.index, 0.0)
        p2_speed_bonus = self.app.last_ship_speed_bonus.get(P2.index, 0.0)
        p1_missile_level = self._seeded_missile_level(P1)
        p2_missile_level = self._seeded_missile_level(P2)
        self._powerup_states = {
            P1: PlayerPowerupState(
                weapon=WeaponState(tree=tree, level=p1_tier),
                bombs=p1_bombs,
                lives=self.app.options.starting_lives,
                ship_speed_bonus=p1_speed_bonus,
                missile_level=p1_missile_level,
            ),
            P2: PlayerPowerupState(
                weapon=WeaponState(tree=tree, level=p2_tier),
                bombs=p2_bombs,
                lives=self.app.options.starting_lives,
                ship_speed_bonus=p2_speed_bonus,
                missile_level=p2_missile_level,
            ),
        }
        self._player_entities = {P1: None, P2: None}
        self._player_positions = {
            P1: Vec2(PLAY_W * 0.40, _PLAYER_SPAWN_Y),
            P2: Vec2(PLAY_W * 0.60, _PLAYER_SPAWN_Y),
        }
        self._missile_next_fire_tick = {P1: 0, P2: 0}
        self._grid = SpatialGrid(cell_size=64.0)
        self._sim_time = 0.0
        self._internal_tick = 0
        self._boss = None
        self._boss_dispatched = False
        self._level_completed = False
        self._level_complete_grace = 0.0
        self._music_playing = None
        self._telegraphed = set()
        self._engaged = set()
        self._deaths_this_level = 0
        # Stray-asteroid hazard (Level 7; fun review 2026-06-12). The
        # first burst fires after the first scheduled interval rather than
        # at t=0 so the level opens clean. -1.0 marks "not yet scheduled"
        # so _maybe_spawn_stray_asteroids seeds the first deadline lazily
        # off the live tick (consistent with the per-level reset pattern).
        self._next_stray_time = -1.0

        # Spawn P1 immediately. P2 only spawns in 2-player mode (added
        # 2026-05-08); the title menu sets app.single_player. The
        # _engaged gate elsewhere already keeps an unspawned P2 out of
        # lifecycle/continue accounting, so all that's needed here is
        # the spawn skip.
        self._spawn_player(world, P1, self._player_positions[P1])
        if not self.app.single_player:
            self._spawn_player(world, P2, self._player_positions[P2])

        # HUD snapshot resource (renderer reads via duck-typed shape).
        world.insert_resource(self._build_hud_state(world))

        # Per-level narrative banner — overlays mid-screen for a few
        # seconds while the kid reads the story beat. Input is NOT
        # blocked (players can shoot through it); the wave scheduler
        # has its own ramp-up so nothing punishing happens during the
        # read. Skipped if the level lacks an entry (defensive).
        intro_text = level_intro_text(self.level_index)
        if intro_text:
            banner_ticks = int(_LEVEL_INTRO_BANNER_SECONDS * 60)
            world.spawn(
                LevelIntroBanner(text=intro_text, total_ticks=banner_ticks),
                TimeToLive(ticks=banner_ticks),
            )

        # Music — start the per-level track. Track names are registered
        # by BootScene as ``level_NN`` (+ ``_b``/``_c`` variants) /
        # ``boss_NN`` (zero-padded). The YAML's ``level.music`` field is
        # a content path, not a bus name, so we derive the registered
        # name from the level index here. The rotation counter is bumped
        # exactly once per entry, AFTER picking this entry's track, so
        # the next entry of the same level hears the following pool slot
        # (fun review 2026-06-12: consecutive entries should differ).
        self._switch_music(self._level_music_name())
        self._advance_music_rotation()

    def exit(self, world: World) -> None:
        self.app.audio.stop_music()
        self.app.last_team_score = self._session.scores.snapshot().team
        self.app.last_p1_score = self._session.scores.snapshot().p1
        self.app.last_p2_score = self._session.scores.snapshot().p2
        self.app.completed_level = self._level_completed
        # Persist weapon tiers across level boundaries — only when the
        # level was actually CLEARED (so a game-over → restart begins
        # at base tier rather than rewarding a wipeout). The dict is
        # keyed by slot index (0 / 1) so AppState stays free of the
        # PlayerSlot dataclass type.
        if self._level_completed:
            self.app.last_weapon_tiers = {
                P1.index: self._powerup_states[P1].weapon.level,
                P2.index: self._powerup_states[P2].weapon.level,
            }
            # Carry bombs + permanent ship-speed bonus into the next level.
            # Kid playtest 2026-04-28 #4 — these used to reset each level.
            self.app.last_bombs = {
                P1.index: self._powerup_states[P1].bombs,
                P2.index: self._powerup_states[P2].bombs,
            }
            self.app.last_ship_speed_bonus = {
                P1.index: self._powerup_states[P1].ship_speed_bonus,
                P2.index: self._powerup_states[P2].ship_speed_bonus,
            }
            # Missile auto-fire tier — same persist-on-clear rule.
            self.app.last_missile_levels = {
                P1.index: self._powerup_states[P1].missile_level,
                P2.index: self._powerup_states[P2].missile_level,
            }
            # Lives — kid playtest 2026-05-02: were silently resetting to
            # starting_lives every level entry. Persist on clear so the
            # tension actually carries through the campaign.
            self.app.last_lives = {
                P1.index: self._session.lifecycle(P1).lives,
                P2.index: self._session.lifecycle(P2).lives,
            }
            # Re-stage live drones into drones_pending so the next level's
            # _spawn_pending_drones recreates them. The despawn sweep below
            # would otherwise destroy them with no path back. Kid playtest
            # 2026-04-28: "drones do not persist between resupplies".
            self.app.drones_pending[P1] = (
                self.app.drones_pending.get(P1, 0) + self._count_drones(world, P1)
            )
            self.app.drones_pending[P2] = (
                self.app.drones_pending.get(P2, 0) + self._count_drones(world, P2)
            )
        # Sweep ALL level entities so they don't ghost into the next scene —
        # kid playtest #6: "ghost ship of me on the screen where my last
        # position was". The world is shared across the scene stack; if
        # we don't clear, player ships, enemies, bullets, pickups, telegraphs,
        # explosions and bombs all persist into GameOverScene → TitleScene
        # → next LevelScene.
        for eid in list(world.alive_entities()):
            world.despawn(eid)

    def _seeded_tier(self, slot: PlayerSlot, max_tree_level: int) -> int:
        """Resolve the starting weapon tier for a slot on level enter.

        Reads ``app.last_weapon_tiers`` (set by the previous level's
        exit) and clamps to the current tree's max. Falls back to 0
        when no prior tier exists.

        Kid playtest 2026-05-03 #5 — docking weapon-tier floor: a
        player arriving at level N is floored to tier ``N - 1`` so a
        player who's been struggling still has a fighting chance at
        each new level. Never downgrades a higher prior tier. Level 1
        has no floor (fresh start). Fun review 2026-06-12 R2: the
        floor caps at tier 2 — the old uncapped floor handed out
        tier 4 for free on level 5, so the back-half tiers were never
        earned. Tiers 3+ now only come from pickups.
        """
        prior = self.app.last_weapon_tiers.get(slot.index, 0)
        floor = max(0, min(self.level_index - 1, 2))
        return max(0, min(max(prior, floor), max_tree_level))

    def _seeded_missile_level(self, slot: PlayerSlot) -> int:
        """Resolve the starting missile auto-fire tier for a slot on enter.

        Mirrors ``_seeded_tier`` for ``app.last_missile_levels``. Clamps
        to ``MISSILE_LEVEL_CAP`` so a stale entry can't push the player
        past the highest pattern.
        """
        prior = self.app.last_missile_levels.get(slot.index, 0)
        return max(0, min(prior, MISSILE_LEVEL_CAP))

    # ───────── per-tick ─────────

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        # Kid playtest 2026-05-02 #10/#17: drive _internal_tick from the
        # scene's own counter rather than deriving from the global clock.
        # The SceneStack returns early while paused, so global `tick`
        # keeps counting up but we DO NOT — _sim_time stays consistent
        # across the pause and the wave scheduler isn't fast-forwarded
        # into the boss spawn on unpause.
        # _internal_tick == 0 on the first scene tick (matches the
        # previous semantics so existing pinned-time tests still pass).
        dt = TICK_DT
        self._sim_time = self._internal_tick * dt

        # Track which slots have actually engaged with the game this
        # level — required for the auto-continue gate below (kid
        # playtest 2026-05-02 #9). A slot is engaged the first time it
        # produces non-default input (any movement, fire, bomb, etc.).
        for slot, inp in ((P1, inputs[0]), (P2, inputs[1])):
            if slot in self._engaged:
                continue
            if _input_is_active(inp):
                self._engaged.add(slot)

        # 1. coop session timers (lifecycle, plus any pending continues)
        self._session.tick(dt)
        # Auto-spend continues — but ONLY for slots that have actually
        # played. An idle P2 (kid playtest 2026-05-02 #9) sat at OUT
        # and was repeatedly resurrected by this loop, with their life
        # count snapping back to options.starting_lives every time.
        # Engaged-only gate keeps solo play sane: an unbound P2 stays
        # OUT forever and the solo player still has continues for P1.
        for slot in (P1, P2):
            if self._session.lifecycle(slot).state != LifecycleState.OUT:
                continue
            if slot not in self._engaged:
                continue
            self._session.try_consume_continue(slot)

        # 2. Speed-boost + shield + fire-rate decay; sync ShieldHalo on
        # player ships. The fire-rate boost is the WEAPON_SPEED pickup's
        # timer (Task #9); ship_speed_bonus is permanent so doesn't tick.
        for slot in (P1, P2):
            ps = self._powerup_states[slot]
            ps = ps.tick_speed_boost(dt)
            ps = ps.tick_shield_decay(dt)
            ps = ps.tick_fire_rate_boost(dt)
            self._powerup_states[slot] = ps
        self._sync_shield_halos(world)

        # 3. Player input → movement, fire, bomb
        self._apply_player_input(world, P1, inputs[0])
        self._apply_player_input(world, P2, inputs[1])

        # 3a. Drones — drain pending pickups into entities, then sync each
        # drone's position to its owner's current formation slot. We do
        # this AFTER player input so drones land on the same tick the
        # player moves (otherwise they'd lag one tick behind).
        self._spawn_pending_drones(world)
        self._sync_drone_positions(world)

        # 4. Wave scheduler → spawn enemies; transition to boss if pending
        self._spawn_wave_enemies(world)
        self._maybe_spawn_boss(world)
        # 4a. Stray-asteroid dodge-fest hazard (Level 7; fun review
        # 2026-06-12). Off unless the level's YAML enables it.
        self._maybe_spawn_stray_asteroids(world)

        # 5. Advance enemies along formations + emit fire beats
        self._advance_enemies(world)

        # 5z. Advance free-roam enemies (sentinel/marauder) — drift +
        # dodge logic; replaces formation following once an entry
        # formation has completed.
        self._advance_free_roam(world)

        # 5a. Decay per-enemy spawn-shield timers; remove ShieldHalo on
        # expiry. Bosses use a separate state machine on BossState
        # (advanced inside _advance_boss).
        self._tick_enemy_shields(world, dt)

        # 6a. Heat-seeking missile homing — adjust velocities BEFORE the
        # motion integrator so the new headings take effect this tick.
        self._tick_missiles(world, dt)

        # 6. Integrate plain Position+Velocity entities (bullets, pickups, particles)
        self._integrate_motion(world, dt)

        # 7. Active bomb shockwaves
        self._tick_bombs(world, dt)

        # 8. Build spatial grid + resolve collisions
        self._resolve_collisions(world)

        # 9. Respawn dead-but-INVULNERABLE players (i-frame entry)
        self._handle_respawns(world)

        # 10. Advance animations + decay flash/blink timers
        self._advance_animations(world)

        # 11. Cull off-screen / TTL-expired entities
        self._cull_entities(world)

        # 12. Refresh HUD snapshot
        world.insert_resource(self._build_hud_state(world))

        # 12. Boss music switch on boss spawn — fade from level to boss
        # track. The boss track name is per-level (boss_NN) so the
        # encounter has its own intensity rather than always reusing
        # boss_01's mood.
        if self._boss is not None:
            boss_name = self._boss_music_name()
            if self._music_playing != boss_name:
                self._switch_music(boss_name)

        # 13. Win/loss transitions
        # Advance the local tick counter at the end of the step so the
        # NEXT call's _sim_time reflects "one more tick" — keeps the
        # first scene tick at sim_time=0 (matches the previous semantics
        # before the global-clock derivation was removed).
        self._internal_tick += 1
        if self._level_completed:
            self._level_complete_grace += dt
            if self._level_complete_grace >= 1.5:
                from ssdq.scenes.level_complete import LevelCompleteScene

                return Replace(
                    scene=LevelCompleteScene(
                        self.app,
                        completed_level_index=self.level_index,
                    )
                )
        if self._session.is_game_over:
            from ssdq.scenes.game_over import GameOverScene

            return Replace(scene=GameOverScene(self.app))

        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        # Renderer is owned by main.py; the level scene relies on the
        # frame-loop calling renderer.draw(world, surface) externally.
        # This method is intentionally a no-op — main owns the draw call
        # so it can pass the right tick + paused flag.
        return None

    # ───────── helpers: spawning ─────────

    def _spawn_player(self, world: World, slot: PlayerSlot, pos: Vec2) -> None:
        bundle = self.app.content
        ship_name = "vanguard" if slot == P1 else "vanguard_red"
        ship = bundle.ships[ship_name]
        eid = world.spawn(
            Position(pos),
            Velocity(Vec2(0.0, 0.0)),
            CircleHitbox(radius=ship.hitbox_radius),
            FactionTag(Faction.PLAYER),
            PlayerOwned(slot),
            # Scale 0.66 (a third smaller) per kid playtest #8 — Kenney
            # ship sprite ~98px → ~65px on screen.
            Sprite(path=ship.sprite, layer=10, scale=0.66),
            PlayerShip(slot=slot, weapon_cooldown=0.0),
        )
        self._player_entities[slot] = eid

    def _apply_player_input(
        self,
        world: World,
        slot: PlayerSlot,
        inp: PlayerInput,
    ) -> None:
        eid = self._player_entities.get(slot)
        if eid is None or not world.is_alive(eid):
            return
        lifecycle = self._session.lifecycle(slot)
        # Even DYING/INVULN players don't accept input — render-only.
        if lifecycle.state in (LifecycleState.DYING, LifecycleState.OUT):
            return

        bundle = self.app.content
        ship = bundle.ships["vanguard" if slot == P1 else "vanguard_red"]
        pstate = self._powerup_states[slot]
        # Multiplicative: temporary SPEED_UP boost on top of the
        # permanent SHIP_SPEED bonus (capped at +60% by the state layer).
        speed_mult = pstate.speed_boost.multiplier if pstate.speed_boost.active else 1.0
        speed_mult *= 1.0 + pstate.ship_speed_bonus
        max_speed = ship.max_speed * speed_mult

        # Movement
        pos = world.must_get(eid, Position).pos
        move = inp.move.clamped_magnitude(1.0)
        new_x = pos.x + move.x * max_speed * TICK_DT
        new_y = pos.y + move.y * max_speed * TICK_DT
        # Clamp to playfield with a small inset so the sprite isn't half off-screen.
        new_x = max(20.0, min(PLAY_W - 20.0, new_x))
        new_y = max(20.0, min(PLAY_H - 20.0, new_y))
        new_pos = Vec2(new_x, new_y)
        world.replace(eid, Position(new_pos))
        self._player_positions[slot] = new_pos

        # Drone-formation cycle (task #10) — only effective if the player
        # has at least one live drone, otherwise the press is silently
        # ignored (no drones to reposition). Cycles in declaration order
        # of ``_DRONE_CONFIGS``.
        if inp.drone_cycle and self._count_drones(world, slot) > 0:
            cur = self.app.drone_config.get(slot, 0)
            self.app.drone_config[slot] = (cur + 1) % len(_DRONE_CONFIGS)

        # Update PlayerShip cooldown
        ps = world.must_get(eid, PlayerShip)
        new_cooldown = max(0.0, ps.weapon_cooldown - TICK_DT)
        # Fire
        fired_this_tick = False
        if inp.fire and new_cooldown <= 0.0 and lifecycle.state == LifecycleState.ALIVE:
            self._fire_player_weapon(world, slot, ship.primary_weapon, pstate.weapon, new_pos)
            fired_this_tick = True
            # Set cooldown from the fire_rate of the *current weapon level*,
            # then divide by the WEAPON_SPEED boost multiplier (Task #9)
            # so the boost shrinks the gap between shots while active.
            tree = bundle.weapon_trees.get(pstate.weapon.tree, ())
            if tree:
                lvl = min(pstate.weapon.level, len(tree) - 1)
                weapon_def = bundle.weapons[tree[lvl]]
                new_cooldown = 1.0 / max(0.001, weapon_def.fire_rate)
            else:
                new_cooldown = 0.125
            if pstate.fire_rate_boost.active:
                new_cooldown /= max(0.001, pstate.fire_rate_boost.multiplier)
        world.replace(eid, PlayerShip(slot=slot, weapon_cooldown=new_cooldown))

        # Drone fire — when the player fires this tick, every owned drone
        # spits out the player's current weapon shape from its own
        # position. Drones never trigger bombs / missiles (primary only).
        if fired_this_tick:
            self._fire_drones_for_slot(world, slot, ship.primary_weapon, pstate.weapon)

        # Bomb
        if inp.bomb and pstate.bombs > 0 and lifecycle.state == LifecycleState.ALIVE:
            self._fire_bomb(world, slot, new_pos)
            self._powerup_states[slot] = pstate.with_bombs(pstate.bombs - 1)
            self.app.audio.play_sfx("bomb")

        # Shield (equippable). Consumes a charge from the AppState
        # inventory and grants a brief invulnerability window via the
        # existing forcefield/halo mechanism. Empty-press is a deliberate
        # no-op + soft chirp so the kid hears the button is "alive".
        if inp.shield and lifecycle.state == LifecycleState.ALIVE:
            if self.app.consume_shield_charge(slot):
                refreshed = self._powerup_states[slot].with_shield(
                    Shield(seconds_remaining=SHIELD_CONSUME_DURATION)
                )
                self._powerup_states[slot] = refreshed
                # Snap the halo on this tick so the forcefield engulfs the
                # ship the moment the button fires (matches pickup behaviour).
                self._sync_shield_halos(world)
                self.app.audio.play_sfx("powerup")
            else:
                # Empty press — quiet chirp; never errors.
                self.app.audio.play_sfx("hit", volume=0.15)

        # Tier-based missile auto-fire (post-2026-04-30 redesign).
        # Each MISSILE pickup bumps PlayerPowerupState.missile_level;
        # the dispatcher fires _MISSILE_PATTERNS[level] missiles on a
        # MISSILE_AUTOFIRE_TICKS cadence while the slot is alive.
        if lifecycle.state == LifecycleState.ALIVE:
            self._maybe_auto_fire_missiles(world, slot, new_pos, self._internal_tick)

    def _fire_player_weapon(
        self,
        world: World,
        slot: PlayerSlot,
        primary_name: str,
        weapon_state: WeaponState,
        ship_pos: Vec2,
    ) -> None:
        bundle = self.app.content
        tree = bundle.weapon_trees.get(weapon_state.tree, ())
        if not tree:
            weapon_name = primary_name
        else:
            lvl = min(weapon_state.level, len(tree) - 1)
            weapon_name = tree[lvl]
        weapon_def = bundle.weapons[weapon_name]
        for fp in weapon_def.pattern:
            angle = math.radians(fp.angle_deg)
            # Bullet upward → angle 0 = straight up; positive angle rotates clockwise.
            vx = math.sin(angle) * weapon_def.speed
            vy = -math.cos(angle) * weapon_def.speed
            spawn_pos = Vec2(ship_pos.x + fp.offset_x, ship_pos.y + fp.offset_y)
            world.spawn(
                Position(spawn_pos),
                Velocity(Vec2(vx, vy)),
                CircleHitbox(radius=4.0),
                FactionTag(Faction.PLAYER_BULLET),
                PlayerOwned(slot),
                Sprite(path=weapon_def.sprite, layer=8),
                Damage(amount=weapon_def.damage),
                TimeToLive(ticks=180),
            )
        self.app.audio.play_sfx("laser", volume=0.4)

    def _fire_missile(self, world: World, slot: PlayerSlot, pos: Vec2) -> None:
        """Spawn a heat-seeking missile owned by ``slot`` at ``pos``.

        Latches onto the closest enemy (boss preferred when present) at
        fire time; the homing system tracks just that one target.
        """
        target = self._closest_enemy_to(world, pos)
        # Initial heading: straight up unless we have a target — in which
        # case we point at the target so the first tick already curves
        # the missile correctly.
        if target is not None:
            tpos = world.get(target, Position)
            if tpos is not None:
                dx = tpos.pos.x - pos.x
                dy = tpos.pos.y - pos.y
                heading = math.atan2(dx, -dy)  # 0 = straight up
            else:
                heading = 0.0
        else:
            heading = 0.0
        # Velocity = initial_speed in the heading direction.
        vx = math.sin(heading) * MISSILE_INITIAL_SPEED
        vy = -math.cos(heading) * MISSILE_INITIAL_SPEED
        world.spawn(
            Position(pos),
            Velocity(Vec2(vx, vy)),
            CircleHitbox(radius=MISSILE_HITBOX_RADIUS),
            FactionTag(Faction.PLAYER_BULLET),
            PlayerOwned(slot),
            Sprite(path=MISSILE_SPRITE, layer=8, scale=1.0),
            Damage(amount=MISSILE_DAMAGE),
            TimeToLive(ticks=int(MISSILE_LIFETIME * 60)),
            Missile(
                target=target,
                speed=MISSILE_INITIAL_SPEED,
                heading=heading,
            ),
        )

    def _auto_fire_missiles_for_slot(
        self, world: World, slot: PlayerSlot, ship_pos: Vec2
    ) -> None:
        """Spawn the per-tier missile pattern at offsets from ``ship_pos``.

        Reads ``missile_level`` off the slot's PlayerPowerupState and
        looks up ``_MISSILE_PATTERNS``. Tier 0 is a no-op. The audio
        cue plays once per fire-cycle, not once per spawned missile.
        """
        pstate = self._powerup_states[slot]
        pattern = _MISSILE_PATTERNS.get(pstate.missile_level, ())
        if not pattern:
            return
        for dx in pattern:
            self._fire_missile(world, slot, Vec2(ship_pos.x + dx, ship_pos.y))
        # 0.42 = 0.6 × 0.7 — kid playtest 2026-05-02 asked for missile SFX
        # 30% quieter (an L5 barrage was drowning out the laser).
        self.app.audio.play_sfx("missile", volume=0.42)

    def _maybe_auto_fire_missiles(
        self,
        world: World,
        slot: PlayerSlot,
        ship_pos: Vec2,
        current_tick: int,
    ) -> None:
        """Per-tick auto-fire trigger. Fires when the slot is due (per
        ``_missile_next_fire_tick[slot]``) and at a non-zero tier; the
        caller is responsible for the lifecycle ALIVE check so a dying
        ship doesn't keep auto-firing.
        """
        if self._powerup_states[slot].missile_level <= 0:
            return
        if current_tick < self._missile_next_fire_tick[slot]:
            return
        self._auto_fire_missiles_for_slot(world, slot, ship_pos)
        self._missile_next_fire_tick[slot] = current_tick + MISSILE_AUTOFIRE_TICKS

    def _closest_enemy_to(self, world: World, pos: Vec2) -> Entity | None:
        """Return the entity id of the closest live ENEMY to ``pos``.

        Bosses are preferred when present (they're the highest-value
        target and the kid spec calls them out: "homes in on a target,
        typically the closest"). Falls back to nearest non-boss enemy.
        Returns ``None`` if no enemies exist.
        """
        if self._boss is not None and world.is_alive(self._boss.entity):
            return self._boss.entity
        best: tuple[float, Entity] | None = None
        for eid, e_pos, ftag in world.query2(Position, FactionTag):
            if ftag.faction != Faction.ENEMY:
                continue
            d2 = (e_pos.pos.x - pos.x) ** 2 + (e_pos.pos.y - pos.y) ** 2
            if best is None or d2 < best[0]:
                best = (d2, eid)
        return best[1] if best else None

    def _tick_missiles(self, world: World, dt: float) -> None:
        """Advance heat-seeking missiles toward their target.

        Each tick:
          * if the target is alive, rotate the heading toward it (clamped
            by ``MISSILE_TURN_RATE``);
          * accelerate up to ``MISSILE_MAX_SPEED``;
          * write the new ``Velocity`` so the standard motion integrator
            does the rest;
          * point the sprite along the heading so the missile reads as
            "tracking" visually.
        """
        from dataclasses import replace as _dc_replace

        for eid, missile in list(world.query1(Missile)):
            pos_c = world.get(eid, Position)
            if pos_c is None:
                continue
            pos = pos_c.pos
            # Target re-acquisition: if the latched target died or vanished,
            # we let the missile coast (per spec: no re-target). The
            # TimeToLive component will cull it after MISSILE_LIFETIME.
            target_alive = (
                missile.target is not None
                and world.is_alive(missile.target)
                and world.get(missile.target, Position) is not None
            )
            if target_alive:
                tpos = world.must_get(missile.target, Position).pos
                desired = math.atan2(tpos.x - pos.x, -(tpos.y - pos.y))
                # Wrap the angular delta to [-pi, pi] so we always turn the
                # short way around.
                delta = (desired - missile.heading + math.pi) % (2 * math.pi) - math.pi
                max_turn = MISSILE_TURN_RATE * dt
                if delta > max_turn:
                    delta = max_turn
                elif delta < -max_turn:
                    delta = -max_turn
                new_heading = missile.heading + delta
            else:
                new_heading = missile.heading
            # Accelerate toward MAX_SPEED.
            new_speed = min(MISSILE_MAX_SPEED, missile.speed + MISSILE_ACCEL * dt)
            vx = math.sin(new_heading) * new_speed
            vy = -math.cos(new_heading) * new_speed
            world.replace(eid, Velocity(Vec2(vx, vy)))
            # Sprite rotation — point the nose along the heading. Sprite
            # rotation in this engine is in radians; the artwork is drawn
            # nose-up so heading 0 == 0 rad rotation.
            spr = world.get(eid, Sprite)
            if spr is not None:
                world.replace(eid, _dc_replace(spr, rotation_rad=new_heading))
            # Persist the new homing state.
            world.replace(
                eid,
                Missile(
                    target=missile.target if target_alive else None,
                    speed=new_speed,
                    heading=new_heading,
                ),
            )

    def _fire_bomb(self, world: World, slot: PlayerSlot, pos: Vec2) -> None:
        bundle = self.app.content
        ship = bundle.ships["vanguard" if slot == P1 else "vanguard_red"]
        bomb_def = bundle.bombs[ship.bomb]
        # The visual ring expands from 0 → aoe_radius over the bomb's
        # lifetime. The damage radius (aoe_radius) is fixed for the whole
        # duration so a "screen-clearing" bomb feels its size from frame 1.
        world.spawn(
            BombActive(
                pos=pos,
                radius=0.0,
                duration_remaining=bomb_def.duration,
                aoe_radius=bomb_def.radius,
                duration_total=bomb_def.duration,
                visual_progress=0.0,
            ),
        )
        if bomb_def.clears_bullets:
            # Wipe all enemy bullets immediately.
            to_kill: list[Entity] = []
            for eid, ftag in world.query1(FactionTag):
                if ftag.faction == Faction.ENEMY_BULLET:
                    to_kill.append(eid)
            for eid in to_kill:
                world.despawn(eid)

    # ───────── helpers: drones ─────────

    def _count_drones(self, world: World, slot: PlayerSlot) -> int:
        """Return how many alive drones currently belong to ``slot``."""
        n = 0
        for _eid, drone in world.query1(Drone):
            if drone.slot == slot:
                n += 1
        return n

    def _drones_for_slot(self, world: World, slot: PlayerSlot) -> list[tuple[Entity, Drone]]:
        """Stable list of (entity, drone) pairs for the given player slot,
        ordered by ``slot_index`` so flank assignment is deterministic."""
        out: list[tuple[Entity, Drone]] = []
        for eid, drone in world.query1(Drone):
            if drone.slot == slot:
                out.append((eid, drone))
        out.sort(key=lambda pair: pair[1].slot_index)
        return out

    def _next_free_drone_slot_index(self, world: World, slot: PlayerSlot) -> int | None:
        """First slot_index in 0..(_DRONE_MAX-1) not currently occupied
        by a live drone for ``slot``. ``None`` if all slots are full."""
        used = {d.slot_index for _e, d in self._drones_for_slot(world, slot)}
        for i in range(_DRONE_MAX):
            if i not in used:
                return i
        return None

    def _spawn_pending_drones(self, world: World) -> None:
        """Drain ``app.drones_pending`` into actual drone entities, one per
        tick per slot, capped at ``_DRONE_MAX`` drones alive per slot.
        Excess pending counts beyond the cap are silently discarded so the
        kid doesn't accumulate a stockpile during shielded full-roster
        moments."""
        for slot in (P1, P2):
            pending = self.app.drones_pending.get(slot, 0)
            if pending <= 0:
                continue
            free_idx = self._next_free_drone_slot_index(world, slot)
            if free_idx is None:
                # At cap — drop the pending count to 0 so a tactical
                # "denied" feedback can be added later if desired.
                self.app.drones_pending[slot] = 0
                continue
            # Only fulfil one drone per slot per tick — keeps the entity
            # spawn order stable and avoids a giant burst from a stacked
            # pickup train.
            self._spawn_drone(world, slot, free_idx)
            self.app.drones_pending[slot] = max(0, pending - 1)

    def _spawn_drone(self, world: World, slot: PlayerSlot, slot_index: int) -> Entity:
        """Spawn a drone entity for ``slot`` at the active formation offset
        from the player ship (or near the player's last known position
        if the ship is currently dead). Returns the new entity id."""
        bundle = self.app.content
        ship_name = "vanguard" if slot == P1 else "vanguard_red"
        ship = bundle.ships[ship_name]
        anchor = self._player_positions.get(slot, Vec2(PLAY_W * 0.5, _PLAYER_SPAWN_Y))
        cfg_idx = self.app.drone_config.get(slot, 0)
        off = drone_offset(cfg_idx, slot_index)
        spawn_pos = Vec2(anchor.x + off.x, anchor.y + off.y)
        # Drone scale = player_scale (0.66) * factor (~0.7) so it visibly
        # reads as smaller than the player without becoming a dot.
        drone_scale = 0.66 * _DRONE_SCALE_FACTOR
        eid = world.spawn(
            Position(spawn_pos),
            Velocity(Vec2(0.0, 0.0)),
            CircleHitbox(radius=_DRONE_HITBOX_RADIUS),
            FactionTag(Faction.PLAYER),
            PlayerOwned(slot),
            Sprite(path=ship.sprite, layer=9, scale=drone_scale),
            Health(hp=_DRONE_HP),
            MaxHealth(hp=_DRONE_HP),
            Drone(slot=slot, slot_index=slot_index),
        )
        return eid

    def _sync_drone_positions(self, world: World) -> None:
        """Snap each live drone to its formation offset relative to its
        owning player. Called every tick after the player has moved so
        drones perfectly mirror movement (no lag)."""
        for slot in (P1, P2):
            anchor = self._player_pos_if_alive(slot)
            if anchor is None:
                # Owner is dead/INVULN — leave drones in place. They'll
                # snap back when the player respawns.
                continue
            cfg_idx = self.app.drone_config.get(slot, 0)
            for eid, drone in self._drones_for_slot(world, slot):
                if not world.is_alive(eid):
                    continue
                off = drone_offset(cfg_idx, drone.slot_index)
                dx = anchor.x + off.x
                dy = anchor.y + off.y
                # Clamp to the same playfield inset as the player so
                # wide formations don't park the drone off-screen.
                dx = max(0.0, min(PLAY_W, dx))
                dy = max(0.0, min(PLAY_H, dy))
                world.replace(eid, Position(Vec2(dx, dy)))

    def _fire_drones_for_slot(
        self,
        world: World,
        slot: PlayerSlot,
        primary_name: str,
        weapon_state: WeaponState,
    ) -> None:
        """Have every live drone for ``slot`` fire the player's current
        primary weapon. Bombs / missiles never echo through drones."""
        for eid, _drone in self._drones_for_slot(world, slot):
            if not world.is_alive(eid):
                continue
            pos = world.get(eid, Position)
            if pos is None:
                continue
            self._fire_player_weapon(world, slot, primary_name, weapon_state, pos.pos)

    # ───────── helpers: waves ─────────

    def _spawn_wave_enemies(self, world: World) -> None:
        # Telegraph: 0.5s before any spawn whose first sample lands inside
        # the visible playfield (vs off-screen entry), spawn a warning
        # circle at the spawn location so the player isn't surprised.
        self._spawn_telegraphs(world)
        events = self._scheduler.tick(TICK_DT)
        for ev in events:
            self._spawn_enemy(world, ev)
        # If the kid cleared the current wave fast, fast-forward toward
        # the next wave so the gap never exceeds 5 seconds. Kid playtest
        # 2026-05-02 #6 — "if a wave finishes quickly cue the next wave
        # within 5 seconds."
        self._scheduler.cue_next_wave_if_idle(
            have_live_enemies=self._has_live_enemies(world)
        )

    def _has_live_enemies(self, world: World) -> bool:
        return any(
            ftag.faction == Faction.ENEMY for _eid, ftag in world.query1(FactionTag)
        )

    def _spawn_telegraphs(self, world: World) -> None:
        bundle = self.app.content
        warn_seconds = 0.5
        warn_ticks = int(warn_seconds * 60)
        for ev in self._scheduler.upcoming(within=warn_seconds):
            key = (ev.wave_index, ev.spawn_index, ev.member_index)
            if key in self._telegraphed:
                continue
            formation = bundle.formations.get(ev.formation)
            if formation is None:
                continue
            sample = evaluate_path(formation, 0.0, mirrored=ev.mirrored)
            # Only telegraph if the enemy's initial position is on-screen
            # (a "surprise" spawn from the player's POV). Off-screen entries
            # via top/sides are visible and don't need warning.
            if not (40 <= sample.pos.y <= PLAY_H - 40):
                self._telegraphed.add(key)
                continue
            if not (0 <= sample.pos.x <= PLAY_W):
                self._telegraphed.add(key)
                continue
            # Spawn a BossTelegraph (re-using the existing render path —
            # it's just a coloured circle outline) with TTL = warn_ticks.
            world.spawn(
                BossTelegraph(pos=sample.pos, radius=28.0, colour=(255, 200, 60)),
                TimeToLive(ticks=warn_ticks),
            )
            self._telegraphed.add(key)

    def _spawn_enemy(self, world: World, ev: SpawnEvent) -> None:
        bundle = self.app.content
        enemy = bundle.enemies.get(ev.enemy)
        if enemy is None:
            return
        formation = bundle.formations.get(ev.formation)
        if formation is None:
            return
        # Initial position = path sample at t=0
        sample = evaluate_path(formation, 0.0, mirrored=ev.mirrored)
        eid = world.spawn(
            Position(sample.pos),
            CircleHitbox(radius=enemy.hitbox_radius),
            FactionTag(Faction.ENEMY),
            Health(hp=enemy.hp),
            MaxHealth(hp=enemy.hp),
            Sprite(path=enemy.sprite, layer=6),
            FormationFollower(
                formation_name=ev.formation,
                mirrored=ev.mirrored,
                path_t0=ev.path_t0,
                speed=enemy.speed_along_path,
                formation_duration=formation.duration,
                fire_beats=enemy.fire_beats,
                weapon_name=enemy.weapon,
                score=enemy.score,
                drop_chance=enemy.drop_chance,
                drop_pool=enemy.drop_pool,
                passes_remaining=10_000 if enemy.passes_unlimited else ev.return_passes,
                guaranteed_drops=enemy.guaranteed_drops,
                level_scaled_drops=enemy.level_scaled_drops,
            ),
            ScoreValue(points=enemy.score),
        )
        if enemy.weapon and enemy.fire_beats:
            world.add(eid, EnemyShooterRef(shooter=EnemyShooter(beats=enemy.fire_beats)))
        # Bullet-blocking cover (level 6 asteroid hulks — fun review
        # 2026-06-12 R5). The marker is what the collision IGNORE path
        # checks; enemies without it pay nothing.
        if enemy.blocks_enemy_bullets:
            world.add(eid, BulletBlocker())
        # Spawn-shield (kid playtest 2026-05-02 #3). Attach an
        # EnemyShield + ShieldHalo so the kid clearly sees the
        # forcefield. The halo radius is generous so the visual reads
        # as "wraps the whole enemy".
        if enemy.shield_on_spawn_seconds > 0.0:
            halo_radius = max(28.0, enemy.hitbox_radius + 14.0)
            world.add(eid, EnemyShield(seconds_remaining=enemy.shield_on_spawn_seconds))
            world.add(eid, ShieldHalo(base_radius=halo_radius))
        # Shield-on-first-hit (kid playtest 2026-05-03 #2 + #9). Dormant
        # at spawn — the bubble pops up only after the first damaging
        # bullet, and is one-shot (ShieldOnHitConsumed prevents
        # re-activation after the shield expires).
        #
        # High-weapon-tier sprinkle (kid playtest 2026-04-28 #7 — "alien
        # ships need to look different and be tougher" at high weapon
        # tier). When max(p1, p2) weapon level ≥ 3, every Nth common
        # enemy spawns with a 1s on-hit shield. Deterministic by spawn
        # index so replays stay bit-identical.
        shield_secs = enemy.shield_on_first_hit_seconds
        if (
            shield_secs == 0.0
            and enemy.shield_on_spawn_seconds == 0.0
            and ev.enemy in _HIGH_TIER_SHIELD_TYPES
        ):
            max_tier = max(
                self._powerup_states[P1].weapon.level,
                self._powerup_states[P2].weapon.level,
            )
            if max_tier >= _HIGH_TIER_SHIELD_THRESHOLD:
                modulus = (
                    _HIGH_TIER_SHIELD_DENSE_MOD
                    if max_tier >= _HIGH_TIER_SHIELD_DENSE_THRESHOLD
                    else _HIGH_TIER_SHIELD_SPARSE_MOD
                )
                if (ev.wave_index + ev.spawn_index + ev.member_index) % modulus == 0:
                    shield_secs = _HIGH_TIER_SHIELD_SECONDS
        if shield_secs > 0.0:
            world.add(eid, ShieldOnHitConfig(seconds=shield_secs))
        # Free-roam capability (kid playtest 2026-05-03 #2 + #10). Marker
        # the formation-end branch reads to know "swap to FreeRoamAI
        # instead of despawning" once the entry formation completes. We
        # also seed an initial Velocity so the motion integrator picks
        # the entity up after the swap (FreeRoamAI replaces Velocity
        # each tick anyway).
        if enemy.free_roam is not None:
            speed, dodge, zt, zb = enemy.free_roam
            # Fire-loop period: longest beat + 0.5s buffer so the next
            # cycle has a clear "rearm gap" the kid can read as rhythm.
            fire_loop = (max(enemy.fire_beats) if enemy.fire_beats else 0.0) + 0.5
            world.add(eid, PendingFreeRoam(
                speed=speed,
                dodge_aggression=dodge,
                zone_top=zt,
                zone_bottom=zb,
                fire_loop_seconds=fire_loop,
            ))

    def _maybe_spawn_boss(self, world: World) -> None:
        if self._boss is not None or self._boss_dispatched:
            return
        ready = self._scheduler.pending_boss_events(self._sim_time)
        if not ready:
            return
        be = ready[0]
        bundle = self.app.content
        boss = bundle.bosses.get(be.boss)
        if boss is None:
            return
        formation = bundle.formations.get(boss.phases[0].formation)
        if formation is None:
            return
        # Arena sweep — the boss gets the stage to itself (fun review
        # 2026-06-12 R4). Must run BEFORE the boss entity spawns so the
        # ENEMY-faction filter can't catch the boss itself.
        self._sweep_enemies_for_boss(world)
        # Spawn boss above screen; it'll slide into formation as t advances.
        sample = evaluate_path(formation, 0.0)
        # Health = total HP across ALL phases; MaxHealth is the same.
        # Kid playtest 2026-05-02 #13: previously Health = phases[0].hp
        # and was REFILLED on each phase transition, so the boss bar
        # pegged at ~50% across a 2-phase fight. Now Health monotonically
        # decreases from total_max → 0 and phase transitions fire when
        # HP drops below the cumulative remaining-phase total.
        # Single-player scaling (kid playtest 2026-05-23): apply
        # _SP_BOSS_HP_SCALE to every phase HP at spawn, then read from
        # BossState.phase_hps everywhere downstream so the threshold +
        # phase-transition math stays consistent.
        hp_scale = _SP_BOSS_HP_SCALE if self.app.single_player else 1.0
        scaled_phase_hps: tuple[int, ...] = tuple(
            max(1, int(p.hp * hp_scale + 0.5)) for p in boss.phases
        )
        total_max = sum(scaled_phase_hps)
        eid = world.spawn(
            Position(sample.pos),
            CircleHitbox(radius=boss.hitbox_radius),
            FactionTag(Faction.ENEMY),
            Health(hp=total_max),
            MaxHealth(hp=total_max),
            Sprite(path=boss.sprite, layer=7),
            ScoreValue(points=boss.score),
            BossTag(),
        )
        # Telegraph entity — auto-cull after intro_telegraph_seconds via TTL.
        intro_ticks = int(boss.intro_telegraph_seconds * 60)
        world.spawn(
            BossTelegraph(
                pos=Vec2(PLAY_W / 2, 80.0),
                radius=_BOSS_INTRO_TELEGRAPH_RADIUS,
            ),
            TimeToLive(ticks=intro_ticks),
        )
        # Narrative banner — same TTL as the telegraph so they fade together.
        # Copy comes from the per-level lookup so each boss gets a story-
        # bearing line instead of a generic one (kid playtest feedback).
        banner_ticks = int(_BOSS_INTRO_BANNER_SECONDS * 60)
        world.spawn(
            BossIntroBanner(
                text=boss_intro_text(self.level_index),
                total_ticks=banner_ticks,
            ),
            TimeToLive(ticks=banner_ticks),
        )
        self._boss = BossState(
            boss=boss,
            entity=eid,
            phase_index=0,
            phase_hp_remaining=scaled_phase_hps[0],
            phase_hps=scaled_phase_hps,
            shooter=EnemyShooter(beats=boss.phases[0].fire_beats),
            path_t0=self._sim_time + boss.intro_telegraph_seconds,
            intro_remaining=boss.intro_telegraph_seconds,
            fight_started_at=self._sim_time + boss.intro_telegraph_seconds,
            cycle_phase="vuln" if boss.shield_cycle_seconds is not None else "off",
            # Kid playtest 2026-05-04: initial-spawn shield window for
            # boss_05. Seeded into shield_remaining; the existing decay
            # + cycle handler in _tick_boss_shield takes over once it
            # hits zero.
            shield_remaining=boss.shield_initial_seconds,
        )
        self._scheduler.consume_boss_event(be)
        self._boss_dispatched = True

    def _sweep_enemies_for_boss(self, world: World) -> None:
        """Despawn straggler mooks when the boss arrives, for half score.

        Fun review 2026-06-12 R4: leftover waves firing through the
        boss intro muddy the "this is THE fight" beat, and unpopped
        mooks during a 1000+ HP fight are clutter, not threat. Each
        swept enemy pays out half its score to the nearest alive player
        (mirrors the bomb-kill nearest-slot fallback) with a small
        explosion so the clear reads as a reward, not a glitch.

        Deliberately NOT routed through _on_enemy_killed — no drop
        rolls, no full score: the sweep is a consolation prize, not a
        free loot piñata at every boss door.

        Exemptions:
        * Resupply-type ships (anything carrying guaranteed_drops /
          level_scaled_drops) survive the sweep — despawning the kid's
          kit delivery mid-cruise is a trust violation.
        * The faction filter alone protects pickups (PICKUP), players +
          drones (PLAYER) and bullets; in-flight enemy bullets clear
          via their own TTL.
        """
        swept = 0
        for eid, ftag in list(world.query1(FactionTag)):
            if ftag.faction != Faction.ENEMY:
                continue
            follower = world.get(eid, FormationFollower)
            if follower is not None and (
                follower.guaranteed_drops or follower.level_scaled_drops
            ):
                continue
            pos_comp = world.get(eid, Position)
            if pos_comp is not None:
                score_val = world.get(eid, ScoreValue)
                if score_val is not None:
                    slot = self._nearest_alive_player_slot(pos_comp.pos)
                    if slot is not None:
                        self._session.scores.award(
                            slot, score_val.points // 2, multiplier=1.0
                        )
                self._spawn_explosion(world, pos_comp.pos, scale=1)
            world.despawn(eid)
            swept += 1
        if swept:
            self.app.audio.play_sfx("explosion", volume=0.5)

    def _maybe_spawn_stray_asteroids(self, world: World) -> None:
        """Level-7 stray-asteroid dodge-fest (fun review 2026-06-12).

        Fast rocks streak across the play area in random directions on a
        deterministic cadence — faster than the player's ship but
        reaction-dodgeable, and destructible for bonus points. Off unless
        the level's YAML carries an enabled ``stray_asteroids:`` block, so
        every other level pays a single attribute check and returns.

        Each rock spawns just outside a random edge with a velocity aimed
        at a point on (roughly) the opposite side, so it actually
        traverses the field rather than clipping a corner and culling
        instantly. A TTL backstops the off-screen cull (which is
        bottom-biased) so a cross-screen exit is always cleaned up.
        """
        cfg = self._level_def.stray_asteroids
        if cfg is None or not cfg.enabled:
            return
        # Lazily seed the first deadline off the live tick so the level
        # opens clean (no t=0 burst) and the cadence is deterministic.
        if self._next_stray_time < 0.0:
            self._next_stray_time = self._sim_time + tick_range(
                self._internal_tick,
                cfg.interval_min_seconds,
                cfg.interval_max_seconds,
                channel=_CH_STRAY_INTERVAL,
            )
            return
        if self._sim_time < self._next_stray_time:
            return
        # Fire a burst, then schedule the next one.
        for i in range(max(0, cfg.count_per_burst)):
            self._spawn_stray_asteroid(world, cfg, member=i)
        self._next_stray_time = self._sim_time + tick_range(
            self._internal_tick,
            cfg.interval_min_seconds,
            cfg.interval_max_seconds,
            channel=_CH_STRAY_INTERVAL,
        )

    def _spawn_stray_asteroid(self, world: World, cfg: Any, *, member: int = 0) -> Entity:
        """Spawn one stray asteroid crossing the play area. Returns the eid.

        Deterministic: every random draw is keyed on (internal_tick,
        member, channel) so replays stay bit-identical. ``cfg`` is the
        level's StrayAsteroidConfig.
        """
        t = self._internal_tick
        # Distinct sub-channels per member so a multi-rock burst doesn't
        # spawn N identical rocks on the same tick.
        seed = t * 17 + member
        # Pick a tumbling size (sprite + collision radius).
        size_i = tick_int(seed, 0, len(_STRAY_SIZES), channel=_CH_STRAY_SIZE)
        sprite_path, radius = _STRAY_SIZES[size_i]
        speed = _STRAY_BASE_SPEED * cfg.speed_multiplier
        # Choose an entry edge (0=top, 1=bottom, 2=left, 3=right) and an
        # exit point on a perpendicular/opposite span so the rock truly
        # traverses the field. Margin keeps the spawn just off-screen.
        margin = radius + 20.0
        edge = tick_int(seed, 0, 4, channel=_CH_STRAY_EDGE)
        # Source point on the entry edge.
        if edge == 0:  # top → heads downward
            src = Vec2(tick_range(seed, 0.0, PLAY_W, channel=_CH_STRAY_FROM), -margin)
            dst = Vec2(tick_range(seed, 0.0, PLAY_W, channel=_CH_STRAY_TO), PLAY_H + margin)
        elif edge == 1:  # bottom → heads upward
            src = Vec2(tick_range(seed, 0.0, PLAY_W, channel=_CH_STRAY_FROM), PLAY_H + margin)
            dst = Vec2(tick_range(seed, 0.0, PLAY_W, channel=_CH_STRAY_TO), -margin)
        elif edge == 2:  # left → heads rightward
            src = Vec2(-margin, tick_range(seed, 0.0, PLAY_H, channel=_CH_STRAY_FROM))
            dst = Vec2(PLAY_W + margin, tick_range(seed, 0.0, PLAY_H, channel=_CH_STRAY_TO))
        else:  # edge == 3, right → heads leftward
            src = Vec2(PLAY_W + margin, tick_range(seed, 0.0, PLAY_H, channel=_CH_STRAY_FROM))
            dst = Vec2(-margin, tick_range(seed, 0.0, PLAY_H, channel=_CH_STRAY_TO))
        dx, dy = dst.x - src.x, dst.y - src.y
        dist = math.hypot(dx, dy) or 1.0
        vel = Vec2(dx / dist * speed, dy / dist * speed)
        # TTL: long enough for the full diagonal traverse, plus a buffer,
        # so a cross-screen rock is always culled even though the level's
        # off-screen cull is bottom-biased. diagonal/speed*60 ⇒ ticks.
        diagonal = math.hypot(PLAY_W + 2 * margin, PLAY_H + 2 * margin)
        ttl_ticks = math.ceil(diagonal / speed * 60.0) + 60
        eid = world.spawn(
            Position(src),
            Velocity(vel),
            CircleHitbox(radius=radius),
            FactionTag(Faction.ENEMY),
            Health(hp=cfg.hp),  # < 50, no MaxHealth ⇒ no health bar
            ScoreValue(points=cfg.score),
            # Tumbling — a fixed per-rock spin angle (cosmetic; the
            # renderer honours Sprite.rotation_rad).
            Sprite(
                path=sprite_path,
                layer=6,
                rotation_rad=tick_angle(seed, channel=_CH_STRAY_SPIN),
            ),
            StrayAsteroid(),
            TimeToLive(ticks=ttl_ticks),
        )
        return eid

    def _advance_enemies(self, world: World) -> None:
        from dataclasses import replace as _dc_replace

        bundle = self.app.content
        # Regular enemies on formations
        for eid, follower in list(world.query1(FormationFollower)):
            formation = bundle.formations.get(follower.formation_name)
            if formation is None:
                continue
            path_dt = self._sim_time - follower.path_t0
            if path_dt < 0.0:
                continue
            # Speed: base 100 px/s == 1.0 multiplier; speed_along_path
            # divides by formation duration so a 6-second formation at
            # speed 90 takes 6/(90/100)=6.67s wall-clock to traverse.
            speed_mult = follower.speed / 100.0
            t_norm = (path_dt * speed_mult) / formation.duration
            if not formation.loop and t_norm >= 1.0:
                # End of pass: either transition to free-roam, despawn
                # (no return passes left, no free-roam), or restart the
                # formation from t=0 for a survivor return.
                pending = world.get(eid, PendingFreeRoam)
                if pending is not None:
                    # Hand-off to FreeRoamAI from the current position;
                    # initial wander target = current pos so the AI's
                    # first step picks a fresh target deterministically.
                    cur_pos = world.must_get(eid, Position).pos
                    world.remove(eid, FormationFollower)
                    world.remove(eid, PendingFreeRoam)
                    world.add(eid, FreeRoamAI(
                        speed=pending.speed,
                        dodge_aggression=pending.dodge_aggression,
                        zone_top=pending.zone_top,
                        zone_bottom=pending.zone_bottom,
                        target_x=cur_pos.x,
                        target_y=cur_pos.y,
                        target_age=999.0,  # forces refresh on first step
                        fire_clock=0.0,
                        fire_loop_seconds=pending.fire_loop_seconds,
                    ))
                    # Seed Velocity so the integrator picks it up.
                    world.add(eid, Velocity(Vec2(0.0, 0.0)))
                    continue
                if follower.passes_remaining <= 0:
                    world.despawn(eid)
                    continue
                # Reset path_t0 to current sim time and decrement counter.
                # Replace the FormationFollower (it's a frozen dataclass —
                # use dataclasses.replace, consistent with the rest of the
                # codebase). Also reset the EnemyShooter beat index so the
                # enemy can fire again on its return pass.
                world.replace(
                    eid,
                    _dc_replace(
                        follower,
                        path_t0=self._sim_time,
                        passes_remaining=follower.passes_remaining - 1,
                    ),
                )
                shooter_ref = world.get(eid, EnemyShooterRef)
                if shooter_ref is not None:
                    shooter_ref.shooter.reset()
                # Sample at t=0 immediately so the enemy snaps to the
                # formation start (which should be off-screen by design).
                sample = evaluate_path(formation, 0.0, mirrored=follower.mirrored)
                world.replace(eid, Position(sample.pos))
                continue
            sample = evaluate_path(formation, t_norm, mirrored=follower.mirrored)
            world.replace(eid, Position(sample.pos))

            # Fire beats — feed wall-clock-equivalent path-time
            shooter_ref = world.get(eid, EnemyShooterRef)
            if shooter_ref is not None and follower.weapon_name is not None:
                weapon_def = bundle.enemy_weapons.get(follower.weapon_name)
                if weapon_def is not None:
                    target = self._nearest_alive_player_pos(sample.pos)
                    events = shooter_ref.shooter.advance(
                        path_dt * speed_mult,
                        enemy_entity=int(eid),
                        enemy_pos=sample.pos,
                        target_pos=target,
                        weapon_name=weapon_def.name,
                        pattern=weapon_def.pattern,
                        bullets_per_beat=weapon_def.bullets_per_beat,
                        fan_arc_deg=weapon_def.fan_arc_deg,
                    )
                    for ev in events:
                        self._spawn_enemy_bullets(world, weapon_def, ev.aim.fire_pos, target)

        # Boss
        if self._boss is not None:
            self._advance_boss(world)

    def _advance_free_roam(self, world: World) -> None:
        """Drift + dodge + fire tick for FreeRoamAI enemies.

        Each free-roam enemy heads toward a wander target inside its
        configured Y zone, refreshing the target on reach or after a
        timeout. Player bullets within 96px nudge it perpendicular to
        the bullet's path. Fire beats are looped via a per-enemy
        fire_clock that resets every fire_loop_seconds.
        """
        # Snapshot incoming player bullets once per tick.
        bullets: list[tuple[Vec2, Vec2]] = []
        for _bid, bftag in world.query1(FactionTag):
            if bftag.faction != Faction.PLAYER_BULLET:
                continue
            bp = world.get(_bid, Position)
            bv = world.get(_bid, Velocity)
            if bp is not None and bv is not None:
                bullets.append((bp.pos, bv.vel))

        bundle = self.app.content
        for eid, ai in list(world.query1(FreeRoamAI)):
            pos = world.must_get(eid, Position).pos
            vel_comp = world.get(eid, Velocity)
            cur_vel = vel_comp.vel if vel_comp is not None else Vec2(0.0, 0.0)
            cfg = FreeRoamConfig(
                speed=ai.speed,
                dodge_aggression=ai.dodge_aggression,
                zone_top=ai.zone_top,
                zone_bottom=ai.zone_bottom,
            )
            new_vel, new_target, new_age = free_roam_step(
                pos=pos,
                velocity=cur_vel,
                cfg=cfg,
                target=Vec2(ai.target_x, ai.target_y),
                target_age=ai.target_age,
                incoming_bullets=bullets,
                sim_time=self._sim_time,
                entity_id=int(eid),
                play_w=PLAY_W,
                dt=TICK_DT,
            )
            ai.target_x = new_target.x
            ai.target_y = new_target.y
            ai.target_age = new_age
            world.replace(eid, Velocity(new_vel))

            # Fire-clock: monotonically advance, reset on loop boundary.
            shooter_ref = world.get(eid, EnemyShooterRef)
            if shooter_ref is None or ai.fire_loop_seconds <= 0.0:
                continue
            ai.fire_clock += TICK_DT
            if ai.fire_clock >= ai.fire_loop_seconds:
                ai.fire_clock -= ai.fire_loop_seconds
                shooter_ref.shooter.reset()
            # Resolve weapon name via sprite → enemy. Free-roam set is
            # small (1-3 enemies) so the linear scan is cheap.
            spr = world.get(eid, Sprite)
            if spr is None:
                continue
            weapon_def: EnemyWeaponDef | None = None
            for edef in bundle.enemies.values():
                if edef.sprite == spr.path and edef.weapon:
                    weapon_def = bundle.enemy_weapons.get(edef.weapon)
                    break
            if weapon_def is None:
                continue
            target_pos = self._nearest_alive_player_pos(pos)
            events = shooter_ref.shooter.advance(
                ai.fire_clock,
                enemy_entity=int(eid),
                enemy_pos=pos,
                target_pos=target_pos,
                weapon_name=weapon_def.name,
                pattern=weapon_def.pattern,
                bullets_per_beat=weapon_def.bullets_per_beat,
                fan_arc_deg=weapon_def.fan_arc_deg,
            )
            for ev in events:
                self._spawn_enemy_bullets(world, weapon_def, ev.aim.fire_pos, target_pos)

    def _advance_boss(self, world: World) -> None:
        boss_state = self._boss
        if boss_state is None:
            return
        if not world.is_alive(boss_state.entity):
            self._boss = None
            self._level_completed = True
            return
        bundle = self.app.content
        if boss_state.intro_remaining > 0.0:
            boss_state.intro_remaining -= TICK_DT
            return
        phase_def = boss_state.boss.phases[boss_state.phase_index]
        formation = bundle.formations.get(phase_def.formation)
        if formation is None:
            return
        path_dt = self._sim_time - boss_state.path_t0
        if path_dt < 0.0:
            return
        # Boss formations are always loop=true. Use modulo wrap so the
        # path naturally cycles without snapping path_t0 — kid playtest
        # 2026-05-02 #14 reported "boss teleports left" because the old
        # path_t0 reset on shooter exhaust collapsed t_norm back to 0
        # mid-flight.
        t_norm = (path_dt % formation.duration) / formation.duration
        sample = evaluate_path(formation, t_norm)
        world.replace(boss_state.entity, Position(sample.pos))

        # Boss fire — beats loop with the formation. We do NOT reset
        # path_t0 on shooter exhaust any more; we reset the shooter at
        # the formation cycle boundary (detected by the wrapped path-
        # time going backwards) and feed it the wrapped path-time.
        wrapped_t = path_dt % formation.duration
        weapon_def = bundle.enemy_weapons.get(phase_def.weapon)
        if weapon_def is not None:
            # Cycle boundary: wrapped_t went backwards → formation
            # looped, so re-arm the shooter exactly once. Resetting on
            # `remaining == 0` instead would re-fire every beat on
            # every tick between the last beat and the wrap (kid
            # playtest 2026-05-03 — boss spat carpets of projectiles).
            if wrapped_t < boss_state.last_path_t_wrap:
                boss_state.shooter.reset()
            target = self._nearest_alive_player_pos(sample.pos)
            events = boss_state.shooter.advance(
                wrapped_t,
                enemy_entity=int(boss_state.entity),
                enemy_pos=sample.pos,
                target_pos=target,
                weapon_name=weapon_def.name,
                pattern=weapon_def.pattern,
                bullets_per_beat=weapon_def.bullets_per_beat,
                fan_arc_deg=weapon_def.fan_arc_deg,
            )
            for ev in events:
                self._spawn_enemy_bullets(world, weapon_def, ev.aim.fire_pos, target)
        boss_state.last_path_t_wrap = wrapped_t

        # Homing-missile salvo (kid playtest 2026-05-03 #3 — final
        # boss). Accumulate the missile clock; on crossing the rate
        # boundary fire a salvo aimed at the nearest live player.
        # Missiles use the same Missile component + _tick_missiles
        # homing as the player's tier-N missiles; only the faction tag
        # (ENEMY_BULLET) differs.
        boss_def = boss_state.boss
        if boss_def.homing_missile_rate_seconds > 0.0 and boss_def.homing_missile_salvo > 0:
            boss_state.missile_clock += TICK_DT
            if boss_state.missile_clock >= boss_def.homing_missile_rate_seconds:
                boss_state.missile_clock = 0.0
                self._fire_boss_missile_salvo(world, sample.pos, boss_def.homing_missile_salvo)

        # Boss shield mechanics (kid playtest #15/#16). Run after the
        # fire-step so shield activation never delays the first volley.
        self._tick_boss_shield(world, boss_state)

        # Phase transition: cumulative HP model (kid playtest #13).
        # Health monotonically decreases from MaxHealth (total) to 0;
        # we trigger a phase transition whenever HP drops below the
        # cumulative HP of the REMAINING phases (i.e. the player has
        # consumed the current phase's pool).
        hp = world.must_get(boss_state.entity, Health).hp
        # Use scaled phase HPs (set at spawn from _SP_BOSS_HP_SCALE) so
        # phase-transition thresholds stay aligned with the scaled total
        # Health pool. Falls back to raw boss.phases for any BossState
        # constructed outside _maybe_spawn_boss (unit tests with the
        # default empty phase_hps tuple).
        phase_hps = (
            boss_state.phase_hps
            if boss_state.phase_hps
            else tuple(p.hp for p in boss_state.boss.phases)
        )
        threshold = sum(phase_hps[boss_state.phase_index + 1 :])
        boss_state.phase_hp_remaining = max(0, hp - threshold)
        if hp <= threshold and boss_state.phase_index + 1 < len(boss_state.boss.phases):
            self._transition_boss_phase(world, boss_state)
        elif hp <= 0:
            self._kill_boss(world, boss_state)

    def _closest_alive_player_entity(self, world: World, from_pos: Vec2) -> Entity | None:
        """Return the entity id of the closest live player (or None if both
        slots are dead). Used by enemy homing missiles to pick a target."""
        best: tuple[float, Entity] | None = None
        for slot in (P1, P2):
            eid = self._player_entities.get(slot)
            if eid is None or not world.is_alive(eid):
                continue
            ppos = world.get(eid, Position)
            if ppos is None:
                continue
            d2 = (ppos.pos.x - from_pos.x) ** 2 + (ppos.pos.y - from_pos.y) ** 2
            if best is None or d2 < best[0]:
                best = (d2, eid)
        return best[1] if best else None

    def _fire_boss_missile_salvo(self, world: World, origin: Vec2, count: int) -> None:
        """Spawn `count` heat-seeking missiles tagged ENEMY_BULLET that
        latch onto the nearest live player. Mirrors `_fire_missile`
        (player path) but with reversed faction + a fan of x-offsets so
        the salvo doesn't all stack at one point."""
        target = self._closest_alive_player_entity(world, origin)
        # Even spread across the salvo: -28, -10, 10, 28 etc.
        offsets: list[float] = []
        if count == 1:
            offsets = [0.0]
        else:
            step = 36.0
            half = (count - 1) * step / 2.0
            offsets = [(-half + i * step) for i in range(count)]
        for dx in offsets:
            spawn_pos = Vec2(origin.x + dx, origin.y + 20.0)
            if target is not None:
                tpos = world.get(target, Position)
                if tpos is not None:
                    tdx = tpos.pos.x - spawn_pos.x
                    tdy = tpos.pos.y - spawn_pos.y
                    heading = math.atan2(tdx, -tdy)
                else:
                    heading = math.pi  # fallback: straight down
            else:
                heading = math.pi
            vx = math.sin(heading) * MISSILE_INITIAL_SPEED
            vy = -math.cos(heading) * MISSILE_INITIAL_SPEED
            world.spawn(
                Position(spawn_pos),
                Velocity(Vec2(vx, vy)),
                CircleHitbox(radius=MISSILE_HITBOX_RADIUS),
                FactionTag(Faction.ENEMY_BULLET),
                Sprite(path=MISSILE_SPRITE, layer=8, scale=1.0),
                Damage(amount=MISSILE_DAMAGE),
                TimeToLive(ticks=int(MISSILE_LIFETIME * 60)),
                Missile(
                    target=target,
                    speed=MISSILE_INITIAL_SPEED,
                    heading=heading,
                ),
            )
        self.app.audio.play_sfx("missile", volume=0.42)

    def _tick_boss_shield(self, world: World, boss_state: BossState) -> None:
        """Advance the boss shield timers + cycle. Adds/removes a
        ShieldHalo on the boss entity to match the active state.

        Two modes:
        * `shield_remaining > 0`: a one-shot or in-progress shield
          window. Decay by dt; on expiry, transition the cycle to
          "vuln" (if cycling) or "off".
        * `cycle_phase == "vuln" / "shield"` with a configured
          `shield_cycle_seconds`: accumulate in `cycle_t`; when the
          phase duration elapses, flip and (for "shield") seed
          `shield_remaining` with the shielded duration.
        """
        boss_def = boss_state.boss
        # Accumulate shielded time BEFORE decay so we don't miss the
        # final tick of a shield window (and don't double-count once
        # decay has driven shield_remaining to 0).
        if boss_state.shield_remaining > 0.0:
            boss_state.shielded_seconds_accumulated += TICK_DT
        # Shield timer decay first.
        if boss_state.shield_remaining > 0.0:
            boss_state.shield_remaining = max(0.0, boss_state.shield_remaining - TICK_DT)
            if boss_state.shield_remaining == 0.0 and boss_def.shield_cycle_seconds is not None:
                # End of a cycled shield window → start vulnerable phase.
                boss_state.cycle_phase = "vuln"
                boss_state.cycle_t = 0.0

        # Cycle accumulator (only when configured + not currently in a
        # one-shot shield burst).
        cycle = boss_def.shield_cycle_seconds
        if cycle is not None and boss_state.shield_remaining == 0.0:
            vuln_secs, shield_secs = cycle
            boss_state.cycle_t += TICK_DT
            if boss_state.cycle_phase == "vuln" and boss_state.cycle_t >= vuln_secs:
                boss_state.cycle_phase = "shield"
                boss_state.cycle_t = 0.0
                boss_state.shield_remaining = shield_secs

        # Sync the boss's ShieldHalo to its actual state. Re-using the
        # player's halo component keeps the renderer code paths shared.
        eid = boss_state.entity
        active = boss_state.shield_remaining > 0.0
        has_halo = world.has(eid, ShieldHalo)
        if active and not has_halo:
            world.add(eid, ShieldHalo(base_radius=boss_def.hitbox_radius + 24.0))
        elif not active and has_halo:
            world.remove(eid, ShieldHalo)

    def _boss_pity_multiplier(self) -> float:
        """Fairness floor for marathon boss fights (fun review
        2026-06-12 R4). Below 60 seconds of UNSHIELDED fight time the
        fight is untouched (1.0). Past that, player-bullet damage ramps
        +2%/s up to a 3.0 cap, so an under-kitted kid grinds the fight
        down in bounded time instead of stalling out. Shielded seconds
        don't count — the same exclusion the time bonus uses — so
        shield-heavy bosses (boss_04/05) don't hit the ramp early.
        """
        boss_state = self._boss
        if boss_state is None:
            return 1.0
        elapsed = max(
            0.0,
            (self._sim_time - boss_state.fight_started_at)
            - boss_state.shielded_seconds_accumulated,
        )
        if elapsed <= 60.0:
            return 1.0
        return min(3.0, 1.0 + 0.02 * (elapsed - 60.0))

    def _kill_boss(self, world: World, boss_state: BossState) -> None:
        """Boss-death routing — explosion fanfare, time-based bonus,
        sfx, level-completed flag. Kid playtest 2026-05-02 #12.
        """
        boss_pos = world.get(boss_state.entity, Position)
        if boss_pos is not None:
            # Big explosion (scale=4 is the largest rung, much chunkier
            # than the regular kill VFX). This is the "boss is dead"
            # punctuation the kid wanted.
            self._spawn_explosion(world, boss_pos.pos, scale=4)
            # Time bonus — full boss score reduces linearly to zero
            # over a 60-second target. Quick kill = full bonus,
            # 60s+ fight = no bonus.
            elapsed = max(
                0.0,
                (self._sim_time - boss_state.fight_started_at)
                - boss_state.shielded_seconds_accumulated,
            )
            bonus_factor = max(0.0, min(1.0, 1.0 - elapsed / 60.0))
            bonus = max(0, int(boss_state.boss.score * bonus_factor))
            if bonus > 0:
                # Award to whichever player is closer to the boss
                # corpse — falls back to P1 if neither is alive.
                slot = self._nearest_alive_player_slot(boss_pos.pos) or P1
                self._session.scores.award(slot, bonus, multiplier=1.0)
                world.spawn(
                    Position(boss_pos.pos),
                    Velocity(Vec2(0.0, -40.0)),
                    FloatingText(
                        text=f"+{bonus} TIME BONUS",
                        colour=(255, 220, 80),
                        ticks_remaining=120,
                    ),
                    TimeToLive(ticks=120),
                )
        world.despawn(boss_state.entity)
        self._session.scores.award(P1, boss_state.boss.score, multiplier=1.0)
        self._boss = None
        self._level_completed = True
        self.app.audio.play_sfx("explosion")

    def _transition_boss_phase(self, world: World, boss_state: BossState) -> None:
        # Warp visual: explosions at both the old AND new positions so
        # the boss reads as "warping" rather than teleporting silently.
        # Kid playtest 2026-05-02 #14 wants L4 + L5 bosses to "have a
        # quick animation when they teleport" — we beef up the visual
        # for those levels (bigger telegraph + brief alpha fade on the
        # boss sprite). Earlier-level bosses use the original idiom.
        bundle = self.app.content
        old_pos = world.must_get(boss_state.entity, Position).pos
        new_phase: BossPhaseDef = boss_state.boss.phases[boss_state.phase_index + 1]
        new_formation = bundle.formations.get(new_phase.formation)
        is_late_boss = self.level_index >= 4
        if new_formation is not None:
            new_pos = evaluate_path(new_formation, 0.0).pos
            warp_scale = 3 if is_late_boss else 2
            self._spawn_explosion(world, old_pos, scale=warp_scale)
            self._spawn_explosion(world, new_pos, scale=warp_scale)
            telegraph_radius = 96.0 if is_late_boss else 72.0
            telegraph_ticks = 45 if is_late_boss else 30
            world.spawn(
                BossTelegraph(pos=new_pos, radius=telegraph_radius, colour=(255, 200, 60)),
                TimeToLive(ticks=telegraph_ticks),
            )
            # L4/L5 quick warp animation — alpha pulse on the boss
            # sprite for ~30 ticks so the kid sees the "blink out and
            # in" rather than a hard cut.
            if is_late_boss:
                world.add(boss_state.entity, InvulnerabilityBlink(ticks_remaining=30))
            self.app.audio.play_sfx("explosion", volume=0.6)
        boss_state.phase_index += 1
        boss_state.shooter = EnemyShooter(beats=new_phase.fire_beats)
        boss_state.path_t0 = self._sim_time
        boss_state.last_path_t_wrap = 0.0
        # Use scaled phase HP if present (single-player), else raw.
        if boss_state.phase_hps:
            boss_state.phase_hp_remaining = boss_state.phase_hps[boss_state.phase_index]
        else:
            boss_state.phase_hp_remaining = new_phase.hp
        # NOTE: Health is NOT refilled — it represents total remaining
        # HP across all phases now (kid playtest #13). The boss bar
        # decreases monotonically.

        # Open the one-shot phase-start shield window if the boss
        # configures one (kid playtest #15 — boss_03 phase 2). FIRST
        # transition only (phase_index == 1 after the increment above):
        # with 3-phase desperation bosses (fun review 2026-06-12 R4) a
        # per-transition shield would hide boss_03's 160-HP gasp behind
        # a 10s forcefield — longer than the gasp itself survives.
        if boss_state.boss.shield_on_phase_start_seconds > 0.0 and boss_state.phase_index == 1:
            boss_state.shield_remaining = boss_state.boss.shield_on_phase_start_seconds

    def _spawn_enemy_bullets(
        self,
        world: World,
        weapon: EnemyWeaponDef,
        origin: Vec2,
        target: Vec2 | None,
    ) -> None:
        # Compute per-bullet direction depending on pattern.
        directions: list[float] = []  # radians; 0 = straight down
        if weapon.pattern == "aimed":
            if target is not None:
                dx = target.x - origin.x
                dy = target.y - origin.y
                directions.append(math.atan2(dx, dy))  # angle from downward axis
            else:
                directions.append(0.0)
        elif weapon.pattern == "fan":
            # Symmetric fan straight down
            n = max(1, weapon.bullets_per_beat)
            arc = math.radians(weapon.fan_arc_deg)
            for i in range(n):
                a = 0.0 if n == 1 else -arc / 2 + arc * (i / (n - 1))
                directions.append(a)
        elif weapon.pattern == "aimed_fan":
            if target is not None:
                dx = target.x - origin.x
                dy = target.y - origin.y
                base = math.atan2(dx, dy)
            else:
                base = 0.0
            n = max(1, weapon.bullets_per_beat)
            arc = math.radians(weapon.fan_arc_deg)
            for i in range(n):
                a = base if n == 1 else base - arc / 2 + arc * (i / (n - 1))
                directions.append(a)
        else:
            return
        for angle in directions:
            vx = math.sin(angle) * weapon.speed
            vy = math.cos(angle) * weapon.speed  # +y down
            world.spawn(
                Position(origin),
                Velocity(Vec2(vx, vy)),
                CircleHitbox(radius=6.0),
                FactionTag(Faction.ENEMY_BULLET),
                Sprite(path=weapon.sprite, layer=7),
                Damage(amount=weapon.damage),
                TimeToLive(ticks=300),
            )

    # ───────── helpers: motion + collisions ─────────

    def _integrate_motion(self, world: World, dt: float) -> None:
        # Move every entity that has both Position and Velocity, except
        # those owned by a formation follower (they get exact path samples).
        for eid, pos, vel in list(world.query2(Position, Velocity)):
            if world.has(eid, FormationFollower):
                continue
            new_pos = Vec2(pos.pos.x + vel.vel.x * dt, pos.pos.y + vel.vel.y * dt)
            world.replace(eid, Position(new_pos))

    def _tick_bombs(self, world: World, dt: float) -> None:
        # Damage each enemy at most once per bomb, then decay duration.
        #
        # Damage model:
        #   * non-boss enemies (MaxHealth.hp < 50, or no MaxHealth) take
        #     the full ``bomb_def.damage`` (8) — comfortably one-shots
        #     drones and most lvl-1 enemies (max ~12 HP).
        #   * bosses (MaxHealth.hp >= 50) take 20% of their *current*
        #     remaining HP — meaningful chunk that doesn't trivialise the
        #     fight. Min 8 (the base damage) so bombs always feel useful.
        #
        # AoE: ``bomb.aoe_radius`` (~360 px on 1280×720 — half the shorter
        # axis). Fixed for the whole bomb lifetime — every enemy whose
        # centre lands inside the circle takes the hit once.
        bundle = self.app.content
        bomb_def = bundle.bombs[bundle.ships["vanguard"].bomb]
        for eid, bomb in list(world.query1(BombActive)):
            for enemy_eid, e_pos, _ftag, hlth in list(world.query3(Position, FactionTag, Health)):
                if _ftag.faction != Faction.ENEMY:
                    continue
                if int(enemy_eid) in bomb._hit:
                    continue
                if not world.is_alive(enemy_eid):
                    continue
                if not circles_overlap(bomb.pos, bomb.aoe_radius, e_pos.pos, 0.0):
                    continue
                bomb._hit.add(int(enemy_eid))
                # Shield gate (kid playtest 2026-05-02 #15/#16): bombs
                # bounce off shielded bosses too. Per-enemy spawn-shields
                # (gunship, etc.) are NOT in the shielded set here — bombs
                # remain decisive against light enemies. ``_target_is_shielded``
                # only returns True for the active boss-shield window.
                if (
                    self._boss is not None
                    and enemy_eid == self._boss.entity
                    and self._boss.shield_remaining > 0.0
                ):
                    continue
                # Boss-aware damage: 20% of current HP, floor at base.
                max_hp = world.get(enemy_eid, MaxHealth)
                if max_hp is not None and max_hp.hp >= 50:
                    boss_dmg = max(bomb_def.damage, int(hlth.hp * 0.20))
                    damage = boss_dmg
                else:
                    damage = bomb_def.damage
                new_hp = hlth.hp - damage
                world.replace(enemy_eid, Health(hp=new_hp))
                if new_hp <= 0:
                    self._on_enemy_killed(world, enemy_eid, e_pos.pos)
            # Advance animation: visual_progress goes 0 → 1 over the
            # bomb's full duration; the visible ring radius eases out
            # (sqrt) so it expands fast at first and lingers wide at
            # the end — reads as "shockwave race outwards".
            bomb.duration_remaining -= dt
            elapsed = max(0.0, bomb.duration_total - bomb.duration_remaining)
            progress = max(0.0, min(1.0, elapsed / bomb.duration_total))
            bomb.visual_progress = progress
            bomb.radius = bomb.aoe_radius * math.sqrt(progress)
            if bomb.duration_remaining <= 0.0:
                world.despawn(eid)

    def _resolve_collisions(self, world: World) -> None:
        self._grid.clear()
        # Insert every entity with Position+CircleHitbox.
        for eid, pos, hit in world.query2(Position, CircleHitbox):
            self._grid.insert(eid, pos.pos, hit.radius)

        for a, b in self._grid.pairs():
            if not world.is_alive(a) or not world.is_alive(b):
                continue
            ftag_a = world.get(a, FactionTag)
            ftag_b = world.get(b, FactionTag)
            if ftag_a is None or ftag_b is None:
                continue
            decision = should_apply_damage(ftag_a.faction, ftag_b.faction, self._session.options)
            if decision == DamageDecision.IGNORE:
                # Bullet-blocking cover (level 6 asteroid hulks — fun
                # review 2026-06-12 R5): ENEMY_BULLET × ENEMY is normally
                # a free ignore, but if the enemy is a BulletBlocker the
                # rock eats the laser. Enemy bullets are already in the
                # grid, so the extra cost lands on this pair type only.
                if frozenset((ftag_a.faction, ftag_b.faction)) == _BULLET_BLOCK_PAIR:
                    if ftag_a.faction == Faction.ENEMY_BULLET:
                        bullet_eid, enemy_eid = a, b
                    else:
                        bullet_eid, enemy_eid = b, a
                    if world.has(enemy_eid, BulletBlocker):
                        pos_a = world.must_get(a, Position).pos
                        pos_b = world.must_get(b, Position).pos
                        r_a = world.must_get(a, CircleHitbox).radius
                        r_b = world.must_get(b, CircleHitbox).radius
                        if circles_overlap(pos_a, r_a, pos_b, r_b):
                            self._absorb_bullet_on_blocker(world, bullet_eid)
                continue
            pos_a = world.must_get(a, Position).pos
            pos_b = world.must_get(b, Position).pos
            r_a = world.must_get(a, CircleHitbox).radius
            r_b = world.must_get(b, CircleHitbox).radius
            if not circles_overlap(pos_a, r_a, pos_b, r_b):
                continue

            if decision == DamageDecision.PICKUP:
                self._handle_pickup_pair(world, a, b, ftag_a, ftag_b)
                continue

            # APPLY: sort the pair by faction so we route correctly.
            if Faction.PLAYER in (ftag_a.faction, ftag_b.faction):
                self._handle_player_hit(world, a, b, ftag_a, ftag_b)
            elif ftag_a.faction == Faction.PLAYER_BULLET and ftag_b.faction == Faction.ENEMY:
                self._handle_enemy_hit(world, b, a)
            elif ftag_b.faction == Faction.PLAYER_BULLET and ftag_a.faction == Faction.ENEMY:
                self._handle_enemy_hit(world, a, b)

    def _absorb_bullet_on_blocker(self, world: World, bullet_eid: Entity) -> None:
        """Despawn an enemy bullet that struck bullet-blocking cover.

        A tiny two-frame spark (half-scale explosion head) marks the
        impact point so the kid SEES the rock eat the laser — without
        it the bullet just vanishes and cover never reads as a
        mechanic. Deliberately small: hulks sit inside dense bullet
        curtains on L6 and a full explosion per absorbed bullet would
        white out the screen (and cost Pi frames)."""
        pos = world.get(bullet_eid, Position)
        if pos is not None:
            world.spawn(
                Position(pos.pos),
                AnimatedSprite(
                    frames=("particles/explosion_00.png", "particles/explosion_01.png"),
                    frame_ticks=3,
                    loop=False,
                    layer=9,
                    scale=0.5,
                ),
            )
        world.despawn(bullet_eid)

    def _handle_player_hit(
        self,
        world: World,
        a: Entity,
        b: Entity,
        ftag_a: FactionTag,
        ftag_b: FactionTag,
    ) -> None:
        # Identify the player entity. Friendly-fire / SoSC have already
        # been gated by should_apply_damage.
        player_eid = a if ftag_a.faction == Faction.PLAYER else b
        other = b if player_eid == a else a
        owned = world.get(player_eid, PlayerOwned)
        if owned is None:
            return
        slot = owned.slot
        # Drone branch: drones share FactionTag(PLAYER) + PlayerOwned for
        # bullet routing and proximity multipliers, but they have
        # independent HP and DO NOT eject the player from a power-up
        # state on hit (per kid playtest brief). Resolve drone collisions
        # here and bail before the ship-death path runs.
        if world.has(player_eid, Drone):
            self._handle_drone_hit(world, player_eid, other, ftag_a, ftag_b)
            return
        if not self._session.lifecycle(slot).can_be_hit:
            return
        # Shield power-up: if active, absorb the hit. The forcefield
        # eats the bullet/enemy contact so the player is invulnerable
        # for the shield's duration. We still consume the offending
        # enemy bullet (so it doesn't double-hit on the next tick) and
        # play a soft pickup chirp so the player hears the shield work.
        pstate = self._powerup_states[slot]
        if pstate.shield.active:
            if ftag_a.faction == Faction.ENEMY_BULLET or ftag_b.faction == Faction.ENEMY_BULLET:
                world.despawn(other)
            self.app.audio.play_sfx("hit", volume=0.3)
            return
        # Visual: explosion at the player's position before despawn.
        player_pos = world.must_get(player_eid, Position).pos
        self._spawn_explosion(world, player_pos, scale=2)
        # Despawn the player entity; lifecycle goes DYING.
        world.despawn(player_eid)
        self._player_entities[slot] = None
        self._session.hit(slot)
        # Past the drone branch, can_be_hit gate and shield-absorb branch
        # above, so only true ship deaths feed the resupply catch-up.
        self._deaths_this_level += 1
        self.app.audio.play_sfx("hit")
        # Drop weapon level, refresh bombs (but lives is owned by lifecycle).
        ship = self.app.content.ships["vanguard" if slot == P1 else "vanguard_red"]
        self._powerup_states[slot] = self._powerup_states[slot].reset_on_death(
            starting_bombs=ship.starting_bombs
        )
        # Despawn the bullet that hit the player (so it doesn't double-hit).
        if ftag_a.faction == Faction.ENEMY_BULLET or ftag_b.faction == Faction.ENEMY_BULLET:
            world.despawn(other)

    def _handle_drone_hit(
        self,
        world: World,
        drone_eid: Entity,
        other: Entity,
        ftag_a: FactionTag,
        ftag_b: FactionTag,
    ) -> None:
        """Apply damage to a drone hit by an enemy bullet / enemy. Drone
        HP=0 ⇒ despawn; the player keeps playing. Player ship vs drone
        is a no-op (no friendly fire between own units)."""
        if not world.is_alive(drone_eid) or not world.is_alive(other):
            return
        # Player ship vs own drone: leave both alone. should_apply_damage
        # might surface this pair if SoSC is on; we just ignore it here.
        if world.has(other, PlayerShip):
            return
        # Determine damage. Enemy bullets carry Damage; raw enemy ships
        # default to 1 (the drone gets 3 HP, so even a body-slam enemy
        # collision should kill the drone in 3 ticks at most).
        dmg_comp = world.get(other, Damage)
        damage = dmg_comp.amount if dmg_comp is not None else 1
        hp_comp = world.get(drone_eid, Health)
        if hp_comp is None:
            return
        new_hp = hp_comp.hp - damage
        # Despawn the offending bullet so it doesn't double-hit on the
        # next tick.
        if (
            ftag_a.faction == Faction.ENEMY_BULLET
            or ftag_b.faction == Faction.ENEMY_BULLET
        ):
            world.despawn(other)
        if new_hp <= 0:
            pos_comp = world.get(drone_eid, Position)
            if pos_comp is not None:
                self._spawn_explosion(world, pos_comp.pos, scale=1)
            world.despawn(drone_eid)
            self.app.audio.play_sfx("hit", volume=0.4)
        else:
            world.replace(drone_eid, Health(hp=new_hp))
            world.replace(drone_eid, HitFlash(ticks_remaining=4))

    def _handle_enemy_hit(self, world: World, enemy_eid: Entity, bullet_eid: Entity) -> None:
        damage = world.get(bullet_eid, Damage)
        hlth = world.get(enemy_eid, Health)
        if damage is None or hlth is None:
            return
        # Off-screen damage gate (kid playtest 2026-05-03 #6 — "Enemies
        # should not take fire when they are off screen. Sometimes I
        # just see power ups 'drop' as the enemies died before being
        # hit.") Despawn the bullet but don't apply damage; the enemy
        # is invisible so any drop would look like spontaneous loot.
        enemy_pos = world.get(enemy_eid, Position)
        if enemy_pos is not None:
            x, y = enemy_pos.pos.x, enemy_pos.pos.y
            if x < 0.0 or x > PLAY_W or y < 0.0 or y > PLAY_H:
                world.despawn(bullet_eid)
                return
        # Shield-on-first-hit (kid playtest 2026-05-03 #2 + #9). If the
        # enemy was configured to bubble up on first hit, this is that
        # hit — pop the shield, attach the halo + Consumed marker, and
        # absorb the bullet. Subsequent hits during the window are
        # caught by the shielded-target gate below.
        cfg = world.get(enemy_eid, ShieldOnHitConfig)
        if cfg is not None and not world.has(enemy_eid, ShieldOnHitConsumed):
            hit_radius = world.get(enemy_eid, CircleHitbox)
            base_radius = hit_radius.radius + 14.0 if hit_radius is not None else 28.0
            halo_radius = max(28.0, base_radius)
            world.add(enemy_eid, EnemyShield(seconds_remaining=cfg.seconds))
            world.add(enemy_eid, ShieldHalo(base_radius=halo_radius))
            world.add(enemy_eid, ShieldOnHitConsumed())
            world.remove(enemy_eid, ShieldOnHitConfig)
            world.despawn(bullet_eid)
            return
        # Boss + enemy shield gates (kid playtest 2026-05-02 #3/#15/#16):
        # if the target is shielded, the bullet is absorbed — despawn it
        # but don't apply damage. The kid sees the shielded ring around
        # the enemy and learns "wait for the shield to drop."
        if self._target_is_shielded(world, enemy_eid):
            world.despawn(bullet_eid)
            return
        amount = damage.amount
        # Fairness floor — player bullets only, boss entity only (the
        # ramp must never touch enemy-on-player or bomb damage paths).
        if self._boss is not None and enemy_eid == self._boss.entity:
            amount = max(1, int(amount * self._boss_pity_multiplier() + 0.5))
        new_hp = hlth.hp - amount
        world.replace(enemy_eid, Health(hp=new_hp))
        # Capture credit before despawning the bullet.
        owned = world.get(bullet_eid, PlayerOwned)
        killer_slot = owned.slot if owned is not None else None
        world.despawn(bullet_eid)
        if new_hp > 0:
            # Multi-HP enemy that survived — flash so the player sees the hit register.
            world.replace(enemy_eid, HitFlash(ticks_remaining=4))
        else:
            pos = world.must_get(enemy_eid, Position).pos
            # Stray asteroids (Level 7) are spawned directly — no
            # FormationFollower / EnemyDef — so the generic kill path's
            # drop-roll machinery has nothing to look up. Special-case
            # the marker: award the bonus score + an explosion, then
            # despawn it WITHOUT touching _on_enemy_killed's def-lookup.
            if world.has(enemy_eid, StrayAsteroid):
                self._kill_stray_asteroid(world, enemy_eid, pos, killer_slot=killer_slot)
            else:
                self._on_enemy_killed(world, enemy_eid, pos, killer_slot=killer_slot)

    def _kill_stray_asteroid(
        self,
        world: World,
        enemy_eid: Entity,
        pos: Vec2,
        *,
        killer_slot: PlayerSlot | None = None,
    ) -> None:
        """Destroy a stray asteroid for bonus points (Level 7).

        Mirrors how the generic path awards score + explosion, but
        deliberately skips the FormationFollower/EnemyDef drop-roll +
        guaranteed/level-scaled drop machinery a directly-spawned rock
        has no data for. No drops — the reward IS the score (fun review
        2026-06-12: "destructible for bonus points, still primarily a
        dodge")."""
        if not world.is_alive(enemy_eid):
            return
        nearest_slot = (
            killer_slot if killer_slot is not None else self._nearest_alive_player_slot(pos)
        )
        score_val = world.get(enemy_eid, ScoreValue)
        if score_val is not None and nearest_slot is not None:
            mult = proximity_multiplier(
                p1_pos=self._player_pos_if_alive(P1),
                p2_pos=self._player_pos_if_alive(P2),
                config=self.app.content.coop,
                play_w=PLAY_W,
                play_h=PLAY_H,
            )
            self._session.scores.award(nearest_slot, score_val.points, multiplier=mult)
        self._spawn_explosion(world, pos, scale=1)
        world.despawn(enemy_eid)
        self.app.audio.play_sfx("explosion", volume=0.5)

    def _target_is_shielded(self, world: World, enemy_eid: Entity) -> bool:
        """True if the entity has an active shield (per-enemy or boss).

        Per-enemy: an `EnemyShield` component with seconds_remaining > 0.
        Boss: the entity belongs to the active BossState and the BossState's
        shield window is open.
        """
        es = world.get(enemy_eid, EnemyShield)
        if es is not None and es.active:
            return True
        if self._boss is not None and enemy_eid == self._boss.entity:
            return self._boss.shield_remaining > 0.0
        return False

    def _tick_enemy_shields(self, world: World, dt: float) -> None:
        """Decay each EnemyShield. Remove the ShieldHalo + component on
        expiry so the visual + collision-gate snap off together."""
        for eid, shield in list(world.query1(EnemyShield)):
            new_remaining = shield.seconds_remaining - dt
            if new_remaining <= 0.0:
                world.remove(eid, EnemyShield)
                if world.has(eid, ShieldHalo):
                    world.remove(eid, ShieldHalo)
            else:
                world.replace(eid, EnemyShield(seconds_remaining=new_remaining))

    def _on_enemy_killed(
        self,
        world: World,
        enemy_eid: Entity,
        pos: Vec2,
        *,
        killer_slot: PlayerSlot | None = None,
    ) -> None:
        if not world.is_alive(enemy_eid):
            return
        # Boss completion is owned by _advance_boss; skip the generic path
        # so we don't double-award + double-process.
        if self._boss is not None and enemy_eid == self._boss.entity:
            return
        # Score: route credit to the player whose bullet landed the kill.
        # Fallback to nearest player for bomb/collision kills (no bullet).
        nearest_slot = (
            killer_slot if killer_slot is not None else self._nearest_alive_player_slot(pos)
        )
        score_val = world.get(enemy_eid, ScoreValue)
        follower = world.get(enemy_eid, FormationFollower)
        if score_val is not None and nearest_slot is not None:
            mult = proximity_multiplier(
                p1_pos=self._player_pos_if_alive(P1),
                p2_pos=self._player_pos_if_alive(P2),
                config=self.app.content.coop,
                play_w=PLAY_W,
                play_h=PLAY_H,
            )
            self._session.scores.award(nearest_slot, score_val.points, multiplier=mult)
        # Roll drop using the enemy's data
        if follower is not None and follower.drop_pool:
            bundle = self.app.content
            # Build an EnemyDef-like slim view from the follower.
            from ssdq.core.content.schema import EnemyDef as _EnemyDef

            slim = _EnemyDef(
                name="",
                sprite="",
                hitbox_radius=0.0,
                hp=0,
                speed_along_path=0.0,
                weapon=None,
                fire_beats=(),
                score=follower.score,
                drop_chance=follower.drop_chance,
                drop_pool=follower.drop_pool,
            )
            pickup_name = roll_drop(slim, tick=self._internal_tick, channel=int(enemy_eid))
            if pickup_name is not None and pickup_name in bundle.pickups:
                self._spawn_pickup(world, pickup_name, pos)
        # Guaranteed drops on top of the random roll (kid playtest
        # 2026-05-03 #1 + #4 — supply ship gives 1 missile + 1 random).
        # Spawned with a small horizontal offset per pickup so they
        # don't stack into a single visible halo.
        if follower is not None and follower.guaranteed_drops:
            offset_step = 24.0
            for i, name in enumerate(follower.guaranteed_drops):
                if name not in self.app.content.pickups:
                    continue
                offset = (i + 1) * offset_step
                self._spawn_pickup(world, name, Vec2(pos.x + offset, pos.y))
        # Level-scaled drops (kid playtest 2026-04-28 #5 — resupply
        # "limped through compensation"): each entry spawns
        # min(level_index, 2) times, fanned symmetrically left/right so
        # the cluster stays visually tight. Fun review 2026-06-12 R2:
        # the old uncapped level_index multiplier guaranteed max kit by
        # L4-5 (20 instrumented runs), so the cap is 2 and struggling
        # players get a death catch-up bonus below instead.
        if follower is not None and follower.level_scaled_drops and self.level_index >= 1:
            scale_offset_step = 24.0
            spawn_index = 0
            for name in follower.level_scaled_drops:
                if name not in self.app.content.pickups:
                    continue
                for _ in range(min(self.level_index, 2)):
                    pair = spawn_index // 2 + 1
                    direction = -1 if spawn_index % 2 == 0 else 1
                    dx = direction * pair * scale_offset_step
                    dy = -scale_offset_step if spawn_index >= 8 else 0.0
                    self._spawn_pickup(world, name, Vec2(pos.x + dx, pos.y + dy))
                    spawn_index += 1
            # Death catch-up (replaces the old uncapped scaling as the
            # "boost when you're behind" mechanism): +1 weapon-tier
            # pickup per ship death this level, capped at 3, continuing
            # the same fan layout so the cluster reads as one drop.
            if "pickup_powerup" in self.app.content.pickups:
                for _ in range(min(self._deaths_this_level, 3)):
                    pair = spawn_index // 2 + 1
                    direction = -1 if spawn_index % 2 == 0 else 1
                    dx = direction * pair * scale_offset_step
                    dy = -scale_offset_step if spawn_index >= 8 else 0.0
                    self._spawn_pickup(
                        world, "pickup_powerup", Vec2(pos.x + dx, pos.y + dy)
                    )
                    spawn_index += 1
        # Visual feedback: explosion at the enemy's position.
        scale = 2 if follower is not None and follower.score >= 600 else 1
        self._spawn_explosion(world, pos, scale=scale)
        world.despawn(enemy_eid)
        self.app.audio.play_sfx("explosion", volume=0.5)

    def _spawn_pickup(self, world: World, name: str, pos: Vec2) -> None:
        bundle = self.app.content
        pdef: PickupDef = bundle.pickups[name]
        # Halo colour by effect — kid playtest 2026-04-27: pickups were
        # invisible without a glow + scale-up.
        from ssdq.core.content.schema import PickupEffect

        halo_colour = {
            PickupEffect.WEAPON_UPGRADE: (255, 220, 80),  # gold
            PickupEffect.SPEED_UP: (80, 220, 255),  # cyan
            PickupEffect.EXTRA_BOMB: (255, 120, 60),  # orange
            PickupEffect.EXTRA_LIFE: (255, 100, 160),  # pink
            PickupEffect.SHIELD: (140, 255, 240),  # bright teal/cyan
            # Task #9 — new pickup pool.
            PickupEffect.SHIP_SPEED: (120, 240, 200),  # mint
            PickupEffect.WEAPON_SPEED: (255, 200, 80),  # warm gold
            PickupEffect.DRONE: (180, 200, 255),  # pale blue
            PickupEffect.MISSILE: (255, 180, 100),  # rust orange
        }.get(pdef.effect, (200, 200, 200))
        world.spawn(
            Position(pos),
            Velocity(Vec2(0.0, pdef.fall_speed)),
            CircleHitbox(radius=pdef.hitbox_radius * 1.6),  # bigger hit area too
            FactionTag(Faction.PICKUP),
            # Scale 1.33 — was 2.0; kid playtest #3 said "third smaller".
            Sprite(path=pdef.sprite, layer=5, scale=1.33),
            PickupHalo(radius=28.0, colour=halo_colour),
            PickupTag(pickup_name=name),
            TimeToLive(ticks=600),  # 10 seconds at 60 Hz
        )

    def _handle_pickup_pair(
        self,
        world: World,
        a: Entity,
        b: Entity,
        ftag_a: FactionTag,
        ftag_b: FactionTag,
    ) -> None:
        pickup_eid = a if ftag_a.faction == Faction.PICKUP else b
        player_eid = b if pickup_eid == a else a
        if not world.is_alive(pickup_eid) or not world.is_alive(player_eid):
            return
        owned = world.get(player_eid, PlayerOwned)
        if owned is None:
            return
        tag = world.get(pickup_eid, PickupTag)
        if tag is None:
            return
        bundle = self.app.content
        pdef = bundle.pickups.get(tag.pickup_name)
        if pdef is None:
            return
        slot = owned.slot
        # Resolve weapon-tree max level.
        cur = self._powerup_states[slot]
        tree = bundle.weapon_trees.get(cur.weapon.tree, ())
        max_lvl = max(0, len(tree) - 1)
        result = apply_pickup(cur, pdef, weapon_tree_max_level=max_lvl)
        self._powerup_states[slot] = result.new_state
        # Lift extra-life into the lifecycle layer.
        if result.extra_life:
            self._session.grant_extra_life(slot)
        # Task #9 — equippable / agent-owned inventory increments.
        # The level scene only mutates the AppState counter; the
        # downstream agent (DRONE / EQUIPPABLE) drains it.
        if result.drone_pickup:
            self.app.drones_pending[slot] = self.app.drones_pending.get(slot, 0) + 1
        if result.shield_charge_added:
            # Auto-shield (10s forcefield) is preserved by `apply_pickup`
            # so things still work pre-EQUIPPABLE merge — we ALSO bump
            # the inventory counter that the EQUIPPABLE agent will read.
            self.app.shield_charges[slot] = self.app.shield_charges.get(slot, 0) + 1
        # Floating-text feedback so the player knows what they got.
        # Pass the resulting state in so the weapon/missile labels can
        # show the actual NEW tier (kid playtest 2026-05-02 #19 —
        # generic "WEAPON UP!" left the kid wondering "up to what?").
        pickup_pos = world.must_get(pickup_eid, Position).pos
        text, colour = self._floating_text_for_result(result)
        if text:
            world.spawn(
                Position(pickup_pos),
                Velocity(Vec2(0.0, -30.0)),
                FloatingText(text=text, colour=colour, ticks_remaining=42),
                TimeToLive(ticks=42),
            )
        world.despawn(pickup_eid)
        if result.shield_up:
            # Equippable: shield pickup grants a charge to the player's
            # AppState inventory; the player triggers the forcefield by
            # pressing the shield button. We do NOT sync halos here —
            # that happens on consume in :meth:`_apply_player_input`.
            self.app.add_shield_charge(slot)
            self.app.audio.play_sfx("powerup")
        elif result.upgraded_weapon:
            self.app.audio.play_sfx("powerup")
        else:
            self.app.audio.play_sfx("pickup")

    @staticmethod
    def _floating_text_for_result(result: object) -> tuple[str, tuple[int, int, int]]:
        """Map a PickupResult to (label, colour) for the floating text.

        Weapon and missile labels now show the resulting tier explicitly
        (kid playtest 2026-05-02 #19 — "supply ship says weapon level X
        but I started at level 1"). The kid sees the new tier on every
        pickup so the HUD's `Weapon Lv N` reading is never a surprise.
        """
        # `result` is a PickupResult — duck-typed read to avoid a circular
        # import here.
        new_state = getattr(result, "new_state", None)
        if getattr(result, "upgraded_weapon", False):
            tier = (
                int(getattr(new_state.weapon, "level", 0)) + 1
                if new_state is not None
                else 0
            )
            return (f"WEAPON Lv {tier}!", (255, 220, 80))
        if getattr(result, "weapon_speed_up", False):
            return ("RAPID FIRE!", (255, 220, 120))
        if getattr(result, "speed_up", False):
            return ("SPEED!", (80, 220, 255))
        if getattr(result, "ship_speed_up", False):
            return ("+SPEED", (80, 200, 220))
        if getattr(result, "extra_bomb", False):
            return ("+1 BOMB", (255, 140, 80))
        if getattr(result, "extra_life", False):
            return ("+1 LIFE", (255, 120, 180))
        if getattr(result, "shield_up", False):
            return ("SHIELD!", (80, 220, 255))
        if getattr(result, "missile_tier_up", False):
            tier = int(getattr(new_state, "missile_level", 0)) if new_state is not None else 0
            return (f"MISSILE Lv {tier}!", (255, 200, 100))
        if getattr(result, "drone_pickup", False):
            return ("+DRONE", (180, 220, 255))
        return ("", (255, 255, 255))

    # ───────── helpers: respawn / culling / hud ─────────

    def _sync_shield_halos(self, world: World) -> None:
        """Add or remove a ShieldHalo on each player ship to match its
        powerup state. Called every tick (cheap — at most 2 entities) so
        the visual stays in lock-step with shield expiry / pickup.
        """
        bundle = self.app.content
        for slot in (P1, P2):
            eid = self._player_entities.get(slot)
            if eid is None or not world.is_alive(eid):
                continue
            ship_name = "vanguard" if slot == P1 else "vanguard_red"
            ship = bundle.ships[ship_name]
            # Halo radius scales with ship hitbox so it visibly engulfs
            # the sprite (ship sprites render at scale 0.66 ≈ 32px wide).
            base_radius = max(28.0, ship.hitbox_radius + 14.0)
            has_halo = world.has(eid, ShieldHalo)
            shield_active = self._powerup_states[slot].shield.active
            if shield_active and not has_halo:
                world.add(eid, ShieldHalo(base_radius=base_radius))
            elif not shield_active and has_halo:
                world.remove(eid, ShieldHalo)

    def _handle_respawns(self, world: World) -> None:
        cfg = self.app.content.coop
        invuln_ticks = int(cfg.respawn_invulnerability * 60)
        for slot in (P1, P2):
            lc = self._session.lifecycle(slot)
            if lc.state == LifecycleState.INVULNERABLE and self._player_entities.get(slot) is None:
                # Spawn back at original position with fresh state.
                spawn_pos = Vec2(
                    PLAY_W * (0.40 if slot == P1 else 0.60),
                    _PLAYER_SPAWN_Y,
                )
                self._spawn_player(world, slot, spawn_pos)
                # Visual feedback: tag the new ship with InvulnerabilityBlink
                # so the kid SEES the i-frames pulse and learns they have
                # ~2s to reposition.
                eid = self._player_entities[slot]
                if eid is not None:
                    world.add(eid, InvulnerabilityBlink(ticks_remaining=invuln_ticks))
                # Acknowledge the clearing-shockwave one-shot
                if lc.fired_clearing_shockwave:
                    self._session.consume_clearing_shockwaves()
                    # Wipe enemy bullets in radius
                    for ebid, pos, ftag in list(world.query2(Position, FactionTag)):
                        if ftag.faction == Faction.ENEMY_BULLET and circles_overlap(
                            spawn_pos, cfg.respawn_clearing_radius, pos.pos, 0.0
                        ):
                            world.despawn(ebid)

    def _advance_animations(self, world: World) -> None:
        """Advance AnimatedSprite frame indices, decay HitFlash + Blink.

        AnimatedSprite frames advance every `frame_ticks` ticks; on the
        last frame of a non-looping animation we despawn the entity so
        explosions auto-clean.
        """
        from dataclasses import replace as _dc_replace

        for eid, anim in list(world.query1(AnimatedSprite)):
            new_elapsed = anim.elapsed_ticks + 1
            if new_elapsed >= anim.frame_ticks:
                next_idx = anim.current_index + 1
                if next_idx >= len(anim.frames):
                    if anim.loop:
                        next_idx = 0
                    else:
                        world.despawn(eid)
                        continue
                world.replace(
                    eid,
                    _dc_replace(anim, current_index=next_idx, elapsed_ticks=0),
                )
            else:
                world.replace(eid, _dc_replace(anim, elapsed_ticks=new_elapsed))

        # Hit-flash decay
        for eid, flash in list(world.query1(HitFlash)):
            if flash.ticks_remaining <= 1:
                world.remove(eid, HitFlash)
            else:
                world.replace(eid, HitFlash(ticks_remaining=flash.ticks_remaining - 1))

        # Invuln blink: pulse the player sprite alpha while in i-frames.
        for eid, blink in list(world.query1(InvulnerabilityBlink)):
            if blink.ticks_remaining <= 1:
                # Restore full opacity on the way out
                spr = world.get(eid, Sprite)
                if spr is not None:
                    world.replace(eid, _dc_replace(spr, alpha=255))
                world.remove(eid, InvulnerabilityBlink)
                continue
            # Square wave: 6 ticks visible (alpha=120), 6 ticks dimmer (alpha=60)
            phase = (blink.ticks_remaining // 6) % 2
            new_alpha = 120 if phase == 0 else 60
            spr = world.get(eid, Sprite)
            if spr is not None:
                world.replace(eid, _dc_replace(spr, alpha=new_alpha))
            world.replace(eid, InvulnerabilityBlink(ticks_remaining=blink.ticks_remaining - 1))

    def _spawn_explosion(self, world: World, pos: Vec2, *, scale: int = 1) -> None:
        """Spawn a one-shot explosion animation at `pos`.

        scale=1 for regular enemies; 2 for large craft / boss phase
        warps; 4 for boss death (kid playtest 2026-05-02 #12 — "killing
        a boss should result in an explosion animation"). The numeric
        ``scale`` is mapped onto the ``AnimatedSprite.scale`` so the
        explosion sprite is visibly chunkier on screen.
        """
        frames = tuple(f"particles/explosion_{i:02d}.png" for i in range(4))
        # Render scale: larger explosions also linger longer on screen
        # (frame_ticks bumps with scale) so the bigger silhouette has
        # time to read at TV-viewing distance.
        sprite_scale = float(scale)
        frame_ticks = 4 if scale <= 2 else 6
        # The animation auto-despawns on its last frame via _advance_animations.
        world.spawn(
            Position(pos),
            AnimatedSprite(
                frames=frames,
                frame_ticks=frame_ticks,
                loop=False,
                layer=9,
                scale=sprite_scale,
            ),
        )

    def _cull_entities(self, world: World) -> None:
        # TTL-based despawn
        for eid, ttl in list(world.query1(TimeToLive)):
            new_ticks = ttl.ticks - 1
            if new_ticks <= 0:
                world.despawn(eid)
            else:
                world.replace(eid, TimeToLive(ticks=new_ticks))

        # Off-screen despawn for things that aren't formation followers
        # (followers manage their own lifetime via t_norm clamp).
        for eid, pos in list(world.query1(Position)):
            if world.has(eid, FormationFollower):
                continue
            if world.has(eid, PlayerShip):
                continue
            x, y = pos.pos.x, pos.pos.y
            if (
                x < -_OFF_SCREEN_MARGIN
                or x > PLAY_W + _OFF_SCREEN_MARGIN
                or y < -_OFF_SCREEN_MARGIN
                or y > PLAY_H + _OFF_SCREEN_MARGIN
            ):
                world.despawn(eid)

    def _build_hud_state(self, world: World) -> HudCoopState:
        snap = self._session.scores.snapshot()
        p1 = self._powerup_states[P1]
        p2 = self._powerup_states[P2]
        lc1 = self._session.lifecycle(P1)
        lc2 = self._session.lifecycle(P2)
        drones_p1 = self._count_drones(world, P1)
        drones_p2 = self._count_drones(world, P2)
        return HudCoopState(
            team_score=snap.team,
            p1=HudPlayerStats(
                lives=lc1.lives,
                bombs=p1.bombs,
                weapon_level=p1.weapon.level + 1,
                score=snap.p1,
                shield_charges=self.app.get_shield_charges(P1),
                missile_level=p1.missile_level,
                drones=drones_p1,
            ),
            p2=HudPlayerStats(
                lives=lc2.lives,
                bombs=p2.bombs,
                weapon_level=p2.weapon.level + 1,
                score=snap.p2,
                shield_charges=self.app.get_shield_charges(P2),
                missile_level=p2.missile_level,
                drones=drones_p2,
            ),
            single_player=self.app.single_player,
        )

    def _switch_music(self, name: str) -> None:
        if self._music_playing == name:
            return
        self.app.audio.crossfade_to(name, ms=600)
        self._music_playing = name

    def _level_music_name(self) -> str:
        """BootScene-registered track name for this level ENTRY.

        Each level owns a pool of up to three tracks (base + _b/_c
        variants — fun review 2026-06-12: one track per level was
        wearing thin). The pool is filtered through AudioBus.has_music
        because ``crossfade_to`` silently keeps the OLD track for
        unregistered names — without the filter a missing variant would
        freeze the rotation on whatever was already playing. Selection
        only READS the per-level rotation counter, so repeated calls
        are idempotent; ``enter()`` bumps the counter exactly once via
        :meth:`_advance_music_rotation`. Out-of-range indices fall back
        to level 1's pool (defensive, pre-pool behaviour preserved).
        """
        idx = clamp_level(self.level_index)
        pool = [name for name in level_music_pool(idx) if self.app.audio.has_music(name)]
        if not pool:
            # Nothing registered (headless tests / missing assets) —
            # the base name keeps the "always return something" contract;
            # crossfade_to no-ops on it just as it always did.
            pool = [level_music_pool(idx)[0]]
        counter = self.app.music_rotation.get(idx, 0)
        return pool[counter % len(pool)]

    def _advance_music_rotation(self) -> None:
        """Bump this level's pool rotation counter — once per entry.

        Kept separate from :meth:`_level_music_name` so tests (and the
        boss-music branch in ``tick``) can call the name helpers freely
        without skipping tracks.
        """
        idx = clamp_level(self.level_index)
        self.app.music_rotation[idx] = self.app.music_rotation.get(idx, 0) + 1

    def _boss_music_name(self) -> str:
        """BootScene-registered boss track name for the current level."""
        return boss_music_name(self.level_index)

    # ───────── helpers: lookups ─────────

    def _player_pos_if_alive(self, slot: PlayerSlot) -> Vec2 | None:
        if self._player_entities.get(slot) is None:
            return None
        if not self._session.lifecycle(slot).can_be_hit:
            return None
        return self._player_positions.get(slot)

    def _nearest_alive_player_pos(self, from_pos: Vec2) -> Vec2 | None:
        candidates: list[Vec2] = []
        for slot in (P1, P2):
            p = self._player_pos_if_alive(slot)
            if p is not None:
                candidates.append(p)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda p: (p.x - from_pos.x) ** 2 + (p.y - from_pos.y) ** 2,
        )

    def _nearest_alive_player_slot(self, from_pos: Vec2) -> PlayerSlot | None:
        best: tuple[float, PlayerSlot] | None = None
        for slot in (P1, P2):
            p = self._player_pos_if_alive(slot)
            if p is None:
                continue
            d = (p.x - from_pos.x) ** 2 + (p.y - from_pos.y) ** 2
            if best is None or d < best[0]:
                best = (d, slot)
        return best[1] if best else None
