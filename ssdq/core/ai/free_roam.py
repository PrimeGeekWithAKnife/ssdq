"""Deterministic drift + dodge AI used by sentinel + marauder.

Behaviour:
- Pick a wander target inside the configured zone (top..bottom Y band,
  full playfield X). The target refreshes when the enemy reaches it
  (within 24px) or after `target_lifetime` seconds — whichever first.
- Each tick, head toward the wander target at `speed`.
- If any incoming player bullet is within `dodge_radius` AND moving
  roughly toward us, add a perpendicular nudge sized by
  `dodge_aggression` (0..1).
- Y is clamped to the zone band; if we're outside, the heading is
  pushed back in.

Deterministic — wander targets derive from sim_time + entity id via a
small mixing hash, no RNG (spec §6.3 — every survivor returns; no
randomness). Two enemies with the same entity id seed at the same
sim_time would land on the same target; in practice entity ids are
unique so they pick distinct targets.

Pure function: ``free_roam_step`` takes a state dataclass + the
current world snapshot bits it needs and returns the next velocity +
the (possibly updated) state. Caller is expected to:
- write the velocity onto a Velocity component (or apply it
  directly to Position),
- replace the FreeRoamAI component with the new state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ssdq.core.types import Vec2

# Wander target counts as "reached" when within this many px.
_REACH_TOLERANCE = 24.0

# Player-bullet proximity that triggers dodge consideration.
_DODGE_RADIUS = 96.0

# Minimum dot product (enemy-relative-to-bullet · bullet-velocity) for
# the bullet to count as "headed toward us". Negative dot means moving
# AWAY; positive means toward. We dodge anything with dot > 0.4.
_DODGE_DOT_THRESHOLD = 0.4

# Wander target lifetime fallback — even if not reached, refresh after
# this many seconds so an enemy doesn't stall against an obstacle.
_TARGET_LIFETIME = 2.5


@dataclass(frozen=True, slots=True)
class FreeRoamConfig:
    """Static config baked at spawn from the EnemyDef.free_roam block."""

    speed: float
    dodge_aggression: float  # 0.0 = ignore bullets, 1.0 = full perpendicular nudge.
    zone_top: float
    zone_bottom: float


def _det_hash01(a: float, b: int) -> float:
    """Deterministic float in [0, 1) from a sim_time + entity id pair.

    Plain ``math`` operations only — no python ``random`` module so the
    output is reproducible across the replay layer.
    """
    # Cheap LCG-style mix: scale + add + take fractional.
    x = a * 8.91 + float(b) * 1.731 + 0.31415
    f = x - math.floor(x)
    # One more shuffle so two close inputs don't produce close outputs.
    f = (f * 9.301) % 1.0
    return f


def _new_target(
    cfg: FreeRoamConfig,
    sim_time: float,
    entity_id: int,
    play_w: float,
) -> Vec2:
    """Pick a wander target inside the configured zone."""
    rx = _det_hash01(sim_time, entity_id) * (play_w - 100.0) + 50.0
    ry = _det_hash01(sim_time + 0.1, entity_id) * (cfg.zone_bottom - cfg.zone_top) + cfg.zone_top
    return Vec2(rx, ry)


def free_roam_step(
    pos: Vec2,
    velocity: Vec2,
    cfg: FreeRoamConfig,
    target: Vec2,
    target_age: float,
    incoming_bullets: list[tuple[Vec2, Vec2]],  # (pos, velocity) pairs
    sim_time: float,
    entity_id: int,
    play_w: float,
    dt: float,
) -> tuple[Vec2, Vec2, float]:
    """Compute next-tick velocity and updated wander state.

    Returns ``(new_velocity, new_target, new_target_age)``. ``velocity``
    is unused for now (kept for future inertia smoothing) — current
    implementation produces an instantaneous heading.
    """
    # Refresh target if reached or expired.
    dx = target.x - pos.x
    dy = target.y - pos.y
    dist_to_target = math.sqrt(dx * dx + dy * dy)
    if dist_to_target < _REACH_TOLERANCE or target_age > _TARGET_LIFETIME:
        target = _new_target(cfg, sim_time, entity_id, play_w)
        target_age = 0.0
        dx = target.x - pos.x
        dy = target.y - pos.y
        dist_to_target = math.sqrt(dx * dx + dy * dy)
    target_age += dt

    # Heading toward target.
    if dist_to_target < 0.001:
        head_x, head_y = 0.0, 1.0
    else:
        head_x = dx / dist_to_target
        head_y = dy / dist_to_target

    # Dodge: perpendicular nudge for any incoming bullet within radius
    # and roughly heading toward us.
    if cfg.dodge_aggression > 0.0:
        dr2 = _DODGE_RADIUS * _DODGE_RADIUS
        for bp, bv in incoming_bullets:
            rdx = pos.x - bp.x
            rdy = pos.y - bp.y
            d2 = rdx * rdx + rdy * rdy
            if d2 > dr2:
                continue
            # Bullet velocity → unit vector. Skip near-zero.
            bv_mag = math.sqrt(bv.x * bv.x + bv.y * bv.y)
            if bv_mag < 0.001:
                continue
            bvx = bv.x / bv_mag
            bvy = bv.y / bv_mag
            # Bullet → enemy unit vector.
            d = math.sqrt(d2)
            if d < 0.001:
                continue
            # Bullet → enemy direction. ``rdx = pos.x - bp.x`` is already
            # enemy minus bullet so it IS the bullet→enemy vector — no
            # extra negation needed (an earlier draft inverted this and
            # the dodge fired on bullets moving AWAY).
            edx = rdx / d
            edy = rdy / d
            # Is bullet heading toward us?
            dot = bvx * edx + bvy * edy
            if dot < _DODGE_DOT_THRESHOLD:
                continue
            # Perpendicular to bullet velocity, choose side away from
            # bullet line (so we step OUT of the bullet's path).
            perp_x = -bvy
            perp_y = bvx
            sign = 1.0 if (perp_x * rdx + perp_y * rdy) > 0.0 else -1.0
            head_x += perp_x * sign * cfg.dodge_aggression
            head_y += perp_y * sign * cfg.dodge_aggression

    # Zone clamp: push heading back toward the band if outside.
    if pos.y < cfg.zone_top:
        head_y = max(head_y, 0.5)
    elif pos.y > cfg.zone_bottom:
        head_y = min(head_y, -0.5)

    # Normalise + scale to speed.
    mag = math.sqrt(head_x * head_x + head_y * head_y)
    if mag < 0.001:
        return Vec2(0.0, 0.0), target, target_age
    new_vx = head_x / mag * cfg.speed
    new_vy = head_y / mag * cfg.speed
    return Vec2(new_vx, new_vy), target, target_age
