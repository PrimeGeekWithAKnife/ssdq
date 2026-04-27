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

    # Scratch flags for Boot → Title → Level transitions to know what to do.
    asset_loaded_levels: set[int] = field(default_factory=set)
