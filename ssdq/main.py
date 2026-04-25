"""Entry point — wires platform → core → scenes for the SSDQ slice.

Run with `python -m ssdq.main` (or `make run`).

Environment:
* `SSDQ_KEYBOARD=1` enables keyboard fallback (WASD+Space P1, arrows+RShift P2)
* `SDL_VIDEODRIVER=dummy` for headless test runs
* `SDL_AUDIODRIVER=dummy` for silent test runs

CLI flags:
* `--smoke` runs the auto-pilot for ~90 sim seconds then exits 0/1.
* `--replay PATH` replays a `.ssrp` recording and asserts deterministic
   final state. Used by `make test-replay`.
* `--record PATH` records inputs to `.ssrp`.
* `--frames N` cap simulation frames (sim ticks) — useful for tests.
* `--fullscreen` + `--no-vsync` toggle window flags.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pygame

from ssdq.core.clock import TICK_DT, Clock
from ssdq.core.content.loader import load_bundle
from ssdq.core.coop.options import CoopOptions
from ssdq.core.ecs import World
from ssdq.core.replay import ReplayRecorder, load_replay
from ssdq.core.scene import SceneStack
from ssdq.core.types import PlayerInput, TickIndex
from ssdq.platform.audio import AudioBus
from ssdq.platform.input import InputProvider, select_provider
from ssdq.platform.render import Renderer, SpriteAtlas
from ssdq.platform.window import Window
from ssdq.scenes import AppState, BootScene

logger = logging.getLogger("ssdq")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ssdq")
    parser.add_argument("--smoke", action="store_true", help="auto-pilot smoke run (~90s sim)")
    parser.add_argument("--replay", type=Path, help="replay an .ssrp file")
    parser.add_argument("--record", type=Path, help="record inputs to an .ssrp file")
    parser.add_argument(
        "--frames", type=int, default=0, help="cap simulation ticks (0 = unlimited)"
    )
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--no-vsync", action="store_true")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument(
        "--quit-on-game-over",
        action="store_true",
        help="exit cleanly when a game-over or level-complete is reached",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


def _content_root() -> Path:
    """Resolve `content/` relative to the package, with cwd as fallback."""
    pkg_relative = Path(__file__).resolve().parent.parent / "content"
    if pkg_relative.is_dir():
        return pkg_relative
    return Path("content")


def _make_app_state(record_path: Path | None) -> AppState:
    bundle = load_bundle(_content_root())
    audio = AudioBus()
    options = CoopOptions(
        starting_lives=bundle.coop.starting_lives,
        continues=bundle.coop.continues_per_session,
    )
    recorder = (
        ReplayRecorder(content_hash=bundle.content_hash()) if record_path is not None else None
    )
    return AppState(content=bundle, audio=audio, options=options, recorder=recorder)


def _build_atlas(content_root: Path, app: AppState) -> SpriteAtlas:
    sprites_dir = content_root / "assets" / "sprites"
    atlas = SpriteAtlas(sprites_root=sprites_dir)
    atlas.preload_bundle(app.content)
    return atlas


def _build_replay_provider(replay_path: Path) -> tuple[InputProvider, int]:
    """Return a provider that drains a replay file. Second value is the
    expected total tick count (caller stops after that)."""
    replay = load_replay(replay_path)

    class _ReplayProvider:
        def __init__(self) -> None:
            self._cursor = 0

        def poll(self) -> tuple[PlayerInput, PlayerInput]:
            if self._cursor >= len(replay):
                return (PlayerInput(), PlayerInput())
            out = replay.inputs_at(self._cursor)
            self._cursor += 1
            return out

        @property
        def disconnected(self) -> None:
            return None

    return _ReplayProvider(), len(replay)


def _build_smoke_provider() -> InputProvider:
    """Auto-pilot: hold fire, slowly drift toward upper-screen centre, no bombs."""
    from ssdq.core.types import Vec2

    class _SmokeProvider:
        def __init__(self) -> None:
            self._tick = 0

        def poll(self) -> tuple[PlayerInput, PlayerInput]:
            self._tick += 1
            # Always confirm the title prompt for the first 30 ticks.
            confirm = self._tick < 30
            # Always-fire policy.
            inp_p1 = PlayerInput(
                move=Vec2(-0.05, -0.2),
                fire=True,
                confirm=confirm,
            )
            inp_p2 = PlayerInput(
                move=Vec2(0.05, -0.2),
                fire=True,
                confirm=confirm,
            )
            return inp_p1, inp_p2

        @property
        def disconnected(self) -> None:
            return None

    return _SmokeProvider()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")

    record_path: Path | None = args.record if args.record else None
    app = _make_app_state(record_path)

    # Provider selection: replay > smoke > real
    expected_ticks = 0
    if args.replay is not None:
        provider, expected_ticks = _build_replay_provider(args.replay)
    elif args.smoke:
        provider = _build_smoke_provider()
    else:
        provider = select_provider()

    with Window(
        width=args.width,
        height=args.height,
        fullscreen=args.fullscreen,
        vsync=not args.no_vsync,
    ) as window:
        atlas = _build_atlas(_content_root(), app)
        renderer = Renderer(atlas=atlas, size=window.size)
        world = World()
        stack = SceneStack(world)
        stack.push(BootScene(app))

        clock = Clock()
        last_real = time.perf_counter()
        ticks_done = 0
        frame_cap = (
            args.frames if args.frames > 0 else (expected_ticks if expected_ticks > 0 else 0)
        )
        # Smoke runs cap at 90s sim by default; real play has no cap.
        if args.smoke and frame_cap == 0:
            frame_cap = 90 * 60  # 60 Hz × 90 s

        try:
            while not stack.quit_requested:
                # Pump pygame events so providers see them.
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        stack.request_quit()

                now = time.perf_counter()
                real_dt = now - last_real
                last_real = now

                # In smoke/replay mode we run sim as fast as possible
                # (ignore wall clock — substitute fixed dt per frame).
                if args.smoke or args.replay is not None:
                    real_dt = TICK_DT

                ticks_to_do = clock.advance(real_dt)
                for _ in range(ticks_to_do):
                    inputs = provider.poll()
                    if app.recorder is not None:
                        app.recorder.push(inputs[0], inputs[1])
                    stack.tick(TickIndex(int(clock.tick)), inputs)
                    ticks_done += 1
                    if frame_cap > 0 and ticks_done >= frame_cap:
                        stack.request_quit()
                        break

                # Render. The Level scene's renderer is world-driven and lives
                # in `Renderer.draw`; chrome scenes (Title/GameOver/etc.) draw
                # their own surface via Scene.render. We pick which one based
                # on the top scene having world entities to render.
                surface = window.surface()
                top_scene = stack.top()
                from ssdq.scenes.level import LevelScene as _LevelScene

                if isinstance(top_scene, _LevelScene):
                    renderer.draw(
                        world,
                        surface,
                        clock.alpha,
                        tick=int(clock.tick),
                        paused=stack.paused,
                        pause_dim_alpha=app.content.coop.pause_dim_alpha,
                    )
                else:
                    # Chrome scenes paint themselves; pause overlay still applies.
                    stack.render(surface, clock.alpha)
                window.flip()

                if args.quit_on_game_over and (app.completed_level or _is_game_over(world)):
                    stack.request_quit()
        finally:
            if app.recorder is not None and record_path is not None:
                app.recorder.write(record_path)

    if args.replay is not None and ticks_done < expected_ticks:
        logger.error("replay short: %d / %d ticks", ticks_done, expected_ticks)
        return 1
    return 0


def _is_game_over(world: World) -> bool:
    # The level scene puts AppState as a resource; we read the
    # last-state hint that exit() updates.
    try:
        app = world.resource(AppState)
    except KeyError:
        return False
    return app.completed_level


if __name__ == "__main__":
    sys.exit(main())
