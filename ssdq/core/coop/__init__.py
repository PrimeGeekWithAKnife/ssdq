"""Co-op rules: lives, continues, respawn, scoring, friendly-fire/SoSC toggles.

The coop layer owns *between-the-rounds* state for both players:

* `PlayerLifecycle` — alive / dying / respawning / out-of-lives, with
  i-frame timers and the clearing-shockwave countdown.
* `CoopSession` — shared continues, options toggles (friendly fire,
  ship-on-ship, unlimited lives/continues), team + per-player scores,
  proximity-bonus multiplier.
* `damage_routing` — pure helper that decides whether a hit between
  factions counts (e.g. friendly-fire toggle, SoSC toggle).
* `score_award` — applies the proximity bonus and routes points to the
  correct personal + team buckets.

Spec §5 covers all defaults; §2 DoD #7 lists the option toggles that
must work via the pause menu.
"""

from ssdq.core.coop.damage import (
    DamageDecision,
    should_apply_damage,
)
from ssdq.core.coop.lifecycle import (
    LifecycleState,
    PlayerLifecycle,
)
from ssdq.core.coop.options import CoopOptions
from ssdq.core.coop.scoring import (
    ScoreLedger,
    ScoreSnapshot,
    proximity_multiplier,
)
from ssdq.core.coop.session import CoopSession

__all__ = [
    "CoopOptions",
    "CoopSession",
    "DamageDecision",
    "LifecycleState",
    "PlayerLifecycle",
    "ScoreLedger",
    "ScoreSnapshot",
    "proximity_multiplier",
    "should_apply_damage",
]
