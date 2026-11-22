"""
Radar/map helpers, kill points and the tick-level timeline / duel /
round replay data. Owns DemoParser.
"""

from __future__ import annotations

import json
import os
import struct
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from demoparser2 import DemoParser
import store

from core.model import (
    ALIVE_PROPS,
    BTN_NAMED,
    BUTTONS,
    DUCK_DROP,
    Demo,
    EYE_STAND,
    HEAD_STAND,
    NA,
    POS,
    TICKRATE,
    _decode_buttons,
    _safe_int,
    _speed_from_positions,
    norm_weapon,
)

from core.filters import apply_filters

from core.ids import _norm_alive, build_id_lookup, fix_ids
from core.openings import _enemy_kill_rows
from core.phases import _parse_ticks_tiered, tickrate_from_header

if TYPE_CHECKING:
    # annotation-only (F821): kill_points' `f: "Filters | None"` resolves
    # without another runtime import next to apply_filters above
    from core.filters import Filters


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
    `<root>/radars/` or `<root>` itself), best first: $AIMAUDIT_RADAR_DIR, then
    <data folder>/radars (written by start.bat / tools/fetch_radars.py). Nothing
    outside the app folder is searched, so deleting the folder leaves no
    radars (or anything else) behind. The app itself never downloads anything.
    """
    roots: list[str] = []
    env = os.environ.get("AIMAUDIT_RADAR_DIR")
    if env:
        roots.append(os.path.abspath(os.path.expanduser(env)))
    roots.append(store.radars_dir())
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


def _radar_png(root: str, name: str) -> str | None:
    """The radar PNG of `name` under a radar root (radars/ subfolder or the
    root itself)."""
    for sub in ("radars", ""):
        q = os.path.join(root, sub, f"{name}.png")
        if os.path.isfile(q):
            return q
    return None


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

        png = _radar_png(root, map_name)
        if not png:
            continue
        lower_png, lower = _radar_png(root, f"{map_name}_lower"), None
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

    def _to_float(row, name):
        v = pd.to_numeric(getattr(row, name, np.nan), errors="coerce")
        return float(v) if pd.notna(v) else np.nan

    rows = []
    for r in de.itertuples():
        killed = r.attacker_steamid == me
        mine, theirs = ("attacker", "user") if killed else ("user", "attacker")
        mx, my = _to_float(r, f"{mine}_X"), _to_float(r, f"{mine}_Y")
        if np.isnan(mx) or np.isnan(my):
            continue
        mz, ox = _to_float(r, f"{mine}_Z"), _to_float(r, f"{theirs}_X")
        oy, oz = _to_float(r, f"{theirs}_Y"), _to_float(r, f"{theirs}_Z")
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
    df = _parse_ticks_tiered(
        parser,
        (
            POS + BTN_NAMED + ["buttons"] + ALIVE_PROPS,
            POS + ["buttons"] + ALIVE_PROPS,
            POS + ALIVE_PROPS,
            POS,
        ),
        ticks,
        players=[int(me)],
    )
    df = _decode_buttons(df)
    df = _norm_alive(df)
    df = _timeline_speed(df, tickrate)
    missing = [x for x in BUTTONS if x not in df.columns]
    return df.sort_values("tick").reset_index(drop=True), missing


# What the replay shows besides position / health: the economy of every
# player (armor, helmet, defuse kit, money, gear value, inventory, defusing).
ECONOMY_PROPS = [
    "armor_value",
    "has_helmet",
    "has_defuser",
    "balance",
    "current_equip_value",
    "inventory",
    "is_defusing",
]


def _round_frames(parser, lo: int, hi: int, max_samples: int):
    """Every player's frames on the uniform grid (<= max_samples points,
    the UI interpolates): {sid: df}, the grid step and the key columns."""
    step = max(1, -(-(hi - lo + 1) // max_samples))
    grid = list(range(lo, hi + 1, step))
    df = _parse_ticks_tiered(
        parser,
        (
            POS
            + BTN_NAMED
            + ["buttons"]
            + ALIVE_PROPS
            + ["health", "active_weapon_name", "flash_duration"]
            + ECONOMY_PROPS,
            POS
            + BTN_NAMED
            + ["buttons"]
            + ALIVE_PROPS
            + ["health", "active_weapon_name", "flash_duration"],
            POS + BTN_NAMED + ["buttons"] + ALIVE_PROPS + ["health"],
            POS + BTN_NAMED + ["buttons"] + ALIVE_PROPS,
            POS + ALIVE_PROPS,
            POS,
        ),
        grid,
    )
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
    return frames, int(step), [k for k in BUTTONS if k in df.columns]


def _round_events(demo: Demo, lo: int, hi: int):
    """The round's shots / kills / hits (tick window, not the round column:
    the window is already the round). Hits cover EVERY pair of the round -
    the replay shows the whole round, teammate trades included."""
    fires, deaths, hurts = demo.fires, demo.deaths, demo.hurts
    out_shots: list[tuple[int, str, str]] = []
    s = _events_in_window(fires, lo, hi, {"tick", "weapon"})
    if s is not None:
        sid_col = "sid" if "sid" in fires.columns else "user_steamid"
        out_shots = [
            (int(r.tick), str(getattr(r, sid_col)), norm_weapon(r.weapon))
            for r in s.itertuples()
        ]
    out_kills: list[tuple[int, str, str, str, bool]] = []
    d = _events_in_window(deaths, lo, hi, {"tick", "attacker_steamid", "user_steamid"})
    if d is not None:
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
    h = _events_in_window(hurts, lo, hi, {"tick", "attacker_steamid", "user_steamid"})
    if h is not None:
        for r in h.itertuples():
            a, u = str(r.attacker_steamid), str(r.user_steamid)
            out_hits.append((int(r.tick), a, u))
    return out_shots, out_kills, out_hits


def round_replay_data(
    demo: Demo,
    round_no: int,
    *,
    max_samples: int = 2400,
    max_s: float = 180.0,
):
    """
    Tick-level data of the WHOLE round for the 2D replay: every player
    of both teams, their positions / view / HP / weapon / keys, plus the
    round's shots, kills and hits.

    The span is the demo's own round window (demo.round_windows, built by
    build_round_windows); a round that cannot be placed raises
    ValueError("no_round_data"). Frames are sampled on a uniform grid
    (<= max_samples points, the UI interpolates) and ffilled per player
    like the timeline. Returns a dict for ui.roundrep.round_payload:

    {"t0", "t1", "step", "tickrate", "round", "frames": {sid: df},
     "keys": [available key names], "shots": [(tick, sid, weapon)],
     "kills": [(tick, attacker, victim, weapon, hs)],
     "hits": [(tick, attacker, victim)],
     "info": how the round ended (Demo.round_info[round])}
    (hits cover EVERY pair of the round - the replay shows the whole
    round, teammate trades included). The frames also carry the economy
    when the parser has it: armor_value, has_helmet, has_defuser,
    balance (money), current_equip_value, inventory, is_defusing
    (ECONOMY_PROPS).
    """
    windows = getattr(demo, "round_windows", None) or {}
    round_no = int(round_no)
    if round_no not in windows:
        raise ValueError("no_round_data")
    lo, hi = int(windows[round_no][0]), int(windows[round_no][1])
    tickrate = int(getattr(demo, "tickrate", None) or TICKRATE)
    hi = min(hi, lo + int(max_s * tickrate))
    if hi <= lo:
        raise ValueError("no_round_data")

    frames, step, keys = _round_frames(DemoParser(demo.path), lo, hi, max_samples)
    out_shots, out_kills, out_hits = _round_events(demo, lo, hi)
    return {
        "t0": int(lo),
        "t1": int(hi),
        "step": step,
        "tickrate": int(tickrate),
        "round": round_no,
        "frames": frames,
        "keys": keys,
        "shots": out_shots,
        "kills": out_kills,
        "hits": out_hits,
        "info": dict((getattr(demo, "round_info", None) or {}).get(round_no) or {}),
    }


def _events_in_window(events, lo: int, hi: int, need: set):
    """An event frame cut to the [lo, hi] tick window, or None when the
    frame is absent/empty or misses columns (old demos) - the guard every
    round-event block repeats."""
    if events is None or not len(events) or not need <= set(events.columns):
        return None
    return events[events["tick"].between(lo, hi)]


def _row_rnd(r, name: str, nd: int = 1):
    """A row column as a finite float rounded to nd, else None."""
    v = getattr(r, name, None)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, nd)


def _row_tick(r, name: str):
    v = getattr(r, name, None)
    return None if v is None or pd.isna(v) else int(v)


def _row_flag(r, name: str) -> bool:
    """A duel modifier column (noscope, thrusmoke, ...): False when the
    column is missing (an older demo) or NaN, never bool(NaN)."""
    v = getattr(r, name, None)
    try:
        return False if v is None or pd.isna(v) else bool(v)
    except (TypeError, ValueError):  # noqa: BLE001 - garbage: not a flag
        return False


def _duel_entry(r, t0: int, t1: int, tk: int, tr: int) -> dict:
    """One round_duels entry: the duel's window bracket, the duelists and
    the per-duelist numbers measured at each side's first shot."""
    kft, vft = _row_tick(r, "killer_first_tick"), _row_tick(r, "victim_first_tick")
    firsts = [x for x in (kft, vft) if x is not None]
    start = min(firsts) if firsts else tk - tr
    return {
        "a": max(t0, start - int(0.75 * tr)),
        "b": min(t1, tk + int(1.5 * tr)),
        "tick": tk,
        "killer": str(r.killer),
        "victim": str(r.victim),
        "weapon": str(r.weapon),
        "hs": bool(r.headshot),
        "dur": _row_rnd(r, "duration_s", 2),
        "dist": _row_rnd(r, "distance", 0),
        "first": str(getattr(r, "first", "") or ""),
        # kill modifiers for the icons (wallbang / noscope / through
        # smoke / a flashed attacker), when the demo carries them
        "ns": _row_flag(r, "noscope"),
        "sm": _row_flag(r, "thrusmoke"),
        "bl": _row_flag(r, "attackerblind"),
        "wb": _row_flag(r, "penetrated"),
        "kft": kft,
        "vft": vft,
        "ksp": _row_rnd(r, "killer_speed", 0),
        "vsp": _row_rnd(r, "victim_speed", 0),
        "kerr": _row_rnd(r, "killer_err", 1),
        "verr": _row_rnd(r, "victim_err", 1),
        "kshots": _row_rnd(r, "killer_shots", 0),
        "vshots": _row_rnd(r, "victim_shots", 0),
    }


def round_duels(
    duels: pd.DataFrame, window: tuple[int, int], tickrate: int
) -> list[dict]:
    """
    EVERY duel of every player inside a round window (one entry per kill
    of a gun fight), earliest first - the right-hand list of the 2D replay.

    ``duels`` is core.build_duels' frame; a duel belongs to the round whose
    window holds the kill tick (the same window the replay plays), never to
    a round number read from another column. ``a``/``b`` bracket the
    segment the replay locks to: from the first shot of EITHER duelist
    (minus a short pre-roll to see the approach) to a bit after the kill
    (to see the outcome / the trade), clamped to the round.

    Besides who killed whom the entry carries the per-duelist numbers the
    duels table already measured at the FIRST SHOT of each side (speed,
    aim error, how many shots, the first-shot tick; k* = killer, v* =
    victim) so the replay can state them next to the live curves.
    """
    if duels is None or not len(duels) or "tick" not in duels.columns:
        return []
    t0, t1 = int(window[0]), int(window[1])
    d = duels[duels["tick"].between(t0, t1)].sort_values("tick", kind="stable")
    tr = max(int(tickrate), 1)
    return [_duel_entry(r, t0, t1, int(r.tick), tr) for r in d.itertuples(index=False)]


def _wrap180(a: np.ndarray) -> np.ndarray:
    """Angle difference wrapped into [-180, 180) (the shortest way round)."""
    return (a + 180.0) % 360.0 - 180.0


def _telemetry_prep(f: pd.DataFrame, ticks: np.ndarray, step: int) -> pd.DataFrame:
    """One player's frame rows on the telemetry grid, with one extra
    sample before the window (speed / turn are deltas)."""
    g = f.drop_duplicates("tick").set_index("tick").sort_index()
    keep = np.concatenate(([ticks[0] - step], ticks))
    return g.reindex(keep)


def _telemetry_series(me: pd.DataFrame, foe: pd.DataFrame, dt_s: float) -> dict:
    """One duelist's live series: speed (position delta, NaN on gaps /
    teleports / a dead pawn), turn (deg/s of the view vector), the
    crosshair-to-enemy-head angle and the distance between them."""
    x, y = me["X"].to_numpy(float), me["Y"].to_numpy(float)
    sp = np.hypot(np.diff(x), np.diff(y)) / dt_s
    yaw = me["yaw"].to_numpy(float)
    pit = me["pitch"].to_numpy(float)
    dyaw = _wrap180(np.diff(yaw))
    dpit = np.diff(pit)
    tn = np.hypot(dyaw * np.cos(np.radians(pit[1:])), dpit) / dt_s
    alive = (
        me["is_alive"].astype(float).fillna(1.0).to_numpy()[1:] > 0
        if "is_alive" in me.columns
        else np.ones(len(sp), bool)
    )
    sp = np.where(alive & (sp <= 600.0), sp, np.nan)
    tn = np.where(alive, tn, np.nan)
    # crosshair -> enemy head
    z = me["Z"].to_numpy(float)[1:] if "Z" in me.columns else np.zeros(len(sp))
    zf = foe["Z"].to_numpy(float)[1:] if "Z" in foe.columns else np.zeros(len(sp))
    duck_me = (
        np.nan_to_num(me["DUCK"].to_numpy(float)[1:]) > 0 if "DUCK" in me.columns else 0
    )
    duck_foe = (
        np.nan_to_num(foe["DUCK"].to_numpy(float)[1:]) > 0 if "DUCK" in foe.columns else 0
    )
    eye = z + EYE_STAND - DUCK_DROP * duck_me
    head = zf + HEAD_STAND - DUCK_DROP * duck_foe
    vx = foe["X"].to_numpy(float)[1:] - x[1:]
    vy = foe["Y"].to_numpy(float)[1:] - y[1:]
    vz = head - eye
    n = np.sqrt(vx * vx + vy * vy + vz * vz)
    p, yw = np.radians(pit[1:]), np.radians(yaw[1:])
    view = np.stack([np.cos(p) * np.cos(yw), np.cos(p) * np.sin(yw), -np.sin(p)])
    with np.errstate(invalid="ignore", divide="ignore"):
        cosang = (view[0] * vx + view[1] * vy + view[2] * vz) / n
    er = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
    er = np.where(alive & (n > 0), er, np.nan)
    return {"sp": sp, "tn": tn, "er": er, "dist": n}


def _telemetry_list(arr, nd: int) -> list:
    """A series as a JSON-friendly list (None for NaN)."""
    return [
        None if not np.isfinite(v) else (int(round(v)) if nd == 0 else round(float(v), nd))
        for v in arr
    ]


def duel_telemetry(
    fa: pd.DataFrame,
    fb: pd.DataFrame,
    window: tuple[int, int],
    t0: int,
    step: int,
    tickrate: int,
) -> dict:
    """
    Live aim numbers of one duel for the replay: both duelists' speed, how
    fast the crosshair turns, and the angle between the crosshair and the
    ENEMY'S HEAD, plus the distance between them - one value per replay
    sample inside ``window`` (``t0``/``step`` are the replay grid, so the
    UI indexes by sample).

    fa / fb are the players' frames on that grid (X, Y, Z, pitch, yaw,
    is_alive, DUCK when the parser has it). Same rules as the timeline:
    speed is the position delta (NaN on gaps, teleports, a dead pawn);
    ``turn`` is deg/s of the view vector (yaw scaled by cos(pitch) + pitch);
    ``err`` is the crosshair-to-head angle with eyes/head heights of a
    standing (or ducking) player. Recoil punch is NOT in these frames, so
    ``err`` is the pre-recoil crosshair - the table's per-duel number at
    the first shot includes the punch.

    Returns {"i0": first sample index, "a"/"b": {"sp", "tn", "er"}, "d": [...]}
    (lists of ints / 1-decimal floats, None for NaN) or {} when there is
    nothing to show.
    """
    step = max(int(step), 1)
    if fa is None or fb is None or not len(fa) or not len(fb):
        return {}
    lo, hi = int(window[0]), int(window[1])
    # a window outside what was sampled has nothing to show
    if hi < max(fa["tick"].min(), fb["tick"].min()) or lo > min(fa["tick"].max(), fb["tick"].max()):
        return {}
    i0 = max(0, -(-(lo - int(t0)) // step))
    i1 = (hi - int(t0)) // step
    if i1 < i0:
        return {}
    ticks = int(t0) + np.arange(i0, i1 + 1) * step

    a, b = _telemetry_prep(fa, ticks, step), _telemetry_prep(fb, ticks, step)
    dt_s = step / max(int(tickrate), 1)
    sa, sb = _telemetry_series(a, b, dt_s), _telemetry_series(b, a, dt_s)
    return {
        "i0": int(i0),
        "a": {"sp": _telemetry_list(sa["sp"], 0), "tn": _telemetry_list(sa["tn"], 0), "er": _telemetry_list(sa["er"], 1)},
        "b": {"sp": _telemetry_list(sb["sp"], 0), "tn": _telemetry_list(sb["tn"], 0), "er": _telemetry_list(sb["er"], 1)},
        "d": _telemetry_list(sa["dist"], 0),
    }
