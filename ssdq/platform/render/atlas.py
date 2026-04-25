"""Sprite atlas — loads on demand, generates deterministic placeholders.

Sprite paths in the YAML content tree (e.g. ``ships/player_blue.png``) are
relative to ``content/assets/sprites/``. If a sprite file is missing during
the pre-asset phase of the slice, we fabricate a coloured rectangle so the
game still runs end-to-end. The colour is derived from a stable hash of the
path so the same sprite key always yields the same placeholder — that makes
screenshot diffs reproducible and turns missing files into a visible-but-not-
ambiguous bug.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from pathlib import Path

import pygame

from ssdq.core.content.loader import ContentBundle

_LOG = logging.getLogger(__name__)

# Default placeholder size (px). Real sprites are loaded at native size.
_PLACEHOLDER_SIZE = (32, 32)


def _placeholder_colour(path: str) -> tuple[int, int, int]:
    """Derive a stable RGB triple from a sprite path."""
    digest = hashlib.blake2b(path.encode("utf-8"), digest_size=3).digest()
    # Bias values upward so placeholders are visible on a black background.
    return (
        80 + (digest[0] % 160),
        80 + (digest[1] % 160),
        80 + (digest[2] % 160),
    )


def _make_placeholder(path: str) -> pygame.Surface:
    surf = pygame.Surface(_PLACEHOLDER_SIZE, flags=pygame.SRCALPHA)
    surf.fill((*_placeholder_colour(path), 255))
    # Magenta border so the eye reads it as "missing asset".
    pygame.draw.rect(surf, (255, 0, 255, 255), surf.get_rect(), width=1)
    return surf


class SpriteAtlas:
    """Caches sprites loaded from ``content/assets/sprites/`` by relative path."""

    __slots__ = ("_cache", "_root", "_warned")

    def __init__(self, sprites_root: Path | str) -> None:
        self._root = Path(sprites_root)
        self._cache: dict[str, pygame.Surface] = {}
        self._warned: set[str] = set()

    @property
    def root(self) -> Path:
        return self._root

    def get(self, path: str) -> pygame.Surface:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        full = self._root / path
        surf: pygame.Surface
        if full.is_file():
            try:
                surf = pygame.image.load(str(full))
                # convert_alpha needs an active display; fall back gracefully
                # if it's missing (e.g. during very early bootstrap).
                if pygame.display.get_init() and pygame.display.get_surface() is not None:
                    surf = surf.convert_alpha()
            except pygame.error as ex:  # pragma: no cover — defensive
                if path not in self._warned:
                    _LOG.warning("failed to load sprite %s: %s — using placeholder", path, ex)
                    self._warned.add(path)
                surf = _make_placeholder(path)
        else:
            if path not in self._warned:
                _LOG.warning("missing sprite %s — using placeholder", path)
                self._warned.add(path)
            surf = _make_placeholder(path)
        self._cache[path] = surf
        return surf

    def preload(self, paths: Iterable[str]) -> None:
        for p in paths:
            self.get(p)

    def preload_bundle(self, bundle: ContentBundle) -> None:
        """Pre-warm the cache with every sprite path referenced in the bundle."""
        self.preload(_iter_bundle_sprite_paths(bundle))


def _iter_bundle_sprite_paths(bundle: ContentBundle) -> Iterable[str]:
    """Yield every sprite path that appears anywhere in the bundle."""
    seen: set[str] = set()

    def emit(path: str | None) -> Iterable[str]:
        if path and path not in seen:
            seen.add(path)
            yield path

    for ship in bundle.ships.values():
        yield from emit(ship.sprite)
        yield from emit(ship.sprite_hit_flash)
    for weapon in bundle.weapons.values():
        yield from emit(weapon.sprite)
    for bomb in bundle.bombs.values():
        yield from emit(bomb.sprite)
    for enemy in bundle.enemies.values():
        yield from emit(enemy.sprite)
    for boss in bundle.bosses.values():
        yield from emit(boss.sprite)
    for ew in bundle.enemy_weapons.values():
        yield from emit(ew.sprite)
    for pickup in bundle.pickups.values():
        yield from emit(pickup.sprite)
