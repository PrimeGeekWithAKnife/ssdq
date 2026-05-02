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
class FireRateBoost:
    """Timed weapon-rate-of-fire boost (separate from temporary ship speed).

    While ``seconds_remaining > 0`` the level scene shrinks the player's
    primary-weapon cooldown by dividing the base cooldown by
    ``multiplier``. Stacking refreshes the timer and adopts the latest
    pickup's multiplier (matches Shield/SpeedBoost semantics).
    """

    multiplier: float
    seconds_remaining: float

    @property
    def active(self) -> bool:
        return self.seconds_remaining > 0.0


_NO_FIRE_RATE = FireRateBoost(multiplier=1.0, seconds_remaining=0.0)


# Permanent additive cap for the ship_speed pickup. A flood of pickups
# can't push a ship past +60% of its base max_speed.
SHIP_SPEED_BONUS_CAP: float = 0.60


@dataclass(frozen=True, slots=True)
class Shield:
    """Active shield (forcefield) timer. ``seconds_remaining`` zero ⇒ no shield.

    While active, the player ship is invulnerable to all incoming damage
    (the level scene short-circuits its hit-handler). The shield is now
    triggered by the player **consuming an equippable charge** (kid playtest
    2026-04-27: "shield is equippable like a bomb you can wait to use it");
    pickups grant charges on the AppState, and pressing the shield button
    activates a brief invulnerability window via this timer.
    """

    seconds_remaining: float

    @property
    def active(self) -> bool:
        return self.seconds_remaining > 0.0


_NO_SHIELD = Shield(seconds_remaining=0.0)

# Duration of the invulnerability window granted when a player consumes a
# shield charge. Short by design (3s) — the kid wanted "very useful in key
# moments", not a permanent god mode.
SHIELD_CONSUME_DURATION: float = 3.0

# Cap for missile_level. Each MISSILE pickup advances the tier by one;
# the auto-fire pattern table in the level scene defines how many missiles
# spawn per 2s tick at each tier (0 = silent, 5 = full barrage).
MISSILE_LEVEL_CAP: int = 5


@dataclass(frozen=True, slots=True)
class PlayerPowerupState:
    """All power-up state for one player. Held by the coop layer.

    Task #9 added:
      * ``ship_speed_bonus`` — permanent additive ship-speed bump applied
        on top of the ship's ``max_speed``. Each ``SHIP_SPEED`` pickup
        adds ``PickupDef.ship_speed_step`` (default +15%); the running
        total is capped at ``SHIP_SPEED_BONUS_CAP`` (+60%).
      * ``fire_rate_boost`` — timed multiplier on weapon rate of fire
        (15s default). Refresh-on-pickup, like Shield.
    """

    weapon: WeaponState
    bombs: int
    lives: int
    speed_boost: SpeedBoost = _NO_BOOST
    shield: Shield = _NO_SHIELD
    ship_speed_bonus: float = 0.0  # additive bonus (e.g. 0.30 == +30%)
    fire_rate_boost: FireRateBoost = _NO_FIRE_RATE
    missile_level: int = 0  # 0..MISSILE_LEVEL_CAP (auto-fire tier)

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

    def with_ship_speed_bonus(self, bonus: float) -> PlayerPowerupState:
        # Cap the bonus so a flood of pickups can't make ships uncontrollable.
        capped = max(0.0, min(SHIP_SPEED_BONUS_CAP, bonus))
        return replace(self, ship_speed_bonus=capped)

    def with_fire_rate_boost(self, boost: FireRateBoost) -> PlayerPowerupState:
        return replace(self, fire_rate_boost=boost)

    def with_missile_level(self, level: int) -> PlayerPowerupState:
        clamped = max(0, min(MISSILE_LEVEL_CAP, level))
        return replace(self, missile_level=clamped)

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

    def tick_fire_rate_boost(self, dt: float) -> PlayerPowerupState:
        """Decay any active fire-rate-boost timer by `dt`. No-op when inactive."""
        if not self.fire_rate_boost.active:
            return self
        new_remaining = self.fire_rate_boost.seconds_remaining - dt
        if new_remaining <= 0.0:
            return self.with_fire_rate_boost(_NO_FIRE_RATE)
        return self.with_fire_rate_boost(
            FireRateBoost(
                multiplier=self.fire_rate_boost.multiplier, seconds_remaining=new_remaining
            )
        )

    def reset_on_death(self, *, starting_bombs: int) -> PlayerPowerupState:
        """Per spec: weapon level drops back to base on death; speed-boost,
        shield and the timed fire-rate boost cleared; permanent ship-speed
        bonus is retained; lives is decremented by the coop layer, not
        here. Bombs are preserved at max(current, starting_bombs) so a
        stockpile from pickups isn't wiped — kid playtest 2026-04-28
        reported pickups appeared to "reset" the bomb count, which traced
        to the death reset clobbering accumulated pickups; same rationale
        as the ship-speed retention.
        """
        return PlayerPowerupState(
            weapon=self.weapon.reset(),
            bombs=max(self.bombs, starting_bombs),
            lives=self.lives,
            speed_boost=_NO_BOOST,
            shield=_NO_SHIELD,
            ship_speed_bonus=self.ship_speed_bonus,
            fire_rate_boost=_NO_FIRE_RATE,
            missile_level=0,
        )


@dataclass(frozen=True, slots=True)
class PickupResult:
    """Outcome of applying a pickup. The level scene routes side effects
    (HUD flash, sfx, AppState inventory increments) from these flags.
    """

    new_state: PlayerPowerupState
    upgraded_weapon: bool = False
    extra_bomb: bool = False
    extra_life: bool = False
    speed_up: bool = False  # legacy temporary speed boost
    shield_up: bool = False
    # Task #9 — kid-playtest pool extensions.
    ship_speed_up: bool = False  # permanent ship-speed bump
    weapon_speed_up: bool = False  # timed rate-of-fire boost
    drone_pickup: bool = False  # queue +1 drone for the DRONE agent
    missile_tier_up: bool = False  # missile_level advanced (false at cap)
    shield_charge_added: bool = False  # +1 shield charge to inventory
    # Legacy: missile pickups used to grant inventory charges. Kept at 0
    # so the level-scene routing block stays a silent no-op until the
    # button-press strip-out in the missile-redesign series removes both
    # this field and the callsite. Do not re-introduce charge logic.
    missile_charges_added: int = 0


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

    For inventory-style effects (DRONE, MISSILE, SHIELD) the increment is
    encoded on the returned ``PickupResult`` and the caller (level scene)
    is responsible for writing it onto ``AppState`` — keeps this module
    decoupled from the scene layer.
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
        # Equippable: shield pickup adds a charge to AppState inventory.
        # It does NOT auto-activate the forcefield (kid playtest
        # 2026-04-27: "you can wait to use it"). The level scene reads
        # `shield_up` plus `shield_charge_added` and routes the charge
        # grant via `AppState.add_shield_charge`. The PlayerPowerupState
        # `shield` field is reserved for the *active* invulnerability
        # window granted on consumption (see SHIELD_CONSUME_DURATION).
        return PickupResult(
            new_state=state,
            shield_up=True,
            shield_charge_added=True,
        )
    if pickup.effect == PickupEffect.SHIP_SPEED:
        # Permanent additive bump (capped). Each pickup adds
        # ``ship_speed_step`` (default +15%), capped at +60% so flooding
        # pickups can't make the ship uncontrollable.
        new_bonus = state.ship_speed_bonus + max(0.0, pickup.ship_speed_step)
        new_state = state.with_ship_speed_bonus(new_bonus)
        # Only flag if the cap didn't entirely swallow the increment —
        # otherwise the floating-text "SPEED!" label would lie.
        flagged = new_state.ship_speed_bonus > state.ship_speed_bonus
        return PickupResult(new_state=new_state, ship_speed_up=flagged)
    if pickup.effect == PickupEffect.WEAPON_SPEED:
        rate_boost = FireRateBoost(
            multiplier=pickup.fire_rate_multiplier,
            seconds_remaining=pickup.duration,
        )
        return PickupResult(
            new_state=state.with_fire_rate_boost(rate_boost),
            weapon_speed_up=True,
        )
    if pickup.effect == PickupEffect.DRONE:
        # Inventory-only — no PlayerPowerupState change. The DRONE agent
        # owns entity spawning; for now AppState.drones_pending tracks
        # the queue so the agent can drain it on its first integration.
        return PickupResult(new_state=state, drone_pickup=True)
    if pickup.effect == PickupEffect.MISSILE:
        # Post-2026-04-30 redesign: missiles are tier-based auto-fire,
        # not stockpiled charges. Each pickup advances the tier by one
        # (capped at MISSILE_LEVEL_CAP). The level scene reads
        # ``missile_level`` directly to drive the per-tier auto-fire
        # spawn pattern; ``missile_count`` on PickupDef is no longer
        # meaningful and is ignored.
        bumped = state.with_missile_level(state.missile_level + 1)
        return PickupResult(
            new_state=bumped,
            missile_tier_up=bumped.missile_level != state.missile_level,
        )
    raise ValueError(f"unknown pickup effect: {pickup.effect}")
