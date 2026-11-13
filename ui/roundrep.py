"""
Round replay: a canvas mini-map of the WHOLE round, not just two duelists.

    round_replay(payload, *, key)          # render (ui/roundrep.py)
    round_payload(data, ...)               # pure JSON model, unit-tested

Layout (everything client-side, like the in-game spectator HUD):

- LEFT: every player of both teams - the weapon in hands, the HP number -
  as a hideable panel (the toggle sits in the control row);
- CENTER: the radar canvas (wheel = zoom, LMB drag = pan, double click or
  the button = reset) with every player drawn - a team-coloured circle
  with the HP inside, an ARROW for the view direction, the name above and
  the current weapon below; dead players become grey crosses, flashed
  ones get a white halo; shots are fading tracers, hits stars, kills
  crosses + a kill feed on the side, so trades are readable;
- the TIMELINE under the map: kill marks coloured by the KILLER'S side
  (CT blue, T yellow), the selected duel's segment highlighted, the
  playhead draggable. While a duel is selected the playhead is LOCKED to
  its segment - exit first (re-click the card / the exit button) or pick
  another duel;
- the key panels of BOTH duelists appear only inside a duel segment;
- RIGHT: one card per duel of yours in this round - future duels are
  semi-transparent, past ones are solid, the selected one gets the
  outline; a click locks the timeline to that duel's segment;
- BELOW the timeline: the rounds of the whole match, their background
  coloured by the winning side (core.round_winners) - a click switches
  the replay to that round (a Streamlit trigger, one fragment rerun).

Python builds the whole payload (round_payload is pure and unit-tested);
the JS only interpolates, draws and animates - playback runs entirely in
the browser on requestAnimationFrame, no Streamlit reruns while playing.
Colours come from theme tokens through the payload, CSS vars only for the
chrome, like ui/table.py and ui/strip.py.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
import streamlit as st

from ui.roundrep_assets import _CSS, _JS


def _r1(v) -> float:
    """Round to 1 decimal (JSON size); NaN -> None so JSON stays valid."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, 1)


def _wshort(v) -> str:
    """active_weapon_name -> the short form ('weapon_ak47' -> 'ak47')."""
    s = str(v or "")
    return s[7:] if s.startswith("weapon_") else s


def round_payload(
    data: Mapping[str, Any],
    *,
    me: str,
    names: Mapping[str, str],
    side_of: Callable[[str], str],
    radar: Mapping[str, Any] | None = None,
    duels: Sequence[Mapping[str, Any]] = (),
    rounds: Sequence[Mapping[str, Any]] = (),
    at: int | None = None,
    sel: int | None = None,
    key_players: Sequence[tuple[str, str]] = (),
    labels: Mapping[str, str] | None = None,
    colors: Mapping[str, str] | None = None,
    max_samples: int = 3200,
) -> dict:
    """
    JSON-ready model of the round replay (pure function: unit-testable).

    ``data`` is core.round_replay_data's dict; ``names``/``side_of``
    resolve players; ``radar`` is core.load_radar's dict (or None: a plain
    unit grid is drawn); ``duels`` are the selected player's duels in this
    round - {"a", "b"} bracket the segment the timeline locks to, "tick"
    is the resolution, "result"/"opp"/"opp_sid"/"weapon"/"hs"/"dur" feed
    the card; ``rounds`` is the whole match [{"n", "win"}] for the round
    bar (win 'CT'/'T'/'' from core.round_winners); ``at`` is the tick to
    start at; ``sel`` preselects that duel (locks the timeline);
    ``key_players`` are (sid, caption) pairs whose held keys get a panel
    inside a duel segment.
    """
    t0, t1 = int(data["t0"]), int(data["t1"])
    step, tr = int(data["step"]), int(data["tickrate"])
    grid = np.arange(t0, t1 + 1, step)
    keys = list(data.get("keys") or ())
    key_sids = {str(s) for s, _ in key_players}
    two_level = bool(radar and radar.get("lower"))
    my_side = side_of(me)

    players: list[dict] = []
    xall, yall = [], []
    for sid, f in data["frames"].items():
        sid = str(sid)
        g = (
            f.drop_duplicates("tick")
            .set_index("tick")
            .reindex(grid)
        )
        wx = pd.to_numeric(g["X"], errors="coerce").ffill().bfill()
        wy = pd.to_numeric(g["Y"], errors="coerce").ffill().bfill()
        if not len(wx) or wx.isna().all():
            continue
        side = side_of(sid)
        role = "me" if sid == me else ("tm" if side == my_side else "en")
        p: dict = {
            "sid": sid,
            "name": str(names.get(sid, sid)),
            "role": role,
            "side": side if side in ("CT", "T") else "?",
            "x": [_r1(v) for v in wx.to_numpy()],
            "y": [_r1(v) for v in wy.to_numpy()],
            "yaw": [
                _r1(v)
                for v in pd.to_numeric(g["yaw"], errors="coerce")
                .ffill()
                .bfill()
                .to_numpy()
            ],
        }
        if "Z" in g.columns and two_level:
            import core as _core

            z = pd.to_numeric(g["Z"], errors="coerce").ffill().bfill().to_numpy()
            p["lvl"] = (
                _core.on_lower_level(z, dict(radar)).astype(int).tolist()
            )
        if "health" in g.columns:
            hp = (
                pd.to_numeric(g["health"], errors="coerce")
                .ffill()
                .bfill()
                .fillna(-1)
                .clip(-1, 100)
                .astype(int)
                .to_numpy()
            )
            p["hp"] = hp.tolist()
        if "is_alive" in g.columns:
            p["al"] = (
                g["is_alive"].astype(float).ffill().fillna(1.0).astype(bool)
                .astype(int)
                .tolist()
            )
        if "flash_duration" in g.columns:
            p["fl"] = [
                _r1(v)
                for v in pd.to_numeric(g["flash_duration"], errors="coerce")
                .fillna(0.0)
                .to_numpy()
            ]
        if "active_weapon_name" in g.columns:
            w = g["active_weapon_name"].ffill().bfill()
            changes: list[list[Any]] = []
            last = None
            for tick, val in zip(grid, w.to_numpy()):
                cur = None if val is None or (isinstance(val, float) and math.isnan(val)) else str(val)
                if cur != last:
                    changes.append([int(tick), _wshort(cur)])
                    last = cur
            p["w"] = changes
        if sid in key_sids:
            p["keys"] = {
                k: (
                    pd.to_numeric(g[k], errors="coerce").fillna(0).to_numpy() != 0
                ).astype(int).tolist()
                for k in keys
                if k in g.columns
            }
        players.append(p)
        xall += [v for v in wx.dropna().tolist()]
        yall += [v for v in wy.dropna().tolist()]

    idx = {p["sid"]: i for i, p in enumerate(players)}

    def _pi(sid: Any) -> int:
        return idx.get(str(sid), -1)

    shots = [
        [int(t), _pi(s), str(w)] for t, s, w in (data.get("shots") or [])
    ]
    kills = [
        [int(t), _pi(a), _pi(v), str(w), bool(hs)]
        for t, a, v, w, hs in (data.get("kills") or [])
    ]
    hits = [[int(t), _pi(a), _pi(v)] for t, a, v in (data.get("hits") or [])]

    # my duels of this round: cards on the right + timeline segments
    du: list[dict] = []
    for x in duels:
        a = int(x.get("a", t0) or t0)
        b = int(x.get("b", t1) or t1)
        a = max(t0, min(a, t1))
        b = max(a, min(b, t1))
        du.append(
            {
                "a": a,
                "b": b,
                "tk": max(a, min(int(x.get("tick", a) or a), b)),
                "res": "kill" if str(x.get("result")) == "kill" else "death",
                "opp": str(x.get("opp") or ""),
                "oi": _pi(x.get("opp_sid")),
                "w": str(x.get("weapon") or ""),
                "hs": bool(x.get("hs")),
                "dur": _r1(x.get("dur")),
            }
        )
    du.sort(key=lambda z: z["tk"])
    marks = [[z["tk"], z["res"]] for z in du]

    n_du = len(du)
    try:
        sel_i = int(sel) if sel is not None else -1
    except (TypeError, ValueError):
        sel_i = -1
    if not (0 <= sel_i < n_du):
        sel_i = -1
    at_t = t0 if at is None else int(at)
    if sel_i >= 0 and at is None:
        at_t = du[sel_i]["a"]
    at_t = max(t0, min(at_t, t1))

    rd = [
        [
            int(r.get("n", 0) or 0),
            str(r.get("win") or "") if str(r.get("win") or "") in ("CT", "T") else "",
        ]
        for r in rounds
    ]

    lab = {k: str(v or "") for k, v in (labels or {}).items()}
    col = {k: str(v or "") for k, v in (colors or {}).items()}

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
        import core as _core

        x0, x1 = min(xall), max(xall)
        y0, y1 = min(yall), max(yall)
        m = _core.SAFE_DIST / 2.0
        gb = {
            "minx": round(x0 - m, 1),
            "miny": round(y0 - m, 1),
            "maxx": round(x1 + m, 1),
            "maxy": round(y1 + m, 1),
        }

    return {
        "t0": t0,
        "t1": t1,
        "step": step,
        "tr": tr,
        "round": int(data.get("round") or 0),
        "players": players,
        "shots": shots,
        "kills": kills,
        "hits": hits,
        "marks": marks,
        "duels": du,
        "rounds": rd,
        "sel": sel_i,
        "at": at_t,
        "myIdx": _pi(me),
        "radar": rp,
        "gridb": gb,
        "keys": keys,
        "kp": [[str(s), str(c)] for s, c in key_players],
        "lab": lab,
        "col": col,
    }


def _renderer():
    # registered on every run: the registry lives in the Streamlit
    # runtime (AppTest and server restarts start with an empty one), and
    # re-registering an identical definition is cheap and idempotent
    return st.components.v2.component("aimaudit_roundrep", css=_CSS, js=_JS)


def round_replay(
    payload: dict,
    *,
    key: str,
    on_round: Callable[[int], None] | None = None,
) -> None:
    """
    Render the round replay player (client-side playback, no reruns).
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


