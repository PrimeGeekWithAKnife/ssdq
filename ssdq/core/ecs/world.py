"""Tiny entity-component system.

Design goals:
  * Deterministic iteration order — important for replay regression.
  * Zero pygame dependency.
  * Components are arbitrary plain (typically frozen) dataclass instances; any type
    that hash by identity works.
  * Entities are monotonic int IDs; never reused within a session.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TypeVar, cast

from ssdq.core.types import Entity

T = TypeVar("T")
T1 = TypeVar("T1")
T2 = TypeVar("T2")
T3 = TypeVar("T3")
T4 = TypeVar("T4")


class World:
    """Storage for entities and their components.

    A component is any instance whose *type* is the key it's stored under.
    Iteration order over a component table is insertion order (CPython dict guarantee),
    which keeps queries deterministic across runs given the same spawn sequence.
    """

    __slots__ = ("_alive", "_components", "_dead", "_next_id", "_resources")

    def __init__(self) -> None:
        self._components: dict[type[Any], dict[Entity, Any]] = {}
        self._next_id: int = 1
        self._alive: set[Entity] = set()
        self._dead: set[Entity] = set()
        self._resources: dict[type[Any], Any] = {}

    # ───────── entities ─────────

    def spawn(self, *components: Any) -> Entity:
        eid = Entity(self._next_id)
        self._next_id += 1
        self._alive.add(eid)
        for comp in components:
            self.add(eid, comp)
        return eid

    def despawn(self, entity: Entity) -> None:
        if entity not in self._alive:
            return
        self._alive.discard(entity)
        self._dead.add(entity)
        for table in self._components.values():
            table.pop(entity, None)

    def is_alive(self, entity: Entity) -> bool:
        return entity in self._alive

    def alive_entities(self) -> Iterator[Entity]:
        # Sorted to keep order deterministic even after despawns
        yield from sorted(self._alive)

    def entity_count(self) -> int:
        return len(self._alive)

    # ───────── components ─────────

    def add(self, entity: Entity, component: Any) -> None:
        if entity not in self._alive:
            raise KeyError(f"entity {entity} is not alive")
        table = self._components.setdefault(type(component), {})
        table[entity] = component

    def remove(self, entity: Entity, component_type: type[T]) -> None:
        table = self._components.get(component_type)
        if table is not None:
            table.pop(entity, None)

    def get(self, entity: Entity, component_type: type[T]) -> T | None:
        table = self._components.get(component_type)
        if table is None:
            return None
        return cast("T | None", table.get(entity))

    def must_get(self, entity: Entity, component_type: type[T]) -> T:
        c = self.get(entity, component_type)
        if c is None:
            raise KeyError(f"entity {entity} has no {component_type.__name__}")
        return c

    def has(self, entity: Entity, component_type: type[Any]) -> bool:
        table = self._components.get(component_type)
        return table is not None and entity in table

    def replace(self, entity: Entity, component: Any) -> None:
        """Add or overwrite a component on an entity."""
        if entity not in self._alive:
            raise KeyError(f"entity {entity} is not alive")
        self._components.setdefault(type(component), {})[entity] = component

    # ───────── queries ─────────

    def query1(self, t: type[T]) -> Iterator[tuple[Entity, T]]:
        table = self._components.get(t)
        if not table:
            return
        yield from table.items()

    def query2(self, t1: type[T1], t2: type[T2]) -> Iterator[tuple[Entity, T1, T2]]:
        a = self._components.get(t1) or {}
        b = self._components.get(t2)
        if not a or not b:
            return
        if len(a) > len(b):
            a, b = b, a
            swap = True
        else:
            swap = False
        for eid, ca in a.items():
            cb = b.get(eid)
            if cb is None:
                continue
            if swap:
                yield eid, cast("T1", cb), cast("T2", ca)
            else:
                yield eid, cast("T1", ca), cast("T2", cb)

    def query3(
        self, t1: type[T1], t2: type[T2], t3: type[T3]
    ) -> Iterator[tuple[Entity, T1, T2, T3]]:
        for eid, c1 in self.query1(t1):
            c2 = self.get(eid, t2)
            if c2 is None:
                continue
            c3 = self.get(eid, t3)
            if c3 is None:
                continue
            yield eid, c1, c2, c3

    def query4(
        self, t1: type[T1], t2: type[T2], t3: type[T3], t4: type[T4]
    ) -> Iterator[tuple[Entity, T1, T2, T3, T4]]:
        for eid, c1 in self.query1(t1):
            c2 = self.get(eid, t2)
            if c2 is None:
                continue
            c3 = self.get(eid, t3)
            if c3 is None:
                continue
            c4 = self.get(eid, t4)
            if c4 is None:
                continue
            yield eid, c1, c2, c3, c4

    # ───────── world resources (singletons keyed by type) ─────────

    def insert_resource(self, resource: Any) -> None:
        self._resources[type(resource)] = resource

    def resource(self, t: type[T]) -> T:
        r = self._resources.get(t)
        if r is None:
            raise KeyError(f"resource {t.__name__} not present in world")
        return cast("T", r)

    def try_resource(self, t: type[T]) -> T | None:
        return cast("T | None", self._resources.get(t))
