"""Enemy fire beats — deterministic, path-time-locked.

Per spec §6.3:
  * An enemy with a weapon fires on fixed beats along its formation path
    (e.g. "fire at path-time 1.2s, 2.4s, 3.6s").
  * An "aimed" shot is aimed at the player's position at the moment of
    fire — *not* tracked through the bullet's flight.
  * No random fire is permitted in the slice.

Per-enemy state is just "which beats have I already fired?". A 1-bit set
indexed by beat-index would suffice; we use a small int counter because
beats are listed in ascending order in the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass

from ssdq.core.types import Vec2


@dataclass(frozen=True, slots=True)
class AimSnapshot:
    """The target position captured at the moment of fire.

    `target_pos` may be None if no live player exists; caller decides
    whether to skip firing or fire forward. SSDQ default: skip.
    """

    target_pos: Vec2 | None
    fire_pos: Vec2  # the enemy's own position when firing — bullet origin


@dataclass(frozen=True, slots=True)
class FireEvent:
    """One bullet (or one beat — caller multiplies by bullets_per_beat)."""

    enemy_entity: int  # ssdq.core.types.Entity int alias — Entity is NewType(int)
    weapon_name: str
    pattern: str  # "aimed" | "fan" | "aimed_fan"
    bullets_per_beat: int
    fan_arc_deg: float
    aim: AimSnapshot
    beat_index: int  # which beat (0, 1, 2, …) within the enemy's beat list


class EnemyShooter:
    """Per-enemy fire-beat tracker.

    Construct with the enemy's beat list (ascending seconds along path).
    Each tick, call `advance(now_path_t, ...)` to emit any beats that
    have crossed `now_path_t` since the previous call.

    Tracker is independent of wall-clock — it always works in the
    enemy's path-local time, so a slowed-down or sped-up enemy fires on
    its expected path-relative beats regardless of speed_along_path.
    """

    __slots__ = ("_beats", "_next_beat_idx")

    def __init__(self, beats: tuple[float, ...]) -> None:
        # Defensive: a regression in the data layer could send unsorted
        # beats. Sort here so callers don't need to.
        self._beats: tuple[float, ...] = tuple(sorted(beats))
        self._next_beat_idx = 0

    @property
    def remaining(self) -> int:
        return len(self._beats) - self._next_beat_idx

    def advance(
        self,
        now_path_t: float,
        *,
        enemy_entity: int,
        enemy_pos: Vec2,
        target_pos: Vec2 | None,
        weapon_name: str,
        pattern: str,
        bullets_per_beat: int,
        fan_arc_deg: float,
    ) -> list[FireEvent]:
        """Emit fire events for every beat reached at or before `now_path_t`."""
        out: list[FireEvent] = []
        while (
            self._next_beat_idx < len(self._beats)
            and self._beats[self._next_beat_idx] <= now_path_t
        ):
            out.append(
                FireEvent(
                    enemy_entity=enemy_entity,
                    weapon_name=weapon_name,
                    pattern=pattern,
                    bullets_per_beat=bullets_per_beat,
                    fan_arc_deg=fan_arc_deg,
                    aim=AimSnapshot(target_pos=target_pos, fire_pos=enemy_pos),
                    beat_index=self._next_beat_idx,
                )
            )
            self._next_beat_idx += 1
        return out

    def reset(self) -> None:
        self._next_beat_idx = 0
