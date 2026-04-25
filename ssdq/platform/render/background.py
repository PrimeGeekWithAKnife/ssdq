"""Parallax starfield — three-layer scrolling background.

Star positions are derived deterministically from each star's id via
``ssdq.core.rng.tick_unit`` (using the star id as the channel). That means
the field is identical across runs and across machines, so screenshot
diffs and replays line up. Stars scroll top-to-bottom; layers move at
30, 60, 120 px/sec.
"""

from __future__ import annotations

import pygame

from ssdq.core.rng import tick_unit

_TICKS_PER_SEC = 60.0
# 80 stars total, split across three layers — far/mid/near.
_LAYERS: tuple[tuple[int, float, tuple[int, int, int]], ...] = (
    # (count, speed_px_per_sec, colour)
    (40, 30.0, (90, 90, 110)),
    (25, 60.0, (160, 160, 190)),
    (15, 120.0, (230, 230, 255)),
)


class ParallaxStarfield:
    """Three-layer starfield. Construct once, call :meth:`draw` per frame."""

    __slots__ = ("_height", "_stars", "_width")

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        # Pre-compute (x_base, y_base, speed_px_per_sec, colour) for every star.
        # x is fixed; y wraps over time. Channels are unique per star so the
        # field looks irregular even though it's deterministic.
        stars: list[tuple[float, float, float, tuple[int, int, int]]] = []
        star_id = 0
        for count, speed, colour in _LAYERS:
            for _ in range(count):
                # Use distinct channels for x and y so they don't correlate.
                x = tick_unit(0, channel=10_000 + star_id) * width
                y = tick_unit(0, channel=20_000 + star_id) * height
                stars.append((x, y, speed, colour))
                star_id += 1
        self._stars: tuple[tuple[float, float, float, tuple[int, int, int]], ...] = tuple(stars)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def draw(self, surface: pygame.Surface, tick: int) -> None:
        """Draw the starfield onto ``surface`` for the given simulation tick."""
        elapsed = tick / _TICKS_PER_SEC
        h = self._height
        w = self._width
        for x, y_base, speed, colour in self._stars:
            y = (y_base + speed * elapsed) % h
            # Faster (nearer) stars draw a 2px square; far ones a single pixel.
            if speed >= 100.0:
                pygame.draw.rect(surface, colour, (int(x), int(y), 2, 2))
            else:
                surface.set_at((int(x) % w, int(y)), colour)
