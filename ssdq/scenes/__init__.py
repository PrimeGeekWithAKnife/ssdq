"""Scene implementations for the SSDQ vertical slice.

Scene state machine per spec §4.4 (Intro is a thin pre-roll between Boot
and Title; the rest matches the spec verbatim):

    Boot ──▶ Intro ──▶ Title ──▶ Level ──▶ LevelComplete ──▶ Credits/Quit
                        ▲          │             ▲
                        │          ▼             │
                        └──── GameOver ◀─────────┘

Each scene exposes enter/tick/render/exit per `core/scene.Scene`. Pause is
the global modal flag on `SceneStack`, not a scene.

Builder W (this layer) wires the four core modules (collision, waves,
powerups, coop) into a runnable game. The Level scene is the engine
room; the others are thin chrome.
"""

from ssdq.scenes.app_state import AppState
from ssdq.scenes.boot import BootScene
from ssdq.scenes.docking import DockingScene
from ssdq.scenes.game_over import GameOverScene
from ssdq.scenes.hud_state import HudCoopState, HudPlayerStats
from ssdq.scenes.intro import IntroScene
from ssdq.scenes.level import LevelScene
from ssdq.scenes.level_complete import LevelCompleteScene
from ssdq.scenes.title import TitleScene

__all__ = [
    "AppState",
    "BootScene",
    "DockingScene",
    "GameOverScene",
    "HudCoopState",
    "HudPlayerStats",
    "IntroScene",
    "LevelCompleteScene",
    "LevelScene",
    "TitleScene",
]
