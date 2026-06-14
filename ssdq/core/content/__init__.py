"""Content data classes + YAML loader.

Strict typed shapes for the YAML files in /content. All loading goes
through ContentBundle.load_dir() which validates as it parses; on the
first malformed value it raises ContentError with a path-and-line hint.
"""

from ssdq.core.content.loader import ContentBundle, ContentError, load_bundle
from ssdq.core.content.schema import (
    BombDef,
    BossDef,
    BossPhaseDef,
    CoopConfig,
    EnemyDef,
    EnemyWeaponDef,
    FirePattern,
    FormationDef,
    FormationKind,
    LevelDef,
    PickupDef,
    PickupEffect,
    ShipDef,
    SpawnDef,
    StrayAsteroidConfig,
    WaveDef,
    WeaponDef,
)

__all__ = [
    "BombDef",
    "BossDef",
    "BossPhaseDef",
    "ContentBundle",
    "ContentError",
    "CoopConfig",
    "EnemyDef",
    "EnemyWeaponDef",
    "FirePattern",
    "FormationDef",
    "FormationKind",
    "LevelDef",
    "PickupDef",
    "PickupEffect",
    "ShipDef",
    "SpawnDef",
    "StrayAsteroidConfig",
    "WaveDef",
    "WeaponDef",
    "load_bundle",
]
