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
# 120 ticks = 2.0s at 60Hz — held-fire-from-victory-screen absorption.
# Kept in sync with VictoryScene / LevelCompleteScene.
_LOCKOUT_TICKS: int = 120
# Auto-repeat tuning for the letter wheel: a fresh push fires immediately,
# then the user must hold for the delay before auto-repeat kicks in at
# the configured interval. Calibrated for kid playtest 2026-05-09 — the
# previous shared-prev_y bug fired at 60Hz which made letters fly past
# unreadably; a tap should land precisely on the next letter, a sustained
# hold should walk through the alphabet in 4–5s.
_AUTO_REPEAT_DELAY_TICKS: int = 24  # ~400ms
_AUTO_REPEAT_INTERVAL_TICKS: int = 8  # ~7Hz once auto-repeat engages


class InitialsScene(Scene):
    """3-letter initials wheel for top-score entry."""

    __slots__ = (
        "_app",
        "_body_font",
        "_hint_font",
        "_letter_font",
        "_lockout_remaining",
        "_on_submit",
        "_prev_submit",
        "_score",
        "_slot_index",
        "_slots",
        "_title_font",
        "_x_dir",
        "_y_dir",
        "_y_hold_ticks",
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
        # Stick-direction tracking for letter-wheel auto-repeat. _y_dir is
        # -1 / 0 / +1, _y_hold_ticks counts consecutive ticks held in the
        # same non-zero direction. _x_dir uses the same encoding but slot
        # navigation is rising-edge only (3 slots — no auto-repeat needed).
        self._y_dir: int = 0
        self._y_hold_ticks: int = 0
        self._x_dir: int = 0
        self._lockout_remaining: int = _LOCKOUT_TICKS
        self._prev_submit: bool = True
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
        # Combine both pads into one logical input — whichever pad has
        # the larger stick magnitude on each axis drives the wheel.
        # Critical: do NOT iterate over both pads with shared prev-state
        # (the previous implementation reset prev_y on every neutral
        # tick from the second pad, causing held-stick to fire at 60Hz —
        # kid playtest 2026-05-09 saw "KAR" become "K" because letters
        # flew past faster than the eye could read).
        x = inputs[0].move.x if abs(inputs[0].move.x) > abs(inputs[1].move.x) else inputs[1].move.x
        y = inputs[0].move.y if abs(inputs[0].move.y) > abs(inputs[1].move.y) else inputs[1].move.y

        # Slot nav (X axis): rising-edge only — only 3 slots so auto-repeat
        # would feel jumpy.
        x_dir = 1 if x > _NAV_THRESHOLD else (-1 if x < -_NAV_THRESHOLD else 0)
        if x_dir != self._x_dir:
            self._x_dir = x_dir
            if x_dir != 0:
                self._slot_index = (self._slot_index + x_dir) % 3

        # Letter nav (Y axis): immediate fire on direction change, then
        # auto-repeat after a hold delay. Tap → one letter; hold → ~7Hz
        # walk after a 400ms grace period.
        y_dir = 1 if y > _NAV_THRESHOLD else (-1 if y < -_NAV_THRESHOLD else 0)
        fire_y = 0
        if y_dir != self._y_dir:
            self._y_dir = y_dir
            self._y_hold_ticks = 0
            if y_dir != 0:
                fire_y = y_dir
        elif y_dir != 0:
            self._y_hold_ticks += 1
            if self._y_hold_ticks >= _AUTO_REPEAT_DELAY_TICKS:
                if (
                    self._y_hold_ticks - _AUTO_REPEAT_DELAY_TICKS
                ) % _AUTO_REPEAT_INTERVAL_TICKS == 0:
                    fire_y = y_dir
        if fire_y != 0:
            self._slots[self._slot_index] = (
                self._slots[self._slot_index] + fire_y
            ) % len(_LETTERS)
        # SUBMIT: post-lockout rising-edge FIRE only. Lockout absorbs the
        # held-fire from the upstream VictoryScene transition; FIRE is
        # the universal button (kid playtest 2026-05-09 final).
        fire_now = inputs[0].fire or inputs[1].fire
        if self._lockout_remaining > 0:
            self._lockout_remaining -= 1
            self._prev_submit = fire_now
            return None
        rising = fire_now and not self._prev_submit
        self._prev_submit = fire_now
        if rising:
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
            "FIRE to submit",
        ]
        hy = h - 24 - len(hints) * 28
        for line in hints:
            r = self._hint_font.render(line, True, _HINT_COLOUR)
            surface.blit(r, r.get_rect(center=(w // 2, hy)))
            hy += 28

    def exit(self, world: World) -> None:
        return None
