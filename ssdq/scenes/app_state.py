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
from ssdq.core.types import P1, P2, PlayerSlot
from ssdq.platform.audio import AudioBus


def _zero_per_slot() -> dict[PlayerSlot, int]:
    """Default factory for per-slot inventory counters."""
    return {P1: 0, P2: 0}


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

    # Task #9 — equippable / agent-owned inventories. The level scene
    # increments these when the matching pickup is collected; the
    # downstream agents (DRONE, EQUIPPABLE) consume them once they
    # land. They survive across LevelScene boundaries within a session.
    drones_pending: dict[PlayerSlot, int] = field(default_factory=_zero_per_slot)
    missile_charges: dict[PlayerSlot, int] = field(default_factory=_zero_per_slot)
    shield_charges: dict[PlayerSlot, int] = field(default_factory=_zero_per_slot)

    # One-shot shield charge bonus granted by DockingScene. Drained into
    # `shield_charges` on next LevelScene.enter (same pattern as
    # bomb_bonus_pending). Distinct from `shield_charges` because it
    # represents the "resupply gave me +1" signal, not the running
    # inventory.
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

    # Active drone-formation index per slot (Tight=0, Spread=1, Trailing=2,
    # Vanguard=3). Persisted across LevelScene re-enters so the kid's chosen
    # formation isn't lost between levels. Cycled via PlayerInput.drone_cycle.
    drone_config: dict[PlayerSlot, int] = field(default_factory=_zero_per_slot)

    # ───────── equippable helpers ─────────

    def get_shield_charges(self, slot: PlayerSlot) -> int:
        return self.shield_charges.get(slot, 0)

    def get_missile_charges(self, slot: PlayerSlot) -> int:
        return self.missile_charges.get(slot, 0)

    def add_shield_charge(self, slot: PlayerSlot, n: int = 1) -> None:
        self.shield_charges[slot] = self.get_shield_charges(slot) + n

    def add_missile_charge(self, slot: PlayerSlot, n: int = 1) -> None:
        self.missile_charges[slot] = self.get_missile_charges(slot) + n

    def consume_shield_charge(self, slot: PlayerSlot) -> bool:
        """Decrement and return True if a charge was available."""
        cur = self.get_shield_charges(slot)
        if cur <= 0:
            return False
        self.shield_charges[slot] = cur - 1
        return True

    def consume_missile_charge(self, slot: PlayerSlot) -> bool:
        """Decrement and return True if a charge was available."""
        cur = self.get_missile_charges(slot)
        if cur <= 0:
            return False
        self.missile_charges[slot] = cur - 1
        return True
