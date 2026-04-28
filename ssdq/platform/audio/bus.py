"""Audio bus — SFX channel pool plus a single music channel.

This module wraps ``pygame.mixer`` so the rest of the engine never imports it
directly. Three runtime concerns drive the design:

* Under ``SDL_AUDIODRIVER=dummy`` the mixer initialises but nothing audible
  happens; the bus must remain a no-op rather than crashing.
* During slice development we run before any real SFX assets exist; missing
  files must log a single warning then become silent no-ops on play.
* SFX must never dominate the channel pool — when we run out of free
  channels we pre-empt the *oldest-started* channel (FIFO), which keeps
  short impact sounds audible during heavy combat.
"""

from __future__ import annotations

import itertools
import logging
import os
from pathlib import Path

import pygame

logger = logging.getLogger(__name__)

# Mixer init defaults — pygame's documented "modern" preset.
_FREQUENCY = 44_100
_BIT_DEPTH = -16  # signed 16-bit
_CHANNELS = 2  # stereo
_BUFFER = 512


class AudioBus:
    """SFX channel pool + music channel.

    Constructed once per session. ``load_sfx`` / ``load_music`` cache by name,
    and ``play_sfx`` / ``play_music`` look up by the same name.
    """

    def __init__(self, sfx_channels: int = 8) -> None:
        self._sfx_channel_count = sfx_channels
        self._sfx_cache: dict[str, pygame.mixer.Sound] = {}
        self._music_paths: dict[str, str] = {}
        self._missing_warned: set[str] = set()
        # Monotonic counter used to find the oldest-started SFX channel for
        # pre-emption.
        self._play_seq = itertools.count()
        # channel index -> sequence number of the play that owns it.
        self._channel_seq: dict[int, int] = {}
        # Music transition state. ``crossfade_to`` schedules a fade-out and
        # records the next track to load here; ``tick()`` (called once per
        # frame from main.py) loads + plays the pending track when the
        # current music actually finishes. Without this two-step approach
        # the fadeout was getting clobbered by an immediate ``load()`` —
        # kid playtest 2026-04-28 #5 ("music sometimes cuts out and
        # restarts").
        self._pending_music: tuple[str, int] | None = None
        self._enabled = False
        self._init_mixer()

    # -- init helpers ---------------------------------------------------

    def _init_mixer(self) -> None:
        # ``pygame.mixer.init`` raises on real-driver failure but succeeds with
        # ``SDL_AUDIODRIVER=dummy``. We swallow init errors so the slice can
        # run on a host with no audio device at all.
        try:
            pygame.mixer.init(
                frequency=_FREQUENCY,
                size=_BIT_DEPTH,
                channels=_CHANNELS,
                buffer=_BUFFER,
            )
        except pygame.error as exc:
            logger.warning("audio mixer unavailable, running silent: %s", exc)
            self._enabled = False
            return

        pygame.mixer.set_num_channels(self._sfx_channel_count)
        # Detect the dummy driver — under it Sound playback is a no-op anyway,
        # but we still want load_sfx / play_sfx to succeed without raising.
        driver = os.environ.get("SDL_AUDIODRIVER", "")
        self._enabled = driver != "dummy"

    # -- loaders --------------------------------------------------------

    def load_sfx(self, name: str, path: str) -> None:
        """Cache an SFX under ``name``. Missing files warn once then no-op."""
        if not Path(path).is_file():
            self._warn_missing(path)
            return
        try:
            self._sfx_cache[name] = pygame.mixer.Sound(path)
        except pygame.error as exc:
            logger.warning("failed to load sfx %r from %s: %s", name, path, exc)

    def load_music(self, name: str, path: str) -> None:
        """Register a music track under ``name``. Missing files warn once."""
        if not Path(path).is_file():
            self._warn_missing(path)
            return
        self._music_paths[name] = path

    # -- playback -------------------------------------------------------

    def play_sfx(self, name: str, volume: float = 1.0) -> None:
        """Play a cached SFX. If no channel is free, pre-empt the oldest one."""
        sound = self._sfx_cache.get(name)
        if sound is None:
            return  # missing or unloaded — silent
        if not self._enabled:
            return
        sound.set_volume(_clamp_volume(volume))
        channel = pygame.mixer.find_channel()
        if channel is None:
            channel = self._oldest_channel()
            if channel is None:
                return
            channel.stop()
        channel.play(sound)
        # Track which sequence number owns this channel so we can pick the
        # oldest one for pre-emption next time. _channel_index returns -1
        # when SDL's Channel objects don't compare equal across calls
        # (observed on aarch64 / SDL 2.30) — skip recording in that case;
        # we'll just rely on `find_channel` returning a free one next tick.
        idx = _channel_index(channel)
        if idx >= 0:
            self._channel_seq[idx] = next(self._play_seq)

    def play_music(self, name: str, loop: bool = True) -> None:
        """Stream a registered music track. ``loop=True`` loops indefinitely."""
        path = self._music_paths.get(name)
        if path is None or not self._enabled:
            return
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play(-1 if loop else 0)
        except pygame.error as exc:
            logger.warning("failed to play music %r: %s", name, exc)

    def stop_music(self) -> None:
        if not self._enabled:
            return
        pygame.mixer.music.stop()
        self._pending_music = None

    def crossfade_to(self, name: str, ms: int) -> None:
        """Fade out the current music over ``ms`` then start ``name`` (looped).

        Pygame's mixer doesn't support a true overlapping crossfade on the
        single music channel. The previous version called
        ``mixer.music.load()`` immediately after ``fadeout(ms)`` which
        clobbered the fade-out — the listener heard an abrupt cut, not
        a smooth fade. Instead, schedule the fade-out and record the
        target track in ``_pending_music``; ``tick()`` (called from the
        main loop) finishes the transition once the mixer is free.
        Kid playtest 2026-04-28 #5 ("music cuts out and restarts").
        """
        if not self._enabled:
            return
        path = self._music_paths.get(name)
        if path is None:
            return
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.fadeout(ms)
                self._pending_music = (path, ms)
            else:
                pygame.mixer.music.load(path)
                pygame.mixer.music.play(-1, fade_ms=ms)
                self._pending_music = None
        except pygame.error as exc:
            logger.warning("failed to crossfade to %r: %s", name, exc)

    def tick(self) -> None:
        """Resolve any pending music transition. Call once per frame.

        When ``crossfade_to`` was issued mid-track it scheduled a fade-out
        and stashed the next track. Once the mixer reports idle (the
        fade-out completed), load the pending track and play it with a
        fade-in. No-op when there's nothing pending.
        """
        if not self._enabled or self._pending_music is None:
            return
        try:
            if pygame.mixer.music.get_busy():
                return
            path, ms = self._pending_music
            self._pending_music = None
            pygame.mixer.music.load(path)
            pygame.mixer.music.play(-1, fade_ms=ms)
        except pygame.error as exc:
            logger.warning("failed to resolve pending music: %s", exc)
            self._pending_music = None

    # -- internals ------------------------------------------------------

    def _warn_missing(self, path: str) -> None:
        if path in self._missing_warned:
            return
        self._missing_warned.add(path)
        logger.warning("audio asset missing: %s (continuing silently)", path)

    def _oldest_channel(self) -> pygame.mixer.Channel | None:
        if not self._channel_seq:
            return None
        oldest_idx = min(self._channel_seq, key=lambda k: self._channel_seq[k])
        return pygame.mixer.Channel(oldest_idx)


def _clamp_volume(volume: float) -> float:
    if volume < 0.0:
        return 0.0
    if volume > 1.0:
        return 1.0
    return volume


def _channel_index(channel: pygame.mixer.Channel) -> int:
    """Resolve a channel object back to its index by linear scan.

    pygame doesn't expose a Channel.id property, so we identify channels by
    object equality. Cheap — at most ``num_channels`` (default 8) comparisons.
    """
    for idx in range(pygame.mixer.get_num_channels()):
        if pygame.mixer.Channel(idx) == channel:
            return idx
    return -1
