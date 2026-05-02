"""Power-up system: weapon-tree progression, drop selection, pickup effects.

Three concerns:

* `WeaponState` per player — current weapon-tree level, capped at top tier;
  reset to level 0 on death.
* `roll_drop` — given an enemy's drop_chance + drop_pool, return the
  pickup name to spawn (or None). Deterministic — uses tick-derived
  noise from `core/rng`, never `random.random()`.
* `apply_pickup` — converts a `PickupEffect` into a player-state mutation
  (extra life, extra bomb, weapon upgrade, speed boost with timer).

Spec §2 #5: weapon upgrade (≥ 2 levels above base), speed up, extra
bomb, extra life. All four ship in the slice; this module owns them.
"""

from ssdq.core.powerups.drops import roll_drop
from ssdq.core.powerups.state import (
    MISSILE_LEVEL_CAP,
    SHIELD_CONSUME_DURATION,
    SHIP_SPEED_BONUS_CAP,
    FireRateBoost,
    PickupResult,
    PlayerPowerupState,
    Shield,
    SpeedBoost,
    WeaponState,
    apply_pickup,
)

__all__ = [
    "MISSILE_LEVEL_CAP",
    "SHIELD_CONSUME_DURATION",
    "SHIP_SPEED_BONUS_CAP",
    "FireRateBoost",
    "PickupResult",
    "PlayerPowerupState",
    "Shield",
    "SpeedBoost",
    "WeaponState",
    "apply_pickup",
    "roll_drop",
]
