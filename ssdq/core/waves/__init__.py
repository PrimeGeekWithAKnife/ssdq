"""Wave system: formation evaluator + scheduler + enemy fire beats.

Three concerns, three modules:

* `path` — `evaluate_path(formation, t_norm) -> PathSample`. Catmull-Rom
  spline through control points, optionally mirrored about screen-centre x.
* `scheduler` — reads a `LevelDef` and converts wave entries into a
  deterministic stream of `SpawnEvent` per simulation tick.
* `fire` — converts an enemy's path-time-locked `fire_beats` into shot
  events; an aimed shot snapshots the target at the moment of fire.

This module is the engine room of the mastery constraint (spec §6): every
enemy's path is a deterministic function of `t`; every shot fires on a
fixed beat tied to that path. No real-time tracking, no random spawns.
"""

from ssdq.core.waves.fire import (
    AimSnapshot,
    EnemyShooter,
    FireEvent,
)
from ssdq.core.waves.path import (
    PathSample,
    evaluate_path,
    path_position,
)
from ssdq.core.waves.scheduler import (
    SpawnEvent,
    WaveScheduler,
)

__all__ = [
    "AimSnapshot",
    "EnemyShooter",
    "FireEvent",
    "PathSample",
    "SpawnEvent",
    "WaveScheduler",
    "evaluate_path",
    "path_position",
]
