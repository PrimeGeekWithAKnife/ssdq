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


def _zero_drone_pending() -> dict[PlayerSlot, int]:
    return {P1: 0, P2: 0}


def _zero_drone_config() -> dict[PlayerSlot, int]:
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

    # Scratch flags for Boot → Title → Level transitions to know what to do.
    asset_loaded_levels: set[int] = field(default_factory=set)

    # ───────── drones (task #10) ─────────
    #
    # ``drones_pending`` is a per-slot count of drone power-ups picked up
    # but not yet materialised into entities. The level scene drains one
    # at a time on tick (capped at 2 active drones per slot). Naming
    # coordinated with POWERUP-INFRA + EQUIPPABLE.
    drones_pending: dict[PlayerSlot, int] = field(default_factory=_zero_drone_pending)
    # Active drone-formation index per slot (Tight=0, Spread=1, Trailing=2,
    # Vanguard=3). Persisted across LevelScene re-enters so the kid's chosen
    # formation isn't lost between levels. Cycled via PlayerInput.drone_cycle.
    drone_config: dict[PlayerSlot, int] = field(default_factory=_zero_drone_config)
