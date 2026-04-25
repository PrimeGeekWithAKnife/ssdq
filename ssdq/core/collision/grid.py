"""Uniform spatial grid for broad-phase collision pruning.

Insert circle hitboxes once per tick; iterate `pairs()` to receive only
those entity pairs that share at least one cell. Narrow-phase (the actual
circle/AABB test) stays in the caller. We deliberately do not store cross-
faction filtering here — it's the caller's job, since rules differ between
player-vs-enemy, player-vs-pickup, friendly-fire, etc.

Determinism: insertion order is preserved per cell (`list.append`), and
`pairs()` yields cells in sorted-key order. Same inputs → same iteration.
"""

from __future__ import annotations

from collections.abc import Iterator

from ssdq.core.types import Entity, Vec2


class SpatialGrid:
    """Uniform grid keyed by (cx, cy) cell coordinates.

    Cell size should be ~2× the largest expected hitbox radius — too small
    and large entities span many cells; too large and broad-phase prunes
    nothing. For SSDQ the largest player hitbox is ~16 px; a cell of 64 px
    is a safe default.
    """

    __slots__ = ("_cell_size", "_cells", "_entries", "_inserted")

    def __init__(self, cell_size: float = 64.0) -> None:
        if cell_size <= 0.0:
            raise ValueError(f"cell_size must be > 0, got {cell_size}")
        self._cell_size = cell_size
        self._cells: dict[tuple[int, int], list[Entity]] = {}
        # entries kept for clear() reset and for inspection/debug
        self._entries: list[tuple[Entity, Vec2, float]] = []
        # set of inserted entities — guards against double-insert producing self-pairs
        self._inserted: set[Entity] = set()

    @property
    def cell_size(self) -> float:
        return self._cell_size

    def clear(self) -> None:
        self._cells.clear()
        self._entries.clear()
        self._inserted.clear()

    def insert(self, entity: Entity, pos: Vec2, radius: float) -> None:
        """Register an entity occupying a circle of `radius` at `pos`."""
        if entity in self._inserted:
            raise ValueError(f"entity {entity} inserted twice into the same grid tick")
        self._inserted.add(entity)
        self._entries.append((entity, pos, radius))
        cs = self._cell_size
        # Inclusive bounds — a circle that touches a cell edge counts as in it
        x0 = int((pos.x - radius) // cs)
        x1 = int((pos.x + radius) // cs)
        y0 = int((pos.y - radius) // cs)
        y1 = int((pos.y + radius) // cs)
        for cy in range(y0, y1 + 1):
            for cx in range(x0, x1 + 1):
                self._cells.setdefault((cx, cy), []).append(entity)

    def query_point(self, pos: Vec2) -> Iterator[Entity]:
        """Entities whose cells contain this point. Same entity may appear
        across multiple cells — caller dedupes if it cares."""
        cs = self._cell_size
        cell = (int(pos.x // cs), int(pos.y // cs))
        bucket = self._cells.get(cell)
        if bucket:
            yield from bucket

    def pairs(self) -> Iterator[tuple[Entity, Entity]]:
        """Yield each unique unordered (a, b) candidate pair exactly once,
        where a < b. Pairs only emitted for entities sharing a cell.

        Determinism: cells iterated in sorted-key order; within a cell,
        entries iterated in insertion order; pairs deduped by a seen-set
        keyed on the sorted tuple."""
        seen: set[tuple[Entity, Entity]] = set()
        for key in sorted(self._cells.keys()):
            bucket = self._cells[key]
            n = len(bucket)
            if n < 2:
                continue
            for i in range(n):
                a = bucket[i]
                for j in range(i + 1, n):
                    b = bucket[j]
                    pair = (a, b) if a < b else (b, a)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    yield pair

    def cell_count(self) -> int:
        return len(self._cells)

    def entry_count(self) -> int:
        return len(self._entries)
