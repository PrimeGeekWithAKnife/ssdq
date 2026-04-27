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
        bus.load_music("level_01", str(music_dir / "level_01.ogg"))
        bus.load_music("boss_01", str(music_dir / "boss_01.ogg"))
