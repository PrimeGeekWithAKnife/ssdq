"""Damage routing: should this faction-pair collision count?

Centralises the friendly-fire and ship-on-ship rules so the collision
system in the level scene doesn't duplicate the logic. Pure functions on
factions + options.
"""

from __future__ import annotations

from enum import Enum

from ssdq.core.components import Faction
from ssdq.core.coop.options import CoopOptions


class DamageDecision(Enum):
    """Outcome categories for a faction-pair collision."""

    APPLY = "apply"  # damage applies normally
    IGNORE = "ignore"  # collision is not a hit (different sides, blocked by toggle)
    PICKUP = "pickup"  # not damage — a player has touched a pickup


def should_apply_damage(
    a: Faction,
    b: Faction,
    options: CoopOptions,
) -> DamageDecision:
    """Decide whether (a, b) collision counts as damage, ignored, or pickup.

    Symmetric — `should_apply_damage(a, b, …) == should_apply_damage(b, a, …)`.
    The level scene uses this to decide whether to invoke the player-death
    pipeline or the bullet-on-enemy damage pipeline.
    """
    pair = frozenset({a, b})

    # Pickups: any pickup vs player is always APPLY-as-pickup; pickups
    # don't interact with anything else.
    if Faction.PICKUP in pair:
        if Faction.PLAYER in pair:
            return DamageDecision.PICKUP
        return DamageDecision.IGNORE

    # Player vs enemy bullet → apply (kills player; i-frames handled
    # by the lifecycle layer, not here).
    if pair == {Faction.PLAYER, Faction.ENEMY_BULLET}:
        return DamageDecision.APPLY

    # Player vs enemy → apply (player dies, enemy may also be damaged).
    if pair == {Faction.PLAYER, Faction.ENEMY}:
        return DamageDecision.APPLY

    # Player bullet vs enemy → apply (damage the enemy).
    if pair == {Faction.PLAYER_BULLET, Faction.ENEMY}:
        return DamageDecision.APPLY

    # Friendly fire: player bullet vs the *other* player. Caller is
    # responsible for filtering same-player self-hits before reaching
    # this layer (PlayerOwned mismatch). We assume the collision is
    # cross-player.
    if pair == {Faction.PLAYER_BULLET, Faction.PLAYER}:
        return DamageDecision.APPLY if options.friendly_fire else DamageDecision.IGNORE

    # Player vs player: ship-on-ship — only when toggled on.
    if a == Faction.PLAYER and b == Faction.PLAYER:
        return DamageDecision.APPLY if options.ship_on_ship_collision else DamageDecision.IGNORE

    # Bullet vs bullet, enemy vs enemy, player_bullet vs player_bullet, etc:
    # never an interaction in the slice.
    return DamageDecision.IGNORE
