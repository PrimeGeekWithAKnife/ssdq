"""Level-complete scene.

Banner + final scores, then routes to the next stop in the multi-level arc:

* If the level just completed has a successor in the content bundle,
  advance ``app.current_level`` and route through DockingScene (the
  supply-ship cinematic that grants +2 bombs to each player).
  DockingScene then auto-transitions onward to LevelScene with the new
  level index.
* If there is no next level, route through DockingScene anyway —
  DockingScene's own next-scene logic falls back to TitleScene when no
  successor level exists.
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState
from ssdq.scenes.docking import DockingScene

_BG_COLOUR = (4, 16, 8)
_TITLE_COLOUR = (140, 255, 200)
_TEXT_COLOUR = (220, 240, 230)


class LevelCompleteScene(Scene):
    """Banner + final scores. Routes to next level via DockingScene."""

    __slots__ = (
        "_app",
        "_body_font",
        "_completed_level_index",
        "_title_font",
    )

    def __init__(self, app: AppState, completed_level_index: int | None = None) -> None:
        self._app = app
        # If the caller doesn't tell us which level was completed (older
        # callers, or scenes that don't track it), assume it's the
        # current one.
        self._completed_level_index: int = (
            completed_level_index if completed_level_index is not None else app.current_level
        )
        self._title_font: pygame.font.Font | None = None
        self._body_font: pygame.font.Font | None = None

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 84, bold=True)
        self._body_font = pygame.font.SysFont(None, 32)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        # END-SCREEN ADVANCE = START button only (kid playtest 2026-05-08).
        # The kid is mashing FIRE through the whole boss fight; even
        # rising-edge FIRE detection trips within 200ms because they
        # naturally release-and-press. START is never held during
        # gameplay (pause toggle uses it but releases immediately) so
        # it cleanly distinguishes "I want to advance" from "I'm still
        # firing reflexively". inp.pause is already edge-triggered.
        if not (inputs[0].pause or inputs[1].pause):
            return None
        # Advance the session pointer to the next level (if any) BEFORE
        # entering the docking cinematic, so DockingScene knows where to
        # send us next. If we just cleared the FINAL level, route to the
        # VictoryScene instead — kid playtest 2026-04-28: "after the final
        # boss the game does not end".
        bundle = self._app.content
        next_index = self._completed_level_index + 1
        if next_index not in bundle.levels:
            from ssdq.scenes.victory import VictoryScene

            return Replace(scene=VictoryScene(self._app))
        self._app.current_level = next_index
        return Replace(scene=DockingScene(self._app))

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if self._title_font is None or self._body_font is None:
            return
        w, h = surface.get_size()
        banner = self._title_font.render("LEVEL CLEAR", True, _TITLE_COLOUR)
        surface.blit(banner, banner.get_rect(center=(w // 2, h // 3)))
        lines = [
            f"Team score: {self._app.last_team_score:08d}",
            f"P1: {self._app.last_p1_score:08d}    P2: {self._app.last_p2_score:08d}",
            "",
            "Press START to continue",
        ]
        y = h // 2
        for line in lines:
            r = self._body_font.render(line, True, _TEXT_COLOUR)
            surface.blit(r, r.get_rect(center=(w // 2, y)))
            y += r.get_height() + 8

    def exit(self, world: World) -> None:
        return None
