"""
CS2 demo analysis logic (no UI). Parsing is done by demoparser2.
All column names / values here are language-neutral; the UI translates them.
"""

from __future__ import annotations

import bisect
import bz2
import gzip
import json
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
import pandas as pd
from demoparser2 import DemoParser

TICKRATE = 64
NA = "<NA>"
NON_GUN = {
    "hegrenade",
    "flashbang",
    "smokegrenade",
    "molotov",
    "incgrenade",
    "decoy",
    "inferno",
    "world",
    "planted_c4",
    "c4",
    "taser",
    "healthshot",
    "tagrenade",
}
HITGROUPS = {
    1: "head",
    2: "chest",
    3: "stomach",
    4: "left_arm",
    5: "right_arm",
    6: "left_leg",
    7: "right_leg",
    8: "neck",
}
# Weapon classes (CS2 internal names after norm_weapon). Lets "all rifles"
# be one click instead of seven. Everything unmapped is "other".
WEAPON_CLASSES: dict[str, set[str]] = {
    "rifle": {"ak47", "m4a1", "m4a1_silencer", "famas", "galilar", "aug", "sg556"},
    "sniper": {"awp", "ssg08", "scar20", "g3sg1"},
    "smg": {"mac10", "mp9", "mp7", "mp5sd", "ump45", "p90", "bizon"},
    "pistol": {
        "glock",
        "hkp2000",
        "usp_silencer",
        "p250",
        "fiveseven",
        "tec9",
        "cz75a",
        "deagle",
        "elite",
        "revolver",
    },
    "shotgun": {"nova", "xm1014", "mag7", "sawedoff"},
    "lmg": {"m249", "negev"},
}
WEAPON_TO_CLASS: dict[str, str] = {w: c for c, ws in WEAPON_CLASSES.items() for w in ws}
# ------------------------------------------------------------- buttons
# Named button props actually supported by demoparser2 (README props table).
# LEFT/RIGHT are strafe A/D (IN_MOVELEFT/IN_MOVERIGHT), TURNLEFT/RIGHT are not
# exposed as named props.
BTN_NAMED = [
    "FORWARD",
    "LEFT",
    "BACK",
    "RIGHT",
    "WALK",
    "RIGHTCLICK",
    "FIRE",
    "RELOAD",
]
# DUCK / JUMP are NOT exposed as named props -> decoded from the raw bitfield.
# Bits follow Source IN_* button codes (see examples/buttons_bitfield).
BTN_BITS = {"JUMP": 1 << 1, "DUCK": 1 << 2}
# Full list used by the UI (order = display order).
BUTTONS = [
    "FORWARD",
    "LEFT",
    "BACK",
    "RIGHT",
    "WALK",
    "DUCK",
    "JUMP",
    "RIGHTCLICK",
    "FIRE",
    "RELOAD",
]
# Movement keys with the direction they push (deg relative to yaw).
# Counter-strafing = tapping the key that pushes AGAINST your motion.
STRAFE_DIRS = {"FORWARD": 0.0, "LEFT": 90.0, "BACK": 180.0, "RIGHT": -90.0}
# NOTE: velocity_X/velocity_Y are deliberately NOT requested. demoparser2
# computes them as (prev sampled pos - prev-prev sampled pos) * 64 over the
# REQUESTED tick rows (see its collect_data.rs::velocity_from_history), so with
# a sparse tick list every real movement is inflated by the tick gap and every
# spawn/respawn teleport becomes a 30k+ u/s spike. We compute speed ourselves
# from positions (see _add_speed).
POS = ["X", "Y", "Z", "pitch", "yaw"]
# m_bPawnIsAlive - lets us drop garbage rows around death/respawn (stale
# button bits and frozen/teleporting positions of a dead pawn).
ALIVE_PROPS = ["is_alive"]
# Recoil punch angle (CCSPlayerPawn.m_aimPunchAngle): requested in addition to
# POS; if the parser does not know the prop we fall back to a plain POS parse.
PUNCH_PROPS = ["aim_punch_angle"]
# Crouch amount (m_flDuckAmount, 0..1): shifts eye/head height down ~18u.
DUCK_PROPS = ["duck_amount"]
TEAMS = {2: "T", 3: "CT"}
SIDE_PROPS = ["team_num"]  # m_iTeamNum per tick: the real per-round side

# ------------------------------------------------- distance-aware risk model
# Heuristic, NOT the real in-game spread table. Weapon inaccuracy grows with
# speed, but whether it matters depends on distance to the enemy:
#   dist <= SAFE_DIST   -> any speed is accepted (point-blank run-and-gun);
#   dist >= STRICT_DIST -> only near-stationary shots (speed <= thr) are;
#   in between          -> the accepted speed falls off linearly.
SAFE_DIST = 500.0
STRICT_DIST = 1500.0
FULL_SPEED = 250.0  # ~ running speed with a rifle
IMPOSSIBLE_SPEED = 350.0  # above this the speed data itself looks broken
# Horizontal speed cap: real movement stays far below it (running ~250 u/s,
# air-strafing a bit more); anything above is a teleport / respawn / dormancy
# artifact in the position stream and becomes NaN.
MAX_SPEED = 600.0
# weapon_fire and player_hurt normally share the tick; a hurt is attributed
# to the shooter's latest same-weapon shot at most this many ticks earlier
# (absorbs off-by-one/two without flagging earlier shots of the same burst).
HIT_WINDOW = 3
MAX_DUEL_DIST = 10000.0
# Eye / head heights (units, above the pawn origin). Crouching lowers both
# by ~18 units; we scale linearly with duck_amount (0..1).
EYE_STAND, HEAD_STAND, DUCK_DROP = 64.0, 62.0, 18.0
# Approximate horizontal size of the head hitbox (units). Used to express
# the aim error in "head widths": 5° at 200 units is a miss, 5° at 2000
# units can still be a headshot.
HEAD_WIDTH = 18.0
# Below these counts a rate is noise: the UI shows a small-sample hint
# instead of pretending the number means something.
MIN_N_SHOTS = 10
MIN_N_DUELS = 5


def wilson_ci(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score interval (in %) for a binomial rate k/n.

    With 8 duels a '62%' means almost nothing; a rate is honest only next
    to its uncertainty. Returns (nan, nan) when n <= 0.
    """

    if n is None or k is None or n <= 0 or pd.isna(n) or pd.isna(k):
        return (float("nan"), float("nan"))
    k = float(k)
    n = float(n)
    p = min(1.0, max(0.0, k / n))  # k > n is possible (e.g. pellets vs shots)
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, (center - half) * 100), min(100.0, (center + half) * 100))


def rank_in_lobby(
    summary: pd.DataFrame, sid: str, col: str, best: str = "high"
) -> tuple[int, int] | None:
    """
    (position, n) of player `sid` in a summary column, 1 = best.

    best='high' -> the largest value is the best (accuracy), best='low' ->
    the smallest wins (risky shots). Players with NaN in `col` are skipped;
    None when the player or the column is not there or nothing to compare.
    """

    if (
        summary is None
        or col not in getattr(summary, "columns", [])
        or not len(summary)
    ):
        return None
    vals = summary[["sid", col]].dropna()
    if sid not in set(vals["sid"]) or len(vals) < 2:
        return None
    order = vals.sort_values(col, ascending=(best == "low"), kind="stable")
    pos = int((order["sid"] == sid).to_numpy().nonzero()[0][0]) + 1
    return pos, len(vals)


@dataclass
class Filters:
    """Global filter applied to shots/duels/hurts before any metric."""

    weapons: set[str] | None = None  # exact weapon names (norm_weapon form)
    classes: set[str] | None = None  # rifle, sniper, smg, pistol, ...
    rounds: tuple[int, int] | None = None  # inclusive 1-based round range
    rounds_set: set[int] | None = None  # exact rounds (pistol rounds preset)
    side: str | None = None  # "T" / "CT" (the player's side in that round)
    results: set[str] | None = None  # {"kill", "death"} (duels_for frames)
    dist: tuple[float, float] | None = None  # inclusive distance range

    def empty(self) -> bool:
        return not any(
            (
                self.weapons,
                self.classes,
                self.rounds,
                self.rounds_set,
                self.side,
                self.results,
                self.dist,
            )
        )


def weapon_class(w) -> str:
    """Class of a weapon name ('rifle', 'sniper', ..., 'other')."""
    return WEAPON_TO_CLASS.get(norm_weapon(w), "other")


# MR12 (current CS2): halves of 12 rounds, overtime starts at round 25.
ROUNDS_PER_HALF = 12


def round_bounds(n_rounds: int, preset: str) -> tuple[int, int]:
    """
    Inclusive 1-based round range of a preset for a match of `n_rounds`
    rounds ('all', 'first', 'second', 'ot'). MR12 assumption: first half
    1-12, second 13-24, overtime 25+. A preset beyond the match gives an
    empty range (lo > hi) on purpose - the UI simply does not offer it.
    """

    n = max(int(n_rounds), 1)
    if preset == "first":
        lo, hi = 1, ROUNDS_PER_HALF
    elif preset == "second":
        lo, hi = ROUNDS_PER_HALF + 1, 2 * ROUNDS_PER_HALF
    elif preset == "ot":
        lo, hi = 2 * ROUNDS_PER_HALF + 1, max(n, 2 * ROUNDS_PER_HALF + 1)
    else:  # 'all' / anything unknown
        lo, hi = 1, n
    hi = max(lo, min(hi, n))
    return (lo, hi)


def pistol_rounds(n_rounds: int) -> set[int]:
    """First round of each half incl. overtime halves (MR12: 1, 13, 25, ...)."""
    return {
        r for r in range(1, max(int(n_rounds), 1) + 1) if (r - 1) % ROUNDS_PER_HALF == 0
    }


def apply_filters(
    shots: pd.DataFrame,
    duels: pd.DataFrame,
    hurts: pd.DataFrame | None = None,
    f: Filters | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """
    Boolean-index shots / duels / hurts by the global filter (copies back).

    Every selection is optional: None/empty means 'no constraint'. Works on
    the raw frames (shots_table / build_duels / demo.hurts) and on
    duels_for() frames - 'my_side' and 'result' columns are used when
    present; raw duels keep a row for the side filter when EITHER role
    matches (each player's kills/deaths then keep the correct rows).
    """

    if f is None or f.empty():
        return (
            shots.copy(),
            duels.copy() if duels is not None else pd.DataFrame(),
            hurts.copy() if hurts is not None else None,
        )

    def _weapon_mask(df: pd.DataFrame) -> pd.Series | None:
        if "weapon" not in df.columns or not len(df):
            return None
        # weapons and classes combine as a UNION: picking "rifles" plus a
        # specific AWP shows both, not their intersection.
        parts: list[pd.Series] = []
        if f.weapons:
            parts.append(df["weapon"].isin(f.weapons))
        if f.classes:
            parts.append(df["weapon"].map(weapon_class).isin(f.classes))
        if not parts:
            return None
        m = parts[0]
        for p in parts[1:]:
            m = m | p
        return m

    def _apply(df: pd.DataFrame | None) -> pd.DataFrame | None:
        if df is None or not len(df):
            return df.copy() if df is not None else None
        out = df
        wm = _weapon_mask(out)
        if wm is not None:
            out = out[wm]
        if f.rounds is not None and "round" in out.columns and len(out):
            lo, hi = f.rounds
            out = out[(out["round"] >= lo) & (out["round"] <= hi)]
        if f.rounds_set and "round" in out.columns and len(out):
            out = out[out["round"].isin(f.rounds_set)]
        if f.side is not None and len(out):
            if "my_side" in out.columns:
                out = out[out["my_side"] == f.side]
            elif {"killer_side", "victim_side"}.issubset(out.columns):
                out = out[
                    (out["killer_side"] == f.side) | (out["victim_side"] == f.side)
                ]
            elif "side" in out.columns:
                out = out[out["side"] == f.side]
        if f.results is not None and "result" in out.columns and len(out):
            out = out[out["result"].isin(f.results)]
        if f.dist is not None and len(out):
            dcol = next((c for c in ("dist", "distance") if c in out.columns), None)
            if dcol is not None:
                d = pd.to_numeric(out[dcol], errors="coerce")
                lo, hi = f.dist
                out = out[(d >= lo) & (d <= hi)]
        return out.copy()

    return _apply(shots), _apply(duels), _apply(hurts)


def allowed_speed(dist, thr, safe=SAFE_DIST, strict=STRICT_DIST, vmax=FULL_SPEED):
    """
    Max speed (units/s) at which a shot at distance `dist` (units) is still
    'acceptable'. Flat `vmax` at/below `safe`, `thr` at/above `strict`, linear
    in between. NaN distance -> NaN (unknown, caller decides).
    """

    scalar = np.ndim(dist) == 0
    d = pd.Series(np.asarray(dist, dtype=float).ravel())
    k = ((strict - d) / (strict - safe)).clip(0.0, 1.0)
    v = thr + k * (vmax - thr)
    v[d <= safe] = vmax
    v[d.isna()] = np.nan
    arr = v.to_numpy()
    return float(arr[0]) if scalar else arr


# ----------------------------------------------------------------- helpers
def norm_weapon(w) -> str:
    return str(w).lower().replace("weapon_", "")


def is_gun(w) -> bool:
    w = norm_weapon(w)
    return w not in NON_GUN and "knife" not in w and w != "bayonet"


def hg_key(x) -> str:
    if isinstance(x, (int, float, np.integer, np.floating)) and not pd.isna(x):
        return HITGROUPS.get(int(x), "other")
    return str(x).lower().replace(" ", "_")


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
    for sid, name in zip(info["steamid"], info["name"]):
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
    16 survive, everyone else silently disappears from the stats (seen in the
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
        [one(v, nm) for v, nm in zip(s, names)], index=s.index, dtype=object
    )


def _safe_int(v, default=0) -> int:
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _round_col(df: pd.DataFrame) -> pd.Series:
    """total_rounds_played -> 0-based round int, -1 when missing/NaN."""
    if "total_rounds_played" in df.columns:
        return (
            pd.to_numeric(df["total_rounds_played"], errors="coerce")
            .fillna(-1)
            .astype(int)
        )
    return pd.Series(-1, index=df.index)


def _decode_buttons(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derive DUCK/JUMP from the raw `buttons` bitfield (bits 2 and 1).

    The bitfield max bit is 35 (IN_INSPECT), far below 2^53, so the
    float64 conversion quirk that hits SteamIDs cannot corrupt it.
    """

    if "buttons" not in df.columns:
        return df
    b = pd.to_numeric(df["buttons"], errors="coerce").fillna(0).astype("int64")
    for name, bit in BTN_BITS.items():
        if name not in df.columns:
            df[name] = (b & bit) != 0
    return df


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


def _speed_from_positions(
    d: pd.DataFrame,
    dt: pd.Series,
    dx: pd.Series,
    dy: pd.Series,
    tickrate: int = TICKRATE,
) -> pd.Series:
    """Horizontal speed (u/s) = |dpos| / dt_ticks * tickrate, NaN on garbage."""
    v = np.hypot(dx, dy) / dt * tickrate
    return v.where((dt >= 1) & (v <= MAX_SPEED))


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


def _counter_strafe_tap(rt, rp) -> bool:
    """
    True when every held movement key (W/A/S/D) at the shot tick - or at
    the tick before - pushed AGAINST the player's movement over that tick:
    an active counter-strafe tap, not just releasing keys. False when
    button data is missing, the player barely moved (no direction to
    oppose) or the held keys are mixed (still steering).
    """

    if rt is None or rp is None:
        return False
    try:
        dx, dy = float(rt.X) - float(rp.X), float(rt.Y) - float(rp.Y)
        yaw = float(rt.yaw)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(yaw):
        return False
    hyp = float(np.hypot(dx, dy))
    if hyp < 0.5:  # barely moving: no direction to oppose
        return False

    def _keys_oppose(row) -> bool:
        if row is None:
            return False
        held = [k for k in STRAFE_DIRS if bool(getattr(row, k, False))]
        if not held:
            return False
        for k in held:
            a = np.radians(yaw + STRAFE_DIRS[k])
            if (np.cos(a) * dx + np.sin(a) * dy) / hyp >= -0.5:
                return False  # this key does not oppose the motion
        return True

    return _keys_oppose(rt) or _keys_oppose(rp)


def _duck_amount(r) -> float:
    """Clamped 0..1 duck_amount of a ticks-table row (0 when unknown)."""
    v = getattr(r, "duck_amount", np.nan)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if pd.isna(v):
        return 0.0
    return min(max(v, 0.0), 1.0)


def angle_err(a, t) -> float:
    """
    Angle (deg) between player a's view direction and player t's head.

    Approximate heights: eyes = Z+64, head = Z+62; both drop by ~18u with
    duck_amount when the parser provides it (crouch-aware). If recoil punch
    angles are known for `a` they are ADDED to the view (bullet direction
    ~= eye angles + aim punch), so `err` reflects where the bullet could
    actually go, not just the pre-recoil crosshair.
    """

    if a is None or t is None:
        return float("nan")
    eye = a.Z + EYE_STAND - DUCK_DROP * _duck_amount(a)
    head = t.Z + HEAD_STAND - DUCK_DROP * _duck_amount(t)
    d = np.array([t.X - a.X, t.Y - a.Y, head - eye])
    n = np.linalg.norm(d)
    if n == 0:
        return float("nan")
    pp = getattr(a, "punch_pitch", 0.0)
    py = getattr(a, "punch_yaw", 0.0)
    pp = 0.0 if pd.isna(pp) else float(pp)
    py = 0.0 if pd.isna(py) else float(py)
    p, y = np.radians(a.pitch + pp), np.radians(a.yaw + py)
    v = np.array([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), -np.sin(p)])
    return float(np.degrees(np.arccos(np.clip(v @ d / n, -1, 1))))


def head_ang(dist) -> float | np.ndarray:
    """
    Angular size (deg) of the head (HEAD_WIDTH wide) at `dist` units.
    Scalar in, scalar out; Series/array in, array out; NaN for dist <= 0.
    """

    d = np.asarray(dist, dtype=float)
    scalar = d.ndim == 0
    d = np.atleast_1d(d).copy()
    out = np.full_like(d, np.nan)
    ok = np.isfinite(d) & (d > 0)
    out[ok] = np.degrees(2.0 * np.arctan((HEAD_WIDTH / 2.0) / d[ok]))
    return float(out[0]) if scalar else out


def _speed(r) -> float:
    """Speed of a ticks-table row (precomputed by _add_speed)."""
    if r is None:
        return float("nan")
    v = getattr(r, "speed", np.nan)
    try:
        return float(v) if pd.notna(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


# -------------------------------------------------------------------- load
COMPRESSED_EXTS = {".bz2", ".gz", ".zst", ".zip"}


def _zstandard_module():
    try:
        import zstandard  # optional dependency, only needed for .zst
    except ImportError as e:
        raise ValueError(
            "The .zst demo needs the 'zstandard' package: pip install zstandard"
        ) from e
    return zstandard


def decompressed_demo(path: str) -> str:
    """
    Path to a plain .dem for `path`, decompressing transparently.

    Demos are often delivered compressed: Valve serves .dem.bz2, FACEIT
    .dem.gz, tournaments .zip. Supported: .bz2 / .gz / .zip (stdlib) and
    .zst (needs the `zstandard` package). Inside a .zip the first *.dem
    member (alphabetically) is used. The decompressed copy is cached in
    the system temp dir under a name derived from source path + size +
    mtime, so re-opening the same archive does not decompress again.
    Anything else is returned unchanged.
    """

    p = os.path.realpath(path)
    ext = os.path.splitext(p)[1].lower()
    if ext not in COMPRESSED_EXTS:
        return p
    stem = os.path.basename(p)
    stem = stem[: -len(ext)] if stem.lower().endswith(ext) else stem
    try:
        st = os.stat(p)
    except OSError as e:
        raise ValueError(f"Cannot read demo file: {p}") from e
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem)[:80]
    target = os.path.join(
        tempfile.gettempdir(),
        f"cs2_aimaudit_{safe}_{st.st_size}_{int(st.st_mtime)}.dem",
    )
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target  # cached from an earlier run
    tmp = target + ".part"
    if ext == ".bz2":
        with bz2.open(p, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
    elif ext == ".gz":
        with gzip.open(p, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
    elif ext == ".zst":
        zstd = _zstandard_module()
        with open(p, "rb") as src, open(tmp, "wb") as dst:
            with zstd.ZstdDecompressor().stream_reader(src) as rdr:
                shutil.copyfileobj(rdr, dst)
    else:  # .zip
        with zipfile.ZipFile(p) as z:
            members = sorted(n for n in z.namelist() if n.lower().endswith(".dem"))
            if not members:
                raise ValueError(
                    f"No .dem file inside the archive: {os.path.basename(p)}"
                )
            with z.open(members[0]) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ValueError(f"Decompressed demo is empty: {os.path.basename(p)}")
    os.replace(tmp, target)  # atomic: no half-written cache on crash
    return target


# ------------------------------------------------------------ app state
# Small local state next to nothing else: recent demo paths and the last
# selected player. Everything stays on this machine; if the file is not
# writable the app simply does not remember anything.
STATE_PATH = os.path.join(os.path.expanduser("~"), ".cs2-aimaudit", "state.json")
RECENT_MAX = 10


def load_state() -> dict:
    """Load the local state file; {} when missing or unreadable."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(d: dict) -> None:
    """Atomically save the state file; silent when the home dir is read-only."""
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def remember_demo(path: str, state: dict) -> dict:
    """Move `path` to the front of state['recent_demos'] (capped at RECENT_MAX)."""
    rec = [p for p in state.get("recent_demos", []) if isinstance(p, str) and p != path]
    rec.insert(0, path)
    state["recent_demos"] = rec[:RECENT_MAX]
    return state


def tickrate_from_header(header: dict) -> int:
    """
    Tick rate from the demo header: playback_ticks / playback_time.

    The previously hard-coded 64 is wrong for 128-tick demos (FACEIT and
    community servers): every speed and every seconds<->ticks conversion
    would be off by 2x. Falls back to 64 when the header is missing or
    implausible (corrupt header, partial demo).
    """

    try:
        ticks = float(header.get("playback_ticks"))
        secs = float(header.get("playback_time"))
        if ticks > 0 and secs > 0:
            tr = ticks / secs
            if 16.0 <= tr <= 512.0:  # sane window: 32 / 64 / 128 tick all fit
                return int(round(tr))
    except (TypeError, ValueError):
        pass
    return TICKRATE


def _parse_round_sides(
    parser, frames: list[pd.DataFrame], flookup: dict, name_to_sid: dict
) -> dict[int, dict[str, str]]:
    """
    Per-round player sides: team_num read at one tick per round.

    parse_player_info().team_number is a single static value (the team at
    the END of the match), so after the halftime swap - and again in every
    overtime half - the T/CT labels are wrong for half of the rounds. The
    enemy/teammate logic is unaffected (teammates share the static label),
    but anything shown per side needs the per-round value. Returns {} when
    the prop is unavailable (old demos / old parser).
    """

    per_round: dict[int, int] = {}  # round(1-based) -> representative tick
    for df in frames:
        if (
            not len(df)
            or "total_rounds_played" not in df.columns
            or "tick" not in df.columns
        ):
            continue
        r = _round_col(df)
        tk = pd.to_numeric(df["tick"], errors="coerce")
        ok = (r >= 0) & tk.notna()
        for ri, tick in zip(r[ok], tk[ok]):
            ri = int(ri) + 1
            t = int(tick)
            if ri not in per_round or t < per_round[ri]:
                per_round[ri] = t
    if not per_round:
        return {}
    try:
        sd = parser.parse_ticks(SIDE_PROPS, ticks=sorted(per_round.values()))
    except Exception:  # noqa: BLE001 - unknown prop on old parser versions
        return {}
    if sd is None or "team_num" not in getattr(sd, "columns", []) or not len(sd):
        return {}
    sd = sd.copy()
    sd["steamid"] = fix_ids(
        sd["steamid"],
        flookup,
        sd["name"] if "name" in sd.columns else None,
        name_to_sid,
    )
    tick_to_round = {t: r for r, t in per_round.items()}
    out: dict[int, dict[str, str]] = {}
    for row in sd.itertuples(index=False):
        ri = tick_to_round.get(int(row.tick))
        sid = getattr(row, "steamid", NA)
        if ri is None or sid in (NA, None):
            continue
        side = TEAMS.get(_safe_int(row.team_num), "?")
        out.setdefault(ri, {})[sid] = side
    return out


@dataclass
class Demo:
    path: str
    header: dict
    players: pd.DataFrame  # sid, name, team
    deaths: pd.DataFrame
    fires: pd.DataFrame  # gun shots only, all players, warmup excluded
    hurts: pd.DataFrame  # gun damage to enemies only, warmup excluded
    ticks: pd.DataFrame  # POS (+punch/alive) of all players at every gun-fire
    #                      tick AND tick-1; `speed` is our own position delta
    warmup_dropped: dict = field(default_factory=dict)
    tickrate: int = TICKRATE  # from the demo header (fallback: 64)
    sides: dict = field(default_factory=dict)  # round(1-based) -> sid -> T/CT

    def side_at(self, round_no: int, sid: str) -> str:
        """Player's side in a given round (falls back to the static team)."""
        s = self.sides.get(int(round_no), {}).get(sid)
        if s and s != "?":
            return s
        return self.teams.get(sid, "?")

    def side_history(self, sid: str) -> list[str]:
        """Sides played, in round order, collapsed to changes (['T', 'CT'])."""
        out: list[str] = []
        for r in sorted(self.sides):
            s = self.sides[r].get(sid)
            if s and s != "?" and (not out or out[-1] != s):
                out.append(s)
        return out or [self.teams.get(sid, "?")]

    @cached_property
    def rows(self) -> dict:
        return {(int(r.tick), r.steamid): r for r in self.ticks.itertuples(index=False)}

    @cached_property
    def names(self) -> dict[str, str]:
        return dict(zip(self.players["sid"], self.players["name"]))

    @cached_property
    def teams(self) -> dict[str, str]:
        return dict(zip(self.players["sid"], self.players["team"]))


def load_demo(path: str, progress=None) -> Demo:
    say = progress or (lambda *a, **k: None)
    p = DemoParser(path)

    say("players")
    info = p.parse_player_info()
    flookup, name_to_sid = build_id_lookup(info)
    players = pd.DataFrame(
        {
            "sid": info["steamid"]
            .astype("object")
            .map(lambda x: str(int(x)) if pd.notna(x) else NA),
            "name": info["name"].astype("object"),
            "team": info["team_number"]
            .astype("object")
            .map(lambda x: TEAMS.get(int(x), "?") if pd.notna(x) else "?"),
        }
    )
    players = (
        players[players["sid"].isin(["0", NA]) == False]
        .drop_duplicates("sid")
        .reset_index(drop=True)
    )  # noqa: E712
    try:
        header = p.parse_header()
    except Exception:  # noqa: BLE001
        header = {}

    say("events")
    ev = dict(
        p.parse_events(
            ["player_death", "weapon_fire", "player_hurt"],
            other=["total_rounds_played", "is_warmup_period"],
        )
    )
    deaths = ev.get("player_death", pd.DataFrame())
    fires = ev.get("weapon_fire", pd.DataFrame())
    hurts = ev.get("player_hurt", pd.DataFrame())
    if fires.empty:
        raise ValueError("no_events")
    for df, pairs in (
        (
            deaths,
            (("attacker_steamid", "attacker_name"), ("user_steamid", "user_name")),
        ),
        (fires, (("user_steamid", "user_name"),)),
        (
            hurts,
            (("attacker_steamid", "attacker_name"), ("user_steamid", "user_name")),
        ),
    ):
        for c, nc in pairs:
            if c in df:
                df[c] = fix_ids(
                    df[c],
                    flookup,
                    df[nc] if nc in df.columns else None,
                    name_to_sid,
                )

    # warmup shots/kills/damage are not part of the match -> drop them
    warmup_dropped: dict[str, int] = {}
    for name, df in (("deaths", deaths), ("fires", fires), ("hurts", hurts)):
        if "is_warmup_period" in df.columns and len(df):
            mask = df["is_warmup_period"].fillna(False).astype(bool)
            warmup_dropped[name] = int(mask.sum())
            if mask.any():
                if name == "deaths":
                    deaths = deaths[~mask].copy()
                elif name == "fires":
                    fires = fires[~mask].copy()
                else:
                    hurts = hurts[~mask].copy()

    teams = dict(zip(players["sid"], players["team"]))
    # NOTE: boolean-mask filtering an ALREADY-empty DataFrame drops all its
    # columns in pandas ((0, n) -> (0, 0)), so every filter is guarded.
    if not fires.empty:
        fires = fires[fires["weapon"].map(is_gun)].copy()
        fires["weapon"] = fires["weapon"].map(norm_weapon)
    if fires.empty:
        raise ValueError("no_events")
    if len(hurts) and "attacker_steamid" in hurts.columns:
        hurts = hurts[
            hurts["weapon"].map(is_gun)
            & (hurts["attacker_steamid"] != NA)
            & (hurts["attacker_steamid"] != hurts["user_steamid"])
        ].copy()
        same_team = hurts["attacker_steamid"].map(teams).eq(
            hurts["user_steamid"].map(teams)
        ) & hurts["attacker_steamid"].isin(teams)
        hurts = hurts[~same_team].copy()
        hurts["weapon"] = hurts["weapon"].map(norm_weapon)
        hurts["hitgroup"] = hurts["hitgroup"].map(hg_key)

    # per-round sides (halftime swap), see _parse_round_sides
    sides = _parse_round_sides(p, [deaths, fires, hurts], flookup, name_to_sid)

    # Fire ticks AND tick-1: the exact one-tick position delta gives the real
    # speed at the moment of each shot without the parser's velocity props.
    fticks = {int(t) for t in fires["tick"]}
    ticks_list = sorted(fticks | {t - 1 for t in fticks if t > 0})
    say("ticks", n=len(fires))
    td = None
    last_err: Exception | None = None
    for props in (
        POS + BTN_NAMED + ["buttons"] + PUNCH_PROPS + DUCK_PROPS + ALIVE_PROPS,
        POS + PUNCH_PROPS + DUCK_PROPS + ALIVE_PROPS,
        POS + PUNCH_PROPS + ALIVE_PROPS,
        POS + ALIVE_PROPS,
        POS,
    ):
        try:
            td = p.parse_ticks(props, ticks=ticks_list)
            break
        except Exception as e:  # noqa: BLE001 - old demos / unknown props
            last_err = e
    if td is None:
        raise last_err  # type: ignore[misc]
    td["steamid"] = fix_ids(
        td["steamid"],
        flookup,
        td["name"] if "name" in td.columns else None,
        name_to_sid,
    )
    td = td.drop_duplicates(["tick", "steamid"]).reset_index(drop=True)
    td = _decode_buttons(td)
    td = _norm_alive(td)
    td = _extract_punch(td)
    tickrate = tickrate_from_header(header)
    td = _add_speed(td, tickrate)
    return Demo(
        path, header, players, deaths, fires, hurts, td, warmup_dropped, tickrate, sides
    )


# ----------------------------------------------------------------- metrics
def _hurts_of(demo: Demo, me: str, hurts: pd.DataFrame | None = None) -> pd.DataFrame:
    """hurts rows caused by `me`; safe when the player_hurt event is absent.
    `hurts` overrides demo.hurts (used by the global filter)."""
    h = demo.hurts if hurts is None else hurts
    if h is not None and "attacker_steamid" in getattr(h, "columns", []) and len(h):
        return h[h["attacker_steamid"] == me]
    return pd.DataFrame()


def _nearest_enemy_dist(demo: Demo, shots: pd.DataFrame) -> pd.Series:
    """
    Distance (units) from each shot to the nearest LIVING enemy.

    Positions come from demo.ticks (every player at every fire tick). An enemy
    is treated as dead if they have a death event in the same round at an
    earlier tick (a death at the same tick as the shot still counts as alive:
    that may be the very victim of this shot). Unknown teams are skipped.
    Approximation; NaN when no living enemy was found at that tick.
    """

    n = len(shots)
    res = pd.Series(np.nan, index=shots.index, dtype=float)
    if n == 0:
        return res
    pos = demo.ticks[["tick", "steamid", "X", "Y", "Z"]].rename(
        columns={"steamid": "sid_o"}
    )
    if pos.empty:
        return res
    base = shots[["tick", "sid", "round", "X", "Y", "Z"]].copy()
    base["shot_ix"] = np.arange(n)
    cand = base.merge(pos, on="tick", how="left", suffixes=("", "_o"))
    teams = demo.teams
    my_t = cand["sid"].map(teams)
    o_t = cand["sid_o"].map(teams)
    keep = (
        (cand["sid"] != cand["sid_o"])
        & my_t.notna()
        & o_t.notna()
        & (my_t != o_t)
        & (my_t != "?")
    )
    cand = cand[keep]
    if cand.empty:
        return res
    d = demo.deaths
    if len(d) and "user_steamid" in d.columns:
        dth = pd.DataFrame(
            {
                "sid_o": d["user_steamid"].to_numpy(),
                "round": (_round_col(d) + 1).to_numpy(),
                "dtick": d["tick"].astype(int).to_numpy(),
            }
        )
        dth = dth[dth["sid_o"] != NA]
        if len(dth):
            cand = cand.merge(dth, on=["sid_o", "round"], how="left")
            cand = cand[cand["dtick"].isna() | (cand["dtick"] >= cand["tick"])]
    if cand.empty:
        return res
    cand = cand.assign(
        _d=np.sqrt(
            (cand["X"] - cand["X_o"]) ** 2
            + (cand["Y"] - cand["Y_o"]) ** 2
            + (cand["Z"] - cand["Z_o"]) ** 2
        )
    )
    dg = cand.groupby("shot_ix")["_d"].min()
    res.iloc[dg.index.to_numpy()] = dg.to_numpy()
    return res


def _match_hits(shots: pd.DataFrame, hurts: pd.DataFrame) -> np.ndarray:
    """
    hit[i] = this shot damaged an enemy.

    Each hurt row is attributed to the shooter's LATEST same-weapon shot at
    tick <= hurt_tick within HIT_WINDOW ticks (weapon_fire and player_hurt
    usually share the tick; the window absorbs off-by-one/two). Taking the
    latest candidate - unlike a naive +-window set lookup - never flags an
    earlier shot of the same burst. Shotgun pellets / wallbangs mark the same
    shot once.
    """

    hit = np.zeros(len(shots), dtype=bool)
    if not len(hurts) or not {"attacker_steamid", "weapon", "tick"}.issubset(
        hurts.columns
    ):
        return hit
    by_key: dict[tuple, list[tuple[int, int]]] = {}
    for i, (sid, w, t) in enumerate(
        zip(shots["sid"], shots["weapon"], shots["tick"].astype(int))
    ):
        by_key.setdefault((sid, w), []).append((int(t), i))
    for lst in by_key.values():
        lst.sort()
    for sid, w, th in zip(
        hurts["attacker_steamid"], hurts["weapon"], hurts["tick"].astype(int)
    ):
        lst = by_key.get((sid, w))
        if not lst:
            continue
        j = bisect.bisect_right([t for t, _ in lst], int(th)) - 1
        if j >= 0 and int(th) - lst[j][0] <= HIT_WINDOW:
            hit[lst[j][1]] = True
    return hit


def shots_table(demo: Demo) -> pd.DataFrame:
    """
    One row per gun shot: tick, sid, weapon, round, speed, position,
    dist (nearest living enemy, approx) and hit (damaged an enemy, approx:
    a hurt event within HIT_WINDOW ticks after the shot; shotgun pellets count once).
    """

    cols = ["tick", "user_steamid", "weapon"] + (
        ["total_rounds_played"] if "total_rounds_played" in demo.fires.columns else []
    )
    s = demo.fires[cols].rename(columns={"user_steamid": "sid"})
    s["round"] = _round_col(s) + 1
    s = s.drop(columns=["total_rounds_played"], errors="ignore")
    t = demo.ticks[["tick", "steamid", "speed", "X", "Y", "Z"]].rename(
        columns={"steamid": "sid"}
    )
    out = s.merge(t, on=["tick", "sid"], how="left").reset_index(drop=True)
    # speed on the tick BEFORE the shot (stop detection: 'stopped' = crossed
    # under the threshold exactly on the shot tick)
    prev = demo.ticks[["tick", "steamid", "speed"]].copy()
    prev["tick"] = prev["tick"] + 1
    out = out.merge(
        prev.rename(columns={"steamid": "sid", "speed": "speed_prev"}),
        on=["tick", "sid"],
        how="left",
    )
    rows = demo.rows
    out["counter_strafe"] = [
        _counter_strafe_tap(rows.get((int(t), s)), rows.get((int(t) - 1, s)))
        for t, s in zip(out["tick"], out["sid"])
    ]
    out["dist"] = _nearest_enemy_dist(demo, out)
    out["hit"] = _match_hits(out, demo.hurts)
    out["side"] = [
        demo.side_at(int(r), sid) for r, sid in zip(out["round"], out["sid"])
    ]
    out["weapon_class"] = out["weapon"].map(weapon_class)
    return out


def add_stop_quality(shots: pd.DataFrame, thr: float) -> pd.DataFrame:
    """
    Adds `stop`: what the player's movement did at the shot tick.

    'stopped'  - speed crossed under `thr` exactly on this tick (the
                 counter-strafe moment: prev tick above, this tick below),
    'standing' - already slow at this tick (and the previous one),
    'moving'   - still fast at the shot,
    ''         - speed unknown.
    """

    out = shots.copy()
    sp = out["speed"] if "speed" in out.columns else pd.Series(np.nan, index=out.index)
    spp = (
        out["speed_prev"]
        if "speed_prev" in out.columns
        else pd.Series(np.nan, index=out.index)
    )
    stop = pd.Series("", index=out.index, dtype=object)
    stop[sp.notna() & (sp > thr)] = "moving"
    slow_now = sp.notna() & (sp <= thr)
    stop[slow_now & (spp > thr)] = "stopped"
    stop[slow_now & ~(spp > thr)] = "standing"  # incl. unknown prev: slow now
    out["stop"] = stop
    return out


def add_risk(
    shots: pd.DataFrame,
    thr: float,
    safe: float = SAFE_DIST,
    strict: float = STRICT_DIST,
) -> pd.DataFrame:
    """
    Adds `allowed_speed` and `risky` columns. A shot is risky when its speed
    is above the distance-aware limit. Unknown speed/distance -> not risky.
    """

    out = shots.copy()
    out["allowed_speed"] = allowed_speed(out["dist"], thr, safe, strict)
    out["risky"] = (out["speed"] > out["allowed_speed"]).fillna(False)
    return out


def build_duels(
    demo: Demo, window_s: float = 3.0, aim_deg: float = 30.0
) -> pd.DataFrame:
    """
    One row per enemy kill. 'Duel' = the shots both players fired in the
    window before the kill *aimed at each other*: a shot counts only when
    the shooter's crosshair was within `aim_deg` degrees of that opponent
    (removes the noise of shooting someone else inside the window). Shots
    without position data keep the old keep-them behaviour. `low_conf` marks
    duels where either player also aimed at a different enemy in the window.
    """

    win = int(window_s * demo.tickrate)
    rows, names, teams = demo.rows, demo.names, demo.teams
    fires_by = {
        k: g["tick"].astype(int).to_numpy()
        for k, g in demo.fires.groupby("user_steamid")
    }
    enemies_of = {
        sid: [
            o
            for o in names
            if o != sid and teams.get(o) not in (None, "?") and teams.get(o) != teams.get(sid)
        ]
        for sid in names
    }

    def _aimed_at(ts: int, sh: str, opp: str) -> bool:
        ra, ro = rows.get((ts, sh)), rows.get((ts, opp))
        if ra is None or ro is None:
            return True  # no data -> keep the shot (old behaviour)
        e = angle_err(ra, ro)
        return not np.isfinite(e) or e <= aim_deg

    def _aimed_elsewhere(ts: int, sh: str, opp: str) -> bool:
        ra = rows.get((ts, sh))
        if ra is None:
            return False
        for other in enemies_of.get(sh, []):
            if other == opp:
                continue
            ro = rows.get((ts, other))
            if ro is None:
                continue
            e = angle_err(ra, ro)
            if np.isfinite(e) and e <= aim_deg:
                return True
        return False

    d = demo.deaths
    if not len(d) or "attacker_steamid" not in d.columns:
        return pd.DataFrame()
    d = d[
        d["attacker_steamid"].isin(names)
        & d["user_steamid"].isin(names)
        & (d["attacker_steamid"] != d["user_steamid"])
    ]
    out = []
    for r in d.itertuples():
        k, v, t = r.attacker_steamid, r.user_steamid, int(r.tick)
        if teams.get(k) == teams.get(v):
            continue

        def in_win(s):
            a = fires_by.get(s, np.array([], dtype=int))
            return a[(a > t - win) & (a <= t)]

        kw_raw, vw_raw = in_win(k), in_win(v)
        kw = np.array([int(ts) for ts in kw_raw if _aimed_at(int(ts), k, v)], dtype=int)
        vw = np.array([int(ts) for ts in vw_raw if _aimed_at(int(ts), v, k)], dtype=int)
        kf = int(kw.min()) if len(kw) else None
        vf = int(vw.min()) if len(vw) else None
        low_conf = any(_aimed_elsewhere(int(ts), k, v) for ts in kw_raw) or any(
            _aimed_elsewhere(int(ts), v, k) for ts in vw_raw
        )

        def _duel_dist(r, fa, fb, f_tick) -> float:
            """Distance for head-size maths: the game value, else positions."""
            d = getattr(r, "distance", np.nan)
            if pd.notna(d) and d > 0:
                return float(d)
            a, b = rows.get((f_tick, fa)), rows.get((f_tick, fb))
            if a is not None and b is not None:
                return float(
                    np.sqrt((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2 + (a.Z - b.Z) ** 2)
                )
            return float("nan")

        k_err = (
            angle_err(rows.get((kf, k)), rows.get((kf, v)))
            if kf is not None
            else np.nan
        )
        v_err = (
            angle_err(rows.get((vf, v)), rows.get((vf, k)))
            if vf is not None
            else np.nan
        )
        k_ang = head_ang(_duel_dist(r, k, v, kf))
        v_ang = head_ang(_duel_dist(r, v, k, vf))
        if kf is None and vf is None:
            first = "none"
        elif vf is None or (kf is not None and kf < vf):
            first = "killer"
        elif kf is None or vf < kf:
            first = "victim"
        else:
            first = "simultaneous"
        start = (
            min(x for x in (kf, vf) if x is not None)
            if (kf is not None or vf is not None)
            else t
        )
        out.append(
            {
                "round": _safe_int(getattr(r, "total_rounds_played", 0)) + 1,
                "tick": t,
                "killer": k,
                "victim": v,
                "killer_side": demo.side_at(
                    _safe_int(getattr(r, "total_rounds_played", 0)) + 1, k
                ),
                "victim_side": demo.side_at(
                    _safe_int(getattr(r, "total_rounds_played", 0)) + 1, v
                ),
                "weapon": norm_weapon(r.weapon),
                "weapon_class": weapon_class(r.weapon),
                "headshot": bool(getattr(r, "headshot", False)),
                "distance": getattr(r, "distance", np.nan),
                "noscope": bool(getattr(r, "noscope", False)),
                "thrusmoke": bool(getattr(r, "thrusmoke", False)),
                "attackerblind": bool(getattr(r, "attackerblind", False)),
                "penetrated": int(getattr(r, "penetrated", 0) or 0) > 0,
                "first": first,
                "killer_shots": len(kw),
                "victim_shots": len(vw),
                "killer_shots_raw": len(kw_raw),
                "victim_shots_raw": len(vw_raw),
                "low_conf": bool(low_conf),
                "killer_first_tick": kf,
                "victim_first_tick": vf,
                "duration_s": round((t - start) / demo.tickrate, 2),
                "killer_speed": _speed(rows.get((kf, k))) if kf is not None else np.nan,
                "victim_speed": _speed(rows.get((vf, v))) if vf is not None else np.nan,
                "killer_err": k_err,
                "victim_err": v_err,
                "killer_err_hw": (
                    float(k_err / k_ang)
                    if np.isfinite(k_err) and np.isfinite(k_ang) and k_ang > 0
                    else np.nan
                ),
                "victim_err_hw": (
                    float(v_err / v_ang)
                    if np.isfinite(v_err) and np.isfinite(v_ang) and v_ang > 0
                    else np.nan
                ),
                # crosshair inside the head box (within half a head width of
                # its center) at the moment of the first shot
                "killer_would_head": bool(
                    np.isfinite(k_err) and np.isfinite(k_ang) and k_err <= k_ang / 2
                ),
                "victim_would_head": bool(
                    np.isfinite(v_err) and np.isfinite(v_ang) and v_err <= v_ang / 2
                ),
            }
        )
    return pd.DataFrame(out)


def duels_for(duels: pd.DataFrame, me: str) -> pd.DataFrame:
    """Duels from one player's perspective (kills and deaths)."""
    parts = []
    for role, mine, opp in (
        ("kill", "killer", "victim"),
        ("death", "victim", "killer"),
    ):
        df = duels[duels[mine] == me]
        if df.empty:
            continue
        parts.append(
            pd.DataFrame(
                {
                    "round": df["round"],
                    "tick": df["tick"],
                    "result": role,
                    "opponent": df[opp],
                    "weapon": df["weapon"],
                    "weapon_class": df["weapon_class"],
                    "my_side": df[f"{mine}_side"],
                    "opp_side": df[f"{opp}_side"],
                    "headshot": df["headshot"],
                    "distance": df["distance"],
                    "first": df["first"].map(
                        lambda f: (
                            f
                            if f in ("none", "simultaneous")
                            else ("me" if f == mine else "opponent")
                        )
                    ),
                    "my_shots": df[f"{mine}_shots"],
                    "opp_shots": df[f"{opp}_shots"],
                    "my_shots_raw": df[f"{mine}_shots_raw"],
                    "opp_shots_raw": df[f"{opp}_shots_raw"],
                    "low_conf": df["low_conf"],
                    "duration_s": df["duration_s"],
                    "my_speed": df[f"{mine}_speed"],
                    "my_err": df[f"{mine}_err"],
                    "opp_speed": df[f"{opp}_speed"],
                    "opp_err": df[f"{opp}_err"],
                    "my_err_hw": df[f"{mine}_err_hw"],
                    "opp_err_hw": df[f"{opp}_err_hw"],
                    "my_would_head": df[f"{mine}_would_head"],
                    "opp_would_head": df[f"{opp}_would_head"],
                    "my_first_tick": df[f"{mine}_first_tick"],
                    "noscope": df["noscope"],
                    "thrusmoke": df["thrusmoke"],
                    "attackerblind": df["attackerblind"],
                    "penetrated": df["penetrated"],
                }
            )
        )
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts).sort_values("tick").reset_index(drop=True)


def player_summary(
    demo: Demo,
    duels: pd.DataFrame,
    shots: pd.DataFrame,
    thr: float,
    safe: float = SAFE_DIST,
    strict: float = STRICT_DIST,
    hurts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    res = []
    for p in demo.players.itertuples():
        sh = shots[shots["sid"] == p.sid]
        hi = _hurts_of(demo, p.sid, hurts)
        dd = duels_for(duels, p.sid) if len(duels) else pd.DataFrame()
        kills = int((duels["killer"] == p.sid).sum()) if len(duels) else 0
        deaths = int((duels["victim"] == p.sid).sum()) if len(duels) else 0
        kd = dd[dd["result"] == "kill"] if len(dd) else dd
        fs = dd[dd["first"].isin(["me", "opponent"])] if len(dd) else dd
        known = (
            sh["dist"].notna() & sh["speed"].notna()
            if len(sh)
            else pd.Series(dtype=bool)
        )
        if len(sh) and known.any():
            alw = allowed_speed(sh.loc[known, "dist"], thr, safe, strict)
            risky = round((sh.loc[known, "speed"].to_numpy() > alw).mean() * 100, 1)
        else:
            risky = np.nan
        res.append(
            {
                "player": p.name,
                "sid": p.sid,
                "team": p.team,
                "sides": "→".join(demo.side_history(p.sid)),
                "kills": kills,
                "deaths": deaths,
                "shots": len(sh),
                "hits": len(hi),
                "accuracy": round(len(hi) / len(sh) * 100, 1) if len(sh) else np.nan,
                "hs_hits": (
                    round((hi["hitgroup"] == "head").mean() * 100, 1)
                    if len(hi)
                    else np.nan
                ),
                "hs_kills": (
                    round(kd["headshot"].mean() * 100, 1) if len(kd) else np.nan
                ),
                "moving": (
                    round((sh["speed"] > thr).mean() * 100, 1) if len(sh) else np.nan
                ),
                "risky": risky,
                "first_shot": (
                    round((fs["first"] == "me").mean() * 100, 1) if len(fs) else np.nan
                ),
                "aim_err": (
                    round(dd["my_err"].median(), 1)
                    if len(dd) and dd["my_err"].notna().any()
                    else np.nan
                ),
                "duration": round(dd["duration_s"].median(), 2) if len(dd) else np.nan,
            }
        )
    return (
        pd.DataFrame(res)
        .sort_values(["kills", "deaths"], ascending=[False, True])
        .reset_index(drop=True)
    )


def weapon_stats(
    demo: Demo,
    shots: pd.DataFrame,
    me: str,
    thr: float,
    safe: float = SAFE_DIST,
    strict: float = STRICT_DIST,
    hurts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    sh = shots[shots["sid"] == me].copy()
    if len(sh):
        sh["_a"] = allowed_speed(sh["dist"], thr, safe, strict)
        sh["_r"] = (sh["speed"] > sh["_a"]).fillna(False)
    g = sh.groupby("weapon").agg(
        shots=("tick", "size"),
        median_speed=("speed", "median"),
        median_dist=("dist", "median"),
        moving=("speed", lambda s: (s > thr).mean() * 100),
    )
    if len(sh):
        g["risky"] = sh.groupby("weapon")["_r"].mean() * 100
    hi = _hurts_of(demo, me, hurts)
    h = (
        hi.groupby("weapon").agg(
            hits=("tick", "size"), hs=("hitgroup", lambda s: (s == "head").mean() * 100)
        )
        if len(hi) and "weapon" in hi.columns
        else pd.DataFrame(
            {"hits": pd.Series(dtype=float), "hs": pd.Series(dtype=float)}
        )
    )
    out = g.join(h, how="left").fillna({"hits": 0})
    out["accuracy"] = out["hits"] / out["shots"] * 100
    return out.sort_values("shots", ascending=False).round(1).reset_index()


def hitgroup_counts(
    demo: Demo, me: str, hurts: pd.DataFrame | None = None
) -> pd.Series:
    hi = _hurts_of(demo, me, hurts)
    if not len(hi) or "hitgroup" not in hi.columns:
        return pd.Series(dtype="int64")
    return hi["hitgroup"].value_counts()


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


# ---------------------------------------------------------- data quality
def data_checks(demo: Demo, shots: pd.DataFrame, duels: pd.DataFrame) -> list[dict]:
    """
    Heuristic checks for data anomalies. Each dict: id, level
    ('ok'|'warn'|'anomaly'), n, pct (+ extras). The UI translates ids.
    """

    def lvl(frac: float, warn: float, anom: float) -> str:
        return "anomaly" if frac >= anom else "warn" if frac >= warn else "ok"

    checks: list[dict] = []

    # 1. steamids that failed to resolve -> stats silently missing
    n_na, total_ev = 0, 0
    for df, cols in (
        (demo.deaths, ["attacker_steamid", "user_steamid"]),
        (demo.fires, ["user_steamid"]),
        (demo.hurts, ["attacker_steamid", "user_steamid"]),
    ):
        for c in cols:
            if c in df and len(df):
                n_na += int((df[c] == NA).sum())
                total_ev += len(df)
    f = n_na / max(total_ev, 1)
    checks.append(
        {"id": "na_ids", "level": lvl(f, 0.05, 0.20), "n": n_na, "pct": f * 100}
    )

    # 2. shots from players outside the player table (bots / parse hiccup)
    n = int((~shots["sid"].isin(demo.names)).sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "unknown_shooters", "level": lvl(f, 0.001, 0.05), "n": n, "pct": f * 100}
    )

    # 3. shots without shooter position (dist unknown)
    n = (
        int(shots[["X", "Y"]].isna().any(axis=1).sum())
        if len(shots) and {"X", "Y"}.issubset(shots.columns)
        else 0
    )
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "missing_pos", "level": lvl(f, 0.001, 0.10), "n": n, "pct": f * 100}
    )

    # 3b. shots with unknown speed (no t-1 tick / death / teleport right
    # before the shot). Better an honest NaN than a garbage number.
    n = int(shots["speed"].isna().sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "no_speed", "level": lvl(f, 0.05, 0.20), "n": n, "pct": f * 100}
    )

    # 4. shots without a living enemy nearby (dist unknown)
    n = int(shots["dist"].isna().sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "no_dist", "level": lvl(f, 0.05, 0.25), "n": n, "pct": f * 100}
    )

    # 5. physically impossible speeds
    n = (
        int((shots["speed"] > IMPOSSIBLE_SPEED).sum())
        if len(shots) and shots["speed"].notna().any()
        else 0
    )
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "fast_speeds", "level": lvl(f, 0.0001, 0.02), "n": n, "pct": f * 100}
    )

    # 6. shots without a round number -> alive-filter unreliable for them
    n = int((shots["round"] <= 0).sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append({"id": "round0", "level": lvl(f, 0.02, 0.20), "n": n, "pct": f * 100})

    # 7. implausible duel distances
    if len(duels) and duels["distance"].notna().any():
        dist = pd.to_numeric(duels["distance"], errors="coerce")
        n = int((dist.isna() | (dist <= 0) | (dist > MAX_DUEL_DIST)).sum())
        f = n / len(duels)
        checks.append(
            {"id": "duel_dist", "level": lvl(f, 0.02, 0.10), "n": n, "pct": f * 100}
        )
    else:
        checks.append({"id": "duel_dist", "level": "ok", "n": 0, "pct": 0.0})

    # 8. aim-error sanity (angle convention / offsets)
    if len(duels) and duels["killer_err"].notna().any():
        med = float(duels["killer_err"].median())
        n = int((duels["killer_err"] > 45).sum())
        level = "anomaly" if med > 25 else "warn" if med > 12 else "ok"
        checks.append({"id": "angles", "level": level, "n": n, "med": med})
    else:
        checks.append({"id": "angles", "level": "ok", "n": 0, "med": float("nan")})

    # 10. per-round side data (halftime swap): without it the shown side
    # silently falls back to the end-of-match team for those rounds
    if len(shots) and demo.sides:
        n = int((~shots["round"].isin(set(demo.sides))).sum())
        f = n / len(shots)
        checks.append(
            {"id": "sides", "level": lvl(f, 0.05, 0.40), "n": n, "pct": f * 100}
        )
    else:
        n = len(shots)
        checks.append(
            {
                "id": "sides",
                "level": "warn" if n else "ok",
                "n": n,
                "pct": (100.0 if n else 0.0),
            }
        )

    # 9. unexpected hitgroup values
    ok_vals = set(HITGROUPS.values()) | {"other"}
    bad = (
        set(demo.hurts["hitgroup"].dropna().unique()) - ok_vals
        if "hitgroup" in demo.hurts.columns and len(demo.hurts)
        else set()
    )
    checks.append(
        {
            "id": "hitgroups",
            "level": "warn" if bad else "ok",
            "n": len(bad),
            "vals": sorted(bad),
        }
    )
    return checks


# ------------------------------------------------------------ debug export
def _records(df: pd.DataFrame, cap: int | None = None) -> list[dict]:
    if df is None or not len(df):
        return []
    d = df if cap is None or len(df) <= cap else df.iloc[:cap].copy()
    d = d.replace({np.nan: None})
    return json.loads(d.to_json(orient="records", date_format="iso"))


def debug_report(
    demo: Demo,
    shots: pd.DataFrame,
    duels: pd.DataFrame,
    me: str,
    params: dict,
    shots_cap: int = 20000,
) -> dict:
    """
    Everything needed to debug a suspicious demo offline: raw counts,
    data-quality checks, per-shot rows of the selected player (speed, dist,
    allowed speed, hit), their duels and the all-player summary.
    """

    try:
        import importlib.metadata as im

        dp_ver = im.version("demoparser2")
    except Exception:  # noqa: BLE001
        dp_ver = "unknown"
    try:
        size = os.path.getsize(demo.path)
    except OSError:
        size = None
    my = duels_for(duels, me) if len(duels) else pd.DataFrame()
    shot_note = (
        f"truncated to first {shots_cap} of {len(shots)}"
        if len(shots) > shots_cap
        else ""
    )
    return {
        "meta": {
            "app": "cs2-aimaudit",
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "demoparser2": dp_ver,
            "tickrate": demo.tickrate,
            "speed_model": f"own pos delta (t-1 -> t) * {demo.tickrate}; parser velocity unused",
            "risk_model": {
                "safe_dist": params.get("safe_dist", SAFE_DIST),
                "strict_dist": params.get("strict_dist", STRICT_DIST),
                "full_speed": FULL_SPEED,
            },
            "params": params,
            "file": {"name": os.path.basename(demo.path), "size": size},
        },
        "header": demo.header,
        "players": _records(demo.players),
        "counts": {
            "deaths": len(demo.deaths),
            "fires": len(demo.fires),
            "hurts": len(demo.hurts),
            "ticks_rows": len(demo.ticks),
            "shots": len(shots),
            "duels": len(duels),
            "warmup_dropped": demo.warmup_dropped,
            "punch_available": "punch_pitch" in demo.ticks.columns,
        },
        "event_columns": {
            "deaths": sorted(map(str, demo.deaths.columns)),
            "fires": sorted(map(str, demo.fires.columns)),
            "hurts": sorted(map(str, demo.hurts.columns)),
        },
        "checks": data_checks(demo, shots, duels),
        "me": {
            "sid": me,
            "name": demo.names.get(me),
            "shots_note": shot_note,
            "shots": _records(
                shots[shots["sid"] == me][
                    [
                        c
                        for c in (
                            "tick",
                            "round",
                            "weapon",
                            "speed",
                            "dist",
                            "allowed_speed",
                            "risky",
                            "hit",
                            "X",
                            "Y",
                            "Z",
                        )
                        if c in shots.columns
                    ]
                ],
                shots_cap,
            ),
            "duels": _records(my),
        },
        "summary": _records(
            player_summary(
                demo,
                duels,
                shots,
                params.get("thr", 40.0),
                params.get("safe_dist", SAFE_DIST),
                params.get("strict_dist", STRICT_DIST),
            )
        ),
    }
