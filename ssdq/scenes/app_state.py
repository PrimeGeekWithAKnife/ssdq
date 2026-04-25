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

    # Scratch flags for Boot → Title → Level transitions to know what to do.
    asset_loaded_levels: set[int] = field(default_factory=set)
