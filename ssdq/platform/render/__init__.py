"""Render subsystem — sprite atlas, particles, background, HUD, renderer."""

from ssdq.platform.render.atlas import SpriteAtlas
from ssdq.platform.render.background import ParallaxStarfield
from ssdq.platform.render.hud import Hud
from ssdq.platform.render.particles import ParticlePool
from ssdq.platform.render.pause_overlay import PauseOverlay
from ssdq.platform.render.renderer import Renderer

__all__ = [
    "Hud",
    "ParallaxStarfield",
    "ParticlePool",
    "PauseOverlay",
    "Renderer",
    "SpriteAtlas",
]
