"""Title scene menu. Doubles as Lobby per spec §4.4.

Five options: 2 PLAYERS / 1 PLAYER / LEVELS / HIGH SCORES / SETTINGS.
Default selection is 2 PLAYERS so existing reflexes (mash FIRE → start
co-op) keep working; 1 PLAYER sits directly below it so a solo player
on a one-button pad can reach it with a single FIRE-hold cycle.

Three nav paths (any one is enough; first to work for the player's pad
wins):
1. Stick-Y / D-pad-as-hat — folded into PlayerInput.move by gamepad.py.
2. Raw-button polling — any pad button OTHER than the FIRE / CONFIRM /
   CANCEL bindings cycles forward.
3. FIRE hold-cycle — a sustained FIRE press past the hold threshold
   cycles once per press; this is the universal fallback for pads
   where (1) and (2) both fail (kid playtest 2026-05-23 — user's pad
   has only FIRE working reliably for menu navigation).

FIRE semantics: a quick tap (rising → falling edge before the hold
threshold) confirms the highlighted option; a long hold past the
threshold cycles the cursor and consumes the press so the release
doesn't also confirm.

The 1P / 2P split (added 2026-05-08) sets `app.single_player` before
routing to the campaign — LevelScene reads the flag and skips P2's
ship spawn + HUD column when solo.
"""

from __future__ import annotations

from typing import Any

import pygame

from ssdq.core.ecs import World
from ssdq.core.scene import Push, Quit, Replace, Scene, SceneTransition
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.scenes.app_state import AppState

_BG_COLOUR = (8, 8, 24)
_TITLE_COLOUR = (255, 240, 120)
_PROMPT_COLOUR = (200, 200, 200)
_PROMPT_SELECTED_COLOUR = (255, 240, 120)
_HINT_COLOUR = (130, 130, 150)

# Stick-Y rising-edge threshold for menu navigation.
_NAV_THRESHOLD = 0.5

_OPTION_ONE_PLAYER = "1 PLAYER"
_OPTION_TWO_PLAYERS = "2 PLAYERS"
_OPTION_LEVELS = "LEVELS"
_OPTION_SCORES = "HIGH SCORES"
_OPTION_SETTINGS = "SETTINGS"
# 2 PLAYERS first so a quick FIRE tap on launch confirms co-op (preserving
# the mash-to-play reflex), and a single FIRE-hold-cycle lands on 1 PLAYER.
# Pre-2026-05-23 ordering put 1 PLAYER first which made co-op the
# two-cycles-away option on broken-pad layouts.
_OPTIONS: tuple[str, ...] = (
    _OPTION_TWO_PLAYERS,
    _OPTION_ONE_PLAYER,
    _OPTION_LEVELS,
    _OPTION_SCORES,
    _OPTION_SETTINGS,
)
_DEFAULT_SELECTED_INDEX = 0  # 2 PLAYERS

# FIRE hold threshold: 30 ticks = 0.5s at 60Hz. A tap-release inside this
# window confirms; a hold past it cycles once. Mash-tap cycles are
# typically 100–200ms (well under 0.5s) so quick mashing never trips
# the cycle path. Tunable per kid playtest feedback.
_FIRE_HOLD_CYCLE_TICKS: int = 30


class TitleScene(Scene):
    """Five-option menu (1 PLAYER / 2 PLAYERS / LEVELS / HIGH SCORES / SETTINGS). Confirm activates the row."""

    __slots__ = (
        "_app",
        "_backdrop",
        "_fire_cycled_this_press",
        "_fire_hold_ticks",
        "_hint_font",
        "_pad_button_state",
        "_prev_fire",
        "_prev_y",
        "_prompt_font",
        "_selected_index",
        "_title_font",
    )

    def __init__(self, app: AppState) -> None:
        self._app = app
        self._title_font: pygame.font.Font | None = None
        self._prompt_font: pygame.font.Font | None = None
        self._hint_font: pygame.font.Font | None = None
        self._backdrop: pygame.Surface | None = None
        # Track previous-tick fire so we only transition on rising edge —
        # otherwise a player holding fire from the prior level would
        # immediately re-enter Level when GameOver bounces back to Title.
        self._prev_fire = True
        self._prev_y: float = 0.0
        self._selected_index: int = _DEFAULT_SELECTED_INDEX
        # Raw per-pad button state for binding-agnostic nav fallback.
        # Keyed by pygame instance id. Seeded in enter() so a button held
        # on scene entry doesn't fire a spurious rising edge on tick 0.
        self._pad_button_state: dict[int, list[bool]] = {}
        # FIRE hold-cycle state. _fire_hold_ticks counts how many
        # consecutive ticks FIRE has been held this press;
        # _fire_cycled_this_press latches once the threshold is crossed
        # so the eventual release does NOT also confirm.
        self._fire_hold_ticks: int = 0
        self._fire_cycled_this_press: bool = False

    def enter(self, world: World) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._title_font = pygame.font.SysFont(None, 96, bold=True)
        self._prompt_font = pygame.font.SysFont(None, 36)
        self._hint_font = pygame.font.SysFont(None, 24)
        # Seed the raw-button prev-state so any button held when the
        # title scene enters (e.g. FIRE held from boss kill on a route
        # back from GameOver) doesn't immediately fire a nav rising edge.
        self._pad_button_state = self._snapshot_pad_buttons()
        # Seed _prev_fire from the live FIRE button state. Previously this
        # was hardcoded True, which correctly blocked a held-from-prior
        # FIRE on the post-game-over route but ALSO blocked the very first
        # FIRE press on a fresh launch — leaving the kid stuck on the
        # default selection until they happened to release-press-release
        # (kid playtest 2026-05-23: "first run cannot change menu
        # selection; later, after I die and return, I can"). Reading the
        # actual button state here gives the right answer in both cases:
        # first launch with no button held → False → first press is a
        # rising edge → tracked.
        self._prev_fire = self._any_fire_button_held()
        # Painted Earth/moon/station backdrop (kid playtest 2026-05-02
        # #5). Generated by tools/gen_title_backdrop.py — load once on
        # enter, fall back to a flat fill if the asset isn't shipped.
        from pathlib import Path

        backdrop_path = Path("content/assets/sprites/backgrounds/title_main.png")
        if backdrop_path.is_file():
            try:
                surf = pygame.image.load(str(backdrop_path))
                if (
                    pygame.display.get_init()
                    and pygame.display.get_surface() is not None
                ):
                    surf = surf.convert()
                self._backdrop = surf
            except pygame.error:
                self._backdrop = None
        else:
            self._backdrop = None

    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        # Stick-Y rising-edge detection — same convention as SettingsScene.
        # Either player's stick can navigate the menu.
        y = inputs[0].move.y if abs(inputs[0].move.y) > abs(inputs[1].move.y) else inputs[1].move.y
        if y > _NAV_THRESHOLD and self._prev_y <= _NAV_THRESHOLD:
            self._selected_index = (self._selected_index + 1) % len(_OPTIONS)
        elif y < -_NAV_THRESHOLD and self._prev_y >= -_NAV_THRESHOLD:
            self._selected_index = (self._selected_index - 1) % len(_OPTIONS)
        self._prev_y = y

        # BINDING-AGNOSTIC NAV FALLBACK: any pad button other than the
        # ones bound to FIRE / CONFIRM / CANCEL on that pad cycles the
        # menu forward. Cheap HID pads (Zikway, Gamesir T4n Lite) place
        # buttons at unpredictable indices, so even our BOMB/SHIELD
        # action mappings often miss — the only reliable rule is "any
        # other button" (kid playtest 2026-05-09 follow-up: stick, hat
        # and BOMB/SHIELD nav all failed on the user's pad). Wraps so
        # one direction is enough to reach every option.
        if self._poll_any_nav_button():
            self._selected_index = (self._selected_index + 1) % len(_OPTIONS)

        # CANCEL on Title is the gamepad-friendly quit path. Without this
        # there's no way to exit the game from the title menu using a
        # controller (the X11/kmsdrm host has no Alt-F4 equivalent on
        # the Pi's TV launch). Kid playtest 2026-04-28: game would pin
        # the Pi at 100% CPU because the user couldn't quit cleanly.
        if inputs[0].cancel or inputs[1].cancel:
            return Quit()

        # FIRE hold-cycle / tap-confirm. A tap (release before the hold
        # threshold) confirms the highlighted option; a sustained hold
        # past the threshold cycles to the next option once per press
        # and latches so the eventual release doesn't also confirm.
        # This is the single-button nav fallback for pads where neither
        # stick/hat nor raw-button polling work (kid playtest 2026-05-23).
        # _fire_hold_ticks acts as a "this press is tracked" flag too:
        # only set on a true rising edge, so held-from-prior-scene FIRE
        # (prev_fire=True from initial seeding) never enters the cycle
        # path until the player releases and presses afresh.
        fire_now = inputs[0].fire or inputs[1].fire
        chosen_to_activate: str | None = None
        if fire_now and not self._prev_fire:
            # Rising edge — start tracking this press.
            self._fire_hold_ticks = 1
            self._fire_cycled_this_press = False
        elif fire_now and self._fire_hold_ticks > 0:
            # Continued hold within a tracked press.
            self._fire_hold_ticks += 1
            if (
                self._fire_hold_ticks >= _FIRE_HOLD_CYCLE_TICKS
                and not self._fire_cycled_this_press
            ):
                self._selected_index = (self._selected_index + 1) % len(_OPTIONS)
                self._fire_cycled_this_press = True
        elif not fire_now and self._prev_fire and self._fire_hold_ticks > 0:
            # Falling edge of a tracked press. Confirm only if the press
            # never crossed the cycle threshold (i.e. it was a tap).
            if not self._fire_cycled_this_press:
                chosen_to_activate = _OPTIONS[self._selected_index]
            self._fire_hold_ticks = 0
            self._fire_cycled_this_press = False
        self._prev_fire = fire_now

        # CONFIRM activation for remapped pads where CONFIRM is bound to
        # a different physical button than FIRE. Guard with `not fire_now`
        # so the same-button default mapping (CONFIRM and FIRE both on
        # button 0) doesn't short-circuit the tap/hold FIRE logic above.
        if chosen_to_activate is None and not fire_now:
            if inputs[0].confirm or inputs[1].confirm:
                chosen_to_activate = _OPTIONS[self._selected_index]
        if chosen_to_activate is None:
            return None
        chosen = chosen_to_activate
        if chosen in (_OPTION_ONE_PLAYER, _OPTION_TWO_PLAYERS):
            from ssdq.scenes.intro import IntroScene
            from ssdq.scenes.level import LevelScene

            # Solo vs co-op flag — LevelScene reads this to decide whether
            # to spawn P2's ship + HUD column. Set BEFORE clear_progression
            # so the selection survives the reset (clear_progression is
            # progression-state only, not session mode).
            self._app.single_player = chosen == _OPTION_ONE_PLAYER
            # Fresh campaign start — clear any carry-forward state from a
            # previous game-over so we don't inherit stale bomb stockpile,
            # weapon tier or score (kid playtest 2026-04-28 #4).
            self._app.clear_progression()
            # Post-2026-05-02 sequence: the intro story crawl plays
            # AFTER PLAY (not before Title). On completion, IntroScene
            # transitions to the level. Captured the level index now so
            # changes to current_level during the intro don't surprise.
            level_index = self._app.current_level
            return Replace(
                scene=IntroScene(
                    self._app,
                    next_scene_factory=lambda: LevelScene(
                        self._app, level_index=level_index
                    ),
                )
            )
        if chosen == _OPTION_LEVELS:
            from ssdq.scenes.level_select import LevelSelectScene

            return Push(scene=LevelSelectScene(app=self._app))
        if chosen == _OPTION_SCORES:
            from ssdq.scenes.leaderboard import LeaderboardScene

            return Push(scene=LeaderboardScene(self._app))
        if chosen == _OPTION_SETTINGS:
            from ssdq.scenes.settings import SettingsScene

            return Push(
                scene=SettingsScene(
                    app=self._app,
                    pad_guid=getattr(self._app, "last_active_pad_guid", "") or "",
                    pad_name=getattr(self._app, "last_active_pad_name", "") or "",
                )
            )
        return None

    def _any_fire_button_held(self) -> bool:
        """True if any connected pad has its FIRE-bound button currently
        held. Reads pygame.joystick directly so this works before any pad
        is bound to a slot (first-launch case) — at that point PlayerInput
        is still NEUTRAL for every slot, but the raw button state on the
        device is accurate. Used by enter() to seed _prev_fire so the
        first FIRE press after entering the scene is correctly classified
        as a held-from-prior (no edge) vs a fresh press (rising edge).
        """
        try:
            count = pygame.joystick.get_count()
        except pygame.error:
            return False
        store = getattr(self._app, "bindings", None)
        for i in range(count):
            try:
                pad = pygame.joystick.Joystick(i)
                pad.init()
            except pygame.error:
                continue
            fire_btn = 0  # default Xbox-layout
            if store is not None:
                try:
                    from ssdq.platform.input.bindings import BindingAction

                    b = store.get(pad.get_guid(), pad_name=pad.get_name())
                    fire_btn = int(b.button_for(BindingAction.FIRE))
                except (pygame.error, KeyError, AttributeError):
                    pass
            try:
                if 0 <= fire_btn < pad.get_numbuttons() and pad.get_button(fire_btn):
                    return True
            except pygame.error:
                continue
        return False

    def _snapshot_pad_buttons(self) -> dict[int, list[bool]]:
        """Capture current button state for every connected pad.

        Returns ``{instance_id: [button_states]}``. Used both as initial
        seed (from ``enter``) and per-tick state in ``_poll_any_nav_button``.
        Reads pygame.joystick directly so the scene is independent of the
        binding system and the GamepadProvider's slot assignment — the
        whole point is to work on pads whose button indices don't match
        the Xbox-layout defaults.
        """
        result: dict[int, list[bool]] = {}
        try:
            count = pygame.joystick.get_count()
        except pygame.error:
            return result
        for i in range(count):
            try:
                pad = pygame.joystick.Joystick(i)
                pad.init()
            except pygame.error:
                continue
            try:
                iid = int(pad.get_instance_id())
                result[iid] = [
                    bool(pad.get_button(b)) for b in range(pad.get_numbuttons())
                ]
            except pygame.error:
                continue
        return result

    def _skip_button_indices(self, pad: Any) -> set[int]:
        """Buttons that must NOT trigger menu nav on this pad — the ones
        bound to FIRE / CONFIRM / CANCEL, since pressing those carries
        a meaningful intent (confirm or quit) that nav would override.
        """
        skip: set[int] = {0, 1}  # default Xbox-layout fallback
        store = getattr(self._app, "bindings", None)
        if store is None:
            return skip
        try:
            from ssdq.platform.input.bindings import BindingAction

            binding = store.get(pad.get_guid(), pad_name=pad.get_name())
            return {
                int(binding.button_for(BindingAction.FIRE)),
                int(binding.button_for(BindingAction.CONFIRM)),
                int(binding.button_for(BindingAction.CANCEL)),
            }
        except (pygame.error, KeyError, AttributeError):
            return skip

    def _poll_any_nav_button(self) -> bool:
        """True if any pad has a rising-edge press on a non-skip button.

        Refreshes ``_pad_button_state`` as a side-effect (so the next
        call sees today's state as 'previous').
        """
        rising = False
        new_state: dict[int, list[bool]] = {}
        try:
            count = pygame.joystick.get_count()
        except pygame.error:
            return False
        for i in range(count):
            try:
                pad = pygame.joystick.Joystick(i)
                pad.init()
                iid = int(pad.get_instance_id())
            except pygame.error:
                continue
            skip = self._skip_button_indices(pad)
            prev = self._pad_button_state.get(iid, [])
            states: list[bool] = []
            for btn in range(pad.get_numbuttons()):
                held = bool(pad.get_button(btn))
                states.append(held)
                if btn in skip:
                    continue
                # Rising edge: was False (or not in prev) and now True.
                if held and (btn >= len(prev) or not prev[btn]):
                    rising = True
            new_state[iid] = states
        self._pad_button_state = new_state
        return rising

    def render(self, world: World, surface: Any, alpha: float) -> None:
        if not isinstance(surface, pygame.Surface):
            return
        if self._backdrop is not None:
            # Scale-fit the backdrop to the current play surface; the asset
            # is authored at 1280×720 to match the standard window.
            if self._backdrop.get_size() != surface.get_size():
                scaled = pygame.transform.smoothscale(self._backdrop, surface.get_size())
                surface.blit(scaled, (0, 0))
            else:
                surface.blit(self._backdrop, (0, 0))
        else:
            surface.fill(_BG_COLOUR)
        if self._title_font is None or self._prompt_font is None or self._hint_font is None:
            return
        w, h = surface.get_size()
        title = self._title_font.render("SSDQ", True, _TITLE_COLOUR)
        surface.blit(title, title.get_rect(center=(w // 2, h // 3)))
        for i, option in enumerate(_OPTIONS):
            colour = _PROMPT_SELECTED_COLOUR if i == self._selected_index else _PROMPT_COLOUR
            text = self._prompt_font.render(option, True, colour)
            surface.blit(text, text.get_rect(center=(w // 2, h // 2 + i * 50)))
        hint = self._hint_font.render(
            "Tap FIRE: choose   Hold FIRE: cycle   CANCEL: quit",
            True,
            _HINT_COLOUR,
        )
        surface.blit(hint, hint.get_rect(center=(w // 2, h // 2 + len(_OPTIONS) * 50 + 30)))

    def exit(self, world: World) -> None:
        return None
