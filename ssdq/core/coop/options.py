"""Per-session options — pause-menu toggles per spec §2 DoD #7."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class CoopOptions:
    """Live options state for a session. Mutated through the pause menu;
    each toggle is a frozen-dataclass replacement."""

    unlimited_lives: bool = False
    unlimited_continues: bool = False
    friendly_fire: bool = False
    ship_on_ship_collision: bool = False
    starting_lives: int = 3
    continues: int = 3

    def with_friendly_fire(self, value: bool) -> CoopOptions:
        return replace(self, friendly_fire=value)

    def with_ship_on_ship(self, value: bool) -> CoopOptions:
        return replace(self, ship_on_ship_collision=value)

    def with_unlimited_lives(self, value: bool) -> CoopOptions:
        return replace(self, unlimited_lives=value)

    def with_unlimited_continues(self, value: bool) -> CoopOptions:
        return replace(self, unlimited_continues=value)
