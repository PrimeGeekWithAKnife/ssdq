"""Pure-function shape overlap tests."""

from __future__ import annotations

from dataclasses import dataclass

from ssdq.core.types import Vec2


@dataclass(frozen=True, slots=True)
class AABB:
    """Axis-aligned bounding box. Half-open interval [min, max)."""

    min: Vec2
    max: Vec2

    @staticmethod
    def from_centre(centre: Vec2, half_w: float, half_h: float) -> AABB:
        return AABB(
            Vec2(centre.x - half_w, centre.y - half_h),
            Vec2(centre.x + half_w, centre.y + half_h),
        )


def circles_overlap(a_pos: Vec2, a_r: float, b_pos: Vec2, b_r: float) -> bool:
    dx = a_pos.x - b_pos.x
    dy = a_pos.y - b_pos.y
    r = a_r + b_r
    return dx * dx + dy * dy <= r * r


def aabb_overlaps(a: AABB, b: AABB) -> bool:
    return a.min.x < b.max.x and a.max.x > b.min.x and a.min.y < b.max.y and a.max.y > b.min.y


def circle_aabb_overlaps(c_pos: Vec2, c_r: float, box: AABB) -> bool:
    cx = max(box.min.x, min(c_pos.x, box.max.x))
    cy = max(box.min.y, min(c_pos.y, box.max.y))
    dx = c_pos.x - cx
    dy = c_pos.y - cy
    return dx * dx + dy * dy <= c_r * c_r
