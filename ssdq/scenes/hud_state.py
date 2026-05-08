"""HUD-facing view of coop session state.

The Hud module duck-types this object via a structural shape (lives,
bombs, weapon_level, score on each player; team_score on the root).
We materialise that snapshot once per render frame from the canonical
session + powerup state, so the HUD doesn't reach into core internals.

Task #9 added:
  * ``shield_charges`` / ``missile_level`` / ``drones`` on each player
    panel — sourced by the level scene's ``_build_hud_state``. The HUD
    draws them only if non-zero so the panel stays clean for fresh
    sessions. ``missile_level`` was originally a charge count; the
    2026-04-30 redesign changed missiles to tier-based auto-fire so the
    HUD now shows the tier (1..MISSILE_LEVEL_CAP) instead.
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
    missile_level: int = 0  # 0 = no missiles; 1..5 = auto-fire tier
    # Active drone count (0..2) — task #10. Surfaced so the HUD can
    # render a small "Drones: N" line per player.
    drones: int = 0


@dataclass(frozen=True, slots=True)
class HudCoopState:
    team_score: int
    p1: HudPlayerStats
    p2: HudPlayerStats
    # Solo-play flag (added 2026-05-08). When True the HUD suppresses
    # the P2 panel — the P2 ship was never spawned in single-player
    # mode so its lives/score column is meaningless. Default False
    # preserves the existing two-panel layout.
    single_player: bool = False
