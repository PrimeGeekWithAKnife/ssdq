"""Level-complete scene.

Banner + final scores, then routes to the next stop in the multi-level
arc:

* If the level just completed has a successor in the content bundle,
  advance ``app.current_level`` and push the next LevelScene. Eventually
  this should route through a DockingScene between levels (parallel
  scene-builder agent owns ``ssdq/scenes/docking.py``); until that file
  lands we go straight to the next LevelScene with a TODO marker.
* If there is no next level (we just cleared the final level the build
  ships with), fall back to TitleScene — the player has reached the cap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (4, 16, 8)
_TITLE_COLOUR = (140, 255, 200)
_TEXT_COLOUR = (220, 240, 230)


class LevelCompleteScene(Scene):
    """Banner + final scores. Routes to next level or back to Title."""

    __slots__ = ("_app", "_body_font", "_completed_level_index", "_title_font")

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
        if not (inputs[0].confirm or inputs[1].confirm or inputs[0].fire or inputs[1].fire):
            return None
        return Replace(scene=self._next_scene())

    def _next_scene(self) -> Scene:
        """Decide what plays after the post-level banner.

        Hand-rolls the routing rather than chaining schedule data — the
        multi-level arc only has 2 levels right now, the rules are short,
        and threading config through here for 2 levels would be more
        ceremony than it's worth.
        """
        completed = self._completed_level_index
        bundle = self._app.content
        next_index = completed + 1
        if next_index in bundle.levels:
            # Advance the session pointer + queue the next level.
            self._app.current_level = next_index
            # TODO(scene-builder): once ssdq/scenes/docking.py lands, route
            # the inter-level transition through DockingScene (which then
            # pushes LevelScene(self._app, level_index=next_index)).
            try:
                from ssdq.scenes import docking as _docking  # type: ignore[attr-defined]

                # Cast to a 2-arg callable returning a Scene so the
                # parallel agent can vary DockingScene's signature
                # without forcing a typing-tax round-trip on us.
                docking_factory = cast(
                    Callable[[AppState, int], Scene],
                    _docking.DockingScene,
                )
                return docking_factory(self._app, next_index)
            except (ImportError, AttributeError):
                from ssdq.scenes.level import LevelScene

                return LevelScene(self._app, level_index=next_index)
        # No next level — we've capped. Bounce back to Title.
        from ssdq.scenes.title import TitleScene

        return TitleScene(self._app)

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
            "Press FIRE to continue",
        ]
        y = h // 2
        for line in lines:
            r = self._body_font.render(line, True, _TEXT_COLOUR)
            surface.blit(r, r.get_rect(center=(w // 2, y)))
            y += r.get_height() + 8

    def exit(self, world: World) -> None:
        return None
