"""Per-pad button bindings — load/save to ~/.config/ssdq/bindings.json.

Action enum values form the on-disk keys, so renaming any value is a
schema-breaking change; bump SCHEMA_VERSION and add a migration if that
happens.

Keyed by SDL pad GUID (``pygame.joystick.Joystick.get_guid()``) so two
different controllers can have different mappings; the GUID is stable
across reconnects of the same physical pad. Unknown GUIDs return
``default_binding()`` (canonical Xbox layout).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

SCHEMA_VERSION = 1


class BindingAction(str, Enum):
    FIRE = "fire"
    BOMB = "bomb"
    SHIELD = "shield"
    MISSILE = "missile"
    DRONE_CYCLE = "drone_cycle"
    PAUSE = "pause"
    CONFIRM = "confirm"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class PadBinding:
    """Maps each BindingAction to a single SDL button index."""

    buttons: dict[BindingAction, int]

    def button_for(self, action: BindingAction) -> int:
        return self.buttons[action]

    def with_button(self, action: BindingAction, index: int) -> PadBinding:
        new_buttons = dict(self.buttons)
        new_buttons[action] = int(index)
        return replace(self, buttons=new_buttons)

    def to_json(self) -> dict[str, int]:
        return {a.value: idx for a, idx in self.buttons.items()}

    @classmethod
    def from_json(cls, data: dict[str, int]) -> PadBinding:
        # Tolerate missing keys by filling from default_binding(); same for
        # extra unknown keys (ignored). Keeps the file forward-compatible
        # with action additions across game versions.
        merged = dict(default_binding().buttons)
        for k, v in data.items():
            try:
                action = BindingAction(k)
            except ValueError:
                continue
            merged[action] = int(v)
        return cls(buttons=merged)


def default_binding() -> PadBinding:
    """Canonical Xbox-360-layout defaults — matches pre-rebind behaviour."""
    return PadBinding(
        buttons={
            BindingAction.FIRE: 0,
            BindingAction.BOMB: 2,
            BindingAction.SHIELD: 4,
            BindingAction.MISSILE: 5,
            BindingAction.DRONE_CYCLE: 6,
            BindingAction.PAUSE: 7,
            BindingAction.CONFIRM: 0,
            BindingAction.CANCEL: 1,
        }
    )


def _default_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ssdq" / "bindings.json"


class BindingsStore:
    """Per-GUID binding registry, persisted as JSON.

    Loads lazily on first access. ``save()`` is explicit — callers (the
    SettingsScene) decide when to write. Malformed files fall back to
    defaults silently rather than crashing the game on launch.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else _default_path()
        self._loaded = False
        self._pads: dict[str, PadBinding] = {}
        self._names: dict[str, str] = {}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return  # malformed → defaults for everyone
        if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
            return
        pads = raw.get("pads", {})
        if not isinstance(pads, dict):
            return
        for guid, entry in pads.items():
            if not isinstance(entry, dict):
                continue
            actions = entry.get("actions")
            if not isinstance(actions, dict):
                continue
            try:
                self._pads[str(guid)] = PadBinding.from_json(actions)
            except (TypeError, ValueError):
                continue
            name = entry.get("name")
            if isinstance(name, str):
                self._names[str(guid)] = name

    def get(self, guid: str, *, pad_name: str = "") -> PadBinding:
        self._ensure_loaded()
        return self._pads.get(guid, default_binding())

    def set(self, guid: str, *, pad_name: str, binding: PadBinding) -> None:
        self._ensure_loaded()
        self._pads[guid] = binding
        if pad_name:
            self._names[guid] = pad_name

    def save(self) -> None:
        self._ensure_loaded()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "pads": {
                guid: {
                    "name": self._names.get(guid, ""),
                    "actions": b.to_json(),
                }
                for guid, b in self._pads.items()
            },
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)
