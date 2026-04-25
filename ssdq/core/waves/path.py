"""Formation path evaluation.

Catmull-Rom spline interpolation through the control points of a
`FormationDef`. The path is a deterministic function of `t ∈ [0, 1]`
(or `[0, ∞)` if `loop=True`).

Mirroring (`mirror(name)` in level YAML) reflects x about screen-centre
640. We do this at evaluation time so a single FormationDef serves both
sides; the SpawnDef.mirrored bit is the only state difference.

Tangents are needed for two things downstream:
* Sprite rotation (so an enemy points along its travel direction).
* Aimed-shot spawn velocity bias when the path itself is moving fast.

Catmull-Rom is the 'tension=0' subset of cardinal splines. We use the
standard centripetal parameterisation for visually pleasing curves
without overshoot near sharp control-point clusters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ssdq.core.content.schema import FormationDef, FormationKind
from ssdq.core.types import Vec2

# Screen-centre x for mirroring. Hard-coded: the playfield is 1280×720.
# Encoded here rather than passed everywhere — wave/level YAML matches.
SCREEN_CENTRE_X = 640.0


@dataclass(frozen=True, slots=True)
class PathSample:
    """A point on a formation path, with tangent for orientation."""

    pos: Vec2
    tangent: Vec2  # unit vector along travel direction (zero if degenerate)


def _wrap_or_clamp(t: float, loop: bool) -> float:
    """Loop wraps to [0, 1); non-loop clamps to [0, 1]."""
    if loop:
        # `t % 1.0` handles negatives correctly in Python (always >= 0).
        return t % 1.0
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t


def _catmull_rom_segment(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    u: float,
) -> tuple[Vec2, Vec2]:
    """Standard centripetal Catmull-Rom on segment p1→p2; u ∈ [0, 1].

    Returns (position, tangent). Uniform parameterisation is fine for
    our roughly-evenly-spaced control points.
    """
    u2 = u * u
    u3 = u2 * u
    # Position basis (uniform Catmull-Rom):
    # 0.5 * ((2*p1) + (-p0+p2)*u + (2*p0 - 5*p1 + 4*p2 - p3)*u² + (-p0 + 3*p1 - 3*p2 + p3)*u³)
    px = 0.5 * (
        (2.0 * p1[0])
        + (-p0[0] + p2[0]) * u
        + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * u2
        + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * u3
    )
    py = 0.5 * (
        (2.0 * p1[1])
        + (-p0[1] + p2[1]) * u
        + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * u2
        + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * u3
    )
    # Tangent = derivative wrt u
    tx = 0.5 * (
        (-p0[0] + p2[0])
        + 2.0 * (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * u
        + 3.0 * (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * u2
    )
    ty = 0.5 * (
        (-p0[1] + p2[1])
        + 2.0 * (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * u
        + 3.0 * (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * u2
    )
    return Vec2(px, py), Vec2(tx, ty)


def evaluate_path(
    formation: FormationDef,
    t_norm: float,
    *,
    mirrored: bool = False,
) -> PathSample:
    """Sample a formation at normalised time `t_norm`.

    `t_norm` is in [0, 1] (clamped) for non-looping paths; for `loop=True`
    paths it wraps to [0, 1) and you can pass any real value.

    Mirrored variant reflects x about SCREEN_CENTRE_X; the tangent's
    x component is also flipped so direction stays consistent.
    """
    if formation.kind != FormationKind.CATMULL_ROM:
        raise ValueError(f"unsupported formation kind: {formation.kind}")

    cps = formation.control_points
    n = len(cps)
    if n < 2:
        raise ValueError(f"formation {formation.name} needs ≥ 2 control points")

    u = _wrap_or_clamp(t_norm, formation.loop)

    if n == 2:
        # Degenerate "spline" — straight-line lerp.
        p1, p2 = cps[0], cps[1]
        px = p1[0] + (p2[0] - p1[0]) * u
        py = p1[1] + (p2[1] - p1[1]) * u
        pos = Vec2(px, py)
        tan = Vec2(p2[0] - p1[0], p2[1] - p1[1])
    else:
        # Segment count: looping paths get a wrap-around segment from
        # cps[n-1] → cps[0]; non-looping paths have (n-1) segments.
        # This means looped formations don't *need* to duplicate their
        # first point as the last (but it's still allowed).
        seg_count = n if formation.loop else n - 1
        seg_f = u * seg_count
        seg_i = int(seg_f)
        if seg_i >= seg_count:
            seg_i = seg_count - 1
        local_u = seg_f - seg_i

        if formation.loop:
            p0 = cps[(seg_i - 1) % n]
            p1 = cps[seg_i % n]
            p2 = cps[(seg_i + 1) % n]
            p3 = cps[(seg_i + 2) % n]
        else:
            p0 = cps[seg_i - 1] if seg_i > 0 else cps[0]
            p1 = cps[seg_i]
            p2 = cps[seg_i + 1]
            p3 = cps[seg_i + 2] if seg_i + 2 < n else cps[seg_i + 1]
        pos, tan = _catmull_rom_segment(p0, p1, p2, p3, local_u)

    if mirrored:
        pos = Vec2(2.0 * SCREEN_CENTRE_X - pos.x, pos.y)
        tan = Vec2(-tan.x, tan.y)

    # Normalise tangent. Zero-length tangent means a degenerate sample
    # (e.g. spline cusp); leave it zero — caller decides what to do.
    tlen2 = tan.x * tan.x + tan.y * tan.y
    if tlen2 > 1e-12:
        inv = 1.0 / math.sqrt(tlen2)
        tan = Vec2(tan.x * inv, tan.y * inv)
    else:
        tan = Vec2(0.0, 0.0)

    return PathSample(pos=pos, tangent=tan)


def path_position(formation: FormationDef, t_norm: float, *, mirrored: bool = False) -> Vec2:
    """Convenience: sample only the position. Same determinism guarantee."""
    return evaluate_path(formation, t_norm, mirrored=mirrored).pos
