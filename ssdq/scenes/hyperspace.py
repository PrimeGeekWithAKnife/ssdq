"""Hyperspace interstitial — a ~70s side-scrolling bonus run.

Plays after the level-5 boss (campaign: ``exit_to="docking"``) and from
the level-select bonus row (``exit_to="level_select"``). Fun review
2026-06-12: the kid asked for "more levels"; this is the palate-cleanser
between the two campaign halves — ships fly RIGHT through an asteroid
stream, dodging rocks, popping a lone raider, and hoovering score orbs.

Reuses the ECS World + Renderer + SpatialGrid + ``should_apply_damage``
+ CoopSession/PlayerLifecycle + the HudCoopState resource. Takes NONE of
the wave/powerup/drone/missile/bomb/boss machinery — every spawn comes
from scene-local timers driven by ``_sim_time`` (advanced inside
``tick()``, so pausing freezes the ride for free) using the
``ssdq.core.rng`` tick helpers on distinct channels, keeping the whole
ride deterministic and replay-stable.

Damage is NORMAL (lives are at stake — the user's call) but tuned
gentle: generous lane spacing, slow rocks, hitboxes at 75% of the
visual radius. Scores + lives persist through ``_finish()`` exactly like
``LevelScene.exit``'s cleared-level branch, so the bonus carries into
the campaign.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from typing import Any

import pygame

from ssdq.core.clock import TICK_DT
from ssdq.core.collision import SpatialGrid, circles_overlap
from ssdq.core.components import (
    AnimatedSprite,
    CircleHitbox,
    Damage,
    Faction,
    FactionTag,
    Health,
    HitFlash,
    InvulnerabilityBlink,
    MaxHealth,
    PickupHalo,
    PlayerOwned,
    Position,
    ScoreValue,
    Sprite,
    TimeToLive,
    Velocity,
)
from ssdq.core.coop import (
    CoopSession,
    DamageDecision,
    LifecycleState,
    should_apply_damage,
)
from ssdq.core.coop.scoring import ScoreLedger
from ssdq.core.ecs import World
from ssdq.core.rng import tick_int, tick_range, tick_unit
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import P1, P2, Entity, PlayerInput, PlayerSlot, TickIndex, Vec2
from ssdq.scenes.app_state import AppState
from ssdq.scenes.hud_state import HudCoopState, HudPlayerStats
from ssdq.scenes.level import PLAY_H, PLAY_W, PlayerShip, _input_is_active

# ───────── run shape ─────────

# Total ride length. Spawning stops at _RIDE_SECONDS and the right-edge
# exit glow ramps over the final stretch; at _TOTAL_SECONDS we _finish().
_RIDE_SECONDS = 68.0
_TOTAL_SECONDS = 72.0
# "HYPERSPACE!" banner hold at the start of the ride.
_BANNER_SECONDS = 3.0
# No new rocks in the final 6s of spawning so the screen is visibly
# clearing as the exit glow approaches — the kid reads "almost there"
# rather than dying to a rock spat out at second 67.
_OBSTACLE_CUTOFF = _RIDE_SECONDS - 6.0

# ───────── movement / firing ─────────

# Gentle acceleration: the per-slot velocity eases toward the stick
# target at this rate (1/s). ~0.25s to reach 87% of full deflection —
# floaty enough to feel like coasting through hyperspace, snappy enough
# that a 9-year-old can still dodge.
_PLAYER_ACCEL_RATE = 8.0
# Fixed fire cadence — weapon tiers deliberately don't apply here, the
# ride is about dodging, not DPS. One tier-1 bolt every 0.18s.
_FIRE_COOLDOWN = 0.18
_BULLET_SPEED = 600.0
_BULLET_SPRITE = "projectiles/pulse_basic.png"
# Left-centre spawn column. Ships face right; respawns come back here.
_SPAWN_X = 90.0

# ───────── obstacle / raider / orb tuning ─────────

# Hard ceiling on live rocks. 24 obstacles + orbs + bullets + 2 ships
# stays far below the L5 crescendo (251 bullets / ~360 entities), the
# Pi 5 perf ceiling nothing new may exceed.
_OBSTACLE_CAP = 24
# Forgiving hitbox: 75% of the visual radius so grazes look like grazes.
_OBSTACLE_HITBOX_FRAC = 0.75
# Never spawn within this many px of the previous lane while it's still
# "fresh" — guarantees a dodge corridor between consecutive rocks.
_LANE_CORRIDOR_PX = 90.0
_LANE_FRESH_SECONDS = 2.0
_LANE_MIN_Y = 60.0
_LANE_MAX_Y = PLAY_H - 60.0

_RAIDER_SPEED = 180.0  # leftward px/s
_RAIDER_SINE_AMP = 60.0
_RAIDER_SINE_RATE = 1.8
_RAIDER_SHOT_OFFSETS = (1.2, 2.6)  # seconds after spawn
_RAIDER_BULLET_SPEED = 220.0

_ORB_POINTS = 150
_ORB_SPEED = -120.0
_ORB_TRAIN_LEN = 5
_ORB_SPACING_PX = 44.0

# Deterministic-noise channels — distinct per decision so the streams
# don't correlate (same convention as the backdrop generators, which
# own the 10_000–96_000 ranges; the hyperspace scene takes 61_xxx).
_CH_OBSTACLE_GAP = 61_001
_CH_OBSTACLE_LANE = 61_002
_CH_OBSTACLE_SIZE = 61_003
_CH_OBSTACLE_SPEED = 61_004
_CH_RAIDER_GAP = 61_010
_CH_RAIDER_LANE = 61_011
_CH_ORB_PATTERN = 61_020
_CH_ORB_SIDE = 61_021
_CH_ORB_DIST = 61_022


# ───────── scene-local components ─────────


@dataclass(frozen=True, slots=True)
class HyperObstacle:
    """Marker on every asteroid. ``size`` is "small" | "med" | "large"."""

    size: str


@dataclass(frozen=True, slots=True)
class HyperRaider:
    """The lone enemy fighter. The scene drives its position directly
    (sine path off ``spawn_time``/``base_y``) and decrements
    ``shots_left`` as the fixed-offset shots fire."""

    spawn_time: float
    base_y: float
    shots_left: int


@dataclass(frozen=True, slots=True)
class HyperOrb:
    """Marker on score orbs so collection (and the deeper right-edge
    cull allowance for incoming trains) can identify them."""


@dataclass(frozen=True, slots=True)
class _ObstacleSpec:
    sprite: str
    visual_radius: float
    hp: int
    score: int
    speed_lo: float
    speed_hi: float


# Size weighting 50/35/15 — mostly small puffs, occasional big hulk.
_OBSTACLE_SPECS: dict[str, _ObstacleSpec] = {
    "small": _ObstacleSpec("enemies/asteroid_small.png", 14.0, 1, 50, 150.0, 210.0),
    "med": _ObstacleSpec("enemies/asteroid_med.png", 24.0, 3, 100, 110.0, 150.0),
    "large": _ObstacleSpec("enemies/asteroid_hulk.png", 40.0, 6, 200, 70.0, 100.0),
}


# ───────── scene ─────────


class HyperspaceScene(Scene):
    """Side-scrolling asteroid bonus run. See module docstring."""

    def __init__(self, app: AppState, *, exit_to: str = "docking") -> None:
        if exit_to not in ("docking", "level_select"):
            raise ValueError(f"exit_to must be 'docking' or 'level_select', got {exit_to!r}")
        self.app = app
        self.exit_to = exit_to
        self._session: CoopSession | None = None
        self._grid = SpatialGrid(cell_size=64.0)
        self._player_entities: dict[PlayerSlot, Entity | None] = {P1: None, P2: None}
        self._player_positions: dict[PlayerSlot, Vec2] = {
            P1: self._spawn_pos(P1),
            P2: self._spawn_pos(P2),
        }
        self._player_vel: dict[PlayerSlot, Vec2] = {P1: Vec2(0.0, 0.0), P2: Vec2(0.0, 0.0)}
        self._engaged: set[PlayerSlot] = set()
        self._sim_time: float = 0.0
        self._internal_tick: int = 0
        # Spawn timers. First rock lands ~1.5s in so the banner gets a
        # beat of clean screen; orbs at ~4s; raider per its 10–12s gap.
        self._next_obstacle_tick: int = 90
        self._next_raider_time: float = tick_range(0, 10.0, 12.0, channel=_CH_RAIDER_GAP)
        self._next_orb_time: float = 4.0
        # Latest obstacle lane — drives the dodge-corridor guarantee and
        # the orb-train placement "through the gap".
        self._last_lane_y: float = PLAY_H / 2.0
        self._last_lane_time: float = -999.0
        self._title_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None

    # Render-branch protocol (read by main.py): this scene's entities
    # are drawn by the world Renderer, against the named backdrop, with
    # Scene.render layered on top as a chrome overlay.
    world_rendered = True

    @property
    def background_name(self) -> str:
        return "bg_hyperspace"

    # ───────── lifecycle ─────────

    def enter(self, world: World) -> None:
        # Despawn-guard: the World is shared across the scene stack.
        # Every caller that Replaces into us sweeps on exit already, but
        # belt-and-braces — a ghost ship under the ride would replay
        # kid playtest bug #6 ("ghost ship of me on the screen").
        for eid in list(world.alive_entities()):
            world.despawn(eid)

        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 84, bold=True)
        self._hint_font = pygame.font.SysFont(None, 32)

        bundle = self.app.content
        # Session seeding mirrors LevelScene.enter exactly: scores from
        # the persisted last_*_score totals, lives from the per-slot
        # last_lives carry-forward (kid playtest 2026-05-02 — lives were
        # silently resetting at every scene seam).
        seeded_p1_lives = self.app.last_lives.get(P1.index)
        seeded_p2_lives = self.app.last_lives.get(P2.index)
        self._session = CoopSession.initial(
            bundle.coop,
            self.app.options,
            scores=ScoreLedger.with_seed(
                team=self.app.last_team_score,
                p1=self.app.last_p1_score,
                p2=self.app.last_p2_score,
            ),
            p1_lives=seeded_p1_lives,
            p2_lives=seeded_p2_lives,
        )
        # Solo mode: P2 OUT immediately, same as LevelScene, so a P1
        # wipeout ends the ride instead of waiting on a ghost slot.
        if self.app.single_player:
            self._session.mark_out(P2)

        self._spawn_player(world, P1, self._spawn_pos(P1))
        if not self.app.single_player:
            self._spawn_player(world, P2, self._spawn_pos(P2))

        # Trimmed HUD snapshot — scores + lives only; bombs/missiles/
        # drones/shields don't exist on the ride so their counters stay
        # zero rather than leaking stale campaign numbers.
        world.insert_resource(self._build_hud_state())

        self.app.audio.crossfade_to("hyperspace", ms=800)

    def exit(self, world: World) -> None:
        # Persist scores on ANY exit (mirrors LevelScene.exit) — the
        # game-over path lands in GameOverScene which renders
        # app.last_*_score, so a wipeout mid-ride still shows the points
        # earned. _finish() re-writes the same values on success.
        assert self._session is not None
        snap = self._session.scores.snapshot()
        self.app.last_team_score = snap.team
        self.app.last_p1_score = snap.p1
        self.app.last_p2_score = snap.p2
        # Sweep every ride entity so nothing ghosts into the next scene.
        for eid in list(world.alive_entities()):
            world.despawn(eid)
        world.remove_resource(HudCoopState)

    # ───────── per-tick ─────────

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        assert self._session is not None
        dt = TICK_DT
        # Scene-local clock (same pattern as LevelScene): the SceneStack
        # returns early while paused, so _sim_time freezes across a
        # pause and every spawn timer below is pause-safe for free.
        self._sim_time = self._internal_tick * dt

        # Engaged tracking + auto-continue, cloned from LevelScene (kid
        # playtest 2026-05-02 #9): only slots that have actually given
        # input get continues auto-spent, so an idle P2 stays OUT.
        for slot, inp in ((P1, inputs[0]), (P2, inputs[1])):
            if slot not in self._engaged and _input_is_active(inp):
                self._engaged.add(slot)

        # 1. lifecycle timers, then continues for engaged OUT slots.
        self._session.tick(dt)
        for slot in (P1, P2):
            if self._session.lifecycle(slot).state != LifecycleState.OUT:
                continue
            if slot not in self._engaged:
                continue
            self._session.try_consume_continue(slot)

        # 2. player input → movement + fire.
        self._apply_player_input(world, P1, inputs[0], dt)
        self._apply_player_input(world, P2, inputs[1], dt)

        # 3. scene-local spawns (all stop at _RIDE_SECONDS).
        if self._sim_time < _RIDE_SECONDS:
            self._maybe_spawn_obstacle(world)
            self._maybe_spawn_raider(world)
            self._maybe_spawn_orbs(world)

        # 4. raider flight path + fixed-offset shots.
        self._advance_raiders(world)

        # 5. integrate plain movers (rocks, orbs, bullets).
        self._integrate_motion(world, dt)

        # 6. collisions.
        self._resolve_collisions(world)

        # 7. respawn dead-but-INVULNERABLE players at left-centre.
        self._handle_respawns(world)

        # 8. animations (explosions, hit flash, invuln blink) + culling.
        self._advance_animations(world)
        self._cull_entities(world)

        # 9. HUD snapshot refresh.
        world.insert_resource(self._build_hud_state())

        self._internal_tick += 1

        # 10. transitions — same game-over route as LevelScene; the
        # successful ride ends through _finish().
        if self._session.is_game_over:
            from ssdq.scenes.game_over import GameOverScene

            return Replace(scene=GameOverScene(self.app))
        if self._sim_time >= _TOTAL_SECONDS:
            return self._finish()
        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        # Overlay only — world sprites come from the Renderer via the
        # main.py world_rendered branch; this draws the chrome on top.
        if not isinstance(surface, pygame.Surface):
            return
        if self._title_font is None or self._hint_font is None:
            return
        w, h = surface.get_size()
        if self._sim_time < _BANNER_SECONDS:
            banner = self._title_font.render("HYPERSPACE!", True, (140, 220, 255))
            surface.blit(banner, banner.get_rect(center=(w // 2, h // 4)))
            hint = self._hint_font.render("Dodge the rocks — grab the orbs!", True, (255, 240, 120))
            surface.blit(hint, hint.get_rect(center=(w // 2, h // 4 + 64)))
        if self._sim_time >= _RIDE_SECONDS:
            # Right-edge exit glow — brightens over the final stretch so
            # the kid sees the end of the tunnel approaching.
            glow_t = min(1.0, (self._sim_time - _RIDE_SECONDS) / (_TOTAL_SECONDS - _RIDE_SECONDS))
            glow_w = max(8, int(w * 0.22 * glow_t))
            bands = 6
            for i in range(bands):
                band_w = max(1, glow_w // bands)
                x = w - glow_w + i * band_w
                a = int(30 + 150 * glow_t * (i + 1) / bands)
                band = pygame.Surface((band_w + 1, h), pygame.SRCALPHA)
                band.fill((210, 235, 255, a))
                surface.blit(band, (x, 0))

    # ───────── helpers: players ─────────

    @staticmethod
    def _spawn_pos(slot: PlayerSlot) -> Vec2:
        # Left-centre column; P1 above, P2 below, mirroring LevelScene's
        # 40/60 split but rotated for horizontal travel.
        return Vec2(_SPAWN_X, PLAY_H * (0.42 if slot == P1 else 0.58))

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
            # Scale 0.66 matches LevelScene (kid playtest #8). Facing:
            # the ship sprite natively points UP; the renderer applies
            # pygame.transform.rotate(surf, -degrees(rotation_rad))
            # (renderer.py ~220), and pygame rotates COUNTER-clockwise
            # for positive degrees — so a positive rotation_rad turns
            # the sprite CLOCKWISE on screen. Up rotated 90° clockwise
            # faces RIGHT, the direction of travel here.
            Sprite(path=ship.sprite, layer=10, scale=0.66, rotation_rad=math.pi / 2.0),
            PlayerShip(slot=slot, weapon_cooldown=0.0),
        )
        self._player_entities[slot] = eid
        self._player_positions[slot] = pos
        self._player_vel[slot] = Vec2(0.0, 0.0)

    def _apply_player_input(
        self, world: World, slot: PlayerSlot, inp: PlayerInput, dt: float
    ) -> None:
        assert self._session is not None
        eid = self._player_entities.get(slot)
        if eid is None or not world.is_alive(eid):
            return
        lifecycle = self._session.lifecycle(slot)
        if lifecycle.state in (LifecycleState.DYING, LifecycleState.OUT):
            return

        ship = self.app.content.ships["vanguard" if slot == P1 else "vanguard_red"]
        move = inp.move.clamped_magnitude(1.0)
        # Gentle accel: ease the velocity toward the stick target rather
        # than snapping to it — hyperspace coasting, not arcade strafing.
        target = Vec2(move.x * ship.max_speed, move.y * ship.max_speed)
        vel = self._player_vel[slot]
        blend = min(1.0, _PLAYER_ACCEL_RATE * dt)
        vel = Vec2(vel.x + (target.x - vel.x) * blend, vel.y + (target.y - vel.y) * blend)
        self._player_vel[slot] = vel

        pos = world.must_get(eid, Position).pos
        new_x = max(20.0, min(PLAY_W - 20.0, pos.x + vel.x * dt))
        new_y = max(20.0, min(PLAY_H - 20.0, pos.y + vel.y * dt))
        new_pos = Vec2(new_x, new_y)
        world.replace(eid, Position(new_pos))
        self._player_positions[slot] = new_pos

        # Fire — fixed cadence, rightward tier-1 bolts. Cooldown is kept
        # on the PlayerShip component (same home as LevelScene) so the
        # respawn-fresh component resets it for free.
        ps = world.must_get(eid, PlayerShip)
        cooldown = max(0.0, ps.weapon_cooldown - dt)
        if inp.fire and cooldown <= 0.0 and lifecycle.state == LifecycleState.ALIVE:
            world.spawn(
                Position(Vec2(new_pos.x + 26.0, new_pos.y)),
                Velocity(Vec2(_BULLET_SPEED, 0.0)),
                CircleHitbox(radius=6.0),
                FactionTag(Faction.PLAYER_BULLET),
                PlayerOwned(slot),
                Damage(amount=1),
                # The pulse bolt sprite is drawn for upward travel —
                # same clockwise-90° rotation as the ships points it
                # along the rightward flight path.
                Sprite(path=_BULLET_SPRITE, layer=8, rotation_rad=math.pi / 2.0),
                TimeToLive(ticks=180),
            )
            cooldown = _FIRE_COOLDOWN
            self.app.audio.play_sfx("laser", volume=0.4)
        world.replace(eid, PlayerShip(slot=slot, weapon_cooldown=cooldown))

    # ───────── helpers: spawns ─────────

    def _maybe_spawn_obstacle(self, world: World) -> None:
        if self._sim_time >= _OBSTACLE_CUTOFF:
            return
        if self._internal_tick < self._next_obstacle_tick:
            return
        t = self._internal_tick
        self._next_obstacle_tick = t + tick_int(t, 45, 61, channel=_CH_OBSTACLE_GAP)
        # Hard cap on live rocks — the timer still re-arms above so the
        # stream resumes as soon as the screen thins out.
        if sum(1 for _ in world.query1(HyperObstacle)) >= _OBSTACLE_CAP:
            return
        lane_y = tick_range(t, _LANE_MIN_Y, _LANE_MAX_Y, channel=_CH_OBSTACLE_LANE)
        # Dodge-corridor guarantee: while the previous lane is fresh,
        # push this rock at least _LANE_CORRIDOR_PX away from it so two
        # consecutive rocks never wall off the same flight line.
        if (
            self._sim_time - self._last_lane_time < _LANE_FRESH_SECONDS
            and abs(lane_y - self._last_lane_y) < _LANE_CORRIDOR_PX
        ):
            direction = 1.0 if lane_y >= self._last_lane_y else -1.0
            lane_y = self._last_lane_y + direction * _LANE_CORRIDOR_PX
            if lane_y < _LANE_MIN_Y or lane_y > _LANE_MAX_Y:
                lane_y = self._last_lane_y - direction * _LANE_CORRIDOR_PX
        lane_y = max(_LANE_MIN_Y, min(_LANE_MAX_Y, lane_y))
        self._last_lane_y = lane_y
        self._last_lane_time = self._sim_time

        u = tick_unit(t, channel=_CH_OBSTACLE_SIZE)
        size = "small" if u < 0.50 else ("med" if u < 0.85 else "large")
        spec = _OBSTACLE_SPECS[size]
        speed = tick_range(t, spec.speed_lo, spec.speed_hi, channel=_CH_OBSTACLE_SPEED)
        world.spawn(
            Position(Vec2(PLAY_W + spec.visual_radius + 10.0, lane_y)),
            Velocity(Vec2(-speed, 0.0)),
            # 75% of the visual radius — grazing a rock's silhouette
            # edge should never kill (gentle by design).
            CircleHitbox(radius=spec.visual_radius * _OBSTACLE_HITBOX_FRAC),
            FactionTag(Faction.ENEMY),
            Health(hp=spec.hp),
            MaxHealth(hp=spec.hp),
            ScoreValue(points=spec.score),
            Sprite(path=spec.sprite, layer=6),
            HyperObstacle(size=size),
        )

    def _maybe_spawn_raider(self, world: World) -> None:
        if self._sim_time < self._next_raider_time:
            return
        t = self._internal_tick
        self._next_raider_time = self._sim_time + tick_range(t, 10.0, 12.0, channel=_CH_RAIDER_GAP)
        base_y = tick_range(t, 120.0, PLAY_H - 120.0, channel=_CH_RAIDER_LANE)
        world.spawn(
            Position(Vec2(PLAY_W + 40.0, base_y)),
            CircleHitbox(radius=20.0),
            FactionTag(Faction.ENEMY),
            Health(hp=3),
            MaxHealth(hp=3),
            ScoreValue(points=400),
            # Interceptor sprite natively faces DOWN; a positive
            # rotation_rad rotates clockwise on screen (see
            # _spawn_player), and down turned 90° clockwise faces LEFT —
            # nose-first along its attack run toward the players.
            Sprite(path="enemies/interceptor.png", layer=6, rotation_rad=math.pi / 2.0),
            HyperRaider(
                spawn_time=self._sim_time,
                base_y=base_y,
                shots_left=len(_RAIDER_SHOT_OFFSETS),
            ),
        )

    def _maybe_spawn_orbs(self, world: World) -> None:
        if self._sim_time < self._next_orb_time:
            return
        t = self._internal_tick
        self._next_orb_time = self._sim_time + 4.0
        # Place the train through the gap relative to the latest rock
        # lane: offset well clear of it on a deterministic side.
        side = 1.0 if tick_unit(t, channel=_CH_ORB_SIDE) < 0.5 else -1.0
        dist = tick_range(t, 140.0, 220.0, channel=_CH_ORB_DIST)
        base_y = max(70.0, min(PLAY_H - 70.0, self._last_lane_y + side * dist))
        arc = tick_unit(t, channel=_CH_ORB_PATTERN) >= 0.5
        for i in range(_ORB_TRAIN_LEN):
            if arc:
                # Gentle hump — peak at the middle orb.
                y = base_y - 70.0 * math.sin(math.pi * i / (_ORB_TRAIN_LEN - 1))
                y = max(70.0, min(PLAY_H - 70.0, y))
            else:
                y = base_y
            world.spawn(
                Position(Vec2(PLAY_W + 40.0 + i * _ORB_SPACING_PX, y)),
                Velocity(Vec2(_ORB_SPEED, 0.0)),
                CircleHitbox(radius=16.0),
                FactionTag(Faction.PICKUP),
                Sprite(path="pickups/score_orb.png", layer=5, scale=1.2),
                PickupHalo(radius=18.0, colour=(255, 220, 100)),
                HyperOrb(),
            )

    def _advance_raiders(self, world: World) -> None:
        for eid, raider in list(world.query1(HyperRaider)):
            if not world.is_alive(eid):
                continue
            t = self._sim_time - raider.spawn_time
            x = PLAY_W + 40.0 - _RAIDER_SPEED * t
            y = raider.base_y + _RAIDER_SINE_AMP * math.sin(_RAIDER_SINE_RATE * t)
            pos = Vec2(x, y)
            world.replace(eid, Position(pos))
            # Two aimed shots at fixed offsets — deterministic, and few
            # enough that the ride stays a dodge-the-rocks game.
            shots_fired = len(_RAIDER_SHOT_OFFSETS) - raider.shots_left
            fired_now = 0
            for offset in _RAIDER_SHOT_OFFSETS[shots_fired:]:
                if t < offset:
                    break
                self._fire_raider_shot(world, pos)
                fired_now += 1
            if fired_now:
                world.replace(eid, _dc_replace(raider, shots_left=raider.shots_left - fired_now))

    def _fire_raider_shot(self, world: World, origin: Vec2) -> None:
        target = self._nearest_alive_player_pos(origin)
        if target is None:
            vel = Vec2(-_RAIDER_BULLET_SPEED, 0.0)
        else:
            dx = target.x - origin.x
            dy = target.y - origin.y
            mag = math.hypot(dx, dy) or 1.0
            vel = Vec2(dx / mag * _RAIDER_BULLET_SPEED, dy / mag * _RAIDER_BULLET_SPEED)
        world.spawn(
            Position(origin),
            Velocity(vel),
            CircleHitbox(radius=6.0),
            FactionTag(Faction.ENEMY_BULLET),
            Sprite(path="projectiles/enemy_orb.png", layer=7),
            Damage(amount=1),
            TimeToLive(ticks=300),
        )

    # ───────── helpers: motion / collisions ─────────

    def _integrate_motion(self, world: World, dt: float) -> None:
        for eid, pos, vel in list(world.query2(Position, Velocity)):
            new_pos = Vec2(pos.pos.x + vel.vel.x * dt, pos.pos.y + vel.vel.y * dt)
            world.replace(eid, Position(new_pos))

    def _resolve_collisions(self, world: World) -> None:
        # ~60-line simplification of LevelScene._resolve_collisions: no
        # drones, no shields, no boss gates, no off-screen damage gate —
        # everything on the ride is on-screen and unshielded.
        assert self._session is not None
        self._grid.clear()
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
                self._handle_orb_collect(world, a, b, ftag_a)
                continue
            if Faction.PLAYER in (ftag_a.faction, ftag_b.faction):
                self._handle_player_hit(world, a, b, ftag_a, ftag_b)
            elif ftag_a.faction == Faction.PLAYER_BULLET and ftag_b.faction == Faction.ENEMY:
                self._handle_enemy_hit(world, b, a)
            elif ftag_b.faction == Faction.PLAYER_BULLET and ftag_a.faction == Faction.ENEMY:
                self._handle_enemy_hit(world, a, b)

    def _handle_orb_collect(self, world: World, a: Entity, b: Entity, ftag_a: FactionTag) -> None:
        assert self._session is not None
        pickup_eid = a if ftag_a.faction == Faction.PICKUP else b
        player_eid = b if pickup_eid == a else a
        if not world.is_alive(pickup_eid) or not world.is_alive(player_eid):
            return
        owned = world.get(player_eid, PlayerOwned)
        if owned is None:
            return
        world.despawn(pickup_eid)
        self._session.scores.award(owned.slot, _ORB_POINTS, multiplier=1.0)
        self.app.audio.play_sfx("pickup")

    def _handle_player_hit(
        self,
        world: World,
        a: Entity,
        b: Entity,
        ftag_a: FactionTag,
        ftag_b: FactionTag,
    ) -> None:
        # Cloned from LevelScene._handle_player_hit, minus the drone
        # branch (no drones here) and the powerup-shield absorb branch —
        # powerup shields don't exist on the ride, so there is nothing
        # between the lifecycle gate and the death path.
        assert self._session is not None
        player_eid = a if ftag_a.faction == Faction.PLAYER else b
        other = b if player_eid == a else a
        other_ftag = ftag_b if player_eid == a else ftag_a
        owned = world.get(player_eid, PlayerOwned)
        if owned is None:
            return
        slot = owned.slot
        # Lifecycle gate KEPT: DYING/INVULNERABLE/OUT ships can't be
        # hit — post-respawn i-frames work exactly as in the campaign.
        if not self._session.lifecycle(slot).can_be_hit:
            return
        player_pos = world.must_get(player_eid, Position).pos
        self._spawn_explosion(world, player_pos, scale=2)
        world.despawn(player_eid)
        self._player_entities[slot] = None
        self._session.hit(slot)
        self.app.audio.play_sfx("hit")
        if other_ftag.faction == Faction.ENEMY_BULLET:
            # Consume the bullet so it can't double-hit next tick.
            world.despawn(other)
        elif other_ftag.faction == Faction.ENEMY and world.is_alive(other):
            # Ship-vs-rock also shatters the rock — one hit, one rock
            # (gentle): the collision already cost a life, the rock must
            # not linger in the respawn corridor to claim a second.
            other_pos = world.get(other, Position)
            if other_pos is not None:
                self._spawn_explosion(world, other_pos.pos, scale=1)
            world.despawn(other)

    def _handle_enemy_hit(self, world: World, enemy_eid: Entity, bullet_eid: Entity) -> None:
        assert self._session is not None
        damage = world.get(bullet_eid, Damage)
        hlth = world.get(enemy_eid, Health)
        if damage is None or hlth is None:
            return
        new_hp = hlth.hp - damage.amount
        world.replace(enemy_eid, Health(hp=new_hp))
        owned = world.get(bullet_eid, PlayerOwned)
        killer_slot = owned.slot if owned is not None else None
        world.despawn(bullet_eid)
        if new_hp > 0:
            world.replace(enemy_eid, HitFlash(ticks_remaining=4))
            return
        pos = world.must_get(enemy_eid, Position).pos
        score_val = world.get(enemy_eid, ScoreValue)
        if score_val is not None and killer_slot is not None:
            # Flat 1.0 multiplier — no proximity bonus machinery here.
            self._session.scores.award(killer_slot, score_val.points, multiplier=1.0)
        self._spawn_explosion(world, pos, scale=1)
        world.despawn(enemy_eid)
        self.app.audio.play_sfx("explosion", volume=0.5)

    # ───────── helpers: respawn / animations / cull / hud ─────────

    def _handle_respawns(self, world: World) -> None:
        # Clone of LevelScene._handle_respawns, respawning at the
        # left-centre column instead of bottom-centre.
        assert self._session is not None
        cfg = self.app.content.coop
        invuln_ticks = int(cfg.respawn_invulnerability * 60)
        for slot in (P1, P2):
            lc = self._session.lifecycle(slot)
            if lc.state == LifecycleState.INVULNERABLE and self._player_entities.get(slot) is None:
                spawn_pos = self._spawn_pos(slot)
                self._spawn_player(world, slot, spawn_pos)
                eid = self._player_entities[slot]
                if eid is not None:
                    world.add(eid, InvulnerabilityBlink(ticks_remaining=invuln_ticks))
                if lc.fired_clearing_shockwave:
                    self._session.consume_clearing_shockwaves()
                    for ebid, pos, ftag in list(world.query2(Position, FactionTag)):
                        if ftag.faction == Faction.ENEMY_BULLET and circles_overlap(
                            spawn_pos, cfg.respawn_clearing_radius, pos.pos, 0.0
                        ):
                            world.despawn(ebid)

    def _spawn_explosion(self, world: World, pos: Vec2, *, scale: int = 1) -> None:
        # Lifted from LevelScene._spawn_explosion (cheap, reuses the
        # same particle frames the atlas already preloads).
        frames = tuple(f"particles/explosion_{i:02d}.png" for i in range(4))
        world.spawn(
            Position(pos),
            AnimatedSprite(
                frames=frames,
                frame_ticks=4 if scale <= 2 else 6,
                loop=False,
                layer=9,
                scale=float(scale),
            ),
        )

    def _advance_animations(self, world: World) -> None:
        # Trimmed clone of LevelScene._advance_animations — explosions
        # auto-despawn on their last frame, hit flashes decay, and the
        # respawn invulnerability blink pulses the ship sprite alpha.
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
                world.replace(eid, _dc_replace(anim, current_index=next_idx, elapsed_ticks=0))
            else:
                world.replace(eid, _dc_replace(anim, elapsed_ticks=new_elapsed))

        for eid, flash in list(world.query1(HitFlash)):
            if flash.ticks_remaining <= 1:
                world.remove(eid, HitFlash)
            else:
                world.replace(eid, HitFlash(ticks_remaining=flash.ticks_remaining - 1))

        for eid, blink in list(world.query1(InvulnerabilityBlink)):
            if blink.ticks_remaining <= 1:
                spr = world.get(eid, Sprite)
                if spr is not None:
                    world.replace(eid, _dc_replace(spr, alpha=255))
                world.remove(eid, InvulnerabilityBlink)
                continue
            phase = (blink.ticks_remaining // 6) % 2
            new_alpha = 120 if phase == 0 else 60
            spr = world.get(eid, Sprite)
            if spr is not None:
                world.replace(eid, _dc_replace(spr, alpha=new_alpha))
            world.replace(eid, InvulnerabilityBlink(ticks_remaining=blink.ticks_remaining - 1))

    def _cull_entities(self, world: World) -> None:
        for eid, ttl in list(world.query1(TimeToLive)):
            new_ticks = ttl.ticks - 1
            if new_ticks <= 0:
                world.despawn(eid)
            else:
                world.replace(eid, TimeToLive(ticks=new_ticks))

        for eid, pos in list(world.query1(Position)):
            if world.has(eid, PlayerShip):
                continue
            x, y = pos.pos.x, pos.pos.y
            # Orb trains spawn up to ~PLAY_W+216 deep so the whole line
            # streams in — give them a deeper right-edge allowance than
            # everything else (which culls at +80 like the campaign).
            right_margin = 260.0 if world.has(eid, HyperOrb) else 80.0
            if x < -60.0 or x > PLAY_W + right_margin or y < -80.0 or y > PLAY_H + 80.0:
                world.despawn(eid)

    def _build_hud_state(self) -> HudCoopState:
        assert self._session is not None
        snap = self._session.scores.snapshot()
        lc1 = self._session.lifecycle(P1)
        lc2 = self._session.lifecycle(P2)
        return HudCoopState(
            team_score=snap.team,
            p1=HudPlayerStats(lives=lc1.lives, bombs=0, weapon_level=1, score=snap.p1),
            p2=HudPlayerStats(lives=lc2.lives, bombs=0, weapon_level=1, score=snap.p2),
            single_player=self.app.single_player,
        )

    def _player_pos_if_alive(self, slot: PlayerSlot) -> Vec2 | None:
        assert self._session is not None
        if self._player_entities.get(slot) is None:
            return None
        if not self._session.lifecycle(slot).can_be_hit:
            return None
        return self._player_positions.get(slot)

    def _nearest_alive_player_pos(self, from_pos: Vec2) -> Vec2 | None:
        candidates = [p for slot in (P1, P2) if (p := self._player_pos_if_alive(slot)) is not None]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda p: (p.x - from_pos.x) ** 2 + (p.y - from_pos.y) ** 2,
        )

    # ───────── completion ─────────

    def _finish(self) -> SceneTransition:
        # Persist scores + lives EXACTLY like LevelScene.exit's
        # cleared-level branch — this is what carries the hyperspace
        # bonus into the campaign: the next LevelScene.enter re-seeds
        # its CoopSession from these fields. (exit() re-writes the same
        # score values; harmless double-write, same session snapshot.)
        assert self._session is not None
        snap = self._session.scores.snapshot()
        self.app.last_team_score = snap.team
        self.app.last_p1_score = snap.p1
        self.app.last_p2_score = snap.p2
        self.app.last_lives = {
            P1.index: self._session.lifecycle(P1).lives,
            P2.index: self._session.lifecycle(P2).lives,
        }
        if self.exit_to == "docking":
            from ssdq.scenes.docking import DockingScene

            return Replace(scene=DockingScene(self.app))
        from ssdq.scenes.level_select import LevelSelectScene

        return Replace(scene=LevelSelectScene(self.app))
