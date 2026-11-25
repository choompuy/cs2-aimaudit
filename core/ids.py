"""SteamID resolution and frame normalization of what demoparser2 hands over."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.model import NA, TICKRATE, _speed_from_positions

__all__ = ["build_id_lookup", "fix_ids"]


def build_id_lookup(info: pd.DataFrame):
    """From parse_player_info: (float64 -> [candidate steamids], name -> steamid).
    SteamID64s above 2^53 get rounded in float64, so two ids may collapse to one float;
    the name disambiguates."""

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
    """SteamID column -> exact strings, whatever dtype it arrives in.

    NEVER convert through Int64: ids arrive as float64 (or rounded floats in object
    columns), where values above 2^53 sit on a 16-unit grid, so most players would
    disappear. Every value is mapped through build_id_lookup instead; anything unknown
    becomes NA."""

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
    """aim_punch_angle (string, list or pre-split columns) -> punch_pitch / punch_yaw."""

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
    """`speed` as the one-tick position delta (fire ticks are parsed with tick-1). NaN
    without a t-1 row, when dead at either tick, or on a teleport."""

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
