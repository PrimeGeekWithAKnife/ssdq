"""Top-10 leaderboard storage — persistent across sessions.

Kid playtest 2026-05-04: "Allow you to save your initials for your top
score and save top scores for future use." Classic arcade flow:
victory → if score qualifies for top 10, prompt for 3 letters → save
→ show table.

Storage shape: ``~/.ssdq/scores.json`` (XDG-style, survives Pi
reflashes via the user's home dir). On parse failure or missing file,
load returns an empty list — never raises, so a corrupted save can't
brick the boot.

JSON format:
    [
      {"initials": "ABC", "score": 12345, "ts": "2026-05-04T18:30"},
      ...
    ]
Sorted descending by score, capped at MAX_ENTRIES.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_ENTRIES: int = 10
DEFAULT_PATH: Path = Path.home() / ".ssdq" / "scores.json"


@dataclass(frozen=True, slots=True)
class Entry:
    """One leaderboard row."""

    initials: str  # exactly 3 chars, A-Z + space
    score: int
    ts: str  # ISO-8601 minute precision, UTC


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")


def _normalise_initials(raw: str) -> str:
    """Coerce arbitrary input to a valid 3-char initials string.

    Permits A-Z and space only; uppercases lowercase; pads/truncates
    to exactly 3 chars. Empty / all-space input becomes "AAA" so a
    saved row is always identifiable.
    """
    out: list[str] = []
    for ch in raw.upper():
        if ch == " " or ("A" <= ch <= "Z"):
            out.append(ch)
        if len(out) >= 3:
            break
    while len(out) < 3:
        out.append(" ")
    s = "".join(out)
    if s.strip() == "":
        return "AAA"
    return s


def load(path: Path | None = None) -> list[Entry]:
    """Read the saved leaderboard. Returns empty list if absent or
    malformed — never raises (a corrupted file just resets to empty).

    The default is resolved at call time from the module-level
    DEFAULT_PATH so tests can monkeypatch the destination without
    needing to thread a path through every call site.
    """
    if path is None:
        path = DEFAULT_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[Entry] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            initials = _normalise_initials(str(row.get("initials", "")))
            score = int(row.get("score", 0))
            ts = str(row.get("ts", ""))
        except (TypeError, ValueError):
            continue
        if score < 0:
            continue
        out.append(Entry(initials=initials, score=score, ts=ts))
    out.sort(key=lambda e: e.score, reverse=True)
    return out[:MAX_ENTRIES]


def save(entries: list[Entry], path: Path | None = None) -> None:
    """Atomically write the leaderboard. Creates parent dir if needed.

    Default destination is resolved at call time from DEFAULT_PATH so
    tests can monkeypatch the module attribute.
    """
    if path is None:
        path = DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_entries = sorted(entries, key=lambda e: e.score, reverse=True)[:MAX_ENTRIES]
    payload = [asdict(e) for e in sorted_entries]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def qualifies(entries: list[Entry], score: int) -> bool:
    """True if `score` would land on the top-10 board."""
    if score <= 0:
        return False
    if len(entries) < MAX_ENTRIES:
        return True
    # Strict > so a tie with the lowest doesn't bump them.
    return score > entries[-1].score


def add_entry(entries: list[Entry], initials: str, score: int) -> list[Entry]:
    """Insert a new entry and return the new top-N list."""
    new_entry = Entry(
        initials=_normalise_initials(initials),
        score=int(score),
        ts=_now_iso(),
    )
    merged = [*entries, new_entry]
    merged.sort(key=lambda e: e.score, reverse=True)
    return merged[:MAX_ENTRIES]
