"""Keyboard input provider — fallback for headless / VM dev environments.

Spec §8.3: gated behind ``SSDQ_KEYBOARD=1``; never used in production.

Bindings (per builder brief):

==========  =================================================
Player      Keys
==========  =================================================
P1          WASD move, Space fire, LShift bomb, E shield,
            Q missile, Enter pause / confirm, Esc cancel
P2          Arrow keys move, RShift fire, RCtrl bomb, Tab pause
==========  =================================================

Both players share one keyboard, so genuine simultaneous co-op on this
provider is awkward — but the slice only needs it as a smoke-test path.
P2 doesn't get keyboard equippable bindings because the kid's playtest
machine is gamepad-only; the smoke-test pathway runs on P1 alone.
"""

from __future__ import annotations

import pygame

from ssdq.core.types import PlayerInput, PlayerSlot, Vec2


class _KeyState:
    """Per-player edge-detection bookkeeping (mirrors gamepad)."""

    __slots__ = (
        "prev_bomb",
        "prev_cancel",
        "prev_confirm",
        "prev_missile",
        "prev_pause",
        "prev_shield",
    )

    def __init__(self) -> None:
        self.prev_bomb: bool = False
        self.prev_pause: bool = False
        self.prev_confirm: bool = False
        self.prev_cancel: bool = False
        self.prev_shield: bool = False
        self.prev_missile: bool = False


# P1 binding: WASD + Space + LShift + Q + E + Enter + Esc.
_P1_UP = pygame.K_w
_P1_DOWN = pygame.K_s
_P1_LEFT = pygame.K_a
_P1_RIGHT = pygame.K_d
_P1_FIRE = pygame.K_SPACE
_P1_BOMB = pygame.K_LSHIFT
_P1_PAUSE = pygame.K_RETURN
_P1_CONFIRM = pygame.K_RETURN
_P1_CANCEL = pygame.K_ESCAPE
_P1_SHIELD = pygame.K_e
_P1_MISSILE = pygame.K_q

# P2 binding: arrows + RShift + RCtrl + Tab.
_P2_UP = pygame.K_UP
_P2_DOWN = pygame.K_DOWN
_P2_LEFT = pygame.K_LEFT
_P2_RIGHT = pygame.K_RIGHT
_P2_FIRE = pygame.K_RSHIFT
_P2_BOMB = pygame.K_RCTRL
_P2_PAUSE = pygame.K_TAB
# P2 doesn't really have menu buttons distinct from P1; the slice only ever
# needs P1 in menus, so we just reuse Tab for confirm and there's no cancel.
_P2_CONFIRM = pygame.K_TAB
_P2_CANCEL: int | None = None
# P2 has no keyboard equippable bindings (see module docstring).
_P2_SHIELD: int | None = None
_P2_MISSILE: int | None = None


class KeyboardProvider:
    """Polls the keyboard state and emits ``(P1 input, P2 input)``.

    Constructing this without an initialised display is fine under
    ``SDL_VIDEODRIVER=dummy``; pygame still tracks key state.
    """

    def __init__(self) -> None:
        if not pygame.get_init():
            pygame.init()
        # ``pygame.key.get_pressed`` requires the event queue to have been
        # pumped at least once; we do this on each poll.
        self._p1_state = _KeyState()
        self._p2_state = _KeyState()

    def poll(self) -> tuple[PlayerInput, PlayerInput]:
        # Pump events so key state is current. We don't otherwise care about
        # the events here — pygame's keystate machinery does the work.
        pygame.event.pump()
        keys = pygame.key.get_pressed()
        return (
            self._read(
                keys,
                self._p1_state,
                up=_P1_UP,
                down=_P1_DOWN,
                left=_P1_LEFT,
                right=_P1_RIGHT,
                fire=_P1_FIRE,
                bomb=_P1_BOMB,
                pause=_P1_PAUSE,
                confirm=_P1_CONFIRM,
                cancel=_P1_CANCEL,
                shield=_P1_SHIELD,
                missile=_P1_MISSILE,
            ),
            self._read(
                keys,
                self._p2_state,
                up=_P2_UP,
                down=_P2_DOWN,
                left=_P2_LEFT,
                right=_P2_RIGHT,
                fire=_P2_FIRE,
                bomb=_P2_BOMB,
                pause=_P2_PAUSE,
                confirm=_P2_CONFIRM,
                cancel=_P2_CANCEL,
                shield=_P2_SHIELD,
                missile=_P2_MISSILE,
            ),
        )

    @property
    def disconnected(self) -> PlayerSlot | None:
        # Keyboards don't disconnect mid-session in any practical sense.
        return None

    def clear_disconnected(self) -> None:  # pragma: no cover — no-op
        return None

    @staticmethod
    def _read(
        keys: pygame.key.ScancodeWrapper,
        state: _KeyState,
        *,
        up: int,
        down: int,
        left: int,
        right: int,
        fire: int,
        bomb: int,
        pause: int,
        confirm: int,
        cancel: int | None,
        shield: int | None,
        missile: int | None,
    ) -> PlayerInput:
        # Move axis: opposing keys cancel out, and the resulting vector is
        # normalised so diagonals match cardinal magnitudes (1.0).
        mx = (1.0 if keys[right] else 0.0) - (1.0 if keys[left] else 0.0)
        my = (1.0 if keys[down] else 0.0) - (1.0 if keys[up] else 0.0)
        move = Vec2(mx, my).normalised() if (mx or my) else Vec2(0.0, 0.0)

        held_bomb = bool(keys[bomb])
        held_pause = bool(keys[pause])
        held_confirm = bool(keys[confirm])
        held_cancel = bool(keys[cancel]) if cancel is not None else False
        held_shield = bool(keys[shield]) if shield is not None else False
        held_missile = bool(keys[missile]) if missile is not None else False

        bomb_edge = held_bomb and not state.prev_bomb
        pause_edge = held_pause and not state.prev_pause
        confirm_edge = held_confirm and not state.prev_confirm
        cancel_edge = held_cancel and not state.prev_cancel
        shield_edge = held_shield and not state.prev_shield
        missile_edge = held_missile and not state.prev_missile

        state.prev_bomb = held_bomb
        state.prev_pause = held_pause
        state.prev_confirm = held_confirm
        state.prev_cancel = held_cancel
        state.prev_shield = held_shield
        state.prev_missile = held_missile

        return PlayerInput(
            move=move,
            fire=bool(keys[fire]),
            bomb=bomb_edge,
            pause=pause_edge,
            confirm=confirm_edge,
            cancel=cancel_edge,
            shield=shield_edge,
            missile=missile_edge,
        )
