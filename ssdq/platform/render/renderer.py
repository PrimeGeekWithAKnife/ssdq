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
    FloatingText,
    Health,
    HitFlash,
    MaxHealth,
    PickupHalo,
    Position,
    ShieldHalo,
    Sprite,
    TimeToLive,
)
from ssdq.core.ecs import World
from ssdq.platform.render.atlas import SpriteAtlas
from ssdq.platform.render.background import (
    DEFAULT_BACKGROUND_NAME,
    Backdrop,
    make_background,
)
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

    __slots__ = (
        "_atlas",
        "_background",
        "_background_name",
        "_hud",
        "_pause_overlay",
        "_size",
        "_tick_counter",
    )

    def __init__(
        self,
        atlas: SpriteAtlas,
        size: tuple[int, int],
    ) -> None:
        self._atlas = atlas
        self._size = size
        self._background_name = DEFAULT_BACKGROUND_NAME
        self._background: Backdrop = make_background(self._background_name, *size)
        self._hud = Hud()
        self._pause_overlay = PauseOverlay()
        self._tick_counter = 0

    @property
    def atlas(self) -> SpriteAtlas:
        return self._atlas

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def background_name(self) -> str:
        """Currently-installed backdrop's registry name."""
        return self._background_name

    def set_background_by_name(self, name: str) -> None:
        """Swap the active backdrop. Called by the Level scene on enter().

        No-op if ``name`` matches the current backdrop — avoids re-running
        the deterministic crater/star setup every level load.
        Unknown names fall back to the default (handled inside
        :func:`make_background`).
        """
        if name == self._background_name:
            return
        self._background_name = name
        self._background = make_background(name, *self._size)

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

        # 2.5 pickup halos (rendered behind sprites so they read as glow)
        self._draw_pickup_halos(world, surface, tick)

        # 3+4. entity sprites (incl. particles, bullets) — single deterministic pass
        for item in self._gather_sprite_items(world):
            self._blit_item(surface, item)

        # 4.4 shield halos (over the player sprite — forcefield "engulfs" the ship)
        self._draw_shield_halos(world, surface, tick)

        # 4.5 hit-flash overlay (multi-HP enemies that just took damage)
        self._draw_hit_flashes(world, surface)

        # 4.55 floating text labels (pickup feedback)
        self._draw_floating_text(world, surface)

        # 4.6 enemy health bars (multi-HP enemies that have taken damage)
        self._draw_enemy_health_bars(world, surface)

        # 4.7 boss health bar (across the top of the playfield)
        self._draw_boss_health_bar(world, surface)

        # 5. boss telegraphs (optional)
        self._draw_boss_telegraphs(world, surface)

        # 5.1 boss intro banner — large centred text above the playfield
        # during the boss telegraph window. Reads narrative copy off any
        # entity with a BossIntroBanner component.
        self._draw_boss_intro_banner(world, surface)

        # 5.2 level intro banner — large centred narrative text shown for
        # the first few seconds of every level. Word-wrapped to ~80% of
        # the playfield width. Players can shoot through it.
        self._draw_level_intro_banner(world, surface)

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
        # Optional scale (used to make pickups visually obvious)
        if item.sprite.scale != 1.0:
            w, h = surf.get_size()
            surf = pygame.transform.scale(
                surf, (int(w * item.sprite.scale), int(h * item.sprite.scale))
            )
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

    def _draw_pickup_halos(self, world: World, surface: pygame.Surface, tick: int) -> None:
        """Pulsing coloured glow behind pickups."""
        import math

        pulse = 0.7 + 0.3 * math.sin(tick * 0.18)
        for _eid, pos, halo in world.query2(Position, PickupHalo):
            radius = int(halo.radius * pulse)
            if radius <= 0:
                continue
            alpha = int(140 * pulse)
            # Pre-multiplied alpha surface — pygame draws antialiased circles
            # via gfxdraw; for cross-version reliability use a Surface blit.
            halo_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(halo_surf, (*halo.colour, alpha), (radius, radius), radius)
            surface.blit(halo_surf, (int(pos.pos.x) - radius, int(pos.pos.y) - radius))

    def _draw_shield_halos(self, world: World, surface: pygame.Surface, tick: int) -> None:
        """Translucent pulsing forcefield ring around shielded player ships.

        Drawn on top of the player sprite so the field reads as
        "engulfing" the ship. Two concentric rings — a soft fill disc
        and a brighter outline — both pulsing on the global tick so
        every shielded ship pulses in sync.
        """
        import math

        pulse = 0.85 + 0.15 * math.sin(tick * 0.22)
        for _eid, pos, halo in world.query2(Position, ShieldHalo):
            radius = int(halo.base_radius * pulse)
            if radius <= 0:
                continue
            # Inner translucent fill — gives the "engulfed" look without
            # hiding the sprite underneath. Alpha 50 reads as gauzy.
            fill_alpha = int(60 * pulse)
            ring_alpha = int(180 * pulse)
            field_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(field_surf, (*halo.colour, fill_alpha), (radius, radius), radius)
            # Bright outline ring — the "force" boundary.
            pygame.draw.circle(
                field_surf, (*halo.colour, ring_alpha), (radius, radius), radius, width=2
            )
            surface.blit(field_surf, (int(pos.pos.x) - radius, int(pos.pos.y) - radius))

    def _draw_floating_text(self, world: World, surface: pygame.Surface) -> None:
        """Drift-up + fade short-lived text labels (pickup feedback)."""
        if not pygame.font.get_init():
            pygame.font.init()
        font = pygame.font.SysFont(None, 24, bold=True)
        for _eid, pos, txt in world.query2(Position, FloatingText):
            if txt.ticks_remaining <= 0:
                continue
            # Fade based on remaining ticks (assume max 60).
            alpha = max(0, min(255, int(txt.ticks_remaining * 6)))
            rendered = font.render(txt.text, True, txt.colour)
            rendered.set_alpha(alpha)
            rect = rendered.get_rect(center=(int(pos.pos.x), int(pos.pos.y)))
            surface.blit(rendered, rect)

    def _draw_enemy_health_bars(self, world: World, surface: pygame.Surface) -> None:
        """Tiny HP bar above multi-HP enemies that have taken damage.

        Skip drones (max=1) and the boss (rendered separately as a top bar).
        """
        for eid, max_hp in world.query1(MaxHealth):
            # Skip the boss — has its own top-of-screen bar (max >= 50).
            if max_hp.hp >= 50 or max_hp.hp <= 1:
                continue
            hp = world.get(eid, Health)
            pos = world.get(eid, Position)
            if hp is None or pos is None:
                continue
            # Show only when damaged.
            if hp.hp >= max_hp.hp or hp.hp <= 0:
                continue
            cx = int(pos.pos.x)
            cy = int(pos.pos.y)
            bar_w = 40
            bar_h = 4
            x = cx - bar_w // 2
            y = cy - 32
            ratio = hp.hp / max_hp.hp
            pygame.draw.rect(surface, (40, 0, 0), (x, y, bar_w, bar_h))
            fill_w = int(bar_w * ratio)
            colour = (
                (220, 60, 40) if ratio < 0.34 else (220, 200, 40) if ratio < 0.67 else (60, 220, 80)
            )
            if fill_w > 0:
                pygame.draw.rect(surface, colour, (x, y, fill_w, bar_h))
            pygame.draw.rect(surface, (200, 200, 200), (x, y, bar_w, bar_h), width=1)

    def _draw_boss_health_bar(self, world: World, surface: pygame.Surface) -> None:
        """Wide bar across the top of the playfield showing boss HP.

        Boss identified as the entity with `MaxHealth.hp >= 50` (level-1
        non-boss enemies cap at 12).
        """
        for boss_eid, max_hp in world.query1(MaxHealth):
            if max_hp.hp < 50:
                continue
            hp = world.get(boss_eid, Health)
            ftag = world.get(boss_eid, FactionTag)
            if hp is None or ftag is None or ftag.faction != Faction.ENEMY:
                continue
            w, _h = surface.get_size()
            bar_w = w - 240
            bar_h = 14
            x = (w - bar_w) // 2
            y = 70
            ratio = max(0.0, hp.hp / max_hp.hp)
            pygame.draw.rect(surface, (50, 0, 0), (x - 2, y - 2, bar_w + 4, bar_h + 4))
            pygame.draw.rect(surface, (10, 10, 10), (x, y, bar_w, bar_h))
            fill_w = int(bar_w * ratio)
            if fill_w > 0:
                pygame.draw.rect(surface, (220, 50, 80), (x, y, fill_w, bar_h))
            # Phase divider at midpoint (2-phase boss)
            pygame.draw.line(
                surface,
                (255, 255, 255),
                (x + bar_w // 2, y - 2),
                (x + bar_w // 2, y + bar_h + 2),
                1,
            )
            pygame.draw.rect(surface, (200, 200, 200), (x, y, bar_w, bar_h), width=1)
            if not pygame.font.get_init():
                pygame.font.init()
            font = pygame.font.SysFont(None, 22, bold=True)
            label = font.render("BOSS", True, (255, 240, 200))
            surface.blit(label, label.get_rect(midright=(x - 8, y + bar_h // 2)))
            return  # only one boss

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

    def _draw_boss_intro_banner(self, world: World, surface: pygame.Surface) -> None:
        """Big top-of-screen text banner during the boss intro telegraph.

        Reads BossIntroBanner-tagged entities (duck-typed) and fades the
        text out over the entity's TimeToLive so the banner gracefully
        retreats just as the boss starts shooting.
        """
        comp_t = _optional_component_type("BossIntroBanner")
        if comp_t is None:
            return
        if not pygame.font.get_init():
            pygame.font.init()
        title_font = pygame.font.SysFont(None, 56, bold=True)
        sub_font = pygame.font.SysFont(None, 26)
        w, _h = surface.get_size()
        for eid, banner in world.query1(comp_t):
            text = str(getattr(banner, "text", "") or "")
            if not text:
                continue
            total = max(1, int(getattr(banner, "total_ticks", 60) or 60))
            ttl = world.get(eid, TimeToLive)
            remaining = ttl.ticks if ttl is not None else total
            # Ease the alpha: hold at full for the first 70% then fade.
            ratio = max(0.0, min(1.0, remaining / total))
            alpha = 255 if ratio > 0.30 else int(255 * (ratio / 0.30))
            # Drop shadow for legibility against the busy playfield.
            shadow = title_font.render(text, True, (0, 0, 0))
            label = title_font.render(text, True, (255, 230, 120))
            shadow.set_alpha(alpha)
            label.set_alpha(alpha)
            cx = w // 2
            cy = 150
            surface.blit(shadow, shadow.get_rect(center=(cx + 2, cy + 2)))
            surface.blit(label, label.get_rect(center=(cx, cy)))
            # Subtitle line — smaller, dimmer, immediately under the banner.
            sub = sub_font.render("WARNING — INCOMING", True, (255, 120, 120))
            sub.set_alpha(alpha)
            surface.blit(sub, sub.get_rect(center=(cx, cy + 44)))
            return  # one banner is enough

    def _draw_level_intro_banner(self, world: World, surface: pygame.Surface) -> None:
        """Big centred narrative text shown at the start of every level.

        Word-wraps to ~80% of the surface width so the longer Level 3-5
        sentences fit on a 1280x720 fullscreen at kid-distance. Holds at
        full alpha for the first ~70% of the banner's life then fades —
        same easing as the boss intro banner so the two read as the
        same UI element.
        """
        comp_t = _optional_component_type("LevelIntroBanner")
        if comp_t is None:
            return
        if not pygame.font.get_init():
            pygame.font.init()
        body_font = pygame.font.SysFont(None, 40, bold=True)
        w, h = surface.get_size()
        max_width = int(w * 0.80)
        for eid, banner in world.query1(comp_t):
            text = str(getattr(banner, "text", "") or "")
            if not text:
                continue
            total = max(1, int(getattr(banner, "total_ticks", 60) or 60))
            ttl = world.get(eid, TimeToLive)
            remaining = ttl.ticks if ttl is not None else total
            ratio = max(0.0, min(1.0, remaining / total))
            # Hold at full for the first 70% then linear-fade.
            alpha = 255 if ratio > 0.30 else int(255 * (ratio / 0.30))
            lines = _wrap_text(text, body_font, max_width)
            line_h = body_font.get_linesize()
            block_h = line_h * len(lines)
            cx = w // 2
            cy = h // 2 - block_h // 2
            for i, line in enumerate(lines):
                shadow = body_font.render(line, True, (0, 0, 0))
                label = body_font.render(line, True, (255, 230, 120))
                shadow.set_alpha(alpha)
                label.set_alpha(alpha)
                y = cy + i * line_h + line_h // 2
                surface.blit(shadow, shadow.get_rect(center=(cx + 2, y + 2)))
                surface.blit(label, label.get_rect(center=(cx, y)))
            return  # one banner is enough

    def _draw_bomb_actives(self, world: World, surface: pygame.Surface) -> None:
        """Expanding shockwave + screen flash + radial sparks.

        Driven entirely by per-bomb fields:
          * ``radius`` — current animated outer ring (px).
          * ``aoe_radius`` — full damage / max ring radius.
          * ``visual_progress`` — 0..1 across the bomb's duration. Drives
            the ring alpha, flash decay, and spark animation. No RNG —
            spark angles are evenly spaced so the visual is deterministic
            and identical on every machine (Pi 5 included).
        """
        import math as _math

        comp_t = _optional_component_type("BombActive")
        if comp_t is None:
            return
        for _eid, bomb in world.query1(comp_t):
            pos = _attr_vec2(bomb, ("pos", "centre", "center"))
            if pos is None:
                continue
            radius = _attr_float(bomb, ("radius",), default=0.0)
            aoe_radius = _attr_float(bomb, ("aoe_radius",), default=radius)
            progress = _attr_float(bomb, ("visual_progress",), default=0.0)
            progress = max(0.0, min(1.0, progress))
            if aoe_radius <= 0.0:
                continue

            cx, cy = int(pos[0]), int(pos[1])
            sw, sh = surface.get_size()

            # 1. brief white-hot screen flash (first ~12% of life).
            if progress < 0.12:
                flash_t = progress / 0.12
                flash_alpha = int(220 * (1.0 - flash_t))
                if flash_alpha > 0:
                    flash = pygame.Surface((sw, sh), pygame.SRCALPHA)
                    flash.fill((255, 255, 240, flash_alpha))
                    surface.blit(flash, (0, 0))

            # 2. expanding outer ring + trailing inner ring.
            outer_r = max(2, int(radius))
            ring_w = max(3, int(8 * (1.0 - progress) + 2))
            ring_alpha = int(220 * (1.0 - progress) ** 2 + 35)
            ring_surf = pygame.Surface(
                (outer_r * 2 + 4, outer_r * 2 + 4), pygame.SRCALPHA
            )
            pygame.draw.circle(
                ring_surf,
                (255, 240, 180, ring_alpha),
                (outer_r + 2, outer_r + 2),
                outer_r,
                width=ring_w,
            )
            inner_alpha = int(140 * (1.0 - progress))
            if inner_alpha > 0:
                pygame.draw.circle(
                    ring_surf,
                    (255, 255, 255, inner_alpha),
                    (outer_r + 2, outer_r + 2),
                    outer_r,
                    width=max(1, ring_w // 2),
                )
            surface.blit(ring_surf, (cx - outer_r - 2, cy - outer_r - 2))

            trail_r = int(outer_r * 0.7)
            if trail_r > 4:
                trail_alpha = int(120 * (1.0 - progress))
                trail_surf = pygame.Surface(
                    (trail_r * 2 + 2, trail_r * 2 + 2), pygame.SRCALPHA
                )
                pygame.draw.circle(
                    trail_surf,
                    (255, 200, 120, trail_alpha),
                    (trail_r + 1, trail_r + 1),
                    trail_r,
                    width=2,
                )
                surface.blit(trail_surf, (cx - trail_r - 1, cy - trail_r - 1))

            # 3. radial spark dots (deterministic — 12 evenly spaced).
            n_sparks = 12
            spark_dist = aoe_radius * min(1.0, progress * 1.15)
            spark_alpha = int(255 * (1.0 - progress))
            spark_size = max(2, int(5 * (1.0 - progress) + 2))
            if spark_alpha > 0 and spark_dist > 4:
                for i in range(n_sparks):
                    angle = (i / n_sparks) * 2 * _math.pi
                    sx = int(cx + _math.cos(angle) * spark_dist)
                    sy = int(cy + _math.sin(angle) * spark_dist)
                    spark = pygame.Surface(
                        (spark_size * 2, spark_size * 2), pygame.SRCALPHA
                    )
                    pygame.draw.circle(
                        spark,
                        (255, 230, 160, spark_alpha),
                        (spark_size, spark_size),
                        spark_size,
                    )
                    surface.blit(spark, (sx - spark_size, sy - spark_size))


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
        # Level scene owns BossTelegraph, BossIntroBanner, LevelIntroBanner,
        # BombActive — without this entry the renderer silently never drew
        # any of them (the import path ran out before reaching them).
        "ssdq.scenes.level",
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


def _wrap_text(text: str, font: pygame.font.Font, max_width: int) -> list[str]:
    """Greedy word-wrap on whitespace; degrades gracefully on a single
    long word that exceeds ``max_width`` (it occupies its own line)."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if font.size(candidate)[0] <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


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
