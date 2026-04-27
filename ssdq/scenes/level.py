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

from ssdq.core.clock import TICK_DT
from ssdq.core.collision import SpatialGrid, circles_overlap
from ssdq.core.components import (
    AnimatedSprite,
    CircleHitbox,
    Damage,
    Faction,
    FactionTag,
    FloatingText,
    Health,
    HitFlash,
    InvulnerabilityBlink,
    MaxHealth,
    PickupHalo,
    PlayerOwned,
    Position,
    ScoreValue,
    ShieldHalo,
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
    PlayerPowerupState,
    WeaponState,
    apply_pickup,
    roll_drop,
)
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

logger = logging.getLogger(__name__)

PLAY_W = 1280.0
PLAY_H = 720.0
_PLAYER_SPAWN_Y = PLAY_H - 80.0
_OFF_SCREEN_MARGIN = 80.0  # cull entities this far past the play boundary
_BOSS_INTRO_TELEGRAPH_RADIUS = 80.0


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


@dataclass(frozen=True, slots=True)
class EnemyShooterRef:
    """Wraps the mutable EnemyShooter so the World can store it."""

    shooter: EnemyShooter


@dataclass(frozen=True, slots=True)
class PickupTag:
    """Marker so the collision layer can identify pickups quickly."""

    pickup_name: str


@dataclass
class BombActive:
    """An active bomb shockwave — drawn by renderer, damages each enemy once.

    `pos` and `radius` are duck-typed by Renderer._draw_bomb_actives.
    `_hit` tracks already-damaged enemy entity IDs to prevent the bomb
    dealing damage every tick of its duration (would otherwise be DPS).
    """

    pos: Vec2
    radius: float
    duration_remaining: float
    _hit: set[int] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class BossTelegraph:
    """Boss-intro shockwave indicator. Drawn by renderer."""

    pos: Vec2
    radius: float
    colour: tuple[int, int, int] = (255, 80, 80)


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
    settled: bool = False


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
    _grid: SpatialGrid = field(init=False)
    _sim_time: float = field(default=0.0, init=False)
    _internal_tick: int = field(default=0, init=False)
    _enter_tick: int = field(default=-1, init=False)
    _boss: BossState | None = field(default=None, init=False)
    _boss_dispatched: bool = field(default=False, init=False)
    _level_completed: bool = field(default=False, init=False)
    _level_complete_grace: float = field(default=0.0, init=False)
    _music_playing: str | None = field(default=None, init=False)
    # Track which (wave_idx, spawn_idx, member_idx) tuples have had a
    # warning telegraph spawned, so we don't double-up.
    _telegraphed: set[tuple[int, int, int]] = field(default_factory=set, init=False)

    def __init__(self, app: AppState, level_index: int = 1) -> None:
        self.app = app
        self.level_index = level_index

    # ───────── lifecycle ─────────

    def enter(self, world: World) -> None:
        bundle = self.app.content
        if self.level_index not in bundle.levels:
            raise RuntimeError(f"level {self.level_index} not in content")
        level = bundle.levels[self.level_index]
        self._scheduler = WaveScheduler(level)
        self._session = CoopSession.initial(bundle.coop, self.app.options)
        # Powerup state per slot — one ship type for the slice; tree name
        # is parsed off the primary weapon name (e.g. "pulse_lvl1" → "pulse").
        ship = bundle.ships["vanguard"]
        tree = ship.primary_weapon.split("_lvl")[0]
        self._powerup_states = {
            P1: PlayerPowerupState(
                weapon=WeaponState(tree=tree, level=0),
                bombs=ship.starting_bombs,
                lives=self.app.options.starting_lives,
            ),
            P2: PlayerPowerupState(
                weapon=WeaponState(tree=tree, level=0),
                bombs=ship.starting_bombs,
                lives=self.app.options.starting_lives,
            ),
        }
        self._player_entities = {P1: None, P2: None}
        self._player_positions = {
            P1: Vec2(PLAY_W * 0.40, _PLAYER_SPAWN_Y),
            P2: Vec2(PLAY_W * 0.60, _PLAYER_SPAWN_Y),
        }
        self._grid = SpatialGrid(cell_size=64.0)
        self._sim_time = 0.0
        self._internal_tick = 0
        self._boss = None
        self._boss_dispatched = False
        self._level_completed = False
        self._level_complete_grace = 0.0
        self._music_playing = None
        self._telegraphed = set()

        # Spawn both player ships immediately (slice has no ship-select).
        self._spawn_player(world, P1, self._player_positions[P1])
        self._spawn_player(world, P2, self._player_positions[P2])

        # HUD snapshot resource (renderer reads via duck-typed shape).
        world.insert_resource(self._build_hud_state())

        # Music — start level track.
        self._switch_music(level.music)

    def exit(self, world: World) -> None:
        self.app.audio.stop_music()
        self.app.last_team_score = self._session.scores.snapshot().team
        self.app.last_p1_score = self._session.scores.snapshot().p1
        self.app.last_p2_score = self._session.scores.snapshot().p2
        self.app.completed_level = self._level_completed
        # Sweep ALL level entities so they don't ghost into the next scene —
        # kid playtest #6: "ghost ship of me on the screen where my last
        # position was". The world is shared across the scene stack; if
        # we don't clear, player ships, enemies, bullets, pickups, telegraphs,
        # explosions and bombs all persist into GameOverScene → TitleScene
        # → next LevelScene.
        for eid in list(world.alive_entities()):
            world.despawn(eid)

    # ───────── per-tick ─────────

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        if self._enter_tick < 0:
            self._enter_tick = int(tick)
        self._internal_tick = int(tick) - self._enter_tick
        dt = TICK_DT
        # Rebuild from tick count each step to avoid float-accumulator drift.
        self._sim_time = self._internal_tick * dt

        # 1. coop session timers (lifecycle, plus any pending continues)
        self._session.tick(dt)
        # Auto-spend continues (slice has no continue prompt UI; either
        # player going OUT immediately spends a continue if available).
        for slot in (P1, P2):
            if self._session.lifecycle(slot).state == LifecycleState.OUT:
                self._session.try_consume_continue(slot)

        # 2. Speed-boost + shield decay; sync ShieldHalo on player ships
        for slot in (P1, P2):
            self._powerup_states[slot] = self._powerup_states[slot].tick_speed_boost(dt)
            self._powerup_states[slot] = self._powerup_states[slot].tick_shield_decay(dt)
        self._sync_shield_halos(world)

        # 3. Player input → movement, fire, bomb
        self._apply_player_input(world, P1, inputs[0])
        self._apply_player_input(world, P2, inputs[1])

        # 4. Wave scheduler → spawn enemies; transition to boss if pending
        self._spawn_wave_enemies(world)
        self._maybe_spawn_boss(world)

        # 5. Advance enemies along formations + emit fire beats
        self._advance_enemies(world)

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
        world.insert_resource(self._build_hud_state())

        # 12. Boss music switch on boss spawn
        if self._boss is not None and self._music_playing != "boss_01":
            self._switch_music("boss_01")

        # 13. Win/loss transitions
        if self._level_completed:
            self._level_complete_grace += dt
            if self._level_complete_grace >= 1.5:
                from ssdq.scenes.level_complete import LevelCompleteScene

                return Replace(scene=LevelCompleteScene(self.app))
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
        speed_mult = pstate.speed_boost.multiplier if pstate.speed_boost.active else 1.0
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

        # Update PlayerShip cooldown
        ps = world.must_get(eid, PlayerShip)
        new_cooldown = max(0.0, ps.weapon_cooldown - TICK_DT)
        # Fire
        if inp.fire and new_cooldown <= 0.0 and lifecycle.state == LifecycleState.ALIVE:
            self._fire_player_weapon(world, slot, ship.primary_weapon, pstate.weapon, new_pos)
            # Set cooldown from the fire_rate of the *current weapon level*.
            tree = bundle.weapon_trees.get(pstate.weapon.tree, ())
            if tree:
                lvl = min(pstate.weapon.level, len(tree) - 1)
                weapon_def = bundle.weapons[tree[lvl]]
                new_cooldown = 1.0 / max(0.001, weapon_def.fire_rate)
            else:
                new_cooldown = 0.125
        world.replace(eid, PlayerShip(slot=slot, weapon_cooldown=new_cooldown))

        # Bomb
        if inp.bomb and pstate.bombs > 0 and lifecycle.state == LifecycleState.ALIVE:
            self._fire_bomb(world, slot, new_pos)
            self._powerup_states[slot] = pstate.with_bombs(pstate.bombs - 1)
            self.app.audio.play_sfx("bomb")

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

    def _fire_bomb(self, world: World, slot: PlayerSlot, pos: Vec2) -> None:
        bundle = self.app.content
        ship = bundle.ships["vanguard" if slot == P1 else "vanguard_red"]
        bomb_def = bundle.bombs[ship.bomb]
        world.spawn(
            BombActive(pos=pos, radius=bomb_def.radius, duration_remaining=bomb_def.duration),
        )
        if bomb_def.clears_bullets:
            # Wipe all enemy bullets immediately.
            to_kill: list[Entity] = []
            for eid, ftag in world.query1(FactionTag):
                if ftag.faction == Faction.ENEMY_BULLET:
                    to_kill.append(eid)
            for eid in to_kill:
                world.despawn(eid)

    # ───────── helpers: waves ─────────

    def _spawn_wave_enemies(self, world: World) -> None:
        # Telegraph: 0.5s before any spawn whose first sample lands inside
        # the visible playfield (vs off-screen entry), spawn a warning
        # circle at the spawn location so the player isn't surprised.
        self._spawn_telegraphs(world)
        events = self._scheduler.tick(TICK_DT)
        for ev in events:
            self._spawn_enemy(world, ev)

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
                passes_remaining=ev.return_passes,
            ),
            ScoreValue(points=enemy.score),
        )
        if enemy.weapon and enemy.fire_beats:
            world.add(eid, EnemyShooterRef(shooter=EnemyShooter(beats=enemy.fire_beats)))

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
        # Spawn boss above screen; it'll slide into formation as t advances.
        sample = evaluate_path(formation, 0.0)
        # Boss MaxHealth: total of all phase pools, so the bar shows
        # cumulative progress across the whole fight (per-phase chunks
        # rendered with dividers).
        total_max = sum(p.hp for p in boss.phases)
        eid = world.spawn(
            Position(sample.pos),
            CircleHitbox(radius=boss.hitbox_radius),
            FactionTag(Faction.ENEMY),
            Health(hp=boss.phases[0].hp),
            MaxHealth(hp=total_max),
            Sprite(path=boss.sprite, layer=7),
            ScoreValue(points=boss.score),
        )
        # Telegraph entity — auto-cull after intro_telegraph_seconds via TTL.
        world.spawn(
            BossTelegraph(
                pos=Vec2(PLAY_W / 2, 80.0),
                radius=_BOSS_INTRO_TELEGRAPH_RADIUS,
            ),
            TimeToLive(ticks=int(boss.intro_telegraph_seconds * 60)),
        )
        self._boss = BossState(
            boss=boss,
            entity=eid,
            phase_index=0,
            phase_hp_remaining=boss.phases[0].hp,
            shooter=EnemyShooter(beats=boss.phases[0].fire_beats),
            path_t0=self._sim_time + boss.intro_telegraph_seconds,
            intro_remaining=boss.intro_telegraph_seconds,
        )
        self._scheduler.consume_boss_event(be)
        self._boss_dispatched = True

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
                # End of pass: either despawn (no return passes left) or
                # restart the formation from t=0 for a survivor return.
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
        # Boss formations are always loop=true.
        t_norm = path_dt / formation.duration
        sample = evaluate_path(formation, t_norm)
        world.replace(boss_state.entity, Position(sample.pos))

        # Boss fire — beats loop with the formation
        weapon_def = bundle.enemy_weapons.get(phase_def.weapon)
        if weapon_def is not None:
            # We re-feed the shooter on each loop by shifting beats with
            # path time mod formation duration. EnemyShooter exhausts
            # its beat list once, then we reset() and shift t0.
            if boss_state.shooter.remaining == 0:
                boss_state.shooter.reset()
                boss_state.path_t0 = self._sim_time
                path_dt = 0.0
            target = self._nearest_alive_player_pos(sample.pos)
            events = boss_state.shooter.advance(
                path_dt,
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

        # Phase transition: each phase has its own HP pool. When the
        # current pool hits 0, either advance to the next phase (reset HP
        # to that phase's pool) or — if no phases remain — kill the boss.
        hp = world.must_get(boss_state.entity, Health).hp
        boss_state.phase_hp_remaining = hp
        if hp <= 0:
            if boss_state.phase_index + 1 < len(boss_state.boss.phases):
                self._transition_boss_phase(world, boss_state)
            else:
                world.despawn(boss_state.entity)
                self._session.scores.award(P1, boss_state.boss.score, multiplier=1.0)
                self._boss = None
                self._level_completed = True
                self.app.audio.play_sfx("explosion")

    def _transition_boss_phase(self, world: World, boss_state: BossState) -> None:
        # Visual: spawn explosions at BOTH the old and new positions so the
        # boss reads as "warping" rather than teleporting silently
        # (kid playtest #4: "boss disappear and reappear at end of screen").
        bundle = self.app.content
        old_pos = world.must_get(boss_state.entity, Position).pos
        new_phase: BossPhaseDef = boss_state.boss.phases[boss_state.phase_index + 1]
        new_formation = bundle.formations.get(new_phase.formation)
        if new_formation is not None:
            new_pos = evaluate_path(new_formation, 0.0).pos
            self._spawn_explosion(world, old_pos, scale=2)
            self._spawn_explosion(world, new_pos, scale=2)
            # Telegraph at the new position so the player sees where to look.
            world.spawn(
                BossTelegraph(pos=new_pos, radius=72.0, colour=(255, 200, 60)),
                TimeToLive(ticks=30),
            )
            self.app.audio.play_sfx("explosion", volume=0.6)
        boss_state.phase_index += 1
        boss_state.shooter = EnemyShooter(beats=new_phase.fire_beats)
        boss_state.path_t0 = self._sim_time
        boss_state.phase_hp_remaining = new_phase.hp
        # Refill the boss's Health pool so phase 2 has its full allocation.
        world.replace(boss_state.entity, Health(hp=new_phase.hp))

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
                if not circles_overlap(bomb.pos, bomb.radius, e_pos.pos, 0.0):
                    continue
                bomb._hit.add(int(enemy_eid))
                new_hp = hlth.hp - bomb_def.damage
                world.replace(enemy_eid, Health(hp=new_hp))
                if new_hp <= 0:
                    self._on_enemy_killed(world, enemy_eid, e_pos.pos)
            bomb.duration_remaining -= dt
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
        self.app.audio.play_sfx("hit")
        # Drop weapon level, refresh bombs (but lives is owned by lifecycle).
        ship = self.app.content.ships["vanguard" if slot == P1 else "vanguard_red"]
        self._powerup_states[slot] = self._powerup_states[slot].reset_on_death(
            starting_bombs=ship.starting_bombs
        )
        # Despawn the bullet that hit the player (so it doesn't double-hit).
        if ftag_a.faction == Faction.ENEMY_BULLET or ftag_b.faction == Faction.ENEMY_BULLET:
            world.despawn(other)

    def _handle_enemy_hit(self, world: World, enemy_eid: Entity, bullet_eid: Entity) -> None:
        damage = world.get(bullet_eid, Damage)
        hlth = world.get(enemy_eid, Health)
        if damage is None or hlth is None:
            return
        new_hp = hlth.hp - damage.amount
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
            self._on_enemy_killed(world, enemy_eid, pos, killer_slot=killer_slot)

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
        # Floating-text feedback so the player knows what they got.
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
            # Attach a ShieldHalo to the player immediately so the
            # forcefield engulfs the ship on the same tick the pickup
            # is collected (otherwise it'd flicker on next tick).
            self._sync_shield_halos(world)
            self.app.audio.play_sfx("powerup")
        elif result.upgraded_weapon:
            self.app.audio.play_sfx("powerup")
        else:
            self.app.audio.play_sfx("pickup")

    @staticmethod
    def _floating_text_for_result(result: object) -> tuple[str, tuple[int, int, int]]:
        """Map a PickupResult to (label, colour) for the floating text."""
        # `result` is a PickupResult — duck-typed read to avoid a circular
        # import here.
        if getattr(result, "upgraded_weapon", False):
            return ("WEAPON UP!", (255, 220, 80))
        if getattr(result, "speed_up", False):
            return ("SPEED!", (80, 220, 255))
        if getattr(result, "extra_bomb", False):
            return ("+1 BOMB", (255, 140, 80))
        if getattr(result, "extra_life", False):
            return ("+1 LIFE", (255, 120, 180))
        if getattr(result, "shield_up", False):
            return ("SHIELD!", (80, 220, 255))
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

        scale=1 for regular enemies; pass 2 for boss / large craft.
        """
        frames = tuple(f"particles/explosion_{i:02d}.png" for i in range(4))
        # The animation auto-despawns on its last frame via _advance_animations.
        world.spawn(
            Position(pos),
            AnimatedSprite(frames=frames, frame_ticks=4, loop=False, layer=9),
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

    def _build_hud_state(self) -> HudCoopState:
        snap = self._session.scores.snapshot()
        p1 = self._powerup_states[P1]
        p2 = self._powerup_states[P2]
        lc1 = self._session.lifecycle(P1)
        lc2 = self._session.lifecycle(P2)
        return HudCoopState(
            team_score=snap.team,
            p1=HudPlayerStats(
                lives=lc1.lives,
                bombs=p1.bombs,
                weapon_level=p1.weapon.level + 1,
                score=snap.p1,
            ),
            p2=HudPlayerStats(
                lives=lc2.lives,
                bombs=p2.bombs,
                weapon_level=p2.weapon.level + 1,
                score=snap.p2,
            ),
        )

    def _switch_music(self, name: str) -> None:
        if self._music_playing == name:
            return
        self.app.audio.crossfade_to(name, ms=600)
        self._music_playing = name

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
