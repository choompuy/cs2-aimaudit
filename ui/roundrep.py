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
from functools import partial
from typing import Any, Callable, Mapping, NamedTuple, Sequence

import numpy as np
import pandas as pd

from ui import component, icons


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


def _weapon_key(name: Any) -> str:
    """A demo weapon / item name -> its key in ``icons`` / ``wlab``."""
    return icons.weapon_key(name) or icons.weapon_id(name)


def _inv_keys(entry) -> tuple:
    """One inventory snapshot -> icon keys, duplicates kept: two
    flashbangs are two."""
    if entry is None or (isinstance(entry, float) and math.isnan(entry)):
        return ()
    try:
        items = list(entry)
    except TypeError:
        return ()
    return tuple(k for k in map(_weapon_key, items) if k)


def _economy_fields(g, grid) -> dict:
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
    if "inventory" in g.columns:
        out["inv"] = _runs(
            grid,
            [_inv_keys(e) for e in g["inventory"].ffill().bfill().to_numpy()],
            lambda v: [list(v)],
        )
    return out



def _weapon_changes(g, grid):
    """The active-weapon timeline as [(tick, weapon)] change points."""
    changes: list[list[Any]] = []
    last: Any = object()
    for tick, val in zip(
        grid, g["active_weapon_name"].ffill().bfill().to_numpy(), strict=True
    ):
        cur = None if val is None or (isinstance(val, float) and math.isnan(val)) else str(val)
        if cur != last:
            changes.append([int(tick), _weapon_key(cur)])
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


def _player_frames(data, grid, keys, two_level, radar, side_of, name_of):
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
            p["w"] = _weapon_changes(g, grid)
        # the economy: armor (+ helmet), kit, money, gear value, inventory
        p.update(_economy_fields(g, grid))
        kb = _key_bits(g, grid, keys, alive)
        if kb is not None:
            p["kb"] = kb
        players.append(p)
        xall += wx[~np.isnan(wx)].tolist()
        yall += wy[~np.isnan(wy)].tolist()
    return players, xall, yall


def _duel_rows(duels, data, pi, name_of, t0, t1, step, tr):
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
            "w": _weapon_key(x.get("weapon")),
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


def _event_rows(data, pi):
    """The round's shots / kills / hits as JSON rows with player indexes."""
    shots = [[int(t), pi(s), str(w)] for t, s, w in (data.get("shots") or [])]
    kills = [
        [int(t), pi(a), pi(v), _weapon_key(w), bool(hs)]
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
    duels: Sequence[Mapping[str, Any]],
) -> _RoundModel:
    """Grid, per-player frames, events and duel rows of the round -
    everything keyed by the replay grid."""
    t0, t1 = int(data["t0"]), int(data["t1"])
    step, tr = max(int(data["step"]), 1), int(data["tickrate"])
    grid = np.arange(t0, t1 + 1, step)
    keys = list(data.get("keys") or ())
    two_level = bool(radar and radar.get("lower"))

    _name = partial(_name_of, names)
    players, xall, yall = _player_frames(
        data, grid, keys, two_level, radar, side_of, _name
    )

    _pi = partial(_pi_of, {p["sid"]: i for i, p in enumerate(players)})
    shots, kills, hits = _event_rows(data, _pi)
    du = _duel_rows(duels, data, _pi, _name, t0, t1, step, tr)
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
) -> dict:
    """
    JSON-ready model of the 2D replay (pure function: unit-testable).

    The full contract lives in this module's docstring ("The payload"
    section); in short: ``data`` is core.replay.round_replay_data's dict,
    ``names``/``side_of`` resolve players, ``radar`` is core.load_radar's
    dict (or None), ``duels`` are core.round_duels' entries, ``rounds`` is
    the whole match. ``thr`` is the "moving" speed threshold (u/s).
    ``icons`` / ``wlab`` are the whole installed icon set and every weapon
    label; without the set the front end shows plain text names.
    """
    m = _round_model(data, radar, side_of, names, duels)

    _pi = partial(_pi_of, {p["sid"]: i for i, p in enumerate(m.players)})
    sel_i, at_t = _sel_and_at(sel, at, m.duels, m.t0, m.t1)

    rd, out = _round_bar(rounds, data)
    bomb_src = bombs if bombs is not None else ((data.get("info") or {}).get("bomb") or [])
    bomb = _bomb_rows(bomb_src, _pi, m.t0, m.t1)

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
        "icons": icons.bundle(),
        "wlab": icons.weapon_labels(),
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
        "roundrep", key=key, data=payload,
        trigger="rnd", handler=on_round,
    )
