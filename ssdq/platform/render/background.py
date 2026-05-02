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

# Single shared scroll speed across all crater layers. Kid playtest
# 2026-05-02 #11: previously two layers scrolled at 22 and 60 px/s and
# the kid read this as "bubbles moving at different speeds" — wanted
# craters to all move together. Visual depth comes from size + tone
# variation per layer, not parallax.
_MOON_SCROLL_SPEED = 36.0

# Two crater size/colour layers — distant (small, dim) and near (bigger,
# more contrast). Both scroll at the same speed (see comment above).
# Radii bumped per kid playtest 2026-05-02 #7 ("perhaps some larger
# craters"): base/jitter doubled so the surface reads as proper craters
# rather than dots.
# Each layer entry is (count, base_radius, radius_jitter, fill, rim).
_MOON_CRATER_LAYERS: tuple[
    tuple[int, int, int, tuple[int, int, int], tuple[int, int, int]], ...
] = (
    (24, 18, 10, (52, 52, 60), (110, 110, 118)),   # was 28 small @ r8±6
    (10, 38, 18, (38, 38, 46), (130, 130, 140)),   # was 12 mid   @ r22±14
)
# Specks (light-grey dots) removed — kid read them as "stars in the
# background" on the moon surface level (kid playtest 2026-05-02 #7).


class MoonSurfaceBackground:
    """Parallax view of the Moon's surface scrolling past beneath the ship.

    Two crater layers + a sprinkle of regolith specks. All positions are
    deterministic (derived from :func:`tick_unit` with per-element
    channels) so screenshots are stable.

    Same construction shape as :class:`ParallaxStarfield`:
    ``MoonSurfaceBackground(width, height)`` then ``draw(surface, tick)``.
    """

    # _craters: tuple of (x, y_base, radius, fill, rim) — all craters
    # scroll at the shared _MOON_SCROLL_SPEED so they move as one.
    __slots__ = ("_craters", "_height", "_width")

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

        craters: list[
            tuple[float, float, int, tuple[int, int, int], tuple[int, int, int]]
        ] = []
        crater_id = 0
        for count, base_r, jitter, fill, rim in _MOON_CRATER_LAYERS:
            for _ in range(count):
                # Distinct channels per crater so positions feel
                # uncorrelated yet entirely deterministic.
                x = tick_unit(0, channel=30_000 + crater_id) * width
                y = tick_unit(0, channel=40_000 + crater_id) * height
                rj = int(tick_unit(0, channel=50_000 + crater_id) * jitter)
                radius = base_r + rj
                craters.append((x, y, radius, fill, rim))
                crater_id += 1
        self._craters: tuple[
            tuple[float, float, int, tuple[int, int, int], tuple[int, int, int]], ...
        ] = tuple(craters)

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

        # All craters share the same scroll speed (kid playtest #11) so
        # the surface reads as a single sliding ground plane rather than
        # a multi-speed parallax. Sort order is insertion order so
        # bigger/darker craters in layer 2 still overdraw the smaller
        # layer-1 ones.
        scroll = _MOON_SCROLL_SPEED * elapsed
        for cx, cy_base, radius, fill, rim in self._craters:
            cy = (cy_base + scroll) % h
            ix = int(cx) % w
            iy = int(cy)
            pygame.draw.circle(surface, fill, (ix, iy), radius)
            pygame.draw.circle(surface, rim, (ix, iy), radius, width=2)


# ───────── Earth horizon (level 4) ─────────

# Level-4 view: the player is above Earth, fighting for the planet's air
# defence. Composition:
#   * Sparse star layer behind the planet (re-uses the deterministic
#     ParallaxStarfield channel allocation for visual consistency).
#   * A big blue Earth limb covering the bottom half of the screen with
#     a soft horizon glow, scrolling slowly so the kid feels motion.
#   * A handful of cloud streaks scrolling slightly faster than the
#     planet for parallax depth.
# Kid playtest 2026-04-28 #6: "fourth level should be above earth so
# some kind of Earth in the background would be good".

_EARTH_OCEAN = (28, 70, 140)
_EARTH_OCEAN_DEEP = (18, 50, 110)
_EARTH_LAND = (66, 130, 78)
_EARTH_LAND_DARK = (44, 100, 56)
_EARTH_LAND_SAND = (170, 150, 100)
_EARTH_ICE = (220, 230, 235)
_EARTH_HORIZON_GLOW = (140, 200, 255)
_EARTH_CLOUD = (220, 230, 240)
# Earth's centre sits well below the screen bottom so only its upper
# limb is visible — frac of screen height below the visible play area.
_EARTH_CENTRE_BELOW_FRAC = 0.6
_EARTH_RADIUS_FRAC = 1.4  # × screen height
_EARTH_CLOUD_COUNT = 8
_EARTH_CLOUD_SPEED = 18.0

# Hand-tuned blob clusters per landmass. Each entry is a list of
# (rel_x, rel_y, radius_px) circles painted on top of the ocean disc;
# overlapping circles merge into an organic landmass silhouette
# rather than the previous three obvious vector circles. Coords are
# fractions of the Earth radius from the disc centre — only the upper
# limb is visible, so all rel_y values are negative.
# Kid playtest 2026-05-02 #18: "Earth looks weird with circular
# continents". The painterly title backdrop already does this, but at
# build time via PIL — we replicate the technique using pygame
# primitives so it stays cheap on the per-frame draw path.
_EARTH_LANDMASSES: tuple[tuple[tuple[float, float, int, tuple[int, int, int]], ...], ...] = (
    # Africa — large central landmass spanning the day-side limb.
    (
        (-0.18, -0.32, 60, _EARTH_LAND),
        (-0.10, -0.26, 70, _EARTH_LAND),
        (-0.05, -0.20, 55, _EARTH_LAND),
        (-0.22, -0.42, 50, _EARTH_LAND),
        (-0.15, -0.50, 45, _EARTH_LAND_DARK),
        # Sahara band on the upper half of Africa.
        (-0.18, -0.55, 55, _EARTH_LAND_SAND),
        (-0.10, -0.58, 40, _EARTH_LAND_SAND),
    ),
    # Europe + UK — a small cluster top-left of Africa.
    (
        (-0.30, -0.78, 24, _EARTH_LAND),
        (-0.22, -0.75, 18, _EARTH_LAND),
        (-0.36, -0.82, 14, _EARTH_LAND),  # UK suggestion
        (-0.40, -0.85, 10, _EARTH_LAND),
    ),
    # Arabia — wedge to the right of Africa.
    (
        (+0.12, -0.50, 36, _EARTH_LAND_SAND),
        (+0.20, -0.45, 30, _EARTH_LAND_SAND),
        (+0.18, -0.55, 24, _EARTH_LAND),
    ),
    # Madagascar — small offshore dot.
    (
        (+0.10, -0.16, 14, _EARTH_LAND),
    ),
    # South America's eastern coast peeking at the day-side edge.
    (
        (-0.65, -0.30, 32, _EARTH_LAND),
        (-0.70, -0.40, 26, _EARTH_LAND_DARK),
        (-0.62, -0.22, 24, _EARTH_LAND),
    ),
    # Greenland / arctic ice cap — bright white near the top edge.
    (
        (-0.40, -0.92, 22, _EARTH_ICE),
        (-0.32, -0.95, 16, _EARTH_ICE),
    ),
    # India / SE-Asia hint to the far right.
    (
        (+0.42, -0.55, 26, _EARTH_LAND),
        (+0.50, -0.50, 22, _EARTH_LAND),
        (+0.48, -0.62, 16, _EARTH_LAND_DARK),
    ),
)


class EarthHorizonBackground:
    """Big blue Earth dominating the bottom of the screen.

    Star layer scrolling behind, Earth limb fixed (we're in orbit, not
    diving), clouds slowly drifting across the limb for parallax. The
    player sits in the top half so the playfield isn't visually crowded.
    """

    __slots__ = ("_clouds", "_height", "_stars", "_width")

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        # Re-use the starfield star generator at a quieter density.
        stars: list[tuple[float, float, float, tuple[int, int, int]]] = []
        star_id = 0
        # Half as many stars as the regular starfield — Earth's glow
        # would drown out a busy sky.
        for count, speed, colour in (
            (20, 24.0, (90, 90, 110)),
            (12, 50.0, (160, 160, 190)),
        ):
            for _ in range(count):
                x = tick_unit(0, channel=80_000 + star_id) * width
                y = tick_unit(0, channel=81_000 + star_id) * (height // 2)
                stars.append((x, y, speed, colour))
                star_id += 1
        self._stars: tuple[tuple[float, float, float, tuple[int, int, int]], ...] = tuple(stars)

        clouds: list[tuple[float, float, int, int]] = []
        for cid in range(_EARTH_CLOUD_COUNT):
            cx = tick_unit(0, channel=82_000 + cid) * width
            cy = tick_unit(0, channel=83_000 + cid) * (height // 3) + height // 2
            cw = 24 + int(tick_unit(0, channel=84_000 + cid) * 36)
            ch = 4 + int(tick_unit(0, channel=85_000 + cid) * 4)
            clouds.append((cx, cy, cw, ch))
        self._clouds: tuple[tuple[float, float, int, int], ...] = tuple(clouds)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def draw(self, surface: pygame.Surface, tick: int) -> None:
        elapsed = tick / _TICKS_PER_SEC
        w = self._width
        h = self._height

        # Deep-space background.
        surface.fill((4, 6, 22))

        # Stars (only in the upper half — Earth covers the rest).
        for x, y_base, speed, colour in self._stars:
            y = (y_base + speed * elapsed) % (h // 2)
            pygame.draw.rect(surface, colour, (int(x) % w, int(y), 1, 1))

        # Earth limb — a giant circle whose centre is way below screen.
        cx = w // 2
        cy_offset = int(h * _EARTH_CENTRE_BELOW_FRAC)
        radius = int(h * _EARTH_RADIUS_FRAC)
        cy = h + cy_offset
        # Soft multi-ring horizon glow above the planet edge — gentler
        # falloff than a single thick ring.
        for ring in range(4):
            ring_r = radius + 4 + ring * 5
            pygame.draw.circle(surface, _EARTH_HORIZON_GLOW, (cx, cy), ring_r, width=2)
        # Ocean disc — solid base; the deep-ocean tone overlaps slightly
        # above the day-side terminator to suggest depth without a full
        # day/night gradient (which would be costly per frame).
        pygame.draw.circle(surface, _EARTH_OCEAN, (cx, cy), radius)
        pygame.draw.circle(surface, _EARTH_OCEAN_DEEP, (cx + radius // 4, cy), radius // 2)

        # Painterly landmasses — overlapping coloured circles per
        # _EARTH_LANDMASSES so each continent reads as an organic blob,
        # not three vector circles. Per kid playtest #18.
        for blobs in _EARTH_LANDMASSES:
            for rel_x, rel_y, br, fill in blobs:
                bx = cx + int(rel_x * radius)
                by = cy + int(rel_y * radius)
                pygame.draw.circle(surface, fill, (bx, by), br)

        # Clouds drift across the limb.
        for x_base, y, cw, ch in self._clouds:
            x = (x_base + _EARTH_CLOUD_SPEED * elapsed) % (w + cw) - cw
            pygame.draw.ellipse(
                surface, _EARTH_CLOUD, pygame.Rect(int(x), int(y), cw, ch)
            )


# ───────── Space station (level 3) ─────────

# Level-3 view: above an alien space station. A dense backdrop of
# modular grey structures scrolls past — long beams, hub modules, and
# the occasional amber light. The player reads as fighting through a
# disorganised industrial scene. Kid playtest 2026-04-28 #6.

_STATION_BG = (8, 10, 20)
_STATION_HULL = (90, 96, 110)
_STATION_HULL_DARK = (50, 55, 70)
_STATION_PANEL = (40, 44, 60)
_STATION_LIGHT = (255, 200, 80)
_STATION_LIGHT_DIM = (160, 110, 40)


class SpaceStationBackground:
    """Modular space-station segments scrolling past beneath the player.

    Procedurally generates a few 'stack' columns of station modules —
    each column has a vertical beam plus a couple of hub blocks at
    deterministic offsets. Columns scroll downward at different speeds
    for parallax. Amber 'porthole' lights pulse subtly to read as alive.
    """

    __slots__ = ("_columns", "_height", "_width")

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        # Three columns at different x positions and parallax speeds.
        cols: list[tuple[int, float, int, int]] = []
        for cid, (x_frac, speed, beam_w, hub_size) in enumerate(
            (
                (0.18, 30.0, 14, 60),
                (0.50, 50.0, 22, 90),
                (0.80, 38.0, 16, 70),
            )
        ):
            cols.append((int(width * x_frac), speed, beam_w, hub_size))
        self._columns: tuple[tuple[int, float, int, int], ...] = tuple(cols)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def draw(self, surface: pygame.Surface, tick: int) -> None:
        elapsed = tick / _TICKS_PER_SEC
        w = self._width
        h = self._height
        surface.fill(_STATION_BG)
        # A faint star sparkle so the gaps between modules don't feel dead.
        for sid in range(30):
            sx = int(tick_unit(0, channel=90_000 + sid) * w)
            sy = int(
                (tick_unit(0, channel=91_000 + sid) * h + 18.0 * elapsed) % h
            )
            surface.set_at((sx, sy), (110, 110, 130))
        # Each column: long vertical beam + a hub somewhere along it.
        for cx, speed, beam_w, hub_size in self._columns:
            offset = (speed * elapsed) % (h * 2)
            # Beam — full height repeated.
            beam_y = int(offset) - h
            pygame.draw.rect(
                surface,
                _STATION_HULL_DARK,
                (cx - beam_w // 2, beam_y, beam_w, h * 2),
            )
            # Beam highlight stripe.
            pygame.draw.rect(
                surface,
                _STATION_HULL,
                (cx - beam_w // 4, beam_y, beam_w // 2, h * 2),
            )
            # Hub at a deterministic Y inside the beam.
            hub_y = (int(offset * 1.4) % (h + hub_size)) - hub_size
            hub_rect = pygame.Rect(
                cx - hub_size // 2, hub_y, hub_size, hub_size // 2
            )
            pygame.draw.rect(surface, _STATION_HULL, hub_rect)
            pygame.draw.rect(surface, _STATION_HULL_DARK, hub_rect, width=2)
            # Porthole row inside the hub.
            light = _STATION_LIGHT if (tick // 30) % 2 == 0 else _STATION_LIGHT_DIM
            for px in range(3):
                pygame.draw.rect(
                    surface,
                    light,
                    (
                        cx - hub_size // 2 + 8 + px * (hub_size // 4),
                        hub_y + hub_size // 6,
                        4,
                        4,
                    ),
                )
            # Solar-panel wings on the larger middle column.
            if hub_size >= 90:
                panel_w = 80
                panel_h = 18
                pygame.draw.rect(
                    surface,
                    _STATION_PANEL,
                    (cx - hub_size // 2 - panel_w, hub_y + 4, panel_w, panel_h),
                )
                pygame.draw.rect(
                    surface,
                    _STATION_PANEL,
                    (cx + hub_size // 2, hub_y + 4, panel_w, panel_h),
                )


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
    "bg_space_station": SpaceStationBackground,
    "bg_earth": EarthHorizonBackground,
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
