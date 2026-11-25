"""
SteamID resolution and frame normalization, the shapes demoparser2
hands over and the rest of core normalizes:

- build_id_lookup / fix_ids: event and tick SteamIDs arrive as float64
  (UInt64 with nulls, values > 2^53 rounded), UInt64 or rounded floats
  hidden in object columns - NEVER convert through Int64, map through
  the exact parse_player_info lookup (see fix_ids for the why);
- _extract_punch: aim_punch_angle in every shape it arrives in;
- _norm_alive: is_alive -> plain bool;
- _add_speed: the one-tick position delta speed of the fire-tick table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.model import NA, TICKRATE, _speed_from_positions

__all__ = ["build_id_lookup", "fix_ids"]


def build_id_lookup(info: pd.DataFrame):
    """
    From parse_player_info: (float64 -> [candidate steamids], name -> steamid).

    The float lookup may map one float to SEVERAL steamids: when a UInt64
    column with nulls becomes float64, SteamID64s (> 2^53) get rounded and
    two ids that differ in the last digits can collapse to the same float.
    The name map is used to disambiguate such collisions (event and tick
    tables always carry player names).
    """

    flookup: dict[float, list[str]] = {}
    name_to_sid: dict[str, str] = {}
    for sid, name in zip(info["steamid"], info["name"], strict=True):
        if pd.isna(sid) or int(sid) == 0:
            continue
        s = str(int(sid))
        flookup.setdefault(float(int(sid)), []).append(s)
        name_to_sid.setdefault(str(name), s)
    return flookup, name_to_sid


def fix_ids(
    s: pd.Series,
    flookup: dict[float, list[str]],
    names: pd.Series | None = None,
    name_to_sid: dict[str, str] | None = None,
) -> pd.Series:
    """
    SteamID column -> exact strings, whatever dtype it arrives in.

    demoparser2 hands event/tick SteamIDs over in several shapes: float64
    (UInt64 with nulls -> float, values > 2^53 rounded to a 16-unit grid),
    plain UInt64, and sometimes ROUNDED FLOATS hidden inside an object /
    nullable column. Converting any of those through Int64 -> str bakes the
    float rounding in: only players whose SteamID happens to be a multiple of
    16 survive, everyone else disappears from the stats (seen in the
    wild: a 10-player match where exactly one player kept his hits).

    So: NEVER convert through Int64. Every numeric-ish value is mapped through
    the {float64(sid): [sids]} lookup built from the exact parse_player_info
    table; the accompanying player name disambiguates collisions; anything
    else becomes NA.
    """

    def one(v, nm):
        try:
            if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
                return NA
            f = float(v)
        except (TypeError, ValueError):
            return NA
        if f == 0.0 or f != f:
            return NA
        cands = flookup.get(f)
        if not cands:
            return NA
        if len(cands) == 1:
            return cands[0]
        if nm is not None and name_to_sid:
            want = name_to_sid.get(str(nm))
            if want in cands:
                return want
        return NA

    if names is None or not name_to_sid:
        return s.map(lambda v: one(v, None)).astype(object)
    return pd.Series(
        [one(v, nm) for v, nm in zip(s, names, strict=True)], index=s.index, dtype=object
    )


def _extract_punch(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize aim_punch_angle into numeric punch_pitch / punch_yaw (degrees).

    demoparser2 may return the QAngle either as one column (string "x y z" or
    a list/tuple) or pre-split into several columns; handle all shapes.
    """

    df = df.copy()
    df["punch_pitch"] = np.nan
    df["punch_yaw"] = np.nan
    cands = [c for c in df.columns if str(c).startswith("aim_punch_angle")]
    if not cands:
        return df

    def _pair(v):
        try:
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return (np.nan, np.nan)
            if isinstance(v, str):
                parts = [
                    float(x)
                    for x in v.replace(",", " ")
                    .replace("[", " ")
                    .replace("]", " ")
                    .split()
                ]
                if len(parts) >= 2:
                    return (parts[0], parts[1])
                if len(parts) == 1:
                    return (parts[0], np.nan)
                return (np.nan, np.nan)
            if isinstance(v, (list, tuple, np.ndarray)):
                a = list(v)
                if len(a) >= 2:
                    return (float(a[0]), float(a[1]))
                if len(a) == 1:
                    return (float(a[0]), np.nan)
            if isinstance(v, (int, float, np.integer, np.floating)):
                f = float(v)
                return (np.nan, np.nan) if np.isnan(f) else (f, np.nan)
        except (ValueError, TypeError):
            return (np.nan, np.nan)
        return (np.nan, np.nan)

    if len(cands) == 1:
        pairs = df[cands[0]].map(_pair)
        df["punch_pitch"] = [p[0] for p in pairs]
        df["punch_yaw"] = [p[1] for p in pairs]
    else:
        # pre-split columns: _0/_x = pitch, _1/_y = yaw
        for c in cands:
            s = str(c).lower()
            if s.endswith(("_0", "_x", "0", "x")):
                df["punch_pitch"] = pd.to_numeric(df[c], errors="coerce")
            elif s.endswith(("_1", "_y", "1", "y")):
                df["punch_yaw"] = pd.to_numeric(df[c], errors="coerce")
    return df


def _norm_alive(df: pd.DataFrame) -> pd.DataFrame:
    """is_alive -> plain bool. Missing/NaN counts as alive (no false cuts)."""
    if "is_alive" not in df.columns:
        return df
    s = df["is_alive"]
    if pd.api.types.is_bool_dtype(s):
        return df
    df = df.copy()
    df["is_alive"] = pd.to_numeric(s, errors="coerce").fillna(1).astype(bool)
    return df


def _add_speed(df: pd.DataFrame, tickrate: int = TICKRATE) -> pd.DataFrame:
    """
    Own `speed` column for the fire-tick table (see the POS note above).

    Fire ticks are parsed together with tick-1, so speed is the exact one-tick
    delta |pos(t) - pos(t-1)| * tickrate. NaN when: no t-1 row, the player was
    dead at either tick, or the delta looks like a teleport (respawn/dormancy).
    """

    df = df.copy()
    if "speed" not in df.columns:
        df["speed"] = np.nan
    if not len(df) or not {"X", "Y", "tick", "steamid"}.issubset(df.columns):
        return df
    d = df.sort_values(["steamid", "tick"], kind="stable")
    g = d.groupby("steamid", sort=False)
    dt = d["tick"].astype(float) - g["tick"].shift(1)
    dx = d["X"] - g["X"].shift(1)
    dy = d["Y"] - g["Y"].shift(1)
    ok = (dt == 1.0) & dx.notna() & dy.notna()
    if "is_alive" in d.columns:
        al = d["is_alive"].astype(float)
        gal = al.groupby(d["steamid"], sort=False)
        ok &= gal.shift(1).fillna(1.0).astype(bool) & al.astype(bool)
    d["speed"] = _speed_from_positions(d, dt, dx, dy, tickrate).where(ok)
    return d
