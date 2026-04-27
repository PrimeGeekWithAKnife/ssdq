"""Boot scene: load content + warm assets, then transition to Intro.

Single-tick scene. Content is loaded eagerly in `__init__` so that any
failure shows up immediately (rather than mid-game). Audio assets are
loaded here so the first SFX hit doesn't stutter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState
from ssdq.scenes.intro import IntroScene

_CONTENT_DIR = Path("content")
_ASSETS_DIR = _CONTENT_DIR / "assets"


class BootScene(Scene):
    """Load content + register audio assets. Transitions to Intro on first tick."""

    __slots__ = ("_app", "_loaded")

    def __init__(self, app: AppState) -> None:
        self._app = app
        self._loaded = False

    def enter(self, world: World) -> None:
        # Resource registration: anything that lives for the session.
        world.insert_resource(self._app)
        self._register_audio()
        self._loaded = True

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        if not self._loaded:
            return None
        return Replace(scene=IntroScene(self._app))

    def render(self, world: World, surface: Any, alpha: float) -> None:
        # One frame of black; immediately replaced by Intro.
        if isinstance(surface, pygame.Surface):
            surface.fill((0, 0, 0))

    def exit(self, world: World) -> None:
        return None

    # ───────── helpers ─────────

    def _register_audio(self) -> None:
        bus = self._app.audio
        sfx_dir = _ASSETS_DIR / "audio" / "sfx"
        music_dir = _ASSETS_DIR / "audio" / "music"
        # Names match what level/coop systems play. Missing files warn
        # once and are silently no-op'd by AudioBus.
        for name in ("laser", "hit", "explosion", "pickup", "pause", "powerup", "bomb"):
            bus.load_sfx(name, str(sfx_dir / f"{name}.ogg"))
        # Music: one track per level (1..5), one per boss (1..5), plus the
        # calmer ``resupply`` track played by DockingScene between levels.
        # Levels 3..5 don't yet have full content but the placeholder
        # tracks are pre-rendered (tools/generate_music.py); missing files
        # are silently no-op'd by AudioBus so the registration is forward
        # -compatible with future level content.
        for i in range(1, 6):
            bus.load_music(f"level_{i:02d}", str(music_dir / f"level_{i:02d}.ogg"))
            bus.load_music(f"boss_{i:02d}", str(music_dir / f"boss_{i:02d}.ogg"))
        bus.load_music("resupply", str(music_dir / "resupply.ogg"))
