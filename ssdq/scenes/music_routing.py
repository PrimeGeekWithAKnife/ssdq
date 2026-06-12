"""Music-track naming: per-level pools, boss cues, clamping.

Pure helpers shared by BootScene (registration) and LevelScene
(selection) so the two can never drift apart on naming. Track names
follow the established convention: bus name == file stem == TRACKS
recipe key (``level_NN`` / ``level_NN_b`` / ``level_NN_c`` /
``boss_NN`` / ``hyperspace``).

Fun review 2026-06-12: one track per level was wearing thin after ~20
playthroughs, so each level owns a pool of three tracks (base + a
darker ``_b`` and a brighter ``_c`` variant) and LevelScene rotates
through the pool per entry via ``AppState.music_rotation``.
"""

from __future__ import annotations

# Highest level index with its own music pool + boss track. Content may
# trail this (the registration side no-ops on missing files), but the
# naming helpers always clamp into this range.
MAX_MUSIC_LEVEL = 7

# Pool suffixes in rotation order: base first so a level's debut entry
# always plays its signature track, then the darker/brighter variants.
LEVEL_VARIANT_SUFFIXES = ("", "_b", "_c")


def clamp_level(level_index: int) -> int:
    """Map any level index into 1..MAX_MUSIC_LEVEL.

    Out-of-range indices fall back to level 1 (matching the pre-pool
    behaviour) so the bus always has *something* registered to play —
    defensive against dev-jumps to levels without content.
    """
    return level_index if 1 <= level_index <= MAX_MUSIC_LEVEL else 1


def level_music_pool(level_index: int) -> list[str]:
    """Ordered track-name pool for a level, e.g. level 3 →
    ``["level_03", "level_03_b", "level_03_c"]``."""
    idx = clamp_level(level_index)
    return [f"level_{idx:02d}{suffix}" for suffix in LEVEL_VARIANT_SUFFIXES]


def boss_music_name(level_index: int) -> str:
    """Boss track name for a level (clamped, no variants — boss fights
    are short enough that one cue per boss stays fresh)."""
    return f"boss_{clamp_level(level_index):02d}"
