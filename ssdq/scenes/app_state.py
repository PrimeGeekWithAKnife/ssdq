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
from ssdq.core.types import PlayerSlot
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

    # Scratch flags for Boot → Title → Level transitions to know what to do.
    asset_loaded_levels: set[int] = field(default_factory=set)

    # Equippable inventories — populated by pickups, consumed when the
    # player presses the corresponding gamepad / keyboard button. Per-slot
    # so each player has their own pool. Defaults to 0 if a slot has no
    # entry yet (read via the helper methods below).
    shield_charges: dict[PlayerSlot, int] = field(default_factory=dict)
    missile_charges: dict[PlayerSlot, int] = field(default_factory=dict)

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
