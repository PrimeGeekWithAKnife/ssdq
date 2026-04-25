"""Pygame display wrapper.

This is one of the only places SDL/pygame is allowed to touch the game
(per spec section 4.2). Headless tests must still be able to construct a
:class:`Window` by setting ``SDL_VIDEODRIVER=dummy`` in the environment.
"""

from __future__ import annotations

import os
import platform
from types import TracebackType
from typing import Self

import pygame

# ---------------- driver selection ----------------


def _select_driver() -> None:
    """Set ``SDL_VIDEODRIVER`` if appropriate for the host.

    On a Pi 5 (aarch64) running headless (no ``DISPLAY``), prefer the SDL2
    KMS/DRM backend so pygame can drive the framebuffer directly. We never
    overwrite an existing ``SDL_VIDEODRIVER`` value (tests rely on
    ``dummy``).
    """
    if "SDL_VIDEODRIVER" in os.environ:
        return
    if platform.machine().lower() == "aarch64" and not os.environ.get("DISPLAY"):
        os.environ["SDL_VIDEODRIVER"] = "kmsdrm"


# ---------------- window ----------------


class Window:
    """Owns the pygame display and provides a render surface.

    Use as a context manager or call :meth:`close` explicitly. Construction
    initialises pygame (``pygame.init()``) — callers should not also call it.
    """

    __slots__ = ("_closed", "_fullscreen", "_height", "_surface", "_vsync", "_width")

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fullscreen: bool = False,
        vsync: bool = True,
    ) -> None:
        _select_driver()
        if not pygame.get_init():
            pygame.init()
        # Display module may be initialised by pygame.init(), but be defensive.
        if not pygame.display.get_init():
            pygame.display.init()

        pygame.display.set_caption("SSDQ")

        flags = 0
        # Honour SDL_VIDEODRIVER=dummy: never request fullscreen against the
        # dummy driver — it just confuses headless CI.
        driver = os.environ.get("SDL_VIDEODRIVER", "")
        if fullscreen and driver != "dummy":
            flags |= pygame.FULLSCREEN

        # vsync is best-effort; pygame ignores it when the driver doesn't
        # support it, which is fine.
        self._surface: pygame.Surface = pygame.display.set_mode(
            (width, height), flags, vsync=1 if vsync else 0
        )
        self._width = width
        self._height = height
        self._fullscreen = fullscreen and driver != "dummy"
        self._vsync = vsync
        self._closed = False

    # ---------------- accessors ----------------

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def size(self) -> tuple[int, int]:
        return (self._width, self._height)

    @property
    def fullscreen(self) -> bool:
        return self._fullscreen

    @property
    def vsync(self) -> bool:
        return self._vsync

    def surface(self) -> pygame.Surface:
        """Return the display surface to draw onto."""
        if self._closed:
            raise RuntimeError("Window is closed")
        return self._surface

    # ---------------- frame loop ----------------

    def flip(self) -> None:
        """Present the current frame to the screen."""
        if self._closed:
            return
        pygame.display.flip()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if pygame.display.get_init():
            pygame.display.quit()

    # ---------------- context manager ----------------

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
