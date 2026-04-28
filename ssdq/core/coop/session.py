"""Session-scope coop state: continues + both players' lifecycle.

Owns the high-level question "is the game over?" — true only when both
players are OUT and there are no continues left.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ssdq.core.content.schema import CoopConfig
from ssdq.core.coop.lifecycle import LifecycleState, PlayerLifecycle
from ssdq.core.coop.options import CoopOptions
from ssdq.core.coop.scoring import ScoreLedger
from ssdq.core.types import P1, P2, PlayerSlot


@dataclass
class CoopSession:
    """Session-level state. Mutable container — the level scene mutates
    it across ticks."""

    config: CoopConfig
    options: CoopOptions
    p1: PlayerLifecycle
    p2: PlayerLifecycle
    continues_remaining: int
    scores: ScoreLedger = field(default_factory=ScoreLedger)

    @staticmethod
    def initial(
        config: CoopConfig,
        options: CoopOptions,
        scores: ScoreLedger | None = None,
    ) -> CoopSession:
        """Build a fresh session. Pass ``scores`` to seed cross-level totals
        (kid playtest 2026-04-28 #4 — points were resetting between levels).
        Default constructs a zero ledger as before."""
        return CoopSession(
            config=config,
            options=options,
            p1=PlayerLifecycle.initial(P1, options.starting_lives),
            p2=PlayerLifecycle.initial(P2, options.starting_lives),
            continues_remaining=options.continues,
            scores=scores if scores is not None else ScoreLedger(),
        )

    def lifecycle(self, slot: PlayerSlot) -> PlayerLifecycle:
        return self.p1 if slot == P1 else self.p2

    def _set_lifecycle(self, slot: PlayerSlot, lc: PlayerLifecycle) -> None:
        if slot == P1:
            self.p1 = lc
        else:
            self.p2 = lc

    def hit(self, slot: PlayerSlot) -> None:
        """Notify session that `slot` just took a fatal hit."""
        self._set_lifecycle(
            slot,
            self.lifecycle(slot).hit(config=self.config, options=self.options),
        )

    def tick(self, dt: float) -> None:
        """Advance both players' lifecycles by `dt`."""
        self.p1 = self.p1.tick(dt, config=self.config)
        self.p2 = self.p2.tick(dt, config=self.config)

    def grant_extra_life(self, slot: PlayerSlot) -> None:
        """Add one life to `slot`. Used by the powerup pickup pipeline so the
        level scene doesn't need to reach for `_set_lifecycle`."""
        from dataclasses import replace as _dc_replace

        lc = self.lifecycle(slot)
        self._set_lifecycle(slot, _dc_replace(lc, lives=lc.lives + 1))

    def consume_clearing_shockwaves(self) -> None:
        """Acknowledge any one-shot clearing-shockwave triggers — called
        by the level scene after it has spawned the shockwave entity."""
        self.p1 = self.p1.consume_clearing_shockwave()
        self.p2 = self.p2.consume_clearing_shockwave()

    def try_consume_continue(self, slot: PlayerSlot) -> bool:
        """Try to spend one continue to bring `slot` back from OUT.

        Returns True if a continue was spent (or was unlimited), False
        if the player isn't OUT or there are no continues left."""
        if self.lifecycle(slot).state != LifecycleState.OUT:
            return False
        if not self.options.unlimited_continues:
            if self.continues_remaining <= 0:
                return False
            self.continues_remaining -= 1
        self._set_lifecycle(
            slot,
            self.lifecycle(slot).consume_continue(config=self.config, options=self.options),
        )
        return True

    @property
    def is_game_over(self) -> bool:
        """True when both players are OUT and no continues remain."""
        if self.options.unlimited_continues:
            return False
        if self.p1.state != LifecycleState.OUT or self.p2.state != LifecycleState.OUT:
            return False
        return self.continues_remaining <= 0
