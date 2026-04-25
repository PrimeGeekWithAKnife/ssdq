"""Scene state machine. Pause is a global modal overlay, not a scene."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ssdq.core.ecs import World
from ssdq.core.types import PlayerInput, TickIndex


class Scene(ABC):
    """Lifecycle: enter() → tick()/render() loop → exit()."""

    @abstractmethod
    def enter(self, world: World) -> None: ...

    @abstractmethod
    def tick(
        self,
        world: World,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> SceneTransition | None:
        """Step simulation by one fixed tick. Return a transition to change scenes,
        or None to stay."""

    @abstractmethod
    def render(self, world: World, surface: Any, alpha: float) -> None:
        """Draw to the platform's surface. `alpha` is the render-interpolation factor
        in [0, 1) — currently unused at sim level but available."""

    @abstractmethod
    def exit(self, world: World) -> None: ...


# ───────── transitions ─────────


@dataclass(frozen=True, slots=True)
class Push:
    """Push a new scene on top of the current one."""

    scene: Scene


@dataclass(frozen=True, slots=True)
class Pop:
    """Pop the current scene; resume the one below."""


@dataclass(frozen=True, slots=True)
class Replace:
    """Replace the current scene with a new one. Same depth."""

    scene: Scene


@dataclass(frozen=True, slots=True)
class Quit:
    """Tear down the whole stack and exit the game."""


SceneTransition = Push | Pop | Replace | Quit


# ───────── stack ─────────


class SceneStack:
    """Holds the scene stack and routes ticks/renders to the top.

    `paused` is a global modal flag — set by either player's pause edge — and
    suspends `tick()` until cleared. `render()` still runs (so we can dim the
    screen and draw a pause banner).
    """

    __slots__ = ("_paused", "_quit_requested", "_stack", "_world")

    def __init__(self, world: World) -> None:
        self._stack: list[Scene] = []
        self._world: World = world
        self._paused: bool = False
        self._quit_requested: bool = False

    @property
    def world(self) -> World:
        return self._world

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def quit_requested(self) -> bool:
        return self._quit_requested

    def toggle_pause(self) -> None:
        self._paused = not self._paused

    def is_empty(self) -> bool:
        return len(self._stack) == 0

    def top(self) -> Scene | None:
        return self._stack[-1] if self._stack else None

    def push(self, scene: Scene) -> None:
        self._stack.append(scene)
        scene.enter(self._world)

    def pop(self) -> None:
        if self._stack:
            self._stack[-1].exit(self._world)
            self._stack.pop()

    def replace(self, scene: Scene) -> None:
        if self._stack:
            self._stack[-1].exit(self._world)
            self._stack.pop()
        self._stack.append(scene)
        scene.enter(self._world)

    def request_quit(self) -> None:
        self._quit_requested = True

    def tick(
        self,
        tick: TickIndex,
        inputs: tuple[PlayerInput, PlayerInput],
    ) -> None:
        # Either player's pause edge consumes this tick — neither side sees it.
        if inputs[0].pause or inputs[1].pause:
            self.toggle_pause()
            return
        if self._paused:
            return
        scene = self.top()
        if scene is None:
            return
        transition = scene.tick(self._world, tick, inputs)
        if transition is not None:
            self._apply_transition(transition)

    def render(self, surface: Any, alpha: float) -> None:
        scene = self.top()
        if scene is None:
            return
        scene.render(self._world, surface, alpha)

    def _apply_transition(self, t: SceneTransition) -> None:
        if isinstance(t, Push):
            self.push(t.scene)
        elif isinstance(t, Pop):
            self.pop()
        elif isinstance(t, Replace):
            self.replace(t.scene)
        elif isinstance(t, Quit):
            while self._stack:
                self._stack[-1].exit(self._world)
                self._stack.pop()
            self._quit_requested = True
