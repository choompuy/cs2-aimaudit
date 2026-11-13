"""
Radar/map helpers, kill points and the tick-level timeline / duel /
round replay data. Owns DemoParser.
"""

from __future__ import annotations

import json
import os
import struct
from typing import Sequence

import numpy as np
import pandas as pd
from demoparser2 import DemoParser
import store

from core.model import (
    ALIVE_PROPS,
    BTN_NAMED,
    BUTTONS,
    Demo,
    NA,
    POS,
    TICKRATE,
    _decode_buttons,
    _round_col,
    _safe_int,
    _speed_from_positions,
    norm_weapon,
)

from core.filters import apply_filters

from core.loader import _norm_alive, build_id_lookup, fix_ids, tickrate_from_header

from core.metrics import _enemy_kill_rows


def _timeline_speed(df: pd.DataFrame, tickrate: int = TICKRATE) -> pd.DataFrame:
    """
    Own `speed` column for a contiguous tick window (timeline chart).

    Adjacent rows are scaled by the real tick difference; gaps, teleports and
    rows of a dead player are masked to NaN (a dead pawn's data is not real
    input - the user-facing charts are cut at death instead).
    """

    df = df.copy()
    if "speed" not in df.columns:
        df["speed"] = np.nan
    if not len(df) or not {"X", "Y", "tick"}.issubset(df.columns):
        return df
    d = df.sort_values("tick", kind="stable")
    dt = d["tick"].astype(float).diff()
    v = _speed_from_positions(d, dt, d["X"].diff(), d["Y"].diff(), tickrate)
    if "is_alive" in d.columns:
        v = v.where(d["is_alive"].astype(bool))
    d["speed"] = v
    return d


# ------------------------------------------------------------ radar map
RADAR_SIZE = 1024  # awpy-data radars are square; calibration is for this size


def radar_roots() -> list[str]:
    """
    Folders that may hold radar data (a map_data.json plus the images, in
    `<root>/radars/` or `<root>` itself), best first: $AIMAUDIT_RADAR_DIR,
    <data folder>/radars (written by tools/fetch_radars.py), a radars folder
    of an older version in ~/.cs2-aimaudit and the newest awpy cache ($AWPY_DATA_DIR or ~/.awpy, one folder per version).
    The app itself never downloads anything.
    """
    roots: list[str] = []
    env = os.environ.get("AIMAUDIT_RADAR_DIR")
    if env:
        roots.append(os.path.expanduser(env))
    roots.append(store.radars_dir())
    roots.append(os.path.join(store.LEGACY_DIR, "radars"))
    awpy = os.environ.get("AWPY_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), ".awpy"
    )
    try:
        vers = sorted(
            (d for d in os.listdir(awpy) if d.isdigit()), key=int, reverse=True
        )
    except OSError:
        vers = []
    roots += [os.path.join(awpy, v) for v in vers]
    return roots


def _png_size(path: str) -> int | None:
    """Side length of a square PNG from its header, None when unreadable."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return int(w) if w == h and w > 0 else None


def load_radar(map_name: str) -> dict | None:
    """
    Radar of a map: {"png", "lower_png", "size", "pos_x", "pos_y", "scale",
    "lower": (z_min, z_max) | None}, or None when no root has both the
    calibration (map_data.json) and the image. `lower` is the altitude
    range of the lower level of a two-level map (Nuke, Vertigo) and is
    only set when its image exists as well.
    """
    if not map_name:
        return None
    for root in radar_roots():
        try:
            with open(os.path.join(root, "map_data.json"), encoding="utf-8") as fh:
                meta = json.load(fh).get(map_name)
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(meta, dict):
            continue
        try:
            pos_x, pos_y = float(meta["pos_x"]), float(meta["pos_y"])
            scale = float(meta["scale"])
        except (KeyError, TypeError, ValueError):
            continue
        if not scale > 0:
            continue

        def _img(name):
            for sub in ("radars", ""):
                q = os.path.join(root, sub, f"{name}.png")
                if os.path.isfile(q):
                    return q
            return None

        png = _img(map_name)
        if not png:
            continue
        lower_png, lower = _img(f"{map_name}_lower"), None
        sec = (meta.get("verticalsections") or {}).get("lower")
        if lower_png and isinstance(sec, dict):
            try:
                lower = (float(sec["AltitudeMin"]), float(sec["AltitudeMax"]))
            except (KeyError, TypeError, ValueError):
                lower = None
        return {
            "png": png,
            "lower_png": lower_png if lower else None,
            "size": _png_size(png) or RADAR_SIZE,
            "pos_x": pos_x,
            "pos_y": pos_y,
            "scale": scale,
            "lower": lower,
        }
    return None


def world_to_radar(x, y, radar: dict):
    """World (X, Y) -> pixel (px, py) of the radar image, y down. Vectorised."""
    k = radar["size"] / RADAR_SIZE
    px = (np.asarray(x, dtype=float) - radar["pos_x"]) / radar["scale"] * k
    py = (radar["pos_y"] - np.asarray(y, dtype=float)) / radar["scale"] * k
    return px, py


def on_lower_level(z, radar: dict):
    """Boolean array: altitude is on the lower level of a two-level map."""
    z = np.asarray(z, dtype=float)
    if not radar or not radar.get("lower"):
        return np.zeros(z.shape, dtype=bool)
    lo, hi = radar["lower"]
    return (z >= lo) & (z < hi)


KILL_POINT_COLS = [
    "round",
    "tick",
    "result",
    "opponent",
    "weapon",
    "headshot",
    "distance",
    "my_side",
    "my_x",
    "my_y",
    "my_z",
    "opp_x",
    "opp_y",
    "opp_z",
]


def kill_points(demo: Demo, me: str, f: "Filters | None" = None) -> pd.DataFrame:
    """
    Every enemy kill and death of player `me` (any weapon, like the K/D)
    with both positions, for the Map page. `result` is "kill" or "death",
    `my_*` is where `me` stood, `opp_*` where the opponent stood;
    `distance` is computed from the two positions (game units, the same
    scale as the shot distances). The global filter applies as everywhere:
    round range, side (me in that round), weapon / class, distance.
    Rows without the player's own position are dropped, a missing opponent
    position stays NaN. Empty frame (with the columns) when the demo's
    kill events carry no positions.
    """
    empty = pd.DataFrame(columns=KILL_POINT_COLS)
    de = _enemy_kill_rows(demo)
    need = {f"{who}_{ax}" for who in ("attacker", "user") for ax in ("X", "Y")}
    if not len(de) or not need <= set(de.columns):
        return empty
    de = de[(de["attacker_steamid"] == me) | (de["user_steamid"] == me)]
    if not len(de):
        return empty

    def _num(row, name):
        v = pd.to_numeric(getattr(row, name, np.nan), errors="coerce")
        return float(v) if pd.notna(v) else np.nan

    rows = []
    for r in de.itertuples():
        killed = r.attacker_steamid == me
        mine, theirs = ("attacker", "user") if killed else ("user", "attacker")
        mx, my = _num(r, f"{mine}_X"), _num(r, f"{mine}_Y")
        if np.isnan(mx) or np.isnan(my):
            continue
        mz, ox = _num(r, f"{mine}_Z"), _num(r, f"{theirs}_X")
        oy, oz = _num(r, f"{theirs}_Y"), _num(r, f"{theirs}_Z")
        rd = _safe_int(getattr(r, "round_idx", 0)) + 1
        dist = (
            float(
                np.sqrt((mx - ox) ** 2 + (my - oy) ** 2 + np.nan_to_num(mz - oz) ** 2)
            )
            if not (np.isnan(ox) or np.isnan(oy))
            else np.nan
        )
        rows.append(
            {
                "round": rd,
                "tick": int(r.tick),
                "result": "kill" if killed else "death",
                "opponent": r.user_steamid if killed else r.attacker_steamid,
                "weapon": norm_weapon(r.weapon) if hasattr(r, "weapon") else NA,
                "headshot": bool(getattr(r, "headshot", False)),
                "distance": dist,
                "my_side": demo.side_at(rd, me),
                "my_x": mx,
                "my_y": my,
                "my_z": mz,
                "opp_x": ox,
                "opp_y": oy,
                "opp_z": oz,
            }
        )
    out = pd.DataFrame(rows, columns=KILL_POINT_COLS)
    if f is not None and not f.empty() and len(out):
        _, out, _ = apply_filters(pd.DataFrame(), out, None, f)
    return out.reset_index(drop=True)


def timeline_data(
    path: str,
    me: str,
    tick: int,
    before_s: float = 3.0,
    after_s: float = 0.25,
    tickrate: int | None = None,
):
    """Tick-level data of one player around an event. Returns (df, missing_buttons)."""
    parser = DemoParser(path)
    if tickrate is None:
        try:
            tickrate = tickrate_from_header(parser.parse_header())
        except Exception:  # noqa: BLE001 - header is optional here
            tickrate = TICKRATE
    ticks = list(
        range(tick - int(before_s * tickrate), tick + int(after_s * tickrate) + 1)
    )
    df = None
    last_err: Exception | None = None
    for props in (
        POS + BTN_NAMED + ["buttons"] + ALIVE_PROPS,
        POS + ["buttons"] + ALIVE_PROPS,
        POS + ALIVE_PROPS,
        POS,
    ):
        try:
            df = parser.parse_ticks(props, ticks=ticks, players=[int(me)])
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if df is None:
        raise last_err  # type: ignore[misc]
    df = _decode_buttons(df)
    df = _norm_alive(df)
    df = _timeline_speed(df, tickrate)
    missing = [x for x in BUTTONS if x not in df.columns]
    return df.sort_values("tick").reset_index(drop=True), missing


def replay_data(
    path: str,
    sids: list[str],
    tick: int,
    before_s: float = 3.0,
    after_s: float = 0.75,
    tickrate: int | None = None,
):
    """
    Tick-level data of the duellists around one duel, for the 2D replay.

    One parse for all `sids` (the two players of the duel), split per
    player: ({sid: frame sorted by tick}, missing_buttons). Each frame has
    X / Y / Z, pitch / yaw, `speed` (same rules as the timeline: NaN on
    gaps, teleports and a dead pawn), `is_alive` and the held keys.
    Nothing but the duellists is read - grenades, teammates and other
    enemies are not part of a duel replay.
    """
    parser = DemoParser(path)
    if tickrate is None:
        try:
            tickrate = tickrate_from_header(parser.parse_header())
        except Exception:  # noqa: BLE001 - header is optional here
            tickrate = TICKRATE
    ticks = list(
        range(tick - int(before_s * tickrate), tick + int(after_s * tickrate) + 1)
    )
    df = None
    last_err: Exception | None = None
    for props in (
        POS + BTN_NAMED + ["buttons"] + ALIVE_PROPS,
        POS + ["buttons"] + ALIVE_PROPS,
        POS + ALIVE_PROPS,
        POS,
    ):
        try:
            df = parser.parse_ticks(props, ticks=ticks, players=[int(x) for x in sids])
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if df is None:
        raise last_err  # type: ignore[misc]
    # exact SteamIDs (the raw column is float-rounded), like every tick frame
    flookup, name_to_sid = build_id_lookup(parser.parse_player_info())
    df["steamid"] = fix_ids(
        df["steamid"],
        flookup,
        df["name"] if "name" in df.columns else None,
        name_to_sid,
    )
    df = _norm_alive(_decode_buttons(df))
    frames: dict[str, pd.DataFrame] = {}
    for sid in sids:
        d = df[df["steamid"] == sid].drop_duplicates("tick")
        frames[sid] = (
            _timeline_speed(d, tickrate).sort_values("tick").reset_index(drop=True)
        )
    missing = [x for x in BUTTONS if x not in df.columns]
    return frames, missing


def _live_freeze_ends(parser) -> list[int]:
    """
    Sorted ticks of round_freeze_end events of the LIVE match: warmup
    freeze ends and everything before begin_new_match (restarts, the knife
    round) are dropped, so index i maps to live round i+1.
    """
    try:
        ev = parser.parse_events(["round_freeze_end", "begin_new_match"])
    except Exception:  # noqa: BLE001 - old parser / no events
        return []
    freeze = dict(ev).get("round_freeze_end", pd.DataFrame())
    bnm = dict(ev).get("begin_new_match", pd.DataFrame())
    live_start = None
    if len(bnm) and "tick" in bnm.columns:
        t = pd.to_numeric(bnm["tick"], errors="coerce").dropna()
        if len(t):
            live_start = int(t.max())
    out: list[int] = []
    if len(freeze) and "tick" in freeze.columns:
        t = pd.to_numeric(freeze["tick"], errors="coerce").dropna()
        warm = (
            freeze["is_warmup_period"].fillna(False).astype(bool)
            if "is_warmup_period" in freeze.columns
            else pd.Series(False, index=freeze.index)
        )
        for tt, ww in zip(t, warm[t.notna()]):
            ti = int(tt)
            if ww:
                continue
            if live_start is not None and ti < live_start:
                continue
            out.append(ti)
    return sorted(set(out))


def round_replay_data(
    path: str,
    round_no: int,
    me: str,
    *,
    key_sids: Sequence[str] = (),
    tickrate: int | None = None,
    fires: pd.DataFrame | None = None,
    deaths: pd.DataFrame | None = None,
    hurts: pd.DataFrame | None = None,
    max_samples: int = 3200,
    max_s: float = 180.0,
):
    """
    Tick-level data of the WHOLE round for the round replay: every player
    of both teams, their positions / view / HP / weapon / keys, plus the
    round's shots, kills and hits around the selected player.

    Unlike replay_data (the old two-duellist replay) this reads everyone,
    so trades by teammates and "where was I shooting" are visible. The
    round boundaries come from the round's own events (deaths/fires with
    that round number) bracketed by live round_freeze_end ticks; without
    freeze ends the window is the events +/- a margin, without events at
    all the freeze-end index of the round is used. Frames are sampled on
    a uniform grid (<= max_samples points, event ticks snap to the grid
    neighbourhood, the UI interpolates) and ffilled per player like the
    timeline. Returns a dict for ui.roundrep.round_payload:

    {"t0", "t1", "step", "tickrate", "round", "frames": {sid: df},
     "keys": [available key names], "shots": [(tick, sid, weapon)],  # from fires
     "kills": [(tick, attacker, victim, weapon, hs)],
     "hits": [(tick, attacker, victim)]}
    (hits cover EVERY pair of the round - the replay shows the whole
    round, teammate trades included). Raises ValueError("no_round_data")
    when neither events nor freeze ends can place the round.
    """
    parser = DemoParser(path)
    if tickrate is None:
        try:
            tickrate = tickrate_from_header(parser.parse_header())
        except Exception:  # noqa: BLE001 - header is optional here
            tickrate = TICKRATE
    round_no = int(round_no)

    # ---- round boundaries -------------------------------------------------
    ev_ticks: list[int] = []
    for df in (deaths, fires):
        if df is None or not len(df):
            continue
        r = _round_col(df)
        d = df[(r + 1) == round_no]
        if len(d) and "tick" in d.columns:
            ev_ticks += [
                int(x) for x in pd.to_numeric(d["tick"], errors="coerce").dropna()
            ]
    fe = _live_freeze_ends(parser)

    def _ev_span() -> tuple[int, int] | None:
        if not ev_ticks:
            return None
        lo, hi = min(ev_ticks), max(ev_ticks)
        lo = max(lo - int(3.0 * tickrate), 0)
        hi = hi + int(5.0 * tickrate)
        return lo, hi

    span = _ev_span()
    if span is not None:
        lo, hi = span
        prev_fe = [x for x in fe if x <= lo + int(3.0 * tickrate)]
        if prev_fe:
            lo = min(prev_fe[-1], lo)  # round starts at its freeze end
        nxt_fe = [x for x in fe if x > lo]
        if nxt_fe:
            hi = min(hi, nxt_fe[0] - 1)
    elif fe and 1 <= round_no <= len(fe):
        lo = fe[round_no - 1]
        hi = (fe[round_no] - 1) if round_no < len(fe) else lo + int(max_s * tickrate)
    else:
        raise ValueError("no_round_data")
    hi = min(hi, lo + int(max_s * tickrate))
    if hi <= lo:
        raise ValueError("no_round_data")

    # ---- uniform grid (<= max_samples points, the UI interpolates) -------
    step = max(1, -(-(hi - lo + 1) // max_samples))
    grid = list(range(lo, hi + 1, step))

    df = None
    last_err: Exception | None = None
    for props in (
        POS
        + BTN_NAMED
        + ["buttons"]
        + ALIVE_PROPS
        + ["health", "active_weapon_name", "flash_duration"],
        POS + BTN_NAMED + ["buttons"] + ALIVE_PROPS + ["health"],
        POS + BTN_NAMED + ["buttons"] + ALIVE_PROPS,
        POS + ALIVE_PROPS,
        POS,
    ):
        try:
            df = parser.parse_ticks(props, ticks=grid)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if df is None:
        raise last_err  # type: ignore[misc]
    # exact SteamIDs (the raw column is float-rounded), like every tick frame
    flookup, name_to_sid = build_id_lookup(parser.parse_player_info())
    df["steamid"] = fix_ids(
        df["steamid"],
        flookup,
        df["name"] if "name" in df.columns else None,
        name_to_sid,
    )
    df = _norm_alive(_decode_buttons(df))

    frames: dict[str, pd.DataFrame] = {}
    for sid, d in df.groupby("steamid", sort=False):
        frames[str(sid)] = (
            d.drop_duplicates("tick").sort_values("tick").reset_index(drop=True)
        )

    # ---- events of the round (tick window, not the round column: the
    # window is already the round) -----------------------------------------
    out_shots: list[tuple[int, str, str]] = []
    if (
        fires is not None
        and len(fires)
        and "tick" in fires.columns
        and "weapon" in fires.columns
    ):
        sid_col = "sid" if "sid" in fires.columns else "user_steamid"
        s = fires[fires["tick"].between(lo, hi)]
        out_shots = [
            (int(r.tick), str(getattr(r, sid_col)), norm_weapon(r.weapon))
            for r in s.itertuples()
        ]
    out_kills: list[tuple[int, str, str, str, bool]] = []
    if (
        deaths is not None
        and len(deaths)
        and {"tick", "attacker_steamid", "user_steamid"} <= set(deaths.columns)
    ):
        d = deaths[deaths["tick"].between(lo, hi)]
        for r in d.itertuples():
            out_kills.append(
                (
                    int(r.tick),
                    str(r.attacker_steamid),
                    str(r.user_steamid),
                    norm_weapon(getattr(r, "weapon", "") or ""),
                    bool(getattr(r, "headshot", False)),
                )
            )
    out_hits: list[tuple[int, str, str]] = []
    if (
        hurts is not None
        and len(hurts)
        and {"tick", "attacker_steamid", "user_steamid"} <= set(hurts.columns)
    ):
        h = hurts[hurts["tick"].between(lo, hi)]
        for r in h.itertuples():
            a, u = str(r.attacker_steamid), str(r.user_steamid)
            # every pair of the round: the replay shows the whole round
            # (teammate trades too), not only the selected duelists
            out_hits.append((int(r.tick), a, u))

    keys = [k for k in BUTTONS if k in df.columns]
    return {
        "t0": int(lo),
        "t1": int(hi),
        "step": int(step),
        "tickrate": int(tickrate),
        "round": round_no,
        "frames": frames,
        "keys": keys,
        "shots": out_shots,
        "kills": out_kills,
        "hits": out_hits,
    }
