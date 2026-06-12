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
from ssdq.scenes.music_routing import LEVEL_VARIANT_SUFFIXES, MAX_MUSIC_LEVEL
from ssdq.scenes.title import TitleScene

_CONTENT_DIR = Path("content")
_ASSETS_DIR = _CONTENT_DIR / "assets"


class BootScene(Scene):
    """Load content + register audio assets. Transitions to Title on first tick.

    Post-2026-05-02 sequencing change: the intro story crawl now plays
    AFTER the player picks PLAY from the title menu rather than before
    the menu — see plan docs/plans/2026-05-02-playtest-followups.md
    (Task 8). That kept the intro's per-launch fixed cost off players
    who just wanted to jump straight back into a level.
    """

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
        return Replace(scene=TitleScene(self._app))

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
        for name in (
            "laser",
            "hit",
            "explosion",
            "pickup",
            "pause",
            "powerup",
            "bomb",
            "missile",
        ):
            bus.load_sfx(name, str(sfx_dir / f"{name}.ogg"))
        # Music: a POOL per level (base + _b/_c variants, levels 1..7 —
        # fun review 2026-06-12: one track per level was wearing thin),
        # one boss track per level, the ``hyperspace`` cue for the
        # post-L5 bonus ride, plus the calmer ``resupply`` track played
        # by DockingScene between levels. Missing files are silently
        # no-op'd by AudioBus (and excluded from has_music) so the
        # registration stays forward-compatible with future content.
        for i in range(1, MAX_MUSIC_LEVEL + 1):
            for suffix in LEVEL_VARIANT_SUFFIXES:
                name = f"level_{i:02d}{suffix}"
                bus.load_music(name, str(music_dir / f"{name}.ogg"))
            bus.load_music(f"boss_{i:02d}", str(music_dir / f"boss_{i:02d}.ogg"))
        bus.load_music("hyperspace", str(music_dir / "hyperspace.ogg"))
        bus.load_music("resupply", str(music_dir / "resupply.ogg"))
        # Cinematic intro cue, played once during the post-PLAY story
        # crawl (kid playtest 2026-05-02 #4 — "intro needs dramatic /
        # epic music").
        bus.load_music("intro_epic", str(music_dir / "intro_epic.ogg"))
