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
  colour dot follows them). A row per player: the weapon in hands, the
  armor, the HP and the money; a second line with the grenades and the
  defuse kit, the live speed and the gear value; the team economy at the
  bottom. An eye button hides a player from the map (the choice survives
  round switches);
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
import re
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


def _runs(grid, values, enc) -> list[list[Any]]:
    """[[tick, *enc(v)]] at every sample where the value CHANGES: slowly
    changing state (armor, money, inventory) costs a few entries, not a
    sample per tick."""
    out: list[list[Any]] = []
    last: Any = object()
    for tick, v in zip(grid, values):
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
}


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

    ``data`` is core.replay.round_replay_data's dict (its ``info`` says how
    the round ended); ``names``/``side_of`` resolve players (``side_of(sid)``
    is the side in THIS round); ``radar`` is core.load_radar's dict (or None:
    a plain unit grid is drawn). ``duels`` are core.round_duels' entries -
    every duel of the round: {"a", "b"} bracket the segment the timeline
    locks to, "tick" is the kill, "killer"/"victim" are SteamIDs, the rest
    the numbers measured at the first shots. ``rounds`` is the whole match
    [{"n", "win", "why"}] for the round bar (win 'CT'/'T'/'', why one of
    core.ROUND_REASONS); ``bombs`` are [(tick, kind, sid)] of the round
    (default: ``data["info"]["bomb"]``); ``thr`` is the "moving" speed
    threshold (u/s); ``at`` is the tick to start at; ``sel`` preselects a
    duel (locks the timeline).

    Players come out grouped by side (CT first, then T) and sorted by
    nickname inside a side, so a player keeps his place in his team's list;
    positions and view angles are whole numbers; held keys are ONE bitmask
    per sample (bit i = ``keys[i]``), so every player's keys cost a single
    array. Every duel carries ``tel``: both duelists' speed / turn rate /
    crosshair-to-head angle and the distance per replay sample inside the
    duel's segment (core.duel_telemetry).

    ``weapon_info`` (ui.icons.weapon_info) maps a demo weapon name to its
    icon key / data URI / size / display label, ``icon_of`` resolves a
    status icon key ("bomb", "headshot", ...) to [data-uri, w, h]. With
    them the payload carries ``icons`` - ONLY the icons this round needs,
    one dict entry each - and ``wlab``, the localized weapon names for the
    tooltips. Without them (no icon set installed) both stay empty and the
    front end shows plain text names, exactly like before.
    """
    import core as _core  # lazy: keeps the payload builder import-light

    t0, t1 = int(data["t0"]), int(data["t1"])
    step, tr = max(int(data["step"]), 1), int(data["tickrate"])
    grid = np.arange(t0, t1 + 1, step)
    keys = list(data.get("keys") or ())
    two_level = bool(radar and radar.get("lower"))

    # the icon set of THIS round: {key: [data-uri, w, h]}, filled lazily by
    # _weapon() / _badge() below; wlab carries the localized weapon names
    icons_out: dict[str, list] = {}
    wlab: dict[str, str] = {}

    def _weapon(name: Any) -> str:
        """The weapon's icon KEY for the payload (the short name when no
        icon exists), registering its icon. Keys - not the demo's spellings -
        are what the front end draws, so aliases (USP-S, M4A1-S, ...) land
        on their icon too."""
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

    def _inv_key(name: Any) -> str:
        """One inventory entry -> the icon key used across the payload:
        weapon_info's resolved key when there is one, else the compacted
        demo name (with the few aliases that do not compact cleanly)."""
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

    def _inv_keys(entry) -> tuple:
        """One inventory snapshot -> icon keys, duplicates kept: two
        flashbangs are two."""
        if entry is None or (isinstance(entry, float) and math.isnan(entry)):
            return ()
        try:
            items = list(entry)
        except TypeError:
            return ()
        return tuple(k for k in (_inv_key(x) for x in items) if k)

    def _badge(key: str) -> None:
        """Register a status icon (bomb, headshot, ...) when it exists."""
        if icon_of is None:
            return
        v = icon_of(key)
        if v:
            icons_out.setdefault(key, list(v))

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
                    changes.append([int(tick), _weapon(cur)])
                    last = cur
            p["w"] = changes
        # the economy: armor (+ helmet), the defuse kit, money, the value of
        # the gear, what is in the inventory, defusing right now - as change
        # lists, so a run of identical samples costs one entry
        if "armor_value" in g.columns:
            av = (
                pd.to_numeric(g["armor_value"], errors="coerce")
                .ffill()
                .bfill()
                .fillna(0)
                .astype(int)
                .to_numpy()
            )
            hv = (
                g["has_helmet"]
                .ffill()
                .bfill()
                .fillna(False)
                .astype(bool)
                .astype(int)
                .to_numpy()
                if "has_helmet" in g.columns
                else np.zeros(len(av), dtype=int)
            )
            p["ar"] = _runs(grid, list(zip(av.tolist(), hv.tolist())), lambda v: [v[0], v[1]])
        for col, field in (("has_defuser", "kit"), ("is_defusing", "dg")):
            if col in g.columns:
                bv = g[col].ffill().bfill().fillna(False).astype(bool).astype(int).to_numpy()
                runs = _runs(grid, bv.tolist(), lambda v: [v])
                if len(runs) > 1 or (runs and runs[0][1]):
                    p[field] = runs
        for col, field in (("balance", "mo"), ("current_equip_value", "ev")):
            if col in g.columns:
                mv = (
                    pd.to_numeric(g[col], errors="coerce")
                    .ffill()
                    .bfill()
                    .fillna(0)
                    .astype(int)
                    .to_numpy()
                )
                p[field] = _runs(grid, mv.tolist(), lambda v: [v])
        if "inventory" in g.columns:
            p["inv"] = _runs(
                grid,
                [_inv_keys(e) for e in g["inventory"].ffill().bfill().to_numpy()],
                lambda v: [list(v)],
            )
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

    # the icons of the economy: every inventory item of the round, the armor
    # states and the status marks on the map (the bomb, the kit, flashed)
    econ_keys: set[str] = set()
    for p in players:
        for run in p.get("inv") or ():
            econ_keys.update(k for k in (run[1] or ()) if k)
    for k in sorted(econ_keys):
        _badge(k)
        if weapon_info is not None:
            info = weapon_info(k)
            if info.get("key") and info.get("uri"):
                icons_out.setdefault(info["key"], [info["uri"], info["w"], info["h"]])
            if info.get("label"):
                wlab.setdefault(k, info["label"])
    if any("ar" in p for p in players):
        for k in ("armor_none", "armor_half", "armor_full",
                  "armor_half_helmet", "armor_helmet"):
            _badge(k)
    if any("inv" in p for p in players):
        for k in ("c4", "defuse", "blind"):
            _badge(k)

    idx = {p["sid"]: i for i, p in enumerate(players)}

    def _pi(sid: Any) -> int:
        return idx.get(str(sid), -1)

    shots = [[int(t), _pi(s), str(w)] for t, s, w in (data.get("shots") or [])]
    kills = [
        [int(t), _pi(a), _pi(v), _weapon(w), bool(hs)]
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
            "w": _weapon(x.get("weapon") or ""),
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

    # the status icons this round needs: the bomb story, the round results,
    # headshots, deaths (the skull in the player list) and the clock
    for _kind in {str(kd) for _, kd, _ in bomb}:
        _badge({"plant": "bomb", "defuse": "defuse", "boom": "bomb_explode"}.get(_kind, ""))
    for _why in {why for _, _, why in rd} | ({why} if (why := out["why"]) else set()):
        _badge(
            {
                "bomb": "bomb_explode",
                "defuse": "defuse",
                "elim": "skull",
                "time": "hourglass",
            }.get(_why, "")
        )
    if any(k[4] for k in kills) or any(z.get("hs") for z in du):
        _badge("headshot")
    if kills or any(p.get("al") and 0 in p["al"] for p in players):
        _badge("skull")
    _badge("timer")
    for z in du:  # the kill modifiers, when the demo carries them
        if z["ns"]:
            _badge("noscope")
        if z["sm"]:
            _badge("smoke_kill")
        if z["bl"]:
            _badge("flashed_kill")
        if z["wb"]:
            _badge("wallbang")

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
        "thr": None if thr is None else float(thr),
        "sel": sel_i,
        "at": at_t,
        "radar": rp,
        "gridb": gb,
        "keys": keys,
        "lab": {k: str(v or "") for k, v in (labels or {}).items()},
        "col": {k: str(v or "") for k, v in (colors or {}).items()},
        "icons": icons_out,
        "wlab": wlab,
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
