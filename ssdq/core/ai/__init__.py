"""Per-enemy AI primitives.

Currently exports the FreeRoamAI tick — a deterministic drift + dodge
behaviour used by sentinel + marauder enemies (kid playtest 2026-05-03
#2 + #10). Both want the same "appears, drifts around, dodges
incoming fire, stays on screen until killed" silhouette but with
different speed / aggression / armour profiles.
"""

from ssdq.core.ai.free_roam import FreeRoamConfig, free_roam_step

__all__ = ["FreeRoamConfig", "free_roam_step"]
