"""Drop-pool selection — deterministic, tick-derived.

`roll_drop(enemy, tick, channel) -> str | None`. Two coin flips:

1. drop-chance flip: `tick_unit(tick, channel) < drop_chance`. If false,
   no drop.
2. pool-index flip: pick `tick_int(tick, 0, len(pool), channel+1)` to
   select which pickup of the pool is dropped.

Channel separation means the same enemy-death tick can host multiple
independent decisions without clashing. The level scene routes its own
channel base (e.g. enemy_entity_id) so concurrent kills don't collide.
"""

from __future__ import annotations

from ssdq.core.content.schema import EnemyDef
from ssdq.core.rng import tick_int, tick_unit


def roll_drop(enemy: EnemyDef, tick: int, channel: int) -> str | None:
    """Return the pickup name to drop, or None if no drop this kill.

    Two channels per call (`channel*2`, `channel*2+1`) so concurrent
    enemy deaths on the same tick don't share noise streams; pass any
    monotonic int (e.g. enemy entity id).
    """
    if not enemy.drop_pool:
        return None
    if enemy.drop_chance <= 0.0:
        return None
    ch_a = channel * 2
    ch_b = channel * 2 + 1
    if enemy.drop_chance < 1.0:
        roll = tick_unit(tick, ch_a)
        if roll >= enemy.drop_chance:
            return None
    idx = tick_int(tick, 0, len(enemy.drop_pool), ch_b)
    return enemy.drop_pool[idx]
