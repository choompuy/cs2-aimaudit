"""
2D replay: a canvas mini-map of the WHOLE round - every player, every duel.

    round_replay(payload, *, key, on_round)   # render (this module)
    round_payload(data, ...)                  # pure JSON model, unit-tested

There is no "selected player" here: a replay shows everyone the same way.
Layout (everything client-side, like the in-game spectator HUD):

- the ROUND RESULT above the map: who won and HOW (bomb exploded, bomb
  defused, everybody eliminated, time ran out) from the demo's own
  round_end event, with the bomb plant / defuse as markers on the timeline;
- LEFT: the players of both teams in ONE list sorted by nickname, so every
  player keeps his place from round to round (sides swap at halftime, the
  colour dot follows them). The weapon in hands, the HP, the live speed, and
  an eye button that hides a player from the map (the choice survives round
  switches);
- CENTER: the radar canvas (wheel = zoom, LMB drag = pan, double click or
  the button = reset): a small team-coloured dot per player (CT / T), a
  view-direction arrow, the name, an HP bar and the weapon; a dashed ring
  marks a player moving faster than the stop threshold; dead players become
  grey crosses, flashed ones get a white halo; shots are fading tracers,
  hits stars, kills crosses + a kill feed on the side;
- the TIMELINE under the map: kill marks coloured by the KILLER'S side, the
  bomb events, the selected duel's segment highlighted, a draggable
  playhead. While a duel is selected the playhead is LOCKED to its segment;
- the DUEL PANEL of the selected duel: for BOTH duelists the speed, the
  crosshair turn rate and the angle to the enemy's head live at the
  playhead, a small chart of those over the whole duel with the shots, the
  numbers measured at the first shot, and the keys they held;
- RIGHT: one card per duel of the round - EVERY duel of every player, not
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
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
import streamlit as st

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


def round_payload(
    data: Mapping[str, Any],
    *,
    names: Mapping[str, str],
    side_of: Callable[[str], str],
    radar: Mapping[str, Any] | None = None,
    duels: Sequence[Mapping[str, Any]] = (),
    rounds: Sequence[Mapping[str, Any]] = (),
    bombs: Sequence[Sequence[Any]] | None = None,
    at: int | None = None,
    sel: int | None = None,
    labels: Mapping[str, str] | None = None,
    colors: Mapping[str, str] | None = None,
) -> dict:
    """
    JSON-ready model of the 2D replay (pure function: unit-testable).

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

    Players come out grouped by side (CT first, then T) and sorted by
    nickname inside a side, so a player keeps his place in his team's list;
    positions and view angles are whole numbers; held keys are ONE bitmask
    per sample (bit i = ``keys[i]``), so every player's keys cost a single
    array. Every duel carries ``tel``: both duelists' speed / turn rate /
    crosshair-to-head angle and the distance per replay sample inside the
    duel's segment (core.duel_telemetry).
    """
    import core as _core  # lazy: keeps the payload builder import-light

    t0, t1 = int(data["t0"]), int(data["t1"])
    step, tr = max(int(data["step"]), 1), int(data["tickrate"])
    grid = np.arange(t0, t1 + 1, step)
    keys = list(data.get("keys") or ())
    two_level = bool(radar and radar.get("lower"))

    def _name(sid: str) -> str:
        return str(names.get(sid, sid))

    players: list[dict] = []
    xall: list[float] = []
    yall: list[float] = []
    side_rank = {"CT": 0, "T": 1}

    def _order(kv):
        sid = kv[0]
        return (side_rank.get(side_of(sid), 2), _name(sid).casefold(), sid)

    for sid, f in sorted(((str(s), fr) for s, fr in data["frames"].items()), key=_order):
        g = f.drop_duplicates("tick").set_index("tick").reindex(grid)
        wx, wy = _ffilled(g, "X"), _ffilled(g, "Y")
        if not len(wx) or np.isnan(wx).all():
            continue
        side = side_of(sid)
        p: dict = {
            "sid": sid,
            "name": _name(sid),
            "side": side if side in ("CT", "T") else "?",
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
            fl = pd.to_numeric(g["flash_duration"], errors="coerce").fillna(0.0).to_numpy()
            if (fl > 0.2).any():  # most players are never flashed: no array
                p["fl"] = [_r1(v) for v in fl]
        if "active_weapon_name" in g.columns:
            changes: list[list[Any]] = []
            last: Any = object()
            for tick, val in zip(grid, g["active_weapon_name"].ffill().bfill().to_numpy()):
                cur = None if val is None or (isinstance(val, float) and math.isnan(val)) else str(val)
                if cur != last:
                    changes.append([int(tick), _wshort(cur)])
                    last = cur
            p["w"] = changes
        kb = np.zeros(len(grid), dtype=np.int64)
        for bit, k in enumerate(keys):
            if k in g.columns:
                kb |= (pd.to_numeric(g[k], errors="coerce").fillna(0).to_numpy() != 0).astype(np.int64) << bit
        if kb.any():
            if alive is not None:
                kb[~alive] = 0  # input recorded after death is a parser artifact
            p["kb"] = kb.tolist()
        players.append(p)
        xall += wx[~np.isnan(wx)].tolist()
        yall += wy[~np.isnan(wy)].tolist()

    idx = {p["sid"]: i for i, p in enumerate(players)}

    def _pi(sid: Any) -> int:
        return idx.get(str(sid), -1)

    shots = [[int(t), _pi(s), str(w)] for t, s, w in (data.get("shots") or [])]
    kills = [
        [int(t), _pi(a), _pi(v), str(w), bool(hs)]
        for t, a, v, w, hs in (data.get("kills") or [])
    ]
    hits = [[int(t), _pi(a), _pi(v)] for t, a, v in (data.get("hits") or [])]

    du: list[dict] = []
    for x in duels:
        a = max(t0, min(int(x.get("a", t0)), t1))
        b = max(a, min(int(x.get("b", t1)), t1))
        k, v = str(x.get("killer") or ""), str(x.get("victim") or "")
        z: dict = {
            "a": a,
            "b": b,
            "tk": max(a, min(int(x.get("tick", a)), b)),
            "ki": _pi(k),
            "vi": _pi(v),
            "kn": _name(k),
            "vn": _name(v),
            "w": str(x.get("weapon") or ""),
            "hs": bool(x.get("hs")),
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

    try:
        sel_i = int(sel) if sel is not None else -1
    except (TypeError, ValueError):
        sel_i = -1
    if not (0 <= sel_i < len(du)):
        sel_i = -1
    at_t = t0 if at is None else int(at)
    if sel_i >= 0 and at is None:
        at_t = du[sel_i]["a"]
    at_t = max(t0, min(at_t, t1))

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
    bomb_src = bombs if bombs is not None else (info.get("bomb") or [])
    bomb = [
        [int(tk), str(kind), _pi(sid)]
        for tk, kind, sid in bomb_src
        if t0 <= int(tk) <= t1
    ]

    rp: dict | None = None
    if radar:
        from mapview import radar_uri

        rp = {
            "png": radar_uri(radar["png"]),
            "lo": radar_uri(radar["lower_png"]) if radar.get("lower_png") else None,
            "size": int(radar["size"]),
            "pos_x": float(radar["pos_x"]),
            "pos_y": float(radar["pos_y"]),
            "scale": float(radar["scale"]),
        }
    gb: dict | None = None
    if rp is None and xall:
        m = _core.SAFE_DIST / 2.0
        gb = {
            "minx": round(min(xall) - m, 1),
            "miny": round(min(yall) - m, 1),
            "maxx": round(max(xall) + m, 1),
            "maxy": round(max(yall) + m, 1),
        }

    return {
        "t0": t0,
        "t1": t1,
        "step": step,
        "tr": tr,
        "round": me_n,
        "players": players,
        "shots": shots,
        "kills": kills,
        "hits": hits,
        "duels": du,
        "rounds": rd,
        "out": out,
        "bomb": bomb,
        "sel": sel_i,
        "at": at_t,
        "radar": rp,
        "gridb": gb,
        "keys": keys,
        "lab": {k: str(v or "") for k, v in (labels or {}).items()},
        "col": {k: str(v or "") for k, v in (colors or {}).items()},
    }


def _renderer():
    # registered on every run on purpose: the registry lives in the Streamlit
    # runtime (AppTest and server restarts start with an empty one), and
    # re-registering an identical definition is cheap and idempotent
    return st.components.v2.component("aimaudit_roundrep2", css=_CSS, js=_JS)


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
    kwargs: dict = {}
    if on_round is not None:

        def _cb() -> None:
            state = st.session_state.get(key)
            n = state.get("rnd") if hasattr(state, "get") else None
            if n is not None:
                on_round(int(n))

        kwargs["on_rnd_change"] = _cb
    _renderer()(key=key, data=payload, **kwargs)
