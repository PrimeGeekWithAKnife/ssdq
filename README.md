# SSDQ — Super Space Defence Quasar

A retro, two-player local co-op vertical-scrolling shoot-'em-up, built in Python
with [pygame-ce](https://pyga.me/). Developed for **gamepad play on a Raspberry
Pi 5** plugged into the TV — but it's fully playable on a **keyboard** too, so it
should also run on earlier Raspberry Pi models (Pi 4 and friends) and on any
desktop. On older boards you may want to drop the resolution or sit a level or
two below the Pi 5's flat-out frame budget.

Seven levels and an optional high-speed "hyperspace" bonus run, an ECS engine,
data-driven content (levels, enemies, formations, bosses and weapons are all
YAML), procedurally generated music and sprites, a co-op scoring system with a
top-ten leaderboard, and one- or two-player modes.

## Running

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m ssdq.main
```

`--smoke` runs a short headless self-test instead of launching the game.

## Controls

The game is built around gamepads, but a keyboard works just as well — handy on
earlier Raspberry Pi models or any machine without a pad. Gamepad bindings are
rebindable in the in-game Settings screen.

**Gamepad** — move with the stick or D-pad:

| Action | Default |
| --- | --- |
| Fire | A |
| Bomb | X / Y |
| Shield | LB |
| Missile | RB |
| Pause | Start |

**Keyboard** — two players can share one board:

| Action | Player 1 | Player 2 |
| --- | --- | --- |
| Move | W A S D | Arrow keys |
| Fire | Space | Right Shift |
| Bomb | Left Shift | Right Ctrl |
| Shield | E | — |
| Cycle drones | F | — |
| Pause / Confirm | Enter | Tab |
| Cancel / Back | Esc | — |

(Missiles auto-fire by weapon tier — there's no manual missile key.)

## Project layout

| Path | What's there |
| --- | --- |
| `ssdq/core/` | Engine — ECS, collision, waves, scoring, power-ups, content loader |
| `ssdq/scenes/` | Game scenes — title, levels, hyperspace, docking, victory, menus |
| `ssdq/platform/` | pygame layer — rendering, audio, input, window |
| `content/` | All game data: `levels/`, `enemies.yaml`, `formations.yaml`, `ships.yaml`, and generated assets under `assets/` |

## Licence

MIT — see [LICENSE](LICENSE).
