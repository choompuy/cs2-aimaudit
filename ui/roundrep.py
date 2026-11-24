"""
2D replay: a canvas mini-map of the WHOLE round - every player, every duel.

    round_replay(payload, *, key, on_round)   # render (this module)
    round_payload(data, ...)                  # pure JSON model, unit-tested

There is no "selected player" here: a replay shows everyone the same way.
Layout (everything client-side, like the in-game spectator HUD):

- the MAP is the whole stage: from 900px of width the player and duel panels
  float OVER the canvas with a transparent chrome (each hides from a
  transport button); under 900px they STACK under the map in the same frame
  (the players a column per team, the duels in a grid). The map is as high
  as the screen allows (360-900px, never over 0.62 of its width), its art is
  toned down and fitted between the panels; a saturated colour always means
  a team, the state of the interface is white;
- NOTHING MOVES while it plays: every line of a card has a fixed height and
  every element of it (weapon, armor, HP, grenades, bomb, status, money,
  speed) is a slot of a fixed width that exists with or without an icon in
  it; the card shows as much as the height of the map allows (data-dens):
  all three lines, or two (the money moves behind the K/D), or the name
  line only;
- LEFT overlay: the players of both teams in ONE list sorted by nickname
  inside a team, so every player keeps his place from round to round (sides
  swap at halftime, the team colour follows them). A spectator-style card
  per player, no photo, three lines: the nickname and the weapon in hands
  with the armor and the HP number; the match kills / deaths (live: the
  round's own kills and the death are added at the moment they happen) and
  the grenades / defuse kit; the money and the live speed. The HP is the
  card's BACKGROUND: the team colour fills it from the left up to hp%. A
  dead player's card is dimmed and the HP turns into the skull. The team
  economy sits in the team header. An eye button hides a player from the
  map (the choice survives round switches); each panel's header carries a
  collapse button;
- CENTER: the radar canvas (wheel = zoom, LMB drag = pan, double click or
  the button = reset): a small team-coloured dot per player (CT / T), a
  view-direction arrow, the name, an HP bar and the weapon; a dashed ring
  marks a player moving faster than the stop threshold; the living stack
  in the left table's order and the dead lie under them in death order,
  grey skulls included; flashed ones turn white with a ring that runs out; shots are fading
  tracers and hits stars;
- the TIMELINE under the map: kill marks coloured by the KILLER'S side, the
  bomb events, the selected duel's segment highlighted, a draggable
  playhead. While a duel is selected the playhead is LOCKED to its segment;
- the DUEL PANEL of the selected duel: for BOTH duelists the speed, the
  crosshair turn rate and the angle to the enemy's head live at the
  playhead, a small chart of those over the whole duel with the shots, the
  numbers measured at the first shot, and the keys they held;
- RIGHT overlay: one card per duel of the round - EVERY duel of every player, not
  only one player's: future duels are semi-transparent, past ones solid, the
  selected one gets the outline; a click locks the timeline to its segment;
- BELOW the timeline: the rounds of the whole match, coloured by the winning
  side and marked with how the round ended - a click switches the replay to
  that round. The speed button opens a menu from 0.1x to 4x.

Python builds the whole payload (round_payload is pure and unit-tested);
the JS (ui/roundrep.js) only interpolates, draws and animates - playback
runs entirely in the browser on requestAnimationFrame, no Streamlit reruns
while playing. Colours come from theme tokens through the payload, CSS
variables only for the chrome (ui/roundrep.css), like ui/table.py.

The payload (round_payload's contract)
--------------------------------------

``data`` is core.replay.round_replay_data's dict (its ``info`` says how
the round ended); ``names``/``side_of`` resolve players (``side_of(sid)``
is the side in THIS round); ``radar`` is core.load_radar's dict (or None:
a plain unit grid is drawn). ``duels`` are core.round_duels' entries -
every duel of the round: {"a", "b"} bracket the segment the timeline
locks to, "tick" is the kill, "killer"/"victim" are SteamIDs, the rest
the numbers measured at the first shots. ``rounds`` is the whole match
[{"n", "win", "why"}] for the round bar (win 'CT'/'T'/'', why one of
core.ROUND_REASONS); ``bombs`` are [(tick, kind, sid)] of the round
(default: ``data["info"]["bomb"]``); ``at`` is the tick to start at;
``sel`` preselects a duel (locks the timeline).

``data["kd0"]`` ({sid: [kills, deaths]} of the match before the round, from
core.replay.round_replay_data) becomes each player's ``kd``.

Players come out grouped by side (CT first, then T) and sorted by
nickname inside a side, so a player keeps his place in his team's list;
positions and view angles are whole numbers; held keys are ONE bitmask
per sample (bit i = ``keys[i]``), so every player's keys cost a single
array. Every duel carries ``tel``: both duelists' speed / turn rate /
crosshair-to-head angle and the distance per replay sample inside the
duel's segment (core.duel_telemetry).
"""

from __future__ import annotations

import math
import re
from functools import partial
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Sequence

import numpy as np
import pandas as pd

from ui import component

_HERE = Path(__file__).resolve().parent
# the front end lives in real files (syntax-checkable, no escaping games);
# colours reach it only through the payload (theme tokens)
_CSS = (_HERE / "roundrep.css").read_text(encoding="utf-8")
_JS = (_HERE / "roundrep.js").read_text(encoding="utf-8")


def _r1(v) -> float | None:
    """Round to 1 decimal (JSON size); NaN -> None so JSON stays valid."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, 1)


def _ri(a) -> list[int | None]:
    """Whole numbers (world units need no decimals); NaN -> None."""
    return [None if not math.isfinite(v) else int(round(v)) for v in a]


def _wshort(v) -> str:
    """active_weapon_name -> the short form ('weapon_ak47' -> 'ak47')."""
    s = str(v or "")
    return s[7:] if s.startswith("weapon_") else s


def _ffilled(g: pd.DataFrame, col: str) -> np.ndarray:
    return pd.to_numeric(g[col], errors="coerce").ffill().bfill().to_numpy(float)


def _runs(grid, values, enc) -> list[list[Any]]:
    """[[tick, *enc(v)]] at every sample where the value CHANGES: slowly
    changing state (armor, money, inventory) costs a few entries, not a
    sample per tick."""
    out: list[list[Any]] = []
    last: Any = object()
    for tick, v in zip(grid, values, strict=True):
        if v != last:
            out.append([int(tick), *enc(v)])
            last = v
    return out


# inventory entries the demo prints that do not compact to their icon key
_INV_ALIASES = {
    "c4explosive": "c4",
    "plantedc4": "c4",
    "defuser": "defuse",
    "itemdefuser": "defuse",
    "defusekit": "defuse",
    "incendiarygrenade": "incgrenade",
    "decoygrenade": "decoy",
    "zeusx27": "taser",
    "usps": "usp_silencer",
    "usp9": "usp_silencer",
    "p2000": "hkp2000",
    "m4a1s": "m4a1_silencer",
    "mp5": "mp5sd",
    # compacted CS2 display names (inventory gives "Desert Eagle", not
    # "deagle"): the ones whose letters do not compact to the icon key
    "deserteagle": "deagle",
    "glock18": "glock",
    "highexplosivegrenade": "hegrenade",
    "r8revolver": "revolver",
    "cz75auto": "cz75a",
    "dualberettas": "elite",
    "ppbizon": "bizon",
    "sg553": "sg556",
    "m4a4": "m4a1",
    "karambit": "knife_karambit",
    "m9bayonet": "knife_m9_bayonet",
    "huntsmanknife": "knife_tactical",
    "nomadknife": "knife_outdoor",
    "shadowdaggers": "knife_push",
    "paracordknife": "knife_cord",
    "navajaknife": "knife_gypsy_jackknife",
    "stilettoknife": "knife_stiletto",
    "talonknife": "knife_widowmaker",
    "bowieknife": "knife_survival_bowie",
    "survivalknife": "knife_canis",
    "classicknife": "knife_css",
    "kukriknife": "knife",
    "urusknife": "knife_ursus",
}


def _inv_key(weapon_info, name: Any) -> str:
    """One inventory entry -> the icon key used across the payload:
    weapon_info's resolved key when there is one, else the compacted demo
    name (with the few aliases that do not compact cleanly)."""
    s = str(name or "").strip()
    if not s:
        return ""
    if weapon_info is not None:
        info = weapon_info(s)
        if info.get("key"):
            return info["key"]
    c = re.sub(r"[^a-z0-9]", "", s.lower().replace("weapon_", ""))
    c = _INV_ALIASES.get(c, c)
    if c.endswith("knife") and not c.startswith("knife"):
        c = "knife_" + c[: -len("knife")]
    return c or s.lower()


def _inv_keys(weapon_info, entry) -> tuple:
    """One inventory snapshot -> icon keys, duplicates kept: two
    flashbangs are two."""
    if entry is None or (isinstance(entry, float) and math.isnan(entry)):
        return ()
    try:
        items = list(entry)
    except TypeError:
        return ()
    return tuple(k for k in (_inv_key(weapon_info, x) for x in items) if k)


def _economy_fields(g, grid, inv_keys) -> dict:
    """The economy of one player as change lists: armor (+ helmet), the
    defuse kit, defusing right now, money, the value of the gear, and what
    is in the inventory. Absent columns give absent fields."""
    out: dict = {}
    if "armor_value" in g.columns:
        av = (
            pd.to_numeric(g["armor_value"], errors="coerce")
            .ffill().bfill().fillna(0).astype(int).to_numpy()
        )
        hv = (
            g["has_helmet"].ffill().bfill().fillna(False).astype(bool).astype(int).to_numpy()
            if "has_helmet" in g.columns
            else np.zeros(len(av), dtype=int)
        )
        out["ar"] = _runs(
            grid, list(zip(av.tolist(), hv.tolist(), strict=True)), lambda v: [v[0], v[1]]
        )
    for col, field in (("has_defuser", "kit"), ("is_defusing", "dg")):
        if col in g.columns:
            bv = g[col].ffill().bfill().fillna(False).astype(bool).astype(int).to_numpy()
            runs = _runs(grid, bv.tolist(), lambda v: [v])
            if len(runs) > 1 or (runs and runs[0][1]):
                out[field] = runs
    for col, field in (("balance", "mo"), ("current_equip_value", "ev")):
        if col in g.columns:
            mv = (
                pd.to_numeric(g[col], errors="coerce")
                .ffill().bfill().fillna(0).astype(int).to_numpy()
            )
            out[field] = _runs(grid, mv.tolist(), lambda v: [v])
    if "inventory" in g.columns and inv_keys is not None:
        out["inv"] = _runs(
            grid,
            [inv_keys(e) for e in g["inventory"].ffill().bfill().to_numpy()],
            lambda v: [list(v)],
        )
    return out



def _weapon_changes(g, grid, weapon_of):
    """The active-weapon timeline as [(tick, weapon)] change points."""
    changes: list[list[Any]] = []
    last: Any = object()
    for tick, val in zip(
        grid, g["active_weapon_name"].ffill().bfill().to_numpy(), strict=True
    ):
        cur = None if val is None or (isinstance(val, float) and math.isnan(val)) else str(val)
        if cur != last:
            changes.append([int(tick), weapon_of(cur)])
            last = cur
    return changes


def _key_bits(g, grid, keys, alive):
    """Held keys as ONE bitmask per sample (bit i = keys[i]); None when
    the round never saw a key. Input recorded after death is a parser
    artifact and comes out 0."""
    kb = np.zeros(len(grid), dtype=np.int64)
    for bit, k in enumerate(keys):
        if k in g.columns:
            kb |= (pd.to_numeric(g[k], errors="coerce").fillna(0).to_numpy() != 0).astype(np.int64) << bit
    if not kb.any():
        return None
    if alive is not None:
        kb[~alive] = 0
    return kb.tolist()


def _player_frames(
    data, grid, keys, two_level, radar, side_of, name_of, weapon_of, inv_keys=None
):
    """
    One row per player of the round, grouped by side (CT first) and sorted
    by nickname inside a side. Positions / view angles are whole numbers;
    arrays the round does not need (flash, keys) stay absent.
    """
    import core as _core  # lazy: keeps the payload builder import-light

    players: list[dict] = []
    xall: list[float] = []
    yall: list[float] = []
    side_rank = {"CT": 0, "T": 1}

    def _order(kv):
        sid = kv[0]
        return (side_rank.get(side_of(sid), 2), name_of(sid).casefold(), sid)

    for sid, f in sorted(((str(s), fr) for s, fr in data["frames"].items()), key=_order):
        g = f.drop_duplicates("tick").set_index("tick").reindex(grid)
        wx, wy = _ffilled(g, "X"), _ffilled(g, "Y")
        if not len(wx) or np.isnan(wx).all():
            continue
        side = side_of(sid)
        kd = (data.get("kd0") or {}).get(sid) or (0, 0)
        p: dict = {
            "sid": sid,
            "name": name_of(sid),
            "side": side if side in ("CT", "T") else "?",
            "kd": [int(kd[0]), int(kd[1])],  # match kills / deaths before the round
            "x": _ri(wx),
            "y": _ri(wy),
            "yaw": _ri(_ffilled(g, "yaw")),
        }
        if "Z" in g.columns and two_level:
            p["lvl"] = _core.on_lower_level(_ffilled(g, "Z"), dict(radar)).astype(int).tolist()
        if "health" in g.columns:
            hp = pd.to_numeric(g["health"], errors="coerce").ffill().bfill().fillna(-1)
            p["hp"] = hp.clip(-1, 100).astype(int).tolist()
        alive = None
        if "is_alive" in g.columns:
            alive = g["is_alive"].astype(float).ffill().fillna(1.0).astype(bool).to_numpy()
            p["al"] = alive.astype(int).tolist()
        if "flash_duration" in g.columns:
            fl = pd.to_numeric(g["flash_duration"], errors="coerce").ffill().fillna(0.0).to_numpy()
            if (fl > 0.2).any():  # most players are never flashed: no array
                p["fl"] = [_r1(v) for v in fl]
        if "active_weapon_name" in g.columns:
            p["w"] = _weapon_changes(g, grid, weapon_of)
        # the economy: armor (+ helmet), kit, money, gear value, inventory
        p.update(_economy_fields(g, grid, inv_keys))
        kb = _key_bits(g, grid, keys, alive)
        if kb is not None:
            p["kb"] = kb
        players.append(p)
        xall += wx[~np.isnan(wx)].tolist()
        yall += wy[~np.isnan(wy)].tolist()
    return players, xall, yall


def _duel_rows(duels, data, pi, name_of, weapon_of, t0, t1, step, tr):
    """Every duel of the round as a JSON row: the window, the kill tick,
    both players, the modifiers, the numbers at the first shots, and the
    telemetry series when both frames exist."""
    import core as _core  # lazy: keeps the payload builder import-light

    du: list[dict] = []
    for x in duels:
        a = max(t0, min(int(x.get("a", t0)), t1))
        b = max(a, min(int(x.get("b", t1)), t1))
        k, v = str(x.get("killer") or ""), str(x.get("victim") or "")
        z: dict = {
            "a": a,
            "b": b,
            "tk": max(a, min(int(x.get("tick", a)), b)),
            "ki": pi(k),
            "vi": pi(v),
            "kn": name_of(k),
            "vn": name_of(v),
            "w": weapon_of(x.get("weapon") or ""),
            "hs": bool(x.get("hs")),
            "ns": bool(x.get("ns")),
            "sm": bool(x.get("sm")),
            "bl": bool(x.get("bl")),
            "wb": bool(x.get("wb")),
            "dur": _r1(x.get("dur")),
            "dist": x.get("dist"),
            "first": str(x.get("first") or ""),
            "kft": x.get("kft"),
            "vft": x.get("vft"),
            "ksp": x.get("ksp"),
            "vsp": x.get("vsp"),
            "ker": x.get("kerr"),
            "ver": x.get("verr"),
            "ksh": x.get("kshots"),
            "vsh": x.get("vshots"),
        }
        fk, fv = data["frames"].get(k), data["frames"].get(v)
        if fk is not None and fv is not None and z["ki"] >= 0 and z["vi"] >= 0:
            tel = _core.duel_telemetry(fk, fv, (a, b), t0, step, tr)
            if tel:
                z["tel"] = {"i0": tel["i0"], "k": tel["a"], "v": tel["b"], "d": tel["d"]}
        du.append(z)
    du.sort(key=lambda z: z["tk"])
    return du


def _round_bar(rounds, data):
    """The whole-match round bar rows + how THIS round ended (the demo's
    own round_end; a winner the bar knows from the elimination guess fills
    the gap)."""
    import core as _core  # lazy: keeps the payload builder import-light
    rd = [
        [
            int(r.get("n", 0) or 0),
            str(r.get("win") or "") if str(r.get("win") or "") in ("CT", "T") else "",
            str(r.get("why") or "") if str(r.get("why") or "") in _core.ROUND_REASONS else "",
        ]
        for r in rounds
    ]
    # how THIS round ended: the demo's own round_end (winner + reason); a
    # winner the rounds bar knows from the elimination guess fills the gap
    info = dict(data.get("info") or {})
    me_n = int(data.get("round") or 0)
    win = str(info.get("winner") or "")
    if win not in ("CT", "T"):
        win = next((w for n, w, _ in rd if n == me_n and w), "")
    why = str(info.get("reason") or "")
    end_tick = info.get("end")
    out = {
        "win": win,
        "why": why if why in _core.ROUND_REASONS else "",
        "end": int(end_tick) if end_tick is not None else None,
        "inferred": bool(info.get("inferred")),  # read from the kills / the bomb
    }
    return rd, out


def _bomb_rows(bomb_src, pi, t0, t1):
    """The round's bomb story rows [(tick, kind, player index)]."""
    return [
        [int(tk), str(kind), pi(sid)]
        for tk, kind, sid in bomb_src
        if t0 <= int(tk) <= t1
    ]


def _register_badges(bomb, rd, out, kills, du, players, badge):
    """Register the status icons this round needs: the bomb story, the
    round results, headshots, deaths (the skull in the player list) and
    the clock."""
    # headshots, deaths (the skull in the player list) and the clock
    for _kind in {str(kd) for _, kd, _ in bomb}:
        badge({"plant": "bomb", "defuse": "defuse", "boom": "bomb_explode"}.get(_kind, ""))
    for _why in {why for _, _, why in rd} | ({why} if (why := out["why"]) else set()):
        badge(
            {
                "bomb": "bomb_explode",
                "defuse": "defuse",
                "elim": "skull",
                "time": "hourglass",
            }.get(_why, "")
        )
    if any(k[4] for k in kills) or any(z.get("hs") for z in du):
        badge("headshot")
    if kills or any(p.get("al") and 0 in p["al"] for p in players):
        badge("skull")
    badge("timer")
    for z in du:  # the kill modifiers, when the demo carries them
        if z["ns"]:
            badge("noscope")
        if z["sm"]:
            badge("smoke_kill")
        if z["bl"]:
            badge("flashed_kill")
        if z["wb"]:
            badge("wallbang")

def _register_economy_icons(players, weapon_info, badge, icons_out, wlab):
    """The icons of the economy: every inventory item of the round, the
    armor states and the status marks on the map (the bomb, the kit,
    flashed)."""
    econ_keys: set[str] = set()
    for p in players:
        for run in p.get("inv") or ():
            econ_keys.update(k for k in (run[1] or ()) if k)
    for k in sorted(econ_keys):
        badge(k)
        if weapon_info is not None:
            info = weapon_info(k)
            if info.get("key") and info.get("uri"):
                icons_out.setdefault(info["key"], [info["uri"], info["w"], info["h"]])
            if info.get("label"):
                wlab.setdefault(k, info["label"])
    if any("ar" in p for p in players):
        for k in ("armor_none", "armor_half", "armor_full", "armor_half_helmet", "armor_helmet"):
            badge(k)
    if any("inv" in p for p in players):
        for k in ("c4", "defuse", "blind"):
            badge(k)


def _radar_payload(radar):
    """The radar image refs + the world->radar calibration."""
    from mapview import radar_uri

    return {
        "png": radar_uri(radar["png"]),
        "lo": radar_uri(radar["lower_png"]) if radar.get("lower_png") else None,
        "size": int(radar["size"]),
        "pos_x": float(radar["pos_x"]),
        "pos_y": float(radar["pos_y"]),
        "scale": float(radar["scale"]),
    }


def _grid_bounds(xall, yall):
    """No radar: a plain unit grid around the players, padded by half
    SAFE_DIST."""
    import core as _core

    m = _core.SAFE_DIST / 2.0
    return {
        "minx": round(min(xall) - m, 1),
        "miny": round(min(yall) - m, 1),
        "maxx": round(max(xall) + m, 1),
        "maxy": round(max(yall) + m, 1),
    }


def _weapon_registry(weapon_info, icons_out, wlab):
    """The _weapon() closure every section shares: the short weapon name
    for the payload, registering its icon + localized label on the way."""

    def _weapon(name: Any) -> str:
        """The weapon's icon KEY (the short name when no icon exists). Keys,
        not the demo's spellings, are what the front end draws, so aliases
        (USP-S, M4A1-S, ...) land on their icon too."""
        short = _wshort(name)
        ret = short
        if weapon_info is not None and short:
            info = weapon_info(short)
            if info.get("key"):
                ret = info["key"]
                if info.get("uri"):
                    icons_out.setdefault(ret, [info["uri"], info["w"], info["h"]])
            if info.get("label"):
                wlab[ret] = info["label"]
        return ret

    return _weapon


def _event_rows(data, pi, weapon_of):
    """The round's shots / kills / hits as JSON rows with player indexes."""
    shots = [[int(t), pi(s), str(w)] for t, s, w in (data.get("shots") or [])]
    kills = [
        [int(t), pi(a), pi(v), weapon_of(w), bool(hs)]
        for t, a, v, w, hs in (data.get("kills") or [])
    ]
    hits = [[int(t), pi(a), pi(v)] for t, a, v in (data.get("hits") or [])]
    return shots, kills, hits


def _sel_and_at(sel, at, du, t0, t1):
    """The preselected duel index (-1 = none) and the start tick: a duel
    selection without an explicit start locks the timeline to its window."""
    try:
        sel_i = int(sel) if sel is not None else -1
    except (TypeError, ValueError):
        sel_i = -1
    if not (0 <= sel_i < len(du)):
        sel_i = -1
    at_t = t0 if at is None else int(at)
    if sel_i >= 0 and at is None:
        at_t = du[sel_i]["a"]
    return sel_i, max(t0, min(at_t, t1))


def _name_of(names: Mapping[str, str], sid: str) -> str:
    """A player's display name (the sid itself when unknown)."""
    return str(names.get(sid, sid))


def _badge_of(icon_of, icons_out: dict, key: str) -> None:
    """Register a status icon (bomb, headshot, ...) when it exists."""
    if icon_of is None:
        return
    v = icon_of(key)
    if v:
        icons_out.setdefault(key, list(v))


def _pi_of(idx: dict, sid: Any) -> int:
    """A sid's index into the payload players list (-1 when unknown)."""
    return idx.get(str(sid), -1)


class _RoundModel(NamedTuple):
    """The replay grid's data sections (round_payload's first half)."""

    t0: int
    t1: int
    step: int
    tr: int
    keys: list
    players: list
    xall: Any
    yall: Any
    shots: list
    kills: list
    hits: list
    duels: list


def _round_model(
    data: Mapping[str, Any],
    radar: Mapping[str, Any] | None,
    side_of: Callable[[str], str],
    names: Mapping[str, str],
    weapon_info,
    duels: Sequence[Mapping[str, Any]],
    icons_out: dict,
    wlab: dict,
) -> _RoundModel:
    """Grid, per-player frames, events and duel rows of the round -
    everything keyed by the replay grid (fills icons_out/wlab on the way)."""
    t0, t1 = int(data["t0"]), int(data["t1"])
    step, tr = max(int(data["step"]), 1), int(data["tickrate"])
    grid = np.arange(t0, t1 + 1, step)
    keys = list(data.get("keys") or ())
    two_level = bool(radar and radar.get("lower"))

    _weapon = _weapon_registry(weapon_info, icons_out, wlab)
    _name = partial(_name_of, names)
    players, xall, yall = _player_frames(
        data, grid, keys, two_level, radar, side_of, _name, _weapon,
        partial(_inv_keys, weapon_info),
    )

    _pi = partial(_pi_of, {p["sid"]: i for i, p in enumerate(players)})
    shots, kills, hits = _event_rows(data, _pi, _weapon)
    du = _duel_rows(duels, data, _pi, _name, _weapon, t0, t1, step, tr)
    return _RoundModel(
        t0, t1, step, tr, keys, players, xall, yall, shots, kills, hits, du
    )


def round_payload(
    data: Mapping[str, Any],
    *,
    names: Mapping[str, str],
    side_of: Callable[[str], str],
    radar: Mapping[str, Any] | None = None,
    duels: Sequence[Mapping[str, Any]] = (),
    rounds: Sequence[Mapping[str, Any]] = (),
    bombs: Sequence[Sequence[Any]] | None = None,
    thr: float | None = None,
    at: int | None = None,
    sel: int | None = None,
    labels: Mapping[str, str] | None = None,
    colors: Mapping[str, str] | None = None,
    weapon_info: Callable[[str], dict] | None = None,
    icon_of: Callable[[str], Sequence | None] | None = None,
) -> dict:
    """
    JSON-ready model of the 2D replay (pure function: unit-testable).

    The full contract lives in this module's docstring ("The payload"
    section); in short: ``data`` is core.replay.round_replay_data's dict,
    ``names``/``side_of`` resolve players, ``radar`` is core.load_radar's
    dict (or None), ``duels`` are core.round_duels' entries, ``rounds`` is
    the whole match. ``weapon_info``/``icon_of`` (ui.icons) add the
    ``icons``/``wlab`` bundles; without them the front end shows plain
    text names, exactly like before. ``thr`` is the "moving" speed
    threshold (u/s).
    """
    icons_out: dict[str, list] = {}
    wlab: dict[str, str] = {}
    m = _round_model(data, radar, side_of, names, weapon_info, duels, icons_out, wlab)

    _pi = partial(_pi_of, {p["sid"]: i for i, p in enumerate(m.players)})
    _badge = partial(_badge_of, icon_of, icons_out)
    sel_i, at_t = _sel_and_at(sel, at, m.duels, m.t0, m.t1)

    rd, out = _round_bar(rounds, data)
    bomb_src = bombs if bombs is not None else ((data.get("info") or {}).get("bomb") or [])
    bomb = _bomb_rows(bomb_src, _pi, m.t0, m.t1)
    _register_badges(bomb, rd, out, m.kills, m.duels, m.players, _badge)
    _register_economy_icons(m.players, weapon_info, _badge, icons_out, wlab)

    rp = _radar_payload(radar) if radar else None
    gb = _grid_bounds(m.xall, m.yall) if rp is None and m.xall else None

    return {
        "t0": m.t0,
        "t1": m.t1,
        "start": int(data.get("start", m.t0)),
        "step": m.step,
        "tr": m.tr,
        "round": int(data.get("round") or 0),
        "players": m.players,
        "shots": m.shots,
        "kills": m.kills,
        "hits": m.hits,
        "duels": m.duels,
        "rounds": rd,
        "out": out,
        "bomb": bomb,
        "thr": None if thr is None else float(thr),
        "sel": sel_i,
        "at": at_t,
        "radar": rp,
        "gridb": gb,
        "keys": m.keys,
        "lab": {k: str(v or "") for k, v in (labels or {}).items()},
        "col": {k: str(v or "") for k, v in (colors or {}).items()},
        "icons": icons_out,
        "wlab": wlab,
    }


def round_replay(
    payload: dict,
    *,
    key: str,
    on_round: Callable[[int], None] | None = None,
) -> None:
    """
    Render the 2D replay player (client-side playback, no reruns).
    A round-bar chip click calls ``on_round(n)`` - the ONE Streamlit
    trigger the component has (switching rounds needs a new payload).
    """
    component.render(
        "aimaudit_roundrep2", _CSS, _JS, key=key, data=payload,
        trigger="rnd", handler=on_round,
    )
