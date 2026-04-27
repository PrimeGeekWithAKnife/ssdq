"""Pause overlay — dim screen + centred banner."""

from __future__ import annotations

import pygame

_BANNER_COLOUR = (255, 255, 255)
_BANNER_FONT_SIZE = 56
_DEFAULT_DIM = 128


class PauseOverlay:
    """Draws a translucent dim layer plus the pause banner."""

    __slots__ = ("_font", "_menu_font")

    def __init__(self) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        try:
            self._font = pygame.font.SysFont(None, _BANNER_FONT_SIZE, bold=True)
            self._menu_font = pygame.font.SysFont(None, 32, bold=False)
        except pygame.error:  # pragma: no cover — extreme fallback
            self._font = pygame.font.Font(None, _BANNER_FONT_SIZE)
            self._menu_font = pygame.font.Font(None, 32)

    def draw(self, surface: pygame.Surface, dim_alpha: int = _DEFAULT_DIM) -> None:
        w, h = surface.get_size()
        dim_alpha = max(0, min(255, int(dim_alpha)))
        dim = pygame.Surface((w, h), flags=pygame.SRCALPHA)
        dim.fill((0, 0, 0, dim_alpha))
        surface.blit(dim, (0, 0))

        banner = self._font.render("PAUSED", True, _BANNER_COLOUR)
        surface.blit(banner, banner.get_rect(center=(w // 2, h // 2 - 40)))
        # Pause menu options — kid playtest 2026-04-27.
        resume = self._menu_font.render("START to resume", True, (220, 220, 220))
        exit_lbl = self._menu_font.render("CANCEL (B / ESC) to quit", True, (220, 180, 180))
        surface.blit(resume, resume.get_rect(center=(w // 2, h // 2 + 30)))
        surface.blit(exit_lbl, exit_lbl.get_rect(center=(w // 2, h // 2 + 70)))
