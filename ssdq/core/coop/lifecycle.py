"""Per-player lifecycle: alive / dying / respawning / out-of-lives.

State machine:

    ALIVE ──hit──▶ DYING ──respawn_delay──▶ INVULNERABLE ──invuln──▶ ALIVE
                          │
                          └── (no lives left + no continues) ──▶ OUT

The `INVULNERABLE` state is the post-respawn i-frame window; the
clearing-shockwave fires once at the moment of transition into
INVULNERABLE so the level scene can pulse a clear-radius particle.

All times are sim-seconds. Use `tick(dt, options)` to advance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from ssdq.core.content.schema import CoopConfig
from ssdq.core.coop.options import CoopOptions
from ssdq.core.types import PlayerSlot


class LifecycleState(Enum):
    ALIVE = "alive"
    DYING = "dying"  # waiting for respawn_delay
    INVULNERABLE = "invulnerable"  # i-frames after respawn
    OUT = "out"  # no lives + no continues


@dataclass(frozen=True, slots=True)
class PlayerLifecycle:
    """All lifecycle state for one player.

    Lives count *available* respawns; reaches 0 when the next death has
    no lives to consume. `state` plus the timer fields tell the level
    scene what to do this tick.
    """

    slot: PlayerSlot
    state: LifecycleState
    lives: int
    dying_remaining: float = 0.0  # seconds left in DYING
    invuln_remaining: float = 0.0  # seconds left in INVULNERABLE
    fired_clearing_shockwave: bool = False  # one-shot trigger for level scene

    @staticmethod
    def initial(slot: PlayerSlot, lives: int) -> PlayerLifecycle:
        return PlayerLifecycle(
            slot=slot,
            state=LifecycleState.ALIVE,
            lives=lives,
        )

    @property
    def can_be_hit(self) -> bool:
        return self.state == LifecycleState.ALIVE

    def hit(self, *, config: CoopConfig, options: CoopOptions) -> PlayerLifecycle:
        """Apply a fatal hit. ALIVE → DYING (or OUT if last life)."""
        if self.state != LifecycleState.ALIVE:
            return self  # i-frames or already dying — ignore.
        new_lives = self.lives if options.unlimited_lives else self.lives - 1
        if new_lives <= 0 and not options.unlimited_lives:
            return PlayerLifecycle(
                slot=self.slot,
                state=LifecycleState.OUT,
                lives=0,
            )
        return PlayerLifecycle(
            slot=self.slot,
            state=LifecycleState.DYING,
            lives=new_lives,
            dying_remaining=config.respawn_delay,
        )

    def consume_continue(self, *, config: CoopConfig, options: CoopOptions) -> PlayerLifecycle:
        """When a player is OUT and a continue is spent on them, transition
        back to DYING then INVULNERABLE (free respawn from a fresh life)."""
        if self.state != LifecycleState.OUT:
            return self
        return PlayerLifecycle(
            slot=self.slot,
            state=LifecycleState.DYING,
            lives=options.starting_lives,
            dying_remaining=config.respawn_delay,
        )

    def tick(self, dt: float, *, config: CoopConfig) -> PlayerLifecycle:
        """Advance lifecycle timers; transition states when timers expire.

        The `fired_clearing_shockwave` flag is set on the tick the player
        transitions DYING → INVULNERABLE; level scene must consume it
        via `consume_clearing_shockwave()` next tick.
        """
        if self.state == LifecycleState.DYING:
            new_remaining = self.dying_remaining - dt
            if new_remaining <= 0.0:
                return PlayerLifecycle(
                    slot=self.slot,
                    state=LifecycleState.INVULNERABLE,
                    lives=self.lives,
                    invuln_remaining=config.respawn_invulnerability,
                    fired_clearing_shockwave=True,
                )
            return replace(self, dying_remaining=new_remaining)

        if self.state == LifecycleState.INVULNERABLE:
            new_remaining = self.invuln_remaining - dt
            if new_remaining <= 0.0:
                return PlayerLifecycle(
                    slot=self.slot,
                    state=LifecycleState.ALIVE,
                    lives=self.lives,
                )
            # The shockwave flag latches until consume_clearing_shockwave()
            # is called explicitly — necessary because the fixed-timestep
            # accumulator can sub-tick multiple times per render frame.
            return replace(self, invuln_remaining=new_remaining)

        return self

    def consume_clearing_shockwave(self) -> PlayerLifecycle:
        """Acknowledge the clearing-shockwave one-shot; subsequent ticks
        of the same INVULNERABLE state will not re-fire it."""
        if not self.fired_clearing_shockwave:
            return self
        return replace(self, fired_clearing_shockwave=False)
