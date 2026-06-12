"""Cross-scene application state.

The scene stack passes `AppState` around so each scene can construct its
successor without re-loading the content bundle or re-instantiating the
audio bus. Stored as an ECS resource on `World`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ssdq.core.content.loader import ContentBundle
from ssdq.core.coop.options import CoopOptions
from ssdq.core.replay import ReplayRecorder
from ssdq.core.types import P1, P2, PlayerSlot
from ssdq.platform.audio import AudioBus
from ssdq.platform.input.bindings import BindingsStore


def _zero_per_slot() -> dict[PlayerSlot, int]:
    """Default factory for per-slot inventory counters."""
    return {P1: 0, P2: 0}


@dataclass
class AppState:
    """Mutable session-scope state shared between scenes."""

    content: ContentBundle
    audio: AudioBus
    options: CoopOptions
    current_level: int = 1
    recorder: ReplayRecorder | None = None
    last_team_score: int = 0
    last_p1_score: int = 0
    last_p2_score: int = 0
    completed_level: bool = False

    # Solo-play mode (added 2026-05-08). When True, LevelScene only spawns
    # P1 and the HUD hides the P2 column. Set by TitleScene on the
    # 1 PLAYER row; cleared on 2 PLAYERS. NOT cleared by
    # clear_progression — it's a session-mode flag set by the title each
    # entry, not a campaign-progression artefact.
    single_player: bool = False

    # Per-pad button bindings. Optional so pre-existing scene tests that
    # build a bare AppState still construct; main.py always wires this in
    # at startup so production code can rely on it being present.
    bindings: BindingsStore | None = None

    # Most-recently bound pad's SDL GUID + display name. Populated by
    # GamepadProvider on slot bind so the SettingsScene targets the pad
    # the player is actually holding without coupling to the provider.
    # Empty strings until the first pad is bound.
    last_active_pad_guid: str = ""
    last_active_pad_name: str = ""

    # Bombs awarded by an inter-level scene (DockingScene) that the next
    # LevelScene should add on top of the ship's `starting_bombs` baseline
    # in `enter()`. Reset to 0 once consumed so re-entering Title → Level
    # without another docking sequence doesn't repeatedly grant bonuses.
    bomb_bonus_pending: int = 0

    # Task #9 — equippable / agent-owned inventories. The level scene
    # increments these when the matching pickup is collected; the
    # downstream agents (DRONE, EQUIPPABLE) consume them once they
    # land. They survive across LevelScene boundaries within a session.
    drones_pending: dict[PlayerSlot, int] = field(default_factory=_zero_per_slot)
    shield_charges: dict[PlayerSlot, int] = field(default_factory=_zero_per_slot)

    # One-shot shield charge bonus granted by DockingScene. Drained into
    # `shield_charges` on next LevelScene.enter (same pattern as
    # bomb_bonus_pending). Distinct from `shield_charges` because it
    # represents the "resupply gave me +1" signal, not the running
    # inventory.
    shield_charge_pending: int = 0

    # Weapon tier carried across LEVEL boundaries. Kid playtest 2026-04-27:
    # "Your weapon level is retained between level transitions." The level
    # scene captures each player's current weapon level on `exit()` and the
    # next LevelScene seeds the player's PlayerPowerupState with it on
    # `enter()` (clamped to the tree's max). Empty dict ⇒ start at base
    # tier (level 0). Per-life death reset INSIDE a level still happens —
    # this only persists across the LevelScene → LevelCompleteScene →
    # DockingScene → next LevelScene seam. Keyed by player slot index
    # (0 = P1, 1 = P2) so the dict is JSON/replay-friendly without
    # leaking the PlayerSlot class.
    last_weapon_tiers: dict[int, int] = field(default_factory=dict)

    # Bomb stockpile carried across cleared levels — kid playtest 2026-04-28
    # #4: "the supply ship does not seem to always add bombs, sometimes the
    # total bomb count goes down after the supply level". Same persist-on-
    # clear rule as last_weapon_tiers. The next LevelScene applies
    # bomb_bonus_pending ON TOP of this stockpile (max-clamped to
    # ship.starting_bombs so an empty stockpile never starts below baseline).
    last_bombs: dict[int, int] = field(default_factory=dict)

    # Permanent ship-speed bonus accumulated via SHIP_SPEED pickups —
    # carried across cleared levels alongside weapon tier and bombs.
    # Within-level death already preserves it via reset_on_death; level
    # boundaries used to drop it. Same persist-on-clear semantics.
    last_ship_speed_bonus: dict[int, float] = field(default_factory=dict)

    # Missile auto-fire tier carried across cleared levels (post-2026-04-30
    # missile redesign). Mirrors ``last_weapon_tiers``: captured on the
    # cleared level's exit, seeded into PlayerPowerupState.missile_level
    # on next LevelScene.enter. Death inside a level still resets the
    # tier to 0 — this only persists across the cleared-level seam.
    last_missile_levels: dict[int, int] = field(default_factory=dict)

    # Lives count carried across cleared levels — kid playtest 2026-05-02
    # found that P2 sat still through level after level and never went
    # below 1 life. Root cause: every LevelScene.enter() called
    # CoopSession.initial() which seeded both players' lives from
    # options.starting_lives. Now we capture each slot's surviving lives
    # at cleared exit and seed the next session from this dict. Empty ⇒
    # use options.starting_lives (fresh campaign / first level).
    last_lives: dict[int, int] = field(default_factory=dict)

    # Scratch flags for Boot → Title → Level transitions to know what to do.
    asset_loaded_levels: set[int] = field(default_factory=set)

    # Active drone-formation index per slot (Tight=0, Spread=1, Trailing=2,
    # Vanguard=3). Persisted across LevelScene re-enters so the kid's chosen
    # formation isn't lost between levels. Cycled via PlayerInput.drone_cycle.
    drone_config: dict[PlayerSlot, int] = field(default_factory=_zero_per_slot)

    # Per-level music rotation counters (level index → entry count). Each
    # LevelScene.enter() picks pool[count % len(pool)] then bumps the count
    # once, so re-entering a level cycles base → _b → _c → base (fun review
    # 2026-06-12: one track per level was wearing thin). NOT cleared by
    # clear_progression — like single_player it's a session ambience
    # artefact, not a campaign-progression artefact; restarting a campaign
    # should still rotate the soundtrack rather than replay last run's.
    music_rotation: dict[int, int] = field(default_factory=dict)

    # ───────── equippable helpers ─────────

    def get_shield_charges(self, slot: PlayerSlot) -> int:
        return self.shield_charges.get(slot, 0)

    def add_shield_charge(self, slot: PlayerSlot, n: int = 1) -> None:
        self.shield_charges[slot] = self.get_shield_charges(slot) + n

    def consume_shield_charge(self, slot: PlayerSlot) -> bool:
        """Decrement and return True if a charge was available."""
        cur = self.get_shield_charges(slot)
        if cur <= 0:
            return False
        self.shield_charges[slot] = cur - 1
        return True

    # ───────── progression carry-forward ─────────

    def clear_progression(self) -> None:
        """Reset every cross-level carry-forward field to its empty default.

        Called by Title→PLAY and LevelSelect entries so a fresh session
        (or dev-jump to a specific level) doesn't inherit stale numbers
        from a previous game-over or campaign run. Does NOT touch the
        in-flight DockingScene staging (``bomb_bonus_pending``, etc.) —
        those are normally zero outside the level-complete chain anyway.
        """
        self.last_team_score = 0
        self.last_p1_score = 0
        self.last_p2_score = 0
        self.last_weapon_tiers = {}
        self.last_bombs = {}
        self.last_ship_speed_bonus = {}
        self.last_missile_levels = {}
        self.last_lives = {}
        # Equippable inventories — kid expects a clean slate on a fresh
        # campaign, not whatever stockpile a prior wipeout left behind.
        self.shield_charges = _zero_per_slot()
        self.drones_pending = _zero_per_slot()
        self.drone_config = _zero_per_slot()
        # music_rotation deliberately NOT reset — like single_player it's
        # a session ambience artefact, not a progression artefact. A
        # fresh campaign should hear the NEXT track in each level's
        # pool, not restart the rotation.
