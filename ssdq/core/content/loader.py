"""Loader for the YAML content tree. Strict — fails fast on malformed data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ssdq.core.content.schema import (
    BombDef,
    BossDef,
    BossPhaseDef,
    CoopConfig,
    EnemyDef,
    EnemyWeaponDef,
    FirePattern,
    FormationDef,
    FormationKind,
    LevelDef,
    PickupDef,
    PickupEffect,
    ShipDef,
    SpawnDef,
    WaveDef,
    WeaponDef,
)


class ContentError(ValueError):
    """Raised when content fails validation. Carries a context path."""


_MIRROR_RX = re.compile(r"^mirror\(([a-z0-9_]+)\)$")


def _require(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise ContentError(f"{where}: missing required key '{key}'")
    return d[key]


def _as_tuple(seq: Any) -> tuple[Any, ...]:
    if seq is None:
        return ()
    if not isinstance(seq, list):
        raise ContentError(f"expected list, got {type(seq).__name__}")
    return tuple(seq)


# ───────── ships.yaml ─────────


def _load_ships(
    path: Path,
) -> tuple[
    dict[str, ShipDef],
    dict[str, WeaponDef],
    dict[str, tuple[str, ...]],
    dict[str, BombDef],
]:
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        raise ContentError(f"{path}: empty file")
    where = str(path)

    ships: dict[str, ShipDef] = {}
    for name, s in (raw.get("ships") or {}).items():
        ships[name] = ShipDef(
            name=name,
            sprite=_require(s, "sprite", f"{where}:ships.{name}"),
            sprite_hit_flash=s.get("sprite_hit_flash", s["sprite"]),
            max_speed=float(_require(s, "max_speed", f"{where}:ships.{name}")),
            accel=float(_require(s, "accel", f"{where}:ships.{name}")),
            hitbox_radius=float(_require(s, "hitbox_radius", f"{where}:ships.{name}")),
            starting_lives=int(s.get("starting_lives", 3)),
            starting_bombs=int(s.get("starting_bombs", 2)),
            respawn_invulnerability=float(s.get("respawn_invulnerability", 2.0)),
            respawn_clearing_radius=float(s.get("respawn_clearing_radius", 160.0)),
            primary_weapon=_require(
                s.get("weapons", {}), "primary", f"{where}:ships.{name}.weapons"
            ),
            bomb=_require(s.get("weapons", {}), "bomb", f"{where}:ships.{name}.weapons"),
        )

    weapons: dict[str, WeaponDef] = {}
    for name, w in (raw.get("weapons") or {}).items():
        pattern = tuple(
            FirePattern(
                angle_deg=float(p["angle_deg"]),
                offset_x=float(p["offset_x"]),
                offset_y=float(p["offset_y"]),
            )
            for p in (w.get("pattern") or [])
        )
        weapons[name] = WeaponDef(
            name=name,
            type=_require(w, "type", f"{where}:weapons.{name}"),
            sprite=_require(w, "sprite", f"{where}:weapons.{name}"),
            damage=int(_require(w, "damage", f"{where}:weapons.{name}")),
            speed=float(_require(w, "speed", f"{where}:weapons.{name}")),
            fire_rate=float(_require(w, "fire_rate", f"{where}:weapons.{name}")),
            pattern=pattern,
        )

    weapon_tree: dict[str, tuple[str, ...]] = {}
    for tree_name, levels in (raw.get("weapon_tree") or {}).items():
        weapon_tree[tree_name] = tuple(levels)
        for lvl in levels:
            if lvl not in weapons:
                raise ContentError(
                    f"{where}:weapon_tree.{tree_name}: references unknown weapon '{lvl}'"
                )

    bombs: dict[str, BombDef] = {}
    for name, b in (raw.get("bombs") or {}).items():
        bombs[name] = BombDef(
            name=name,
            sprite=_require(b, "sprite", f"{where}:bombs.{name}"),
            damage=int(_require(b, "damage", f"{where}:bombs.{name}")),
            radius=float(_require(b, "radius", f"{where}:bombs.{name}")),
            duration=float(_require(b, "duration", f"{where}:bombs.{name}")),
            clears_bullets=bool(b.get("clears_bullets", False)),
        )

    # Cross-check: ship's primary_weapon and bomb resolve.
    for ship in ships.values():
        if ship.primary_weapon not in weapons:
            raise ContentError(
                f"{where}:ships.{ship.name}: primary weapon "
                f"'{ship.primary_weapon}' not found in weapons"
            )
        if ship.bomb not in bombs:
            raise ContentError(f"{where}:ships.{ship.name}: bomb '{ship.bomb}' not found in bombs")

    return ships, weapons, weapon_tree, bombs


# ───────── enemies.yaml ─────────


def _load_enemies(
    path: Path,
) -> tuple[
    dict[str, EnemyDef],
    dict[str, BossDef],
    dict[str, EnemyWeaponDef],
    dict[str, PickupDef],
]:
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        raise ContentError(f"{path}: empty file")
    where = str(path)

    enemy_weapons: dict[str, EnemyWeaponDef] = {}
    for name, w in (raw.get("enemy_weapons") or {}).items():
        enemy_weapons[name] = EnemyWeaponDef(
            name=name,
            sprite=_require(w, "sprite", f"{where}:enemy_weapons.{name}"),
            damage=int(_require(w, "damage", f"{where}:enemy_weapons.{name}")),
            speed=float(_require(w, "speed", f"{where}:enemy_weapons.{name}")),
            pattern=_require(w, "pattern", f"{where}:enemy_weapons.{name}"),
            bullets_per_beat=int(_require(w, "bullets_per_beat", f"{where}:enemy_weapons.{name}")),
            fan_arc_deg=float(w.get("fan_arc_deg", 0.0)),
        )

    enemies: dict[str, EnemyDef] = {}
    for name, e in (raw.get("enemies") or {}).items():
        weapon = e.get("weapon")
        if weapon is not None and weapon not in enemy_weapons:
            raise ContentError(
                f"{where}:enemies.{name}: weapon '{weapon}' not found in enemy_weapons"
            )
        enemies[name] = EnemyDef(
            name=name,
            sprite=_require(e, "sprite", f"{where}:enemies.{name}"),
            hitbox_radius=float(_require(e, "hitbox_radius", f"{where}:enemies.{name}")),
            hp=int(_require(e, "hp", f"{where}:enemies.{name}")),
            speed_along_path=float(_require(e, "speed_along_path", f"{where}:enemies.{name}")),
            weapon=weapon,
            fire_beats=tuple(float(b) for b in (e.get("fire_beats") or [])),
            score=int(_require(e, "score", f"{where}:enemies.{name}")),
            drop_chance=float(e.get("drop_chance", 0.0)),
            drop_pool=tuple(e.get("drop_pool") or []),
        )

    pickups: dict[str, PickupDef] = {}
    for name, p in (raw.get("pickups") or {}).items():
        try:
            effect = PickupEffect(_require(p, "effect", f"{where}:pickups.{name}"))
        except ValueError as ex:
            raise ContentError(f"{where}:pickups.{name}: bad effect '{p.get('effect')}'") from ex
        pickups[name] = PickupDef(
            name=name,
            sprite=_require(p, "sprite", f"{where}:pickups.{name}"),
            hitbox_radius=float(_require(p, "hitbox_radius", f"{where}:pickups.{name}")),
            fall_speed=float(_require(p, "fall_speed", f"{where}:pickups.{name}")),
            effect=effect,
            speed_multiplier=float(p.get("speed_multiplier", 1.0)),
            duration=float(p.get("duration", 0.0)),
            ship_speed_step=float(p.get("ship_speed_step", 0.15)),
            fire_rate_multiplier=float(p.get("fire_rate_multiplier", 1.5)),
            missile_count=int(p.get("missile_count", 3)),
        )

    # Cross-check: enemies' drop_pool entries resolve to known pickups.
    for enemy in enemies.values():
        for d in enemy.drop_pool:
            if d not in pickups:
                raise ContentError(
                    f"{where}:enemies.{enemy.name}: drop_pool references unknown pickup '{d}'"
                )

    bosses: dict[str, BossDef] = {}
    for name, b in (raw.get("bosses") or {}).items():
        phases = tuple(
            BossPhaseDef(
                hp=int(_require(p, "hp", f"{where}:bosses.{name}.phases[{i}]")),
                formation=_require(p, "formation", f"{where}:bosses.{name}.phases[{i}]"),
                weapon=_require(p, "weapon", f"{where}:bosses.{name}.phases[{i}]"),
                fire_beats=tuple(float(b) for b in (p.get("fire_beats") or [])),
            )
            for i, p in enumerate(b.get("phases") or [])
        )
        if len(phases) < 1:
            raise ContentError(f"{where}:bosses.{name}: must have ≥ 1 phase")
        bosses[name] = BossDef(
            name=name,
            sprite=_require(b, "sprite", f"{where}:bosses.{name}"),
            hitbox_radius=float(_require(b, "hitbox_radius", f"{where}:bosses.{name}")),
            score=int(_require(b, "score", f"{where}:bosses.{name}")),
            intro_telegraph_seconds=float(b.get("intro_telegraph_seconds", 2.0)),
            phases=phases,
        )

    return enemies, bosses, enemy_weapons, pickups


# ───────── formations.yaml ─────────


def _load_formations(path: Path) -> dict[str, FormationDef]:
    raw = yaml.safe_load(path.read_text())
    where = str(path)
    out: dict[str, FormationDef] = {}
    for name, f in (raw.get("formations") or {}).items():
        try:
            kind = FormationKind(_require(f, "type", f"{where}:formations.{name}"))
        except ValueError as ex:
            raise ContentError(f"{where}:formations.{name}: bad type '{f.get('type')}'") from ex
        cps = tuple(
            (float(p[0]), float(p[1]))
            for p in _require(f, "control_points", f"{where}:formations.{name}")
        )
        if len(cps) < 2:
            raise ContentError(f"{where}:formations.{name}: need ≥ 2 control points")
        out[name] = FormationDef(
            name=name,
            kind=kind,
            duration=float(_require(f, "duration", f"{where}:formations.{name}")),
            control_points=cps,
            loop=bool(f.get("loop", False)),
        )
    return out


# ───────── levels/level_NN.yaml ─────────


def _load_level(path: Path, formations: dict[str, FormationDef]) -> LevelDef:
    raw = yaml.safe_load(path.read_text())
    where = str(path)

    waves: list[WaveDef] = []
    for i, w in enumerate(raw.get("waves") or []):
        spawns: list[SpawnDef] = []
        boss = w.get("boss")
        for j, sp in enumerate(w.get("spawn") or []):
            f_name = _require(sp, "formation", f"{where}:waves[{i}].spawn[{j}]")
            mirrored = False
            m = _MIRROR_RX.match(f_name)
            if m is not None:
                f_name = m.group(1)
                mirrored = True
            if f_name not in formations:
                raise ContentError(f"{where}:waves[{i}].spawn[{j}]: formation '{f_name}' not found")
            return_passes = int(sp.get("return_passes", 0))
            if return_passes < 0:
                raise ContentError(
                    f"{where}:waves[{i}].spawn[{j}]: return_passes must be ≥ 0, got {return_passes}"
                )
            spawns.append(
                SpawnDef(
                    enemy=_require(sp, "enemy", f"{where}:waves[{i}].spawn[{j}]"),
                    count=int(_require(sp, "count", f"{where}:waves[{i}].spawn[{j}]")),
                    formation=f_name,
                    spacing=float(_require(sp, "spacing", f"{where}:waves[{i}].spawn[{j}]")),
                    delay=float(sp.get("delay", 0.0)),
                    mirrored=mirrored,
                    return_passes=return_passes,
                )
            )
        waves.append(
            WaveDef(
                at=float(_require(w, "at", f"{where}:waves[{i}]")),
                stage=str(w.get("stage", "")),
                spawns=tuple(spawns),
                boss=boss,
            )
        )

    return LevelDef(
        level=int(_require(raw, "level", where)),
        title=str(_require(raw, "title", where)),
        codename=str(raw.get("codename", "")),
        music=str(_require(raw, "music", where)),
        boss_music=str(raw.get("boss_music", raw.get("music", ""))),
        background=str(_require(raw, "background", where)),
        length_seconds=float(_require(raw, "length_seconds", where)),
        waves=tuple(sorted(waves, key=lambda w: w.at)),
    )


# ───────── coop.yaml ─────────


def _load_coop(path: Path) -> CoopConfig:
    raw = yaml.safe_load(path.read_text())
    c = _require(raw, "coop", str(path))
    where = f"{path}:coop"
    return CoopConfig(
        starting_lives=int(_require(c, "starting_lives", where)),
        continues_per_session=int(_require(c, "continues_per_session", where)),
        respawn_delay=float(_require(c, "respawn_delay", where)),
        respawn_invulnerability=float(_require(c, "respawn_invulnerability", where)),
        respawn_clearing_radius=float(_require(c, "respawn_clearing_radius", where)),
        friendly_fire=bool(c.get("friendly_fire", False)),
        ship_on_ship_collision=bool(c.get("ship_on_ship_collision", False)),
        proximity_bonus_radius=float(c.get("proximity_bonus_radius", 200.0)),
        proximity_bonus_multiplier=float(c.get("proximity_bonus_multiplier", 1.5)),
        proximity_bonus_edge_zone=float(c.get("proximity_bonus_edge_zone", 60.0)),
        pause_dim_alpha=int(c.get("pause_dim_alpha", 128)),
    )


# ───────── bundle ─────────


@dataclass(frozen=True, slots=True)
class ContentBundle:
    """The full loaded content tree, ready to drive simulation."""

    ships: dict[str, ShipDef]
    weapons: dict[str, WeaponDef]
    weapon_trees: dict[str, tuple[str, ...]]
    bombs: dict[str, BombDef]
    enemies: dict[str, EnemyDef]
    bosses: dict[str, BossDef]
    enemy_weapons: dict[str, EnemyWeaponDef]
    pickups: dict[str, PickupDef]
    formations: dict[str, FormationDef]
    levels: dict[int, LevelDef]
    coop: CoopConfig

    def content_hash(self) -> str:
        """Stable hash of all content. Replays bind to this — if content
        changes in any way, replays from before are flagged stale."""
        import hashlib

        h = hashlib.blake2b(digest_size=16)
        # Hash by deterministic dataclass repr.
        for obj in (
            sorted(self.ships.items()),
            sorted(self.weapons.items()),
            sorted(self.weapon_trees.items()),
            sorted(self.bombs.items()),
            sorted(self.enemies.items()),
            sorted(self.bosses.items()),
            sorted(self.enemy_weapons.items()),
            sorted(self.pickups.items()),
            sorted(self.formations.items()),
            sorted(self.levels.items()),
            self.coop,
        ):
            h.update(repr(obj).encode())
        return h.hexdigest()


def load_bundle(content_dir: Path | str) -> ContentBundle:
    root = Path(content_dir)
    if not root.is_dir():
        raise ContentError(f"content dir not found: {root}")

    ships, weapons, weapon_trees, bombs = _load_ships(root / "ships.yaml")
    enemies, bosses, enemy_weapons, pickups = _load_enemies(root / "enemies.yaml")
    formations = _load_formations(root / "formations.yaml")
    coop = _load_coop(root / "coop.yaml")

    levels: dict[int, LevelDef] = {}
    levels_dir = root / "levels"
    if levels_dir.is_dir():
        for lp in sorted(levels_dir.glob("level_*.yaml")):
            ld = _load_level(lp, formations)
            levels[ld.level] = ld

    # Cross-checks across files
    for level in levels.values():
        for wave in level.waves:
            for sp in wave.spawns:
                if sp.enemy not in enemies:
                    raise ContentError(
                        f"level {level.level}: spawn references unknown enemy '{sp.enemy}'"
                    )
            if wave.boss is not None and wave.boss not in bosses:
                raise ContentError(
                    f"level {level.level}: wave references unknown boss '{wave.boss}'"
                )

    for boss in bosses.values():
        for i, ph in enumerate(boss.phases):
            if ph.formation not in formations:
                raise ContentError(
                    f"boss {boss.name} phase {i}: formation '{ph.formation}' not found"
                )
            if ph.weapon not in enemy_weapons:
                raise ContentError(f"boss {boss.name} phase {i}: weapon '{ph.weapon}' not found")

    return ContentBundle(
        ships=ships,
        weapons=weapons,
        weapon_trees=weapon_trees,
        bombs=bombs,
        enemies=enemies,
        bosses=bosses,
        enemy_weapons=enemy_weapons,
        pickups=pickups,
        formations=formations,
        levels=levels,
        coop=coop,
    )
