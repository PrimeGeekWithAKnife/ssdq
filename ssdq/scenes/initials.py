"""Three-letter initials entry — classic arcade wheel.

Pushed by VictoryScene when the team score qualifies for the top 10.
Stick-Y (or D-pad) cycles the current letter A..Z + space.
Stick-X moves to the previous/next letter slot. CONFIRM submits.

On submit: the InitialsScene calls a caller-provided callback with
the entered initials, then returns a SceneTransition (typically a
Replace into LeaderboardScene). Keeping the side-effect on the
caller side means this scene is reusable for non-victory scoring
contexts later if we want.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (4, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_LETTER_COLOUR = (220, 230, 255)
_LETTER_ACTIVE = (255, 255, 255)
_BOX_OUTLINE = (130, 150, 180)
_BOX_OUTLINE_ACTIVE = (255, 240, 120)
_HINT_COLOUR = (130, 150, 180)

_NAV_THRESHOLD = 0.5
# A..Z + space — 27 cycle positions.
_LETTERS: tuple[str, ...] = (*(chr(ord("A") + i) for i in range(26)), " ")


class InitialsScene(Scene):
    """3-letter initials wheel for top-score entry."""

    __slots__ = (
        "_app",
        "_body_font",
        "_hint_font",
        "_letter_font",
        "_on_submit",
        "_prev_confirm",
        "_prev_fire",
        "_prev_x",
        "_prev_y",
        "_score",
        "_slot_index",
        "_slots",
        "_title_font",
    )

    def __init__(
        self,
        app: AppState,
        score: int,
        on_submit: Callable[[str], SceneTransition | None],
    ) -> None:
        self._app = app
        self._score = score
        self._on_submit = on_submit
        # Three letter indices into _LETTERS. Default to "AAA".
        self._slots: list[int] = [0, 0, 0]
        self._slot_index: int = 0
        self._prev_x: float = 0.0
        self._prev_y: float = 0.0
        # Held-fire blocker — without rising-edge detection, FIRE held
        # from the upstream VictoryScene auto-submits "AAA" on the very
        # first tick of this scene. Kid playtest 2026-05-08 #3.
        self._prev_fire: bool = True
        self._prev_confirm: bool = True
        self._title_font: pygame.font.Font | None = None
        self._letter_font: pygame.font.Font | None = None
        self._body_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 64, bold=True)
        self._letter_font = pygame.font.SysFont(None, 128, bold=True)
        self._body_font = pygame.font.SysFont(None, 32)
        self._hint_font = pygame.font.SysFont(None, 24)

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        # Either pad navigates — typical for handing the controller
        # over to whichever player is faster off the mark.
        fire_now = inputs[0].fire or inputs[1].fire
        confirm_now = inputs[0].confirm or inputs[1].confirm
        rising_fire = fire_now and not self._prev_fire
        rising_confirm = confirm_now and not self._prev_confirm
        self._prev_fire = fire_now
        self._prev_confirm = confirm_now
        for inp in inputs:
            x = inp.move.x
            y = inp.move.y
            # Edge-triggered horizontal: change slot.
            if x > _NAV_THRESHOLD and self._prev_x <= _NAV_THRESHOLD:
                self._slot_index = (self._slot_index + 1) % 3
            elif x < -_NAV_THRESHOLD and self._prev_x >= -_NAV_THRESHOLD:
                self._slot_index = (self._slot_index - 1) % 3
            # Edge-triggered vertical: cycle current letter.
            if y > _NAV_THRESHOLD and self._prev_y <= _NAV_THRESHOLD:
                self._slots[self._slot_index] = (
                    self._slots[self._slot_index] + 1
                ) % len(_LETTERS)
            elif y < -_NAV_THRESHOLD and self._prev_y >= -_NAV_THRESHOLD:
                self._slots[self._slot_index] = (
                    self._slots[self._slot_index] - 1
                ) % len(_LETTERS)
            self._prev_x = x
            self._prev_y = y
        if rising_fire or rising_confirm:
            initials = "".join(_LETTERS[i] for i in self._slots)
            return self._on_submit(initials)
        return None

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        surface.fill(_BG_COLOUR)
        if (
            self._title_font is None
            or self._letter_font is None
            or self._body_font is None
            or self._hint_font is None
        ):
            return
        w, h = surface.get_size()
        title = self._title_font.render("NEW HIGH SCORE!", True, _TITLE_COLOUR)
        surface.blit(title, title.get_rect(center=(w // 2, h // 4)))
        score_line = self._body_font.render(
            f"Score: {self._score:08d}", True, _LETTER_COLOUR
        )
        surface.blit(score_line, score_line.get_rect(center=(w // 2, h // 4 + 64)))
        # Three letter boxes, centred horizontally.
        box_w, box_h = 96, 128
        gap = 24
        total_w = box_w * 3 + gap * 2
        start_x = (w - total_w) // 2
        y = h // 2 - 20
        for i, letter_idx in enumerate(self._slots):
            x = start_x + i * (box_w + gap)
            outline = _BOX_OUTLINE_ACTIVE if i == self._slot_index else _BOX_OUTLINE
            colour = _LETTER_ACTIVE if i == self._slot_index else _LETTER_COLOUR
            pygame.draw.rect(surface, outline, (x, y, box_w, box_h), width=3)
            ch = _LETTERS[letter_idx]
            r = self._letter_font.render(ch if ch != " " else "_", True, colour)
            surface.blit(r, r.get_rect(center=(x + box_w // 2, y + box_h // 2)))
        hints = [
            "STICK UP/DOWN to change letter",
            "STICK LEFT/RIGHT to move between letters",
            "FIRE to confirm",
        ]
        hy = h - 24 - len(hints) * 28
        for line in hints:
            r = self._hint_font.render(line, True, _HINT_COLOUR)
            surface.blit(r, r.get_rect(center=(w // 2, hy)))
            hy += 28

    def exit(self, world: World) -> None:
        return None
