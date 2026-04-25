"""Voice-line stub.

Reserved for future co-op encouragement / boss taunts; the slice doesn't
ship voice content so this is a no-op placeholder. Keeping the type around
means scenes can wire calls today and we don't have to refactor them when
real voice arrives.
"""

from __future__ import annotations


class VoiceLines:
    """Placeholder voice-line player. ``play`` is a no-op."""

    def __init__(self) -> None:
        # Intentional: no state. Subclass / replace once content lands.
        pass

    def play(self, name: str) -> None:
        """Schedule a voice line by name. Currently silent."""
        del name  # unused — noop until real voice content arrives
