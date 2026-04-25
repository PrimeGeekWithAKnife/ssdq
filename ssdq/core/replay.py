"""Replay record/playback. Per spec §8.4.

A replay is `(content_hash, [tick_inputs, ...])`. Because the simulation
is fully deterministic, replaying the recorded inputs against the same
content produces a bit-identical state — including particle jitter,
which is derived from tick index via SplitMix64 (see core/rng.py).

Wire format:
    header : 4-byte magic "SSRP" | uint16 version | uint16 reserved
           : 32-byte content_hash hex (left-padded with NUL if shorter)
           : uint64 tick_count
    body   : tick_count × 12-byte tick records
             each tick: 6 bytes per player × 2 players
             per player:
               int16 move_x_q8     # move.x × 127, clamped to [-127,127]
               int16 move_y_q8     # move.y × 127
               uint8 buttons_bits  # bit0 fire, 1 bomb, 2 pause, 3 confirm, 4 cancel
               uint8 reserved
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ssdq.core.types import PlayerInput, Vec2

_MAGIC = b"SSRP"
_VERSION = 1
_HEADER = struct.Struct("<4sHH32sQ")  # magic, ver, reserved, hash, tick_count
_TICK = struct.Struct("<hhBBhhBB")  # 12 bytes per tick (P1 then P2)


def _encode_input(p: PlayerInput) -> tuple[int, int, int]:
    mx = max(-127, min(127, round(p.move.x * 127.0)))
    my = max(-127, min(127, round(p.move.y * 127.0)))
    b = (
        (1 if p.fire else 0)
        | ((1 if p.bomb else 0) << 1)
        | ((1 if p.pause else 0) << 2)
        | ((1 if p.confirm else 0) << 3)
        | ((1 if p.cancel else 0) << 4)
    )
    return mx, my, b


def _decode_input(mx: int, my: int, b: int) -> PlayerInput:
    return PlayerInput(
        move=Vec2(mx / 127.0, my / 127.0),
        fire=bool(b & 0b00001),
        bomb=bool(b & 0b00010),
        pause=bool(b & 0b00100),
        confirm=bool(b & 0b01000),
        cancel=bool(b & 0b10000),
    )


@dataclass(slots=True)
class ReplayRecorder:
    """Append a (P1, P2) pair every tick. Cheap — just a list of tuples."""

    content_hash: str
    _ticks: list[tuple[PlayerInput, PlayerInput]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._ticks is None:
            self._ticks = []

    def push(self, p1: PlayerInput, p2: PlayerInput) -> None:
        self._ticks.append((p1, p2))

    def __len__(self) -> int:
        return len(self._ticks)

    def to_bytes(self) -> bytes:
        out = bytearray()
        h_bytes = self.content_hash.encode("ascii")[:32].ljust(32, b"\x00")
        out += _HEADER.pack(_MAGIC, _VERSION, 0, h_bytes, len(self._ticks))
        for p1, p2 in self._ticks:
            mx1, my1, b1 = _encode_input(p1)
            mx2, my2, b2 = _encode_input(p2)
            out += _TICK.pack(mx1, my1, b1, 0, mx2, my2, b2, 0)
        return bytes(out)

    def write(self, path: Path | str) -> None:
        Path(path).write_bytes(self.to_bytes())


@dataclass(frozen=True, slots=True)
class Replay:
    """A loaded replay. Iterate with `inputs_at(tick)`; len() is total ticks."""

    content_hash: str
    inputs: tuple[tuple[PlayerInput, PlayerInput], ...]

    def __len__(self) -> int:
        return len(self.inputs)

    def inputs_at(self, tick: int) -> tuple[PlayerInput, PlayerInput]:
        return self.inputs[tick]


def load_replay(path: Path | str) -> Replay:
    raw = Path(path).read_bytes()
    if len(raw) < _HEADER.size:
        raise ValueError(f"{path}: too small to be a replay")
    magic, ver, _reserved, h_bytes, tick_count = _HEADER.unpack_from(raw, 0)
    if magic != _MAGIC:
        raise ValueError(f"{path}: bad magic {magic!r}")
    if ver != _VERSION:
        raise ValueError(f"{path}: version {ver} not supported")
    expected = _HEADER.size + tick_count * _TICK.size
    if len(raw) != expected:
        raise ValueError(f"{path}: size mismatch — expected {expected} bytes, got {len(raw)}")
    content_hash = h_bytes.rstrip(b"\x00").decode("ascii")
    inputs: list[tuple[PlayerInput, PlayerInput]] = []
    off = _HEADER.size
    for _ in range(tick_count):
        mx1, my1, b1, _r1, mx2, my2, b2, _r2 = _TICK.unpack_from(raw, off)
        off += _TICK.size
        inputs.append((_decode_input(mx1, my1, b1), _decode_input(mx2, my2, b2)))
    return Replay(content_hash=content_hash, inputs=tuple(inputs))
