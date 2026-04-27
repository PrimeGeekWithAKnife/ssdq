"""Per-player power-up state and pickup application.

Frozen-dataclass state with helper functions that produce *new* states
(immutable update). The level scene stores one PlayerPowerupState per
player and replaces it via `apply_pickup`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ssdq.core.content.schema import PickupDef, PickupEffect


@dataclass(frozen=True, slots=True)
class WeaponState:
    """Where in the weapon-tree progression this player sits."""

    tree: str  # tree name, e.g. "pulse"
    level: int  # 0..len(tree.levels)-1

    def upgrade(self, *, max_level: int) -> WeaponState:
        if self.level >= max_level:
            return self
        return WeaponState(tree=self.tree, level=self.level + 1)

    def reset(self) -> WeaponState:
        return WeaponState(tree=self.tree, level=0)


@dataclass(frozen=True, slots=True)
class SpeedBoost:
    """Active speed-up timer. Both fields zero ⇒ no boost."""

    multiplier: float
    seconds_remaining: float

    @property
    def active(self) -> bool:
        return self.seconds_remaining > 0.0


_NO_BOOST = SpeedBoost(multiplier=1.0, seconds_remaining=0.0)


@dataclass(frozen=True, slots=True)
class Shield:
    """Active shield (forcefield) timer. ``seconds_remaining`` zero ⇒ no shield.

    While active, the player ship is invulnerable to all incoming damage
    (the level scene short-circuits its hit-handler). Stacking is
    handled by ``apply_pickup``: collecting a second shield while one is
    already up RESETs the timer to the new pickup's duration rather than
    summing — matches the user-feedback spec ("makes it invulnerable for
    10 seconds").
    """

    seconds_remaining: float

    @property
    def active(self) -> bool:
        return self.seconds_remaining > 0.0


_NO_SHIELD = Shield(seconds_remaining=0.0)


@dataclass(frozen=True, slots=True)
class PlayerPowerupState:
    """All power-up state for one player. Held by the coop layer."""

    weapon: WeaponState
    bombs: int
    lives: int
    speed_boost: SpeedBoost = _NO_BOOST
    shield: Shield = _NO_SHIELD

    def with_weapon(self, weapon: WeaponState) -> PlayerPowerupState:
        return replace(self, weapon=weapon)

    def with_bombs(self, bombs: int) -> PlayerPowerupState:
        return replace(self, bombs=max(0, bombs))

    def with_lives(self, lives: int) -> PlayerPowerupState:
        return replace(self, lives=max(0, lives))

    def with_speed_boost(self, boost: SpeedBoost) -> PlayerPowerupState:
        return replace(self, speed_boost=boost)

    def with_shield(self, shield: Shield) -> PlayerPowerupState:
        return replace(self, shield=shield)

    def tick_speed_boost(self, dt: float) -> PlayerPowerupState:
        """Decay any active speed-boost timer by `dt`. No-op when inactive."""
        if not self.speed_boost.active:
            return self
        new_remaining = self.speed_boost.seconds_remaining - dt
        if new_remaining <= 0.0:
            return self.with_speed_boost(_NO_BOOST)
        return self.with_speed_boost(
            SpeedBoost(multiplier=self.speed_boost.multiplier, seconds_remaining=new_remaining)
        )

    def tick_shield_decay(self, dt: float) -> PlayerPowerupState:
        """Decay any active shield timer by `dt`. No-op when inactive."""
        if not self.shield.active:
            return self
        new_remaining = self.shield.seconds_remaining - dt
        if new_remaining <= 0.0:
            return self.with_shield(_NO_SHIELD)
        return self.with_shield(Shield(seconds_remaining=new_remaining))

    def reset_on_death(self, *, starting_bombs: int) -> PlayerPowerupState:
        """Per spec: weapon level drops back to base on death; bombs reset
        to `starting_bombs` (from ShipDef); speed-boost and shield cleared;
        lives is decremented by the coop layer, not here."""
        return PlayerPowerupState(
            weapon=self.weapon.reset(),
            bombs=max(0, starting_bombs),
            lives=self.lives,
            speed_boost=_NO_BOOST,
            shield=_NO_SHIELD,
        )


@dataclass(frozen=True, slots=True)
class PickupResult:
    """Outcome of applying a pickup. The level scene routes side effects
    (HUD flash, sfx) from these flags."""

    new_state: PlayerPowerupState
    upgraded_weapon: bool = False
    extra_bomb: bool = False
    extra_life: bool = False
    speed_up: bool = False
    shield_up: bool = False


def apply_pickup(
    state: PlayerPowerupState,
    pickup: PickupDef,
    *,
    weapon_tree_max_level: int,
) -> PickupResult:
    """Apply a pickup's effect to a player's power-up state.

    `weapon_tree_max_level` is `len(weapon_tree[tree]) - 1`; passed in so
    the powerup module doesn't need a back-reference to the content
    bundle.
    """
    if pickup.effect == PickupEffect.WEAPON_UPGRADE:
        new_weapon = state.weapon.upgrade(max_level=weapon_tree_max_level)
        upgraded = new_weapon.level != state.weapon.level
        return PickupResult(new_state=state.with_weapon(new_weapon), upgraded_weapon=upgraded)
    if pickup.effect == PickupEffect.EXTRA_BOMB:
        return PickupResult(new_state=state.with_bombs(state.bombs + 1), extra_bomb=True)
    if pickup.effect == PickupEffect.EXTRA_LIFE:
        return PickupResult(new_state=state.with_lives(state.lives + 1), extra_life=True)
    if pickup.effect == PickupEffect.SPEED_UP:
        boost = SpeedBoost(
            multiplier=pickup.speed_multiplier,
            seconds_remaining=pickup.duration,
        )
        return PickupResult(new_state=state.with_speed_boost(boost), speed_up=True)
    if pickup.effect == PickupEffect.SHIELD:
        # Stacking semantics: collecting a second shield while one is
        # already active RESETS the remaining time to the new pickup's
        # duration (not additive). Matches the user-feedback spec — the
        # forcefield "engulfs the ship for 10 seconds", any subsequent
        # pickup refreshes that 10-second window.
        shield = Shield(seconds_remaining=pickup.duration)
        return PickupResult(new_state=state.with_shield(shield), shield_up=True)
    raise ValueError(f"unknown pickup effect: {pickup.effect}")
