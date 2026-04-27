"""HUD-facing view of coop session state.

The Hud module duck-types this object via a structural shape (lives,
bombs, weapon_level, score on each player; team_score on the root).
We materialise that snapshot once per render frame from the canonical
session + powerup state, so the HUD doesn't reach into core internals.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HudPlayerStats:
    lives: int
    bombs: int
    weapon_level: int
    score: int
    # Active drone count (0..2) — task #10. Surfaced so the HUD can
    # render a small "Drones: N" line per player.
    drones: int = 0


@dataclass(frozen=True, slots=True)
class HudCoopState:
    team_score: int
    p1: HudPlayerStats
    p2: HudPlayerStats
