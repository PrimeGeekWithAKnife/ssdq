"""HUD-facing view of coop session state.

The Hud module duck-types this object via a structural shape (lives,
bombs, weapon_level, score on each player; team_score on the root).
We materialise that snapshot once per render frame from the canonical
session + powerup state, so the HUD doesn't reach into core internals.

Task #9 added:
  * ``shield_charges`` / ``missile_charges`` / ``drones`` on each player
    panel — sourced from ``AppState`` inventory counters by the level
    scene's ``_build_hud_state``. The HUD draws them only if non-zero so
    the panel stays clean for fresh sessions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HudPlayerStats:
    lives: int
    bombs: int
    weapon_level: int
    score: int
    # Inventory counters (read by Hud panel).
    shield_charges: int = 0
    missile_charges: int = 0
    drones: int = 0


@dataclass(frozen=True, slots=True)
class HudCoopState:
    team_score: int
    p1: HudPlayerStats
    p2: HudPlayerStats
