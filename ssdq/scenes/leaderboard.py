"""High-scores table — pushed from Title or after victory.

Reads ``ssdq.core.leaderboard`` on enter so the scene reflects the
saved state at display time. If a row was just added (passed via
``highlight_initials`` + ``highlight_ts``), it's drawn in a brighter
colour so the kid can find their entry immediately.

CONFIRM/FIRE/CANCEL all return to the title scene.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.leaderboard import DEFAULT_PATH, MAX_ENTRIES, Entry, load
from ssdq.core.scene import Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (4, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_HEADER_COLOUR = (180, 200, 230)
_ROW_COLOUR = (220, 230, 255)
_HIGHLIGHT_COLOUR = (255, 240, 120)
_HINT_COLOUR = (130, 150, 180)
_EMPTY_COLOUR = (100, 110, 130)


class LeaderboardScene(Scene):
    """Displays the top-N scores. Returns to title on any confirm/cancel."""

    __slots__ = (
        "_app",
        "_body_font",
        "_entries",
        "_header_font",
        "_highlight_initials",
        "_highlight_ts",
        "_hint_font",
        "_prev_fire",
        "_storage_path",
        "_title_font",
    )

    def __init__(
        self,
        app: AppState,
        *,
        highlight_initials: str | None = None,
        highlight_ts: str | None = None,
        storage_path: Path = DEFAULT_PATH,
    ) -> None:
        self._app = app
        self._highlight_initials = highlight_initials
        self._highlight_ts = highlight_ts
        self._storage_path = storage_path
        self._entries: list[Entry] = []
        self._prev_fire = True  # Same trick as TitleScene — block instant exit.
        self._title_font: pygame.font.Font | None = None
        self._header_font: pygame.font.Font | None = None
        self._body_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 72, bold=True)
        self._header_font = pygame.font.SysFont(None, 28, bold=True)
        self._body_font = pygame.font.SysFont(None, 36)
        self._hint_font = pygame.font.SysFont(None, 24)
        self._entries = load(self._storage_path)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        fire_now = inputs[0].fire or inputs[1].fire
        rising_fire = fire_now and not self._prev_fire
        self._prev_fire = fire_now
        if (
            inputs[0].confirm or inputs[1].confirm
            or inputs[0].cancel or inputs[1].cancel
            or rising_fire
        ):
            from ssdq.scenes.title import TitleScene

            return Replace(scene=TitleScene(self._app))
        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if (
            self._title_font is None
            or self._header_font is None
            or self._body_font is None
            or self._hint_font is None
        ):
            return
        w, h = surface.get_size()
        title = self._title_font.render("HIGH SCORES", True, _TITLE_COLOUR)
        surface.blit(title, title.get_rect(center=(w // 2, 80)))
        # Column layout — RANK (left), NAME (centre-left), SCORE (right).
        row_w = 720
        x0 = (w - row_w) // 2
        rank_x = x0 + 60
        name_x = x0 + 200
        score_x = x0 + row_w - 40  # right-aligned anchor
        y = 170
        header = self._header_font.render(
            "RANK     NAME              SCORE", True, _HEADER_COLOUR
        )
        surface.blit(header, header.get_rect(midleft=(rank_x - 30, y)))
        y += 36
        if not self._entries:
            r = self._body_font.render("(no scores yet — be the first!)", True, _EMPTY_COLOUR)
            surface.blit(r, r.get_rect(center=(w // 2, y + 80)))
        else:
            for i, e in enumerate(self._entries[:MAX_ENTRIES]):
                colour = (
                    _HIGHLIGHT_COLOUR
                    if (
                        e.initials == self._highlight_initials
                        and e.ts == self._highlight_ts
                    )
                    else _ROW_COLOUR
                )
                rank_r = self._body_font.render(f"{i + 1:>2}.", True, colour)
                name_r = self._body_font.render(e.initials, True, colour)
                score_r = self._body_font.render(f"{e.score:08d}", True, colour)
                surface.blit(rank_r, rank_r.get_rect(midleft=(rank_x, y)))
                surface.blit(name_r, name_r.get_rect(midleft=(name_x, y)))
                surface.blit(score_r, score_r.get_rect(midright=(score_x, y)))
                y += 40
        hint = self._hint_font.render("FIRE to return to title", True, _HINT_COLOUR)
        surface.blit(hint, hint.get_rect(center=(w // 2, h - 60)))

    def exit(self, world: World) -> None:
        return None
