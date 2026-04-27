"""Scrolling parallax backdrops.

Two backdrops ship today:

* :class:`ParallaxStarfield` — three-layer star field for deep-space
  levels. Star positions are derived deterministically from each star's
  id via :func:`ssdq.core.rng.tick_unit`, so the field is identical
  across runs and across machines.
* :class:`MoonSurfaceBackground` — procedural greys + craters,
  scrolling top-to-bottom underneath the player. Used for level 1
  ("above Moon Base Delta Bravo's surface").

Both classes share the same shape: ``__init__(width, height)`` then
``draw(surface, tick)`` per frame. The :data:`BACKGROUND_REGISTRY`
maps a string name (as written into a level's ``background:`` field)
to the class to instantiate. The Renderer reads the active level's
background name and asks the registry for a matching class.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import pygame

from ssdq.core.rng import tick_unit

_TICKS_PER_SEC = 60.0

# 80 stars total, split across three layers — far/mid/near.
_STAR_LAYERS: tuple[tuple[int, float, tuple[int, int, int]], ...] = (
    # (count, speed_px_per_sec, colour)
    (40, 30.0, (90, 90, 110)),
    (25, 60.0, (160, 160, 190)),
    (15, 120.0, (230, 230, 255)),
)


class Backdrop(Protocol):
    """Common shape every backdrop honours so the Renderer can swap them."""

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    def draw(self, surface: pygame.Surface, tick: int) -> None: ...


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
        for count, speed, colour in _STAR_LAYERS:
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


# ───────── Moon surface ─────────

# Level-1 backdrop. The "view" is the player looking down past their ship
# toward the Moon's surface, which scrolls upward past them as they fly.
# Procedural — two layers of crater discs of varying greys, plus a few
# bright micro-craters / regolith specks for depth. No external assets.

# Base ground colour (mid grey) — drawn over the renderer's clear before
# any craters land on top.
_MOON_GROUND_COLOUR = (78, 78, 86)
# Highlight specks (regolith glints).
_MOON_SPECK_COLOUR = (200, 200, 210)

# Two crater layers — distant (small, dim, slow) and near (bigger, more
# contrast, faster). Each layer entry is
# (count, scroll_speed_px_per_sec, base_radius, radius_jitter, fill_colour, rim_colour).
_MOON_CRATER_LAYERS: tuple[
    tuple[int, float, int, int, tuple[int, int, int], tuple[int, int, int]], ...
] = (
    (28, 22.0, 8, 6, (52, 52, 60), (110, 110, 118)),
    (12, 60.0, 22, 14, (38, 38, 46), (130, 130, 140)),
)
# Specks scroll at the mid layer's speed and exist purely as texture.
_MOON_SPECK_COUNT = 60
_MOON_SPECK_SPEED = 40.0


class MoonSurfaceBackground:
    """Parallax view of the Moon's surface scrolling past beneath the ship.

    Two crater layers + a sprinkle of regolith specks. All positions are
    deterministic (derived from :func:`tick_unit` with per-element
    channels) so screenshots are stable.

    Same construction shape as :class:`ParallaxStarfield`:
    ``MoonSurfaceBackground(width, height)`` then ``draw(surface, tick)``.
    """

    # _craters: tuple of (x, y_base, radius, scroll_speed_px_s, fill, rim)
    # _specks:  tuple of (x, y_base)
    __slots__ = ("_craters", "_height", "_specks", "_width")

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

        craters: list[
            tuple[float, float, int, float, tuple[int, int, int], tuple[int, int, int]]
        ] = []
        crater_id = 0
        for count, speed, base_r, jitter, fill, rim in _MOON_CRATER_LAYERS:
            for _ in range(count):
                # Distinct channels per crater so the layers feel
                # uncorrelated yet entirely deterministic.
                x = tick_unit(0, channel=30_000 + crater_id) * width
                y = tick_unit(0, channel=40_000 + crater_id) * height
                rj = int(tick_unit(0, channel=50_000 + crater_id) * jitter)
                radius = base_r + rj
                craters.append((x, y, radius, speed, fill, rim))
                crater_id += 1
        self._craters: tuple[
            tuple[float, float, int, float, tuple[int, int, int], tuple[int, int, int]], ...
        ] = tuple(craters)

        specks: list[tuple[float, float]] = []
        for sid in range(_MOON_SPECK_COUNT):
            sx = tick_unit(0, channel=60_000 + sid) * width
            sy = tick_unit(0, channel=70_000 + sid) * height
            specks.append((sx, sy))
        self._specks: tuple[tuple[float, float], ...] = tuple(specks)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def draw(self, surface: pygame.Surface, tick: int) -> None:
        """Draw the scrolling Moon surface onto ``surface``."""
        elapsed = tick / _TICKS_PER_SEC
        w = self._width
        h = self._height

        # Solid grey ground first — overrides the renderer's clear so we
        # see Moon, not deep space, before any craters land.
        surface.fill(_MOON_GROUND_COLOUR)

        # Far → near so the near layer overdraws the far. The Y axis
        # scrolls top-to-bottom because the player is moving "up" the
        # screen; ground beneath them appears to move downward.
        for cx, cy_base, radius, speed, fill, rim in self._craters:
            cy = (cy_base + speed * elapsed) % h
            ix = int(cx) % w
            iy = int(cy)
            pygame.draw.circle(surface, fill, (ix, iy), radius)
            pygame.draw.circle(surface, rim, (ix, iy), radius, width=1)

        for sx, sy_base in self._specks:
            y = (sy_base + _MOON_SPECK_SPEED * elapsed) % h
            surface.set_at((int(sx) % w, int(y)), _MOON_SPECK_COLOUR)


# ───────── registry ─────────


# Map of level YAML ``background:`` string → factory ``(width, height)
# -> Backdrop``. New level settings register here. Renderer reads via
# :func:`make_background`; an unknown name falls back to the starfield
# default rather than crashing — a typo in level data shouldn't blow up
# a play session.
#
# Stored as a Callable rather than ``type[Backdrop]`` so future
# backdrops can be functions / partials with extra config without
# inheritance ceremony.
BackgroundFactory = Callable[[int, int], Backdrop]
BACKGROUND_REGISTRY: dict[str, BackgroundFactory] = {
    "bg_starfield_01": ParallaxStarfield,
    "bg_moon_surface": MoonSurfaceBackground,
}

# Default name used when the active level is unknown / unset (e.g. the
# render-smoke test which doesn't load a level).
DEFAULT_BACKGROUND_NAME = "bg_starfield_01"


def make_background(name: str, width: int, height: int) -> Backdrop:
    """Look up a backdrop by name and instantiate it.

    Unknown names fall back to the default (starfield) so a content typo
    degrades gracefully — a missing backdrop should not crash a play
    session, just make a sterile-looking level.
    """
    factory = BACKGROUND_REGISTRY.get(name) or BACKGROUND_REGISTRY[DEFAULT_BACKGROUND_NAME]
    return factory(width, height)
