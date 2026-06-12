"""Wave scheduler: walks a level's wave script and emits spawn events.

Stateful: tracks elapsed sim time and which sub-spawn within a wave has
fired so far. Each `tick()` call advances sim time by `dt` and returns
the new spawn events that crossed the threshold this tick.

Determinism: ordering is by (wave_at + delay + spacing*i, wave_idx,
spawn_idx, i) so two identical schedulers tick-for-tick emit identical
event sequences regardless of host platform.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ssdq.core.content.schema import LevelDef, WaveDef


@dataclass(frozen=True, slots=True)
class SpawnEvent:
    """One enemy to spawn at sim_time = absolute_time, following formation."""

    enemy: str
    formation: str
    mirrored: bool
    # path_t0 is the wall-clock time the enemy "started" its formation;
    # at any later sim_time the enemy is at evaluate_path(formation,
    # (sim_time - path_t0) * speed_multiplier / formation.duration, mirrored).
    path_t0: float
    wave_index: int  # for debug/tracing
    spawn_index: int
    member_index: int  # 0..count-1 within the spawn
    return_passes: int = 0  # extra formation passes if enemy survives


@dataclass(frozen=True, slots=True)
class BossEvent:
    """A boss-spawn event. Distinct from SpawnEvent — boss has its own
    state machine in the level scene."""

    boss: str
    wave_index: int


@dataclass(frozen=True, slots=True)
class _PendingMember:
    """Internal: one not-yet-fired member of one spawn of one wave."""

    fire_at: float
    enemy: str
    formation: str
    mirrored: bool
    wave_index: int
    spawn_index: int
    member_index: int
    return_passes: int


class WaveScheduler:
    """Drives spawn-event emission over a level's lifetime.

    Construct from a `LevelDef`; call `tick(dt)` each simulation step.
    Returns the events that fired this tick. Boss waves are returned
    via `boss_events()` as a separate stream (caller routes them to
    the boss state machine).
    """

    __slots__ = (
        "_boss_pending",
        "_density_multiplier",
        "_elapsed",
        "_level",
        "_pending",
    )

    def __init__(self, level: LevelDef, *, density_multiplier: float = 1.0) -> None:
        self._level = level
        # Density multiplier scales each spawn's member count at schedule
        # build time. 1.0 = co-op (every member fires); <1.0 thins out
        # the wave (single-player gets fewer enemies per spawn). Always
        # at least 1 member per spawn so a wave never disappears entirely
        # (which would break boss-trigger timing in some level scripts).
        # Kid playtest 2026-05-23: solo runs need less density to feel
        # comparable to co-op.
        self._density_multiplier = max(0.0, density_multiplier)
        self._elapsed = 0.0
        self._pending: list[_PendingMember] = []
        self._boss_pending: list[BossEvent] = []
        self._build_schedule()

    @property
    def elapsed(self) -> float:
        return self._elapsed

    @property
    def remaining(self) -> int:
        """Spawn events still to be emitted (excludes boss events)."""
        return len(self._pending)

    @property
    def boss_pending(self) -> int:
        return len(self._boss_pending)

    def _build_schedule(self) -> None:
        """Flatten the WaveDef tree into a sorted list of pending spawns."""
        for wi, wave in enumerate(self._level.waves):
            if wave.boss is not None:
                self._boss_pending.append(BossEvent(boss=wave.boss, wave_index=wi))
                continue
            for si, spawn in enumerate(wave.spawns):
                base = wave.at + spawn.delay
                # Apply density scaling — floor of 1 so a spawn never
                # disappears entirely. Uses banker's-style rounding via
                # int(x + 0.5) for deterministic behaviour on the .5
                # boundary across hosts.
                effective_count = max(
                    1, int(spawn.count * self._density_multiplier + 0.5)
                )
                for mi in range(effective_count):
                    self._pending.append(
                        _PendingMember(
                            fire_at=base + spawn.spacing * mi,
                            enemy=spawn.enemy,
                            formation=spawn.formation,
                            mirrored=spawn.mirrored,
                            wave_index=wi,
                            spawn_index=si,
                            member_index=mi,
                            return_passes=spawn.return_passes,
                        )
                    )
        # Stable sort by absolute fire time, then origin coords for determinism.
        self._pending.sort(key=lambda m: (m.fire_at, m.wave_index, m.spawn_index, m.member_index))
        # Boss events likewise sorted by their wave's fire time so
        # `pending_boss_events()` can `break` on first-not-ready safely.
        self._boss_pending.sort(key=lambda be: self._level.waves[be.wave_index].at)

    def tick(self, dt: float) -> list[SpawnEvent]:
        """Advance sim time by `dt`; return events that fired this tick."""
        if dt < 0.0:
            raise ValueError(f"dt must be ≥ 0, got {dt}")
        new_elapsed = self._elapsed + dt
        out: list[SpawnEvent] = []
        # _pending is sorted by fire_at; pop from the front while ready.
        keep_from = 0
        for i, m in enumerate(self._pending):
            if m.fire_at <= new_elapsed:
                out.append(
                    SpawnEvent(
                        enemy=m.enemy,
                        formation=m.formation,
                        mirrored=m.mirrored,
                        path_t0=m.fire_at,
                        wave_index=m.wave_index,
                        spawn_index=m.spawn_index,
                        member_index=m.member_index,
                        return_passes=m.return_passes,
                    )
                )
                keep_from = i + 1
            else:
                break
        if keep_from > 0:
            self._pending = self._pending[keep_from:]
        self._elapsed = new_elapsed
        return out

    def cue_next_wave_if_idle(
        self,
        have_live_enemies: bool,
        *,
        max_gap: float = 5.0,
    ) -> None:
        """Fast-forward elapsed time so the next pending spawn fires
        within ``max_gap`` seconds, but only when the level is genuinely
        idle (no live enemies AND a pending spawn currently > ``max_gap``
        away). Caller passes ``have_live_enemies`` because the scheduler
        is content-only and doesn't see the ECS world.

        No-ops when:
        * the player still has enemies to clear (``have_live_enemies``);
        * the pending list is empty (boss waves still arrive on their own
          schedule via ``pending_boss_events``);
        * the next spawn is already within ``max_gap`` (would otherwise
          rewind elapsed time, which is wrong).

        Kid playtest 2026-05-02 #6: "if a wave finishes quickly cue the
        next wave within 5 seconds." Replay determinism is broken by this
        mid-level clock advance — there are no shipped saves so the
        trade-off is acceptable; flagged in the commit message.
        """
        if have_live_enemies or not self._pending:
            return
        next_fire = self._pending[0].fire_at
        target_elapsed = next_fire - max_gap
        if target_elapsed <= self._elapsed:
            return  # already close enough; never rewind
        self._elapsed = target_elapsed

    def pending_boss_events(self, before_sim_time: float) -> list[BossEvent]:
        """Boss events whose wave.at ≤ before_sim_time. Returned in
        order; caller is responsible for consuming them via
        `consume_boss_event()`."""
        ready: list[BossEvent] = []
        for be in self._boss_pending:
            wave = self._level.waves[be.wave_index]
            if wave.at <= before_sim_time:
                ready.append(be)
            else:
                break
        return ready

    def consume_boss_event(self, event: BossEvent) -> None:
        """Mark a boss event as handled — the boss state machine has
        taken responsibility for it."""
        try:
            self._boss_pending.remove(event)
        except ValueError as ex:
            raise ValueError(f"boss event {event!r} not pending") from ex

    def upcoming(self, within: float) -> Iterator[SpawnEvent]:
        """Peek at spawn events fitting in [now, now+within] — for debug
        overlays or AI lookahead. Does not advance state."""
        cutoff = self._elapsed + within
        for m in self._pending:
            if m.fire_at > cutoff:
                break
            yield SpawnEvent(
                enemy=m.enemy,
                formation=m.formation,
                mirrored=m.mirrored,
                path_t0=m.fire_at,
                wave_index=m.wave_index,
                spawn_index=m.spawn_index,
                member_index=m.member_index,
                return_passes=m.return_passes,
            )

    @property
    def waves(self) -> tuple[WaveDef, ...]:
        return self._level.waves
