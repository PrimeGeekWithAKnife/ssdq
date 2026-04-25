"""Score ledger: combined team score + per-player personal scores.

Per spec §5: combined team score is prominent in HUD, personal scores
small. Proximity bonus (multiplier when both alive and not at screen
edges) applies on award.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ssdq.core.content.schema import CoopConfig
from ssdq.core.types import P1, P2, PlayerSlot, Vec2


@dataclass(frozen=True, slots=True)
class ScoreSnapshot:
    """Immutable view of the scoreboard for HUD rendering."""

    team: int
    p1: int
    p2: int


def proximity_multiplier(
    *,
    p1_pos: Vec2 | None,
    p2_pos: Vec2 | None,
    config: CoopConfig,
    play_w: float,
    play_h: float,
) -> float:
    """Return the score multiplier when both players are alive and within
    `proximity_bonus_radius`, and not in the edge zone. Otherwise 1.0.

    `p1_pos` / `p2_pos` are None when that player isn't alive.
    """
    if p1_pos is None or p2_pos is None:
        return 1.0
    edge = config.proximity_bonus_edge_zone
    if p1_pos.x < edge or p1_pos.x > play_w - edge or p1_pos.y < edge or p1_pos.y > play_h - edge:
        return 1.0
    if p2_pos.x < edge or p2_pos.x > play_w - edge or p2_pos.y < edge or p2_pos.y > play_h - edge:
        return 1.0
    dx = p1_pos.x - p2_pos.x
    dy = p1_pos.y - p2_pos.y
    if dx * dx + dy * dy <= config.proximity_bonus_radius * config.proximity_bonus_radius:
        return config.proximity_bonus_multiplier
    return 1.0


class ScoreLedger:
    """Mutable accumulator of personal + team scores. The level scene
    holds one of these and calls `award` on each enemy kill."""

    __slots__ = ("_p1", "_p2", "_team")

    def __init__(self) -> None:
        self._p1: int = 0
        self._p2: int = 0
        self._team: int = 0

    def award(self, slot: PlayerSlot, base_points: int, *, multiplier: float = 1.0) -> int:
        """Award `base_points * multiplier` to `slot`'s personal score and
        the team total. Returns the actual points awarded (after
        `math.floor` and clamping)."""
        if base_points < 0:
            raise ValueError(f"base_points must be ≥ 0, got {base_points}")
        if multiplier < 0.0:
            raise ValueError(f"multiplier must be ≥ 0, got {multiplier}")
        awarded = math.floor(base_points * multiplier)
        if slot == P1:
            self._p1 += awarded
        elif slot == P2:
            self._p2 += awarded
        else:
            raise ValueError(f"unknown slot: {slot}")
        self._team += awarded
        return awarded

    def snapshot(self) -> ScoreSnapshot:
        return ScoreSnapshot(team=self._team, p1=self._p1, p2=self._p2)
