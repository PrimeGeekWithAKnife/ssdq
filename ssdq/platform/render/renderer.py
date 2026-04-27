"""Top-level draw orchestration.

The Renderer is the only thing that walks the ECS World to produce pixels.
Order, per spec section 4.2:

  1. clear
  2. parallax background
  3. entity sprites (sorted by Sprite.layer, then entity id)
  4. particles + bullets (drawn via the same sprite path; the sort key
     keeps them above world entities by virtue of higher Sprite.layer)
  5. boss telegraphs (if any BossTelegraph components are present)
  6. HUD
  7. pause overlay (if scene-stack reports paused)

The renderer reads optional components (BombActive, BossTelegraph) by
*name* via :func:`_optional_component_type` so it doesn't hard-import
modules Builder A owns. If those types don't exist yet we just skip
that pass.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

import pygame

from ssdq.core.components import (
    AnimatedSprite,
    Faction,
    FactionTag,
    HitFlash,
    Position,
    Sprite,
)
from ssdq.core.ecs import World
from ssdq.platform.render.atlas import SpriteAtlas
from ssdq.platform.render.background import ParallaxStarfield
from ssdq.platform.render.hud import Hud
from ssdq.platform.render.pause_overlay import PauseOverlay

_CLEAR_COLOUR = (5, 5, 12)


@dataclass(frozen=True, slots=True)
class _DrawItem:
    layer: int
    eid_index: int  # secondary sort key for determinism
    pos: Position
    sprite: Sprite


class Renderer:
    """Draw orchestration. Construct once per session, call :meth:`draw` per frame."""

    __slots__ = ("_atlas", "_background", "_hud", "_pause_overlay", "_size", "_tick_counter")

    def __init__(
        self,
        atlas: SpriteAtlas,
        size: tuple[int, int],
    ) -> None:
        self._atlas = atlas
        self._size = size
        self._background = ParallaxStarfield(*size)
        self._hud = Hud()
        self._pause_overlay = PauseOverlay()
        self._tick_counter = 0

    @property
    def atlas(self) -> SpriteAtlas:
        return self._atlas

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    # ---------------- main entry ----------------

    def draw(
        self,
        world: World,
        surface: pygame.Surface,
        alpha: float,
        *,
        tick: int | None = None,
        paused: bool = False,
        pause_dim_alpha: int = 128,
    ) -> None:
        """Render one frame onto ``surface``.

        ``alpha`` is the render-interpolation factor (0..1) — currently unused
        but kept in the signature so Scene.render() callers don't break.
        ``tick`` drives the parallax scroll; if omitted we use an internal
        counter so the smoke test still animates.
        """
        if tick is None:
            self._tick_counter += 1
            tick = self._tick_counter

        # 1. clear
        surface.fill(_CLEAR_COLOUR)

        # 2. background
        self._background.draw(surface, tick)

        # 3+4. entity sprites (incl. particles, bullets) — single deterministic pass
        for item in self._gather_sprite_items(world):
            self._blit_item(surface, item)

        # 4.5 hit-flash overlay (multi-HP enemies that just took damage)
        self._draw_hit_flashes(world, surface)

        # 5. boss telegraphs (optional)
        self._draw_boss_telegraphs(world, surface)

        # extra: bomb shockwaves (optional)
        self._draw_bomb_actives(world, surface)

        # 6. HUD
        self._hud.draw(world, surface)

        # 7. pause overlay
        if paused:
            self._pause_overlay.draw(surface, pause_dim_alpha)

    # ---------------- entity gathering ----------------

    def _gather_sprite_items(self, world: World) -> list[_DrawItem]:
        # Build (layer, eid, pos, sprite). Sort primary by layer asc (lower
        # draws first), secondary by entity id asc — keeps determinism.
        items: list[_DrawItem] = [
            _DrawItem(layer=sprite.layer, eid_index=int(eid), pos=pos, sprite=sprite)
            for eid, pos, sprite in world.query2(Position, Sprite)
        ]
        # Animated sprites: synthesise a Sprite-like draw item from the
        # current animation frame. AnimatedSprite is a render-only proxy;
        # the AnimationSystem advances `current_index`.
        for eid, pos, anim in world.query2(Position, AnimatedSprite):
            if not anim.frames:
                continue
            idx = min(anim.current_index, len(anim.frames) - 1)
            synthetic = Sprite(path=anim.frames[idx], layer=anim.layer)
            items.append(_DrawItem(layer=anim.layer, eid_index=int(eid), pos=pos, sprite=synthetic))
        items.sort(key=lambda i: (i.layer, i.eid_index))
        return items

    def _blit_item(self, surface: pygame.Surface, item: _DrawItem) -> None:
        surf = self._atlas.get(item.sprite.path)
        # Rotation is rare in the slice — only apply if explicitly set.
        if item.sprite.rotation_rad != 0.0:
            from math import degrees

            surf = pygame.transform.rotate(surf, -degrees(item.sprite.rotation_rad))
        # Apply alpha if specified (used by InvulnerabilityBlink)
        if item.sprite.alpha != 255:
            surf = surf.copy()
            surf.set_alpha(item.sprite.alpha)
        rect = surf.get_rect(center=(int(item.pos.pos.x), int(item.pos.pos.y)))
        surface.blit(surf, rect)
        # Hit-flash overlay: bright white blend on top of the sprite.
        # Reading flash from world is the renderer's concern but we'd
        # need entity context here — we instead expose a separate pass.

    # ---------------- optional draws ----------------

    def _draw_hit_flashes(self, world: World, surface: pygame.Surface) -> None:
        """White-tint overlay on every entity that has HitFlash.ticks_remaining > 0."""
        for _eid, pos, sprite, flash in world.query3(Position, Sprite, HitFlash):
            if flash.ticks_remaining <= 0:
                continue
            base = self._atlas.get(sprite.path)
            tint = base.copy()
            # Multiply alpha by a fading factor; modulate with a bright tint.
            tint.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_ADD)
            tint.set_alpha(min(180, flash.ticks_remaining * 40))
            rect = tint.get_rect(center=(int(pos.pos.x), int(pos.pos.y)))
            surface.blit(tint, rect)

    def _draw_boss_telegraphs(self, world: World, surface: pygame.Surface) -> None:
        comp_t = _optional_component_type("BossTelegraph")
        if comp_t is None:
            return
        for _eid, telegraph in world.query1(comp_t):
            pos = _attr_vec2(telegraph, ("pos", "centre", "center"))
            radius = _attr_float(telegraph, ("radius",), default=0.0)
            colour = _attr_tuple3(telegraph, ("colour", "color"), default=(255, 80, 80))
            if pos is None or radius <= 0.0:
                continue
            pygame.draw.circle(surface, colour, (int(pos[0]), int(pos[1])), int(radius), width=2)

    def _draw_bomb_actives(self, world: World, surface: pygame.Surface) -> None:
        comp_t = _optional_component_type("BombActive")
        if comp_t is None:
            return
        for _eid, bomb in world.query1(comp_t):
            pos = _attr_vec2(bomb, ("pos", "centre", "center"))
            radius = _attr_float(bomb, ("radius",), default=0.0)
            if pos is None or radius <= 0.0:
                continue
            pygame.draw.circle(
                surface, (255, 255, 200), (int(pos[0]), int(pos[1])), int(radius), width=3
            )


# ---------------- duck-typed component lookup ----------------


def _optional_component_type(name: str) -> type[Any] | None:
    """Try common locations for an optional ECS component type.

    We avoid hard-importing modules Builder A owns — if the component
    doesn't exist yet, we silently skip its render pass.
    """
    candidates = (
        "ssdq.core.components",
        "ssdq.core.coop.components",
        "ssdq.core.waves.components",
    )
    for module_path in candidates:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        cls = getattr(mod, name, None)
        if isinstance(cls, type):
            return cls
    return None


def _attr_vec2(obj: Any, names: tuple[str, ...]) -> tuple[float, float] | None:
    for n in names:
        v = getattr(obj, n, None)
        if v is None:
            continue
        if hasattr(v, "x") and hasattr(v, "y"):
            return float(v.x), float(v.y)
        # also accept (x, y) tuples
        if isinstance(v, tuple) and len(v) == 2:
            return float(v[0]), float(v[1])
    return None


def _attr_float(obj: Any, names: tuple[str, ...], default: float) -> float:
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def _attr_tuple3(
    obj: Any, names: tuple[str, ...], default: tuple[int, int, int]
) -> tuple[int, int, int]:
    for n in names:
        v = getattr(obj, n, None)
        if isinstance(v, tuple) and len(v) == 3:
            return (int(v[0]), int(v[1]), int(v[2]))
    return default


# ---------------- exported sentinel for FactionTag (so renderer compiles) ----------------

# Re-exported so consumers don't need to import core/components directly to
# tell us they want to filter by faction. Kept at module bottom to avoid
# polluting the main symbol list.
PLAYER_FACTION = FactionTag(Faction.PLAYER)
