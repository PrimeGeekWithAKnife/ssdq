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

import math
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


# ───────── Earth orbit (level 4, prerendered) ─────────

# Level-4 dedicated backdrop, replacing the per-frame-primitives
# EarthHorizonBackground stopgap. The whole planet — ocean, continents,
# day/night terminator, atmosphere rim glow — is painted ONCE at init
# into an opaque base Surface covering the lower part of the frame;
# clouds go into a handful of small per-pixel-alpha band surfaces that
# sway a few px/s relative to the planet surface so it reads as alive.
# Per-frame cost is one opaque blit, ~7 small cloud blits, ~50 star ops
# and a handful of tiny "distant fighter battle" primitives. Like
# ParallaxStarfield, the sky above the planet relies on the Renderer's
# frame clear rather than a redundant fill.
#
# Determinism: every random choice comes from tick_unit(0, channel=…)
# at init or is tick-derived in draw(). This backdrop draws from
# channels 60_0xx (fighters) and 64_0xx/65_0xx (stars) — chosen to stay
# clear of the gameplay channel blocks (hyperspace owns 61_0xx, strays
# own 70_00x); check for collisions before adding new allocations.

# Matches renderer._CLEAR_COLOUR exactly — the base layer's baked-in
# sky (the sliver between its top row and the limb curve) must be
# indistinguishable from the renderer's clear above it, or a seam line
# appears across the frame.
_ORBIT_SPACE = (5, 5, 12)
_ORBIT_OCEAN = (24, 68, 148)
_ORBIT_OCEAN_DEEP = (14, 46, 112)
_ORBIT_LAND = (70, 132, 74)
_ORBIT_LAND_DARK = (46, 100, 54)
_ORBIT_LAND_SAND = (172, 150, 96)
_ORBIT_ICE = (222, 232, 238)
_ORBIT_CLOUD = (245, 250, 255)
_ORBIT_GLOW = (120, 210, 255)
_ORBIT_LIMB_BRIGHT = (190, 235, 255)
# Limb top sits at this fraction of screen height at centre-x, so the
# planet fills roughly the lower 42% of the frame.
_ORBIT_LIMB_TOP_FRAC = 0.58
_ORBIT_RADIUS_FRAC = 2.2  # × screen height — huge, so the limb is a gentle curve
_ORBIT_GLOW_MARGIN = 22  # px of sky above the limb baked into the base layer
# Cloud layer sways sinusoidally ±this many px; peak relative speed vs
# the surface layer ≈ sway·2π/period ≈ 4 px/s. Sway (rather than an
# endless one-way creep) keeps the init-time limb mask valid forever.
_ORBIT_CLOUD_SWAY_PX = 20.0
_ORBIT_CLOUD_SWAY_PERIOD_S = 34.0
# Quieter star density than ParallaxStarfield — the planet owns the frame.
_ORBIT_STAR_LAYERS: tuple[tuple[int, float, tuple[int, int, int]], ...] = (
    (26, 22.0, (90, 90, 110)),
    (16, 44.0, (160, 160, 190)),
    (8, 88.0, (230, 230, 255)),
)
# Distant fighter battle: small, muted, strictly behind gameplay. Two
# "factions" tinted apart; laser + explosion colours kept clearly below
# gameplay sprite brightness so nothing reads as a real threat/pickup —
# but bright enough to actually catch the eye on a TV (playtest
# 2026-07-09: the first pass was invisible in gameplay).
_ORBIT_FIGHTER_COUNT = 8
_ORBIT_FIGHTER_A = (150, 162, 196)
_ORBIT_FIGHTER_B = (196, 148, 122)
_ORBIT_LASER_A = (190, 80, 80)
_ORBIT_LASER_B = (80, 180, 130)
_ORBIT_BLIP_PERIOD = 300  # ticks between explosion blips (~5s)
_ORBIT_BLIP_TICKS = 14  # blip visible for this many ticks
_ORBIT_BLIP_COLOURS = ((255, 190, 120), (200, 130, 75), (120, 70, 50))


def _lerp_colour(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    """Integer colour lerp for prerendered gradient rings/strips."""
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


class EarthOrbit:
    """Prerendered Earth limb + drifting clouds + distant fighter battle.

    All heavy painting happens once in ``__init__``: an opaque planet
    base layer (ocean, blob-walk continents, terminator shading, rim
    glow) plus a handful of small bounding-box cloud-band surfaces
    masked inside the limb. ``draw`` is one opaque blit, ~7 small alpha
    blits (cost scales with actual cloud pixels, not screen area), the
    star loop and ≤10 tiny battle primitives — nothing is allocated per
    frame.
    """

    __slots__ = (
        "_base",
        "_cloud_bands",
        "_fighters",
        "_height",
        "_stars",
        "_width",
        "_y0",
    )

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        w = width
        h = height

        # ── geometry ──
        limb_top = int(h * _ORBIT_LIMB_TOP_FRAC)
        radius = int(h * _ORBIT_RADIUS_FRAC)
        cx = w // 2
        cy = limb_top + radius  # planet centre, far below the screen
        y0 = max(0, limb_top - _ORBIT_GLOW_MARGIN)  # top row of the planet base layer
        self._y0 = y0

        # Limb height per column, extended past the screen edges so the
        # cloud-safety check below covers the sway range. Index offset
        # by `ext`; on-screen columns are limb_ext[x + ext].
        ext = int(_ORBIT_CLOUD_SWAY_PX) + 12
        rr = float(radius * radius)
        limb_ext: list[int] = []
        for x in range(-ext, w + ext):
            dx = float(x - cx)
            limb_ext.append(int(cy - math.sqrt(max(0.0, rr - dx * dx))))

        # Init-time art channels: 63_000+ consumed sequentially.
        chan = 63_000

        def unit() -> float:
            nonlocal chan
            chan += 1
            return tick_unit(0, channel=chan)

        # ── base layer: ocean + continents + terminator + limb + glow ──
        # Opaque band from just above the limb down to the screen bottom
        # (local coords are screen coords shifted up by y0). The sliver
        # of sky baked into the band is _ORBIT_SPACE == renderer clear.
        base_h = h - y0
        base = pygame.Surface((w, base_h))
        base.fill(_ORBIT_SPACE)
        cy_local = cy - y0
        pygame.draw.circle(base, _ORBIT_OCEAN, (cx, cy_local), radius)
        # Deep-ocean patches — large dim blobs for water depth variation.
        for _ in range(4):
            ox = int(unit() * w)
            oy = int(limb_ext[ox + ext] + 120 + unit() * (h - limb_ext[ox + ext]))
            orr = 90 + int(unit() * 130)
            pygame.draw.circle(base, _ORBIT_OCEAN_DEEP, (ox, oy - y0), orr)
        # Continents — blob-walk landmasses: overlapping circles wander
        # from a seed point so each landmass reads as an organic shape.
        # Blobs that poke above the limb get shaved by the per-column
        # sky repaint below, i.e. land meeting the horizon is fine.
        for _ in range(6):
            bx = unit() * w
            local_limb = limb_ext[int(bx) % w + ext]
            by = local_limb + 26.0 + unit() * (h - local_limb) * 0.8
            sandy = unit() < 0.33  # desert-heavy landmass
            for _blob in range(9 + int(unit() * 8)):
                br = 14 + int(unit() * 30)
                roll = unit()
                if sandy and roll < 0.45:
                    colour = _ORBIT_LAND_SAND
                elif roll < 0.2:
                    colour = _ORBIT_LAND_DARK
                else:
                    colour = _ORBIT_LAND
                pygame.draw.circle(base, colour, (int(bx), int(by) - y0), br)
                bx += (unit() - 0.5) * 66.0
                by += (unit() - 0.5) * 50.0
        # Polar ice hugging the limb — shaved to the horizon curve below.
        ix = w * (0.35 + unit() * 0.3)
        for _ in range(4):
            iy = limb_ext[int(ix) % w + ext] + 4 + unit() * 14
            ir = 10 + int(unit() * 12)
            pygame.draw.circle(base, _ORBIT_ICE, (int(ix), int(iy) - y0), ir)
            ix += (unit() - 0.5) * 60.0
        # Day/night terminator — night falls off to the right. Painted
        # as 8px vertical alpha strips (smooth enough; init-only cost).
        shade = pygame.Surface((w, base_h), pygame.SRCALPHA)
        for sx in range(0, w, 8):
            t = max(0.0, min(1.0, (sx / w - 0.42) / 0.58))
            alpha = int(150 * t * t)
            if alpha > 0:
                shade.fill((0, 0, 0, alpha), (sx, 0, 8, base_h))
        base.blit(shade, (0, 0))
        # Per-column sky repaint above the limb curve — restores space
        # colour over any blob/terminator spill and hard-clips the disc.
        for x in range(w):
            ly = limb_ext[x + ext]
            if ly > y0:
                base.fill(_ORBIT_SPACE, (x, 0, 1, ly - y0))
        # Atmosphere rim: bright cyan limb line fading inward, plus an
        # outer glow pre-blended against the space colour (drawing them
        # opaque keeps the base layer a fast colourless blit).
        for k in range(9):
            ring = _lerp_colour(_ORBIT_GLOW, _ORBIT_SPACE, k / 8.0)
            pygame.draw.circle(base, ring, (cx, cy_local), radius + 2 + k * 2, width=3)
        for k in range(3):
            ring = _lerp_colour(_ORBIT_LIMB_BRIGHT, _ORBIT_OCEAN, k / 3.0)
            pygame.draw.circle(base, ring, (cx, cy_local), radius - 1 - k * 2, width=3)

        # ── cloud bands: soft white streaks, masked inside the limb ──
        # Each band of puffs is baked into its own bounding-box SRCALPHA
        # surface (wide dim haze ellipse under a brighter core per puff)
        # so the per-frame alpha-blit cost tracks actual cloud pixels
        # rather than the whole planet area. Night-side puffs are
        # pre-dimmed toward space blue. Every puff is placed so its top
        # edge stays below the limb for any sway offset (checked against
        # the extended limb table), so the swaying bands never spill
        # into space.
        margin = int(_ORBIT_CLOUD_SWAY_PX) + 6
        bands: list[tuple[pygame.Surface, int, int]] = []
        for _band in range(7):
            bx = unit() * w
            band_limb = limb_ext[max(0, min(w - 1, int(bx))) + ext]
            by = band_limb + 18.0 + unit() * max(24.0, h - band_limb - 24.0)
            puffs: list[tuple[pygame.Rect, tuple[int, int, int]]] = []
            for _puff in range(3 + int(unit() * 4)):
                ew = 44 + int(unit() * 70)
                eh = 8 + int(unit() * 10)
                lo = max(0, int(bx - ew / 2) - margin - 10 + ext)
                hi = min(len(limb_ext) - 1, int(bx + ew / 2) + margin + 10 + ext)
                limb_floor = max(limb_ext[lo : hi + 1], default=limb_top)
                py = max(by, limb_floor + 10.0 + eh / 2.0)
                night = max(0.0, min(1.0, (bx / w - 0.42) / 0.58))
                tone = _lerp_colour(_ORBIT_CLOUD, _ORBIT_SPACE, 0.55 * night)
                puffs.append(
                    (pygame.Rect(int(bx - ew / 2), int(py - eh / 2), ew, eh), tone)
                )
                bx += ew * (0.45 + unit() * 0.25)
                by = py + (unit() - 0.5) * 18.0
            # Band bounding box, inflated to fit the haze halos.
            bbox = puffs[0][0].inflate(20, 10)
            for rect, _tone in puffs[1:]:
                bbox.union_ip(rect.inflate(20, 10))
            band_surf = pygame.Surface(bbox.size, pygame.SRCALPHA)
            for rect, tone in puffs:
                local = rect.move(-bbox.x, -bbox.y)
                pygame.draw.ellipse(band_surf, (*tone, 44), local.inflate(18, 8))
            for rect, tone in puffs:
                local = rect.move(-bbox.x, -bbox.y)
                pygame.draw.ellipse(band_surf, (*tone, 88), local)
            bands.append((band_surf, bbox.x, bbox.y))

        # convert() for fast blits; skipped when no display mode is set
        # (pure-logic tests can still construct the backdrop).
        if pygame.display.get_surface() is not None:
            base = base.convert()
            bands = [(s.convert_alpha(), bx_, by_) for s, bx_, by_ in bands]
        self._base = base
        self._cloud_bands: tuple[tuple[pygame.Surface, int, int], ...] = tuple(bands)

        # ── stars (channels 64_000/65_000 — 61_0xx belongs to hyperspace) ──
        # Same idiom as ParallaxStarfield, but each star wraps at its own
        # column's limb height so it slides behind the planet edge. Wrap is
        # clamped to the surface height: at extreme aspect ratios the limb
        # curve exceeds h at the frame edges and set_at would raise.
        stars: list[tuple[int, float, float, int, tuple[int, int, int]]] = []
        star_id = 0
        for count, speed, colour in _ORBIT_STAR_LAYERS:
            for _ in range(count):
                sx = int(tick_unit(0, channel=64_000 + star_id) * w) % w
                sy = tick_unit(0, channel=65_000 + star_id) * limb_top
                wrap = max(1, min(h, limb_ext[sx + ext]))
                stars.append((sx, sy, speed, wrap, colour))
                star_id += 1
        self._stars: tuple[tuple[int, float, float, int, tuple[int, int, int]], ...] = (
            tuple(stars)
        )

        # ── distant fighter battle (channels 60_000–60_099) ──
        # Smooth deterministic Lissajous weaving in the sky band, well
        # clear of the limb: (cx, cy, ax, ay, wx, wy, px, py, size, colour).
        fighters: list[
            tuple[float, float, float, float, float, float, float, float, int, tuple[int, int, int]]
        ] = []
        for i in range(_ORBIT_FIGHTER_COUNT):
            fighters.append(
                (
                    w * (0.10 + 0.80 * tick_unit(0, channel=60_000 + i)),
                    66.0 + tick_unit(0, channel=60_010 + i) * max(20.0, limb_top - 190.0),
                    46.0 + tick_unit(0, channel=60_020 + i) * 90.0,
                    18.0 + tick_unit(0, channel=60_030 + i) * 42.0,
                    math.tau / (9.0 + tick_unit(0, channel=60_040 + i) * 8.0),
                    math.tau / (5.0 + tick_unit(0, channel=60_050 + i) * 5.0),
                    tick_unit(0, channel=60_060 + i) * math.tau,
                    tick_unit(0, channel=60_070 + i) * math.tau,
                    5 + int(tick_unit(0, channel=60_080 + i) * 4),
                    _ORBIT_FIGHTER_A if i % 2 == 0 else _ORBIT_FIGHTER_B,
                )
            )
        self._fighters: tuple[
            tuple[float, float, float, float, float, float, float, float, int, tuple[int, int, int]],
            ...,
        ] = tuple(fighters)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def _fighter_xy(self, index: int, elapsed: float) -> tuple[float, float]:
        fx, fy, ax, ay, wx, wy, px, py, _size, _colour = self._fighters[index]
        return (
            fx + ax * math.sin(wx * elapsed + px),
            fy + ay * math.sin(wy * elapsed + py),
        )

    def draw(self, surface: pygame.Surface, tick: int) -> None:
        """Blit the prerendered planet + animate stars, clouds, battle."""
        elapsed = tick / _TICKS_PER_SEC

        # Planet band in one opaque blit. The sky above relies on the
        # Renderer's frame clear (same deal as ParallaxStarfield).
        surface.blit(self._base, (0, self._y0))

        # Stars wrap at their own column's limb — they set behind Earth.
        for sx, y_base, speed, wrap, colour in self._stars:
            y = (y_base + speed * elapsed) % wrap
            if speed >= 80.0:
                pygame.draw.rect(surface, colour, (sx, int(y), 2, 2))
            else:
                surface.set_at((sx, int(y)), colour)

        # Cloud bands sway a few px/s relative to the planet surface.
        sway = int(
            _ORBIT_CLOUD_SWAY_PX
            * math.sin(math.tau * elapsed / _ORBIT_CLOUD_SWAY_PERIOD_S)
        )
        for band_surf, band_x, band_y in self._cloud_bands:
            surface.blit(band_surf, (band_x + sway, band_y))

        # Distant fighter battle — tiny dim slivers weaving in the sky.
        n = len(self._fighters)
        for i in range(n):
            x, y = self._fighter_xy(i, elapsed)
            size = self._fighters[i][8]
            colour = self._fighters[i][9]
            pygame.draw.rect(
                surface, colour, (int(x), int(y), size, 3 if size < 7 else 4)
            )
        # Brief 1px laser exchanges (4-tick flashes, staggered periods —
        # tuned so some exchange is visible roughly a tenth of the time).
        # Shooters 0/3/4/7 → both factions fire (odd and even indices).
        for s_idx, shooter in enumerate((0, 3, 4, 7)):
            period = 128 + 34 * s_idx
            if (tick + 53 * s_idx) % period < 4:
                x1, y1 = self._fighter_xy(shooter, elapsed)
                x2, y2 = self._fighter_xy((shooter + 3) % n, elapsed)
                laser = _ORBIT_LASER_A if shooter % 2 == 0 else _ORBIT_LASER_B
                pygame.draw.line(
                    surface, laser, (int(x1), int(y1)), (int(x2), int(y2))
                )
        # Rare tiny explosion blip: fixed at the victim's position at the
        # window-start tick so the flash doesn't slide around.
        blip_phase = (tick + 137) % _ORBIT_BLIP_PERIOD
        if blip_phase < _ORBIT_BLIP_TICKS:
            start_tick = tick - blip_phase
            victim = (start_tick // _ORBIT_BLIP_PERIOD) % n
            bx, by = self._fighter_xy(victim, start_tick / _TICKS_PER_SEC)
            colour = _ORBIT_BLIP_COLOURS[min(2, blip_phase // 5)]
            pygame.draw.circle(
                surface, colour, (int(bx), int(by)), 1 + blip_phase // 4
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


# ───────── hyperspace streaks (bonus interstitial) ─────────

# HyperspaceScene backdrop. Same three-layer deterministic recipe as
# ParallaxStarfield but the motion is HORIZONTAL (stars rush right-to-
# left past ships flying right) and the two faster layers draw short
# streak lines instead of points — the classic "stars smeared by speed"
# read. Channels 95_000+ keep the star positions uncorrelated with the
# vertical starfield's 10_000/20_000 allocations.

_HYPER_LAYERS: tuple[tuple[int, float, tuple[int, int, int]], ...] = (
    # (count, speed_px_per_sec, colour)
    (40, 90.0, (90, 100, 130)),
    (28, 220.0, (150, 170, 210)),
    (14, 420.0, (220, 235, 255)),
)
_HYPER_BG_COLOUR = (4, 4, 18)
# Streak length range for the fast layers. Per-star deterministic so
# the field looks irregular; faster layer skews longer via speed.
_HYPER_STREAK_MIN = 4
_HYPER_STREAK_MAX = 14


class HyperspaceBackground:
    """Horizontal three-layer star-streak parallax for the bonus run.

    Cloned from :class:`ParallaxStarfield`: deterministic star placement
    via :func:`tick_unit` channels, ``x = (x_base - speed * elapsed) % w``
    so the field scrolls leftward forever. Slow layer draws pixels; the
    two fast layers draw 4–14px horizontal streaks (trail extends to the
    right of the star — motion blur opposite the direction of travel).
    """

    __slots__ = ("_height", "_stars", "_width")

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        stars: list[tuple[float, float, float, int, tuple[int, int, int]]] = []
        star_id = 0
        for count, speed, colour in _HYPER_LAYERS:
            for _ in range(count):
                x = tick_unit(0, channel=95_000 + star_id) * width
                y = tick_unit(0, channel=96_000 + star_id) * height
                # Streak length: speed-proportional base ± per-star jitter.
                base = _HYPER_STREAK_MIN + (
                    (_HYPER_STREAK_MAX - _HYPER_STREAK_MIN) * speed / 420.0
                )
                jitter = tick_unit(0, channel=97_000 + star_id) * 4.0 - 2.0
                length = int(max(_HYPER_STREAK_MIN, min(_HYPER_STREAK_MAX, base + jitter)))
                stars.append((x, y, speed, length, colour))
                star_id += 1
        self._stars: tuple[tuple[float, float, float, int, tuple[int, int, int]], ...] = (
            tuple(stars)
        )

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
        surface.fill(_HYPER_BG_COLOUR)
        for x_base, y, speed, length, colour in self._stars:
            x = (x_base - speed * elapsed) % w
            iy = int(y) % h
            if speed >= 200.0:
                # Fast layers: horizontal streak trailing to the right.
                pygame.draw.line(surface, colour, (int(x), iy), (int(x) + length, iy))
            else:
                surface.set_at((int(x), iy), colour)


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
    "bg_earth_orbit": EarthOrbit,
    "bg_hyperspace": HyperspaceBackground,
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
