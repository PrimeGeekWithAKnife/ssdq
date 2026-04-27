"""Cross-scene application state.

The scene stack passes `AppState` around so each scene can construct its
successor without re-loading the content bundle or re-instantiating the
audio bus. Stored as an ECS resource on `World`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ssdq.core.content.loader import ContentBundle
from ssdq.core.coop.options import CoopOptions
from ssdq.core.replay import ReplayRecorder
from ssdq.platform.audio import AudioBus


@dataclass
class AppState:
    """Mutable session-scope state shared between scenes."""

    content: ContentBundle
    audio: AudioBus
    options: CoopOptions
    current_level: int = 1
    recorder: ReplayRecorder | None = None
    last_team_score: int = 0
    last_p1_score: int = 0
    last_p2_score: int = 0
    completed_level: bool = False

    # Bombs awarded by an inter-level scene (DockingScene) that the next
    # LevelScene should add on top of the ship's `starting_bombs` baseline
    # in `enter()`. Reset to 0 once consumed so re-entering Title → Level
    # without another docking sequence doesn't repeatedly grant bonuses.
    bomb_bonus_pending: int = 0

    # Shield charges queued by the resupply scene (DockingScene). Until
    # the equippable-shield power-up (#4) lands and the level scene
    # tracks ``shield_charges`` per slot, this counter is staged but
    # not yet consumed by LevelScene.enter — wire it up in #4. Reset
    # to 0 on consumption (one-shot, same pattern as bomb_bonus_pending).
    # TODO(#4): consume in LevelScene.enter once equippable shields land.
    shield_charge_pending: int = 0

    # Weapon tier carried across LEVEL boundaries. Kid playtest 2026-04-27:
    # "Your weapon level is retained between level transitions." The level
    # scene captures each player's current weapon level on `exit()` and the
    # next LevelScene seeds the player's PlayerPowerupState with it on
    # `enter()` (clamped to the tree's max). Empty dict ⇒ start at base
    # tier (level 0). Per-life death reset INSIDE a level still happens —
    # this only persists across the LevelScene → LevelCompleteScene →
    # DockingScene → next LevelScene seam. Keyed by player slot index
    # (0 = P1, 1 = P2) so the dict is JSON/replay-friendly without
    # leaking the PlayerSlot class.
    last_weapon_tiers: dict[int, int] = field(default_factory=dict)

    # Scratch flags for Boot → Title → Level transitions to know what to do.
    asset_loaded_levels: set[int] = field(default_factory=set)
