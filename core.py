"""
CS2 demo analysis logic (no UI). Parsing is done by demoparser2.
All column names / values here are language-neutral; the UI translates them.
"""

from __future__ import annotations

import bisect
import bz2
import gzip
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from functools import cached_property
from typing import Sequence

import numpy as np
import pandas as pd
from demoparser2 import DemoParser

import store

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

# ------------------------------------------------- shot context
# Per-shot context from server fields (docs/DEMOPARSER_NOTES.md s.13):
# flash_duration above FLASH_SHOT_S -> the shot counts as fired while
# flashed; velo_modifier below SLOWED_MAX -> still slowed by recent damage;
# round_second outside [0, ROUND_SEC_MAX] -> bad clock data -> NaN.
FLASH_SHOT_S = 0.5
SLOWED_MAX = 0.99
ROUND_SEC_MAX = 300.0
# First shot of an engagement: no shot by the same player in the previous
# ENGAGE_GAP_TICKS (~350 ms at 64 tick) - the crosshair had time to leave
# the target, so the next shot starts a fresh aim event. Spray follow-ups
# inside the gap are recoil control, not aim.
ENGAGE_GAP_TICKS = 22
# Pre-fire mouse window: usercmd deltas summed over the FLICK_WIN_TICKS
# ticks (~250 ms) before a first shot = how much the mouse was moving when
# the player committed (docs/DEMOPARSER_NOTES.md s.15).
FLICK_WIN_TICKS = 16
# Trade window: a teammate avenging your death within this many seconds
# counts as a trade (the usual community definition; notes s.16).
TRADE_S = 5.0
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


def halves_from_sides(sides: dict, n_rounds: int) -> list[tuple[int, int]]:
    """
    Inclusive (lo, hi) round ranges of the halves (including every overtime
    half), derived from the per-round sides: a half boundary is a round in
    which (almost) every player with known sides swapped T<->CT. Returns []
    when sides are missing or no full swap is found - the caller then falls
    back to the MR12 assumption in round_bounds/pistol_rounds.

    A pair of rounds is only compared when both are known AND consecutive:
    with a gap (rounds without any events) the swap position is unknowable,
    and guessing it would misplace the halves.
    """
    if not sides:
        return []
    rounds = sorted(r for r in sides if isinstance(r, int) and r >= 1)
    if not rounds:
        return []
    bounds = []
    for prev, cur in zip(rounds, rounds[1:]):
        if cur != prev + 1:
            continue
        known = swapped = 0
        for sid, s_prev in sides[prev].items():
            s_cur = sides[cur].get(sid)
            if s_prev in ("T", "CT") and s_cur in ("T", "CT"):
                known += 1
                if s_prev != s_cur:
                    swapped += 1
        if known >= 2 and swapped >= 0.75 * known:
            bounds.append(cur)
    if not bounds:
        return []
    n = max(int(n_rounds), 1)
    out = []
    lo = rounds[0]
    for b in bounds + [n + 1]:
        hi = min(b - 1, n)
        if lo <= hi:
            out.append((lo, hi))
        lo = b
    return out


def round_winners(demo: "Demo") -> dict[int, str]:
    """
    Round winners ('CT'/'T') for the replay's round bar, inferred from
    eliminations: CS2 demos expose no round_end/winner event (docs
    s.12.1), so the only honest signal is "a side lost every player in
    that round" - then the other side won it. Every death counts towards
    the VICTIM's side loss count (kills, teamkills, suicides/world, the
    bomb), because the team did lose that player either way.

    Rounds that ended with both sides fielding survivors (bomb/timeout/
    save wins) are NOT guessed: they stay absent from the dict and the
    UI renders them winner-neutral. Halftime swaps are per round
    (demo.sides), a missing side for a round skips that round.

    Known wrong edge case, accepted: T eliminated post-plant and the
    bomb still explodes - the dead-T side shows no survivors so CT is
    inferred. Without a winner event this cannot be told apart.
    """
    d = demo.deaths
    if d is None or not len(d) or "user_steamid" not in d.columns:
        return {}
    sides = demo.sides
    rounds = sorted(r for r in sides if isinstance(r, int) and r >= 1)
    if not rounds:
        return {}
    dr = _round_col(d)
    warm = (
        d["is_warmup_period"].astype(bool) if "is_warmup_period" in d.columns
        else pd.Series(False, index=d.index)
    )
    out: dict[int, str] = {}
    for r in rounds:
        fielded = {"T": 0, "CT": 0}
        for s in sides[r].values():
            if s in fielded:
                fielded[s] += 1
        if fielded["T"] < 1 or fielded["CT"] < 1:
            continue  # sides unknown for this round -> nothing to compare
        rows = d[(dr == r - 1) & ~warm]
        if not len(rows):
            continue  # nobody died at all: timeout/quick save, not guessable
        lost = {"T": 0, "CT": 0}
        for sid in rows["user_steamid"]:
            s = sides[r].get(str(sid))
            if s in lost:
                lost[s] += 1
        out_t = lost["T"] >= fielded["T"]
        out_ct = lost["CT"] >= fielded["CT"]
        if out_t != out_ct:  # exactly one side eliminated
            out[r] = "CT" if out_t else "T"
    return out


def round_bounds(n_rounds: int, preset: str, halves=None) -> tuple[int, int]:
    """
    Inclusive 1-based round range of a preset for a match of `n_rounds`
    rounds ('all', 'first', 'second', 'ot'). `halves` are the data-driven
    (lo, hi) ranges from halves_from_sides; when given they define the
    halves (MR15, overtime lengths and all), otherwise MR12 is assumed:
    first half 1-12, second 13-24, overtime 25+. A preset beyond the match
    gives an empty range (lo > hi) on purpose - the UI simply does not
    offer it.
    """

    n = max(int(n_rounds), 1)
    if halves:
        if preset == "first":
            lo, hi = halves[0]
        elif preset == "second" and len(halves) >= 2:
            lo, hi = halves[1]
        elif preset == "ot" and len(halves) >= 3:
            lo, hi = halves[2][0], n
        else:  # 'all' / anything unknown
            lo, hi = 1, n
        hi = max(lo, min(hi, n))
        return (lo, hi)
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


def pistol_rounds(n_rounds: int, halves=None) -> set[int]:
    """
    First round of each half incl. overtime halves. With `halves` from
    halves_from_sides that is data-driven (MR15 -> {1, 16}); the MR12
    fallback gives {1, 13, 25, ...}.
    """
    if halves:
        return {lo for lo, hi in halves if lo <= max(int(n_rounds), 1)}
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


def _zone(v) -> str:
    """last_place_name -> clean zone string ('' when missing/NA)."""
    if v is None or pd.isna(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "nan", "none") else s


def _row_round_sec(r) -> float:
    """
    Seconds into the live round at the row's tick.

    round_second = game_time - round_start_time; round_start_time is the
    freeze END, so this is play time without the buy time. NaN when the
    fields are absent (old demos) or out of [0, ROUND_SEC_MAX].
    """

    gt = getattr(r, "game_time", None)
    rst = getattr(r, "round_start_time", None)
    try:
        rs = float(gt) - float(rst)
    except (TypeError, ValueError):
        return float("nan")
    if not (np.isfinite(rs) and 0.0 <= rs <= ROUND_SEC_MAX):
        return float("nan")
    return round(rs, 2)


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


def _unpack(ext: str, p: str, tmp: str) -> None:
    """Stream-decompress archive `p` into file `tmp` (may raise on corrupt input)."""
    try:
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
    except ValueError:
        # one of our own clear messages - just drop the partial .part
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    except Exception as e:  # noqa: BLE001 - broken archive, any library error
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ValueError(
            f"Cannot decompress {os.path.basename(p)}: {type(e).__name__}: {e}"
        ) from e


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

    A truncated or corrupt archive raises ValueError (whatever the
    decompressor throws - EOFError, BadZipFile, ZstdError, OSError...):
    the UI only catches ValueError, and a raw traceback is not a friendly
    message. A half-written .part is never left behind either - it can be
    as large as the unpacked demo and nothing prunes the temp dir.
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
        _unpack_dir(),
        f"cs2_aimaudit_{safe}_{st.st_size}_{int(st.st_mtime)}.dem",
    )
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target  # cached from an earlier run
    tmp = target + ".part"

    def _drop_part() -> None:
        try:
            os.remove(tmp)
        except OSError:
            pass

    try:
        _unpack(ext, p, tmp)
    except ValueError:
        _drop_part()
        raise
    except (
        Exception
    ) as e:  # noqa: BLE001 - truncated/corrupt archive, bad zip, zstd error...
        _drop_part()
        raise ValueError(
            f"Cannot unpack {os.path.basename(p)}: {type(e).__name__}: {e}"
        ) from e
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ValueError(f"Decompressed demo is empty: {os.path.basename(p)}")
    os.replace(tmp, target)  # atomic: no half-written cache on crash
    return target


def _unpack_dir() -> str:
    """cache/unpacked in the data folder; the system temp dir when it is read-only."""
    d = store.unpacked_dir()
    return d if os.path.isdir(d) and os.access(d, os.W_OK) else tempfile.gettempdir()


# ------------------------------------------------------------ app state
# Small local state next to the demo library (store.state_path): the last
# selected demo and player. Everything stays in the data folder; if it is not
# writable the app simply does not remember anything. A state file left by an
# older version in ~/.cs2-aimaudit is read once and then superseded.
def load_state() -> dict:
    """Load the local state file; {} when missing or unreadable."""
    for path in (store.state_path(), os.path.join(store.LEGACY_DIR, "state.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            continue
    return {}


def save_state(d: dict) -> None:
    """Atomically save the state file; silent when the data folder is read-only."""
    try:
        path = store.state_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


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
    excluded: dict = field(default_factory=dict)
    #                      rounds dropped before the live match: {warmup,
    #                      knife_round, restart: round counts} - see
    #                      docs/DEMOPARSER_NOTES.md section 12
    tickrate: int = TICKRATE  # from the demo header (fallback: 64)
    sides: dict = field(default_factory=dict)  # round(1-based) -> sid -> T/CT
    totals: pd.DataFrame | None = None
    #                      game kills_total / deaths_total per player,
    #                      sampled at the last event tick (None when the
    #                      parser or props are unavailable) - input of the
    #                      data_checks scoreboard cross-check, notes s.14
    mouse: pd.DataFrame | None = None
    #                      usercmd mouse deltas (|dx|+|dy| per tick) around
    #                      first-shot windows, None when the demo/parser
    #                      does not provide them - input of flick_move, s.15

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


def _first_shot_arr(ticks, sids) -> np.ndarray:
    """
    Boolean array aligned to the inputs: True where the row is the FIRST
    shot of an engagement for that player - no shot by the same sid within
    the previous ENGAGE_GAP_TICKS ticks (~350 ms, notes s.15). A spray's
    follow-up shots are recoil control; the fresh aim event is what the
    flick analysis looks at. Rows may arrive in any order.
    """

    n = len(ticks)
    if not n:
        return np.zeros(0, dtype=bool)
    # plain numpy values: a filtered shots table carries a holey index and
    # the fresh frame must not inherit it
    d = pd.DataFrame(
        {
            "tick": np.asarray(pd.Series(ticks).astype(int)),
            "sid": np.asarray(pd.Series(sids), dtype=object),
        }
    )
    d = d.sort_values(["sid", "tick"], kind="stable")
    prev = d.groupby("sid")["tick"].shift(1)
    gap = d["tick"] - prev
    first = ((gap > ENGAGE_GAP_TICKS) | gap.isna()).to_numpy()
    res = np.zeros(n, dtype=bool)
    res[d.index.to_numpy()] = first
    return res


def _first_shot_windows(fires: pd.DataFrame) -> list[int]:
    """
    Sorted ticks covering the FLICK_WIN_TICKS window before every first
    shot of an engagement in `fires` (gun shots, warmup already excluded)
    - what the usercmd mouse parse is sampled on. Ticks below 0 (a first
    shot right at the demo start) are clipped away.
    """

    if (
        fires is None
        or not len(fires)
        or "user_steamid" not in getattr(fires, "columns", [])
    ):
        return []
    first = _first_shot_arr(fires["tick"], fires["user_steamid"])
    want: set[int] = set()
    for t in fires["tick"].to_numpy()[first]:
        want.update(range(int(t) - FLICK_WIN_TICKS, int(t)))
    return sorted(t for t in want if t >= 0)


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
    _events = [
        "player_death",
        "weapon_fire",
        "player_hurt",
        # round/match structure (docs/DEMOPARSER_NOTES.md s.12):
        # begin_new_match marks the live-match start (FACEIT knife
        # rounds sit before it), round_freeze_end bounds the rounds
        "begin_new_match",
        "round_freeze_end",
    ]
    _other = ["total_rounds_played", "is_warmup_period"]
    ev: dict | None = None
    last_ev_err: Exception | None = None
    # shot/duel context: the named map zone of both players at
    # every event (player=) plus the round clock (other=): round_second =
    # game_time - round_start_time, where round_start_time is the freeze
    # END (verified on real demos). Old parsers/demos may not know the
    # fields - fall back step by step; the columns then simply stay absent
    # and every consumer treats them as missing data, never as false.
    for kw in (
        # first choice also asks for the position (X/Y/Z) of both players
        # at every event: the Map page draws kills / deaths from it. Old
        # parsers that reject it fall through to the next step, the
        # columns stay absent and the page says so instead of guessing.
        {
            "player": ["last_place_name", "X", "Y", "Z"],
            "other": _other + ["game_time", "round_start_time"],
        },
        {
            "player": ["last_place_name"],
            "other": _other + ["game_time", "round_start_time"],
        },
        {"other": _other},
        {},
    ):
        try:
            ev = dict(p.parse_events(_events, **kw))
            break
        except Exception as e:  # noqa: BLE001 - unknown fields on old parsers
            last_ev_err = e
    if ev is None:
        raise last_ev_err  # type: ignore[misc]
    deaths = ev.get("player_death", pd.DataFrame())
    fires = ev.get("weapon_fire", pd.DataFrame())
    hurts = ev.get("player_hurt", pd.DataFrame())
    if fires.empty:
        raise ValueError("no_events")
    for df, pairs in (
        (
            deaths,
            (
                ("attacker_steamid", "attacker_name"),
                ("user_steamid", "user_name"),
                # assists for the impact stats; the same float64/steamid
                # collision rules apply as for the attacker column
                ("assister_steamid", "assister_name"),
            ),
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
    warmup_rows: dict[str, int] = {}
    for name, df in (("deaths", deaths), ("fires", fires), ("hurts", hurts)):
        if "is_warmup_period" in df.columns and len(df):
            mask = df["is_warmup_period"].fillna(False).astype(bool)
            warmup_rows[name] = int(mask.sum())
            if mask.any():
                if name == "deaths":
                    deaths = deaths[~mask].copy()
                elif name == "fires":
                    fires = fires[~mask].copy()
                else:
                    hurts = hurts[~mask].copy()

    # ------------------------------------------- live match start
    # FACEIT-style demos open with a knife round (often followed by a warmup
    # round) that is NOT flagged is_warmup_period; the live match begins at
    # the LAST begin_new_match. Everything before that tick is dropped and
    # counted per cause for the UI banner. Fallback without the event: the
    # first round segment with zero gun fires and knife-only kills is the
    # knife round. See docs/DEMOPARSER_NOTES.md section 12.
    excluded: dict[str, int] = {"warmup": 0, "knife_round": 0, "restart": 0}

    def _between(df, lo, hi):
        if df is None or not len(df) or "tick" not in df.columns:
            return pd.DataFrame()
        return df[(df["tick"] >= lo) & (df["tick"] < hi)]

    def _has_gun_fire(df) -> bool:
        return (
            bool(len(df)) and "weapon" in df.columns and df["weapon"].map(is_gun).any()
        )

    def _knife_only(df) -> bool:
        """At least one kill and every kill by knife/bayonet."""
        if not len(df) or "weapon" not in df.columns:
            return False
        w = df["weapon"].map(norm_weapon)
        kn = w.str.contains("knife", na=False) | w.eq("bayonet")
        return bool(kn.any() and kn.all())

    def _has_knife_death(df) -> bool:
        if not len(df) or "weapon" not in df.columns:
            return False
        w = df["weapon"].map(norm_weapon)
        return bool((w.str.contains("knife", na=False) | w.eq("bayonet")).any())

    bnm = ev.get("begin_new_match", pd.DataFrame())
    freeze = ev.get("round_freeze_end", pd.DataFrame())
    fe_rows = []
    if len(freeze) and "tick" in freeze.columns:
        t = pd.to_numeric(freeze["tick"], errors="coerce")
        warm = (
            freeze["is_warmup_period"].fillna(False).astype(bool)
            if "is_warmup_period" in freeze.columns
            else pd.Series(False, index=freeze.index)
        )
        fe_rows = sorted(
            (int(tt), bool(ww)) for tt, ww in zip(t.dropna(), warm[t.notna()])
        )
    live_start = None
    if len(bnm) and "tick" in bnm.columns:
        t = pd.to_numeric(bnm["tick"], errors="coerce").dropna()
        if len(t):
            live_start = int(t.max())

    if live_start is not None and live_start > 0:
        pre = [r for r in fe_rows if r[0] < live_start]
        for i, (fe_tick, fe_warm) in enumerate(pre):
            seg_end = pre[i + 1][0] if i + 1 < len(pre) else live_start
            if fe_warm:
                excluded["warmup"] += 1
            elif not _has_gun_fire(
                _between(fires, fe_tick, seg_end)
            ) and _has_knife_death(_between(deaths, fe_tick, seg_end)):
                excluded["knife_round"] += 1
            else:
                excluded["restart"] += 1
        if len(deaths) and "tick" in deaths.columns:
            deaths = deaths[deaths["tick"] >= live_start].copy()
        if len(fires) and "tick" in fires.columns:
            fires = fires[fires["tick"] >= live_start].copy()
        if len(hurts) and "tick" in hurts.columns:
            hurts = hurts[hurts["tick"] >= live_start].copy()
    elif fe_rows or not any(warmup_rows.values()):
        # no begin_new_match: knife fallback on the FIRST round segment
        # (freeze_end-bounded when available, else the trp=0 rows). The
        # gate is strict - no gun shots and knife-only kills - so a pistol
        # round is never mistaken for a knife round.
        if fe_rows:
            lo = fe_rows[0][0]
            hi = fe_rows[1][0] if len(fe_rows) > 1 else float("inf")
            seg_fires, seg_deaths = _between(fires, lo, hi), _between(deaths, lo, hi)
            later_fires = fires[fires["tick"] >= hi] if len(fires) else fires
            if (
                not _has_gun_fire(seg_fires)
                and _knife_only(seg_deaths)
                and _has_gun_fire(later_fires)
            ):
                excluded["knife_round"] = 1
                deaths = deaths[(deaths["tick"] < lo) | (deaths["tick"] >= hi)].copy()
                fires = fires[(fires["tick"] < lo) | (fires["tick"] >= hi)].copy()
                hurts = hurts[(hurts["tick"] < lo) | (hurts["tick"] >= hi)].copy()
            excluded["warmup"] = sum(1 for _, w in fe_rows if w)
        else:
            r0d = _round_col(deaths)
            f0 = fires[_round_col(fires) == 0] if len(fires) else fires
            f1 = fires[_round_col(fires) > 0] if len(fires) else fires
            if (
                len(deaths)
                and not _has_gun_fire(f0)
                and _knife_only(deaths[r0d == 0])
                and _has_gun_fire(f1)
            ):
                excluded["knife_round"] = 1
                deaths = deaths[_round_col(deaths) != 0].copy()
                fires = fires[_round_col(fires) != 0].copy()
                if len(hurts) and "total_rounds_played" in hurts.columns:
                    hurts = hurts[_round_col(hurts) != 0].copy()
    if not fe_rows and any(warmup_rows.values()):
        # warmup rows were dropped but there are no round boundary events:
        # report the activity without pretending to know the round count
        excluded["warmup"] = max(excluded["warmup"], 1)

    # first live round must be round 1: when the counter kept counting the
    # excluded round (knife round without a begin_new_match restart), shift
    # total_rounds_played so the first remaining round is 0
    if live_start is not None or excluded["knife_round"]:
        trp_min = None
        for df in (deaths, fires, hurts):
            if "total_rounds_played" in df.columns and len(df):
                v = pd.to_numeric(df["total_rounds_played"], errors="coerce")
                v = v[v >= 0]
                if len(v):
                    trp_min = (
                        int(v.min()) if trp_min is None else min(trp_min, int(v.min()))
                    )
        if trp_min:
            for df in (deaths, fires, hurts):
                if "total_rounds_played" in df.columns and len(df):
                    df["total_rounds_played"] = (
                        pd.to_numeric(df["total_rounds_played"], errors="coerce")
                        - trp_min
                    )

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
    # shooter state at the fire ticks: flash_duration (seconds of
    # blindness left) and velo_modifier (<1 = slowed by recent damage). A
    # separate parse_ticks call: an unknown prop must not take the
    # POS/buttons chain down with it (the buttons lesson, notes s.4). On
    # failure the columns stay absent -> flags come out NA, not False.
    try:
        state = p.parse_ticks(["flash_duration", "velo_modifier"], ticks=ticks_list)
    except Exception:  # noqa: BLE001 - old parser / prop unavailable
        state = None
    if state is not None and len(state) and "steamid" in state.columns:
        state = state.copy()
        state["steamid"] = fix_ids(
            state["steamid"],
            flookup,
            state["name"] if "name" in state.columns else None,
            name_to_sid,
        )
        state = state.drop_duplicates(["tick", "steamid"])
        _keep = [c for c in ("flash_duration", "velo_modifier") if c in state.columns]
        if _keep:
            td = td.merge(
                state[["tick", "steamid"] + _keep], on=["tick", "steamid"], how="left"
            )
    tickrate = tickrate_from_header(header)
    td = _add_speed(td, tickrate)
    # usercmd mouse deltas around first-shot windows (notes s.15): a THIRD
    # separate parse_ticks call - the rows must never merge into `td`
    # (they would change the delta-position speed chain), and an unknown
    # prop must not take anything down with it (the buttons lesson). None
    # -> flick_move comes out NA everywhere, never 0.
    mouse = None
    _mwin = _first_shot_windows(fires)
    if _mwin:
        try:
            md = p.parse_ticks(["usercmd_mouse_dx", "usercmd_mouse_dy"], ticks=_mwin)
        except Exception:  # noqa: BLE001 - old parser / props unavailable
            md = None
        if (
            md is not None
            and len(md)
            and "steamid" in md.columns
            and {"usercmd_mouse_dx", "usercmd_mouse_dy"} <= set(md.columns)
        ):
            md = md.copy()
            md["sid"] = fix_ids(
                md["steamid"],
                flookup,
                md["name"] if "name" in md.columns else None,
                name_to_sid,
            )
            dx = pd.to_numeric(md["usercmd_mouse_dx"], errors="coerce")
            dy = pd.to_numeric(md["usercmd_mouse_dy"], errors="coerce")
            md = pd.DataFrame(
                {
                    "tick": md["tick"].astype(int),
                    "sid": md["sid"],
                    "mag": dx.abs() + dy.abs(),
                }
            )
            md = md.dropna(subset=["sid", "mag"])
            md = md.drop_duplicates(["tick", "sid"])
            if len(md):
                mouse = md.sort_values(["tick", "sid"]).reset_index(drop=True)
    # scoreboard aggregates (notes s.14): the game's own kills_total /
    # deaths_total at the last event tick, for the data_checks
    # cross-check. Verified on the real demos: the game updates them
    # live (a kill is already counted at the tick of the last death of
    # its round) and resets both at begin_new_match, so the sample is
    # directly comparable to our live-only tables. Any failure ->
    # totals stays None and the check reports a skip, never a guess.
    totals = None
    _ev_max = [int(df["tick"].max()) for df in (deaths, fires, hurts) if len(df)]
    if _ev_max:
        try:
            agg = p.parse_ticks(["kills_total", "deaths_total"], ticks=[max(_ev_max)])
        except Exception:  # noqa: BLE001 - old parser / props unavailable
            agg = None
        if agg is not None and len(agg) and "steamid" in agg.columns:
            agg = agg.copy()
            agg["sid"] = fix_ids(
                agg["steamid"],
                flookup,
                agg["name"] if "name" in agg.columns else None,
                name_to_sid,
            )
            agg = agg.drop_duplicates(["sid"])
            keep = ["sid"] + [
                c for c in ("kills_total", "deaths_total") if c in agg.columns
            ]
            if len(keep) > 1:
                totals = (
                    agg[keep]
                    .rename(columns={"kills_total": "kills", "deaths_total": "deaths"})
                    .reset_index(drop=True)
                )
    return Demo(
        path,
        header,
        players,
        deaths,
        fires,
        hurts,
        td,
        excluded,
        tickrate,
        sides,
        totals=totals,
        mouse=mouse,
    )


# ----------------------------------------------------------------- metrics
def _hurts_of(demo: Demo, me: str, hurts: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    hurts rows caused by `me`; safe when the player_hurt event is absent.
    `hurts` overrides demo.hurts (used by the global filter).
    """
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

    Shot/duel context columns: `zone` (the game's named place at the shot,
    '' unknown), `round_sec` (seconds into the live round, NaN unknown),
    `flashed` / `slowed` (shooter state at the fire tick; NA when the
    demo/parser does not provide the prop - missing is never False).
    """

    ctx = [
        c
        for c in ("user_last_place_name", "game_time", "round_start_time")
        if c in demo.fires.columns
    ]
    cols = (
        ["tick", "user_steamid", "weapon"]
        + (
            ["total_rounds_played"]
            if "total_rounds_played" in demo.fires.columns
            else []
        )
        + ctx
    )
    s = demo.fires[cols].rename(columns={"user_steamid": "sid"})
    s["round"] = _round_col(s) + 1
    s = s.drop(columns=["total_rounds_played"], errors="ignore")
    # named zone at the shot (last_place_name of the fire event)
    if "user_last_place_name" in s.columns:
        s["zone"] = s["user_last_place_name"].map(_zone)
    else:
        s["zone"] = ""
    # seconds into the live round at the shot (freeze excluded)
    if {"game_time", "round_start_time"} <= set(s.columns):
        rs = pd.to_numeric(s["game_time"], errors="coerce") - pd.to_numeric(
            s["round_start_time"], errors="coerce"
        )
        s["round_sec"] = rs.where((rs >= 0) & (rs <= ROUND_SEC_MAX)).round(2)
    else:
        s["round_sec"] = np.nan
    s = s.drop(
        columns=["user_last_place_name", "game_time", "round_start_time"],
        errors="ignore",
    )
    tcols = ["tick", "steamid", "speed", "X", "Y", "Z"] + [
        c for c in ("flash_duration", "velo_modifier") if c in demo.ticks.columns
    ]
    t = demo.ticks[tcols].rename(columns={"steamid": "sid"})
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
    # shooter state at the fire tick: True/False when known, NA when the
    # prop is unavailable (an old demo must not look like "0% flashed")
    if "flash_duration" in out.columns:
        fd = pd.to_numeric(out["flash_duration"], errors="coerce")
    else:
        fd = pd.Series(np.nan, index=out.index, dtype=float)
    fl = pd.Series(pd.NA, index=out.index, dtype="boolean")
    _fok = fd.notna()
    fl[_fok] = (fd[_fok] > FLASH_SHOT_S).to_numpy()
    out["flashed"] = fl
    if "velo_modifier" in out.columns:
        vm = pd.to_numeric(out["velo_modifier"], errors="coerce")
    else:
        vm = pd.Series(np.nan, index=out.index, dtype=float)
    sw = pd.Series(pd.NA, index=out.index, dtype="boolean")
    _sok = vm.notna()
    sw[_sok] = (vm[_sok] < SLOWED_MAX).to_numpy()
    out["slowed"] = sw
    # first shot of an engagement (gap > ENGAGE_GAP_TICKS since the same
    # sid's previous shot) and the mouse movement that preceded it: the
    # sum of |dx|+|dy| over the FLICK_WIN_TICKS ticks before the shot.
    # first_shot is always known; flick_move is NA for spray follow-ups
    # (recoil control is not aim) and when the demo/parser has no usercmd
    # props (missing is never 0, the buttons lesson).
    out["first_shot"] = _first_shot_arr(out["tick"], out["sid"])
    out["flick_move"] = np.nan
    if demo.mouse is not None and len(demo.mouse):
        _by = {sid: g.set_index("tick")["mag"] for sid, g in demo.mouse.groupby("sid")}
        _fm = []
        for t, first, sid in zip(out["tick"], out["first_shot"], out["sid"]):
            if not first or sid not in _by:
                _fm.append(np.nan)
                continue
            got = _by[sid].reindex(range(int(t) - FLICK_WIN_TICKS, int(t)))
            v = got.sum(min_count=1)
            _fm.append(float(v) if pd.notna(v) else np.nan)
        out["flick_move"] = _fm
    out = out.drop(columns=["flash_duration", "velo_modifier"], errors="ignore")
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
            if o != sid
            and teams.get(o) not in (None, "?")
            and teams.get(o) != teams.get(sid)
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
    # duels are gun fights: knife / grenade / zeus / world / C4 kills have
    # no shots to analyse (a mid-match knife kill is a real thing, and it
    # must not appear as a "duel without shots"). Overview K/D still counts
    # every enemy kill - player_summary reads demo.deaths directly.
    if "weapon" in d.columns and len(d):
        d = d[d["weapon"].map(is_gun)]
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
                # context: when in the round and on which named zone
                # the duel ended (kill-time values from the death event)
                "round_sec": _row_round_sec(r),
                "killer_zone": _zone(getattr(r, "attacker_last_place_name", None)),
                "victim_zone": _zone(getattr(r, "user_last_place_name", None)),
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
                    "opp_first_tick": df[f"{opp}_first_tick"],
                    "noscope": df["noscope"],
                    "thrusmoke": df["thrusmoke"],
                    "attackerblind": df["attackerblind"],
                    "penetrated": df["penetrated"],
                    "round_sec": df["round_sec"],
                    "my_zone": df[f"{mine}_zone"],
                    "opp_zone": df[f"{opp}_zone"],
                }
            )
        )
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts).sort_values("tick").reset_index(drop=True)


def _enemy_kill_rows(demo: Demo) -> pd.DataFrame:
    """
    Deaths with a known attacker of the ENEMY team (suicides / world /
    teamkills out), with a 0-based `round_idx` column. The same enemy rule
    as the scoreboard K/D in player_summary. The column name must stay a
    valid namedtuple field (no leading underscore) because both consumers
    iterate with itertuples().
    """
    d = demo.deaths
    if (
        not len(d)
        or "attacker_steamid" not in d.columns
        or "user_steamid" not in d.columns
        or "tick" not in d.columns
    ):
        return pd.DataFrame()
    teams = demo.teams
    ta = d["attacker_steamid"].map(teams)
    tu = d["user_steamid"].map(teams)
    enemy = (
        ta.notna()
        & tu.notna()
        & (ta != tu)
        & (d["attacker_steamid"] != d["user_steamid"])
    )
    out = d[enemy].copy()
    if not len(out):
        return out
    # NB: itertuples() below - keep the name namedtuple-safe
    out["round_idx"] = (
        out["total_rounds_played"].map(_safe_int)
        if "total_rounds_played" in out.columns
        else 0
    )
    return out.sort_values("tick").reset_index(drop=True)


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


def _round_side_keep(demo: Demo, f: "Filters | None"):
    """
    keep(round_no, sid) -> bool for the round-structure metrics (K/D,
    openings, trades, multi-kills, assists), or None when no filter applies.

    Only the filters that mean something for a PLAYER in a ROUND are used:
    the round range / pistol preset and the side the player had in that
    round (Demo.side_at, so a halftime swap is handled per round). Weapon,
    class and distance are shot-level filters and stay out of it. None is
    also returned when the deaths carry no round counter - guessing rounds
    would silently drop or keep the wrong kills.
    """
    if f is None or not (f.rounds or f.rounds_set or f.side):
        return None
    if "total_rounds_played" not in demo.deaths.columns:
        return None

    def keep(rd: int, sid) -> bool:
        if f.rounds is not None and not (f.rounds[0] <= rd <= f.rounds[1]):
            return False
        if f.rounds_set and rd not in f.rounds_set:
            return False
        if f.side is not None and demo.side_at(rd, sid) != f.side:
            return False
        return True

    return keep


def openings_table(demo: Demo) -> pd.DataFrame:
    """
    The first enemy kill of every round - both teams, match context.

    An 'opening duel' is the round's first death caused by an enemy:
    suicides / world damage / teamkill rows are skipped and the next valid
    death of that round becomes the opening (on the real demos 2-3 rounds
    per match start with such a row, notes s.16). ANY weapon counts,
    knife and grenades included - this is 'how the round started', not a
    gun-duel metric; the Duels page table marks rows with the selected
    player. Returns an empty frame when the demo has no enemy kills.
    """
    de = _enemy_kill_rows(demo)
    if not len(de):
        return pd.DataFrame()
    first = de.groupby("round_idx", sort=True).head(1)
    out = []
    for r in first.itertuples():
        rd = int(getattr(r, "round_idx", 0)) + 1
        out.append(
            {
                "round": rd,
                "tick": int(r.tick),
                "killer": r.attacker_steamid,
                "victim": r.user_steamid,
                "killer_side": demo.side_at(rd, r.attacker_steamid),
                "victim_side": demo.side_at(rd, r.user_steamid),
                "weapon": norm_weapon(r.weapon) if "weapon" in first.columns else NA,
                "weapon_class": (
                    weapon_class(r.weapon) if "weapon" in first.columns else "other"
                ),
                "headshot": bool(getattr(r, "headshot", False)),
                "distance": getattr(r, "distance", np.nan),
                "killer_zone": _zone(getattr(r, "attacker_last_place_name", None)),
                "victim_zone": _zone(getattr(r, "user_last_place_name", None)),
                "round_sec": _row_round_sec(r),
            }
        )
    return pd.DataFrame(out)


def impact_stats(
    demo: Demo, trade_s: float = TRADE_S, f: "Filters | None" = None
) -> pd.DataFrame:
    """
    Per-player round-impact numbers, all from the enemy-kill deaths:

    - opening duels: attempts (killer OR victim of the round's first enemy
      kill) and wins (the killer); `open_rate` is the win rate in %
    - trades: `trade_kills` - killed the attacker of a teammate's death
      within `trade_s` seconds, same round (the avenging definition);
      `traded_deaths` - own deaths avenged that way, `traded_pct` their
      share of enemy deaths (a teammate stat, labelled as such in the UI)
    - multi-kills: `k2`..`k5` - rounds with exactly that many enemy kills
    - assists: `assists` (assister of an enemy kill, any form) and
      `flash_assists` (the subset flagged assistedflash); NaN when the
      demo/parser has no assister column

    Round structure, not a per-shot metric: only the round range and the
    side filter apply (`f`), through _round_side_keep - a player's numbers
    then count only the rounds he played on that side. Weapon / distance
    filters do not apply. Without a filter it is the whole match.
    """
    keep = _round_side_keep(demo, f)

    def _ok(round_idx, sid) -> bool:
        return keep is None or keep(int(round_idx) + 1, sid)

    names = demo.names
    res = {
        sid: {
            "sid": sid,
            "open_attempts": 0,
            "open_wins": 0,
            "open_rate": np.nan,
            "trade_kills": 0,
            "traded_deaths": 0,
            "deaths": 0,
            "traded_pct": np.nan,
            "k2": 0,
            "k3": 0,
            "k4": 0,
            "k5": 0,
            "assists": np.nan,
            "flash_assists": np.nan,
        }
        for sid in names
    }
    de = _enemy_kill_rows(demo)
    if len(de):
        teams = demo.teams
        # ---- openings
        op = de.groupby("round_idx", sort=True).head(1)
        for r in op.itertuples():
            k, v = r.attacker_steamid, r.user_steamid
            for sid in (k, v):
                if sid in res and _ok(r.round_idx, sid):
                    res[sid]["open_attempts"] += 1
            if k in res and _ok(r.round_idx, k):
                res[k]["open_wins"] += 1
        # ---- deaths + multi-kills (k2..k5: ROUNDS with exactly that
        # many enemy kills - one 3-kill round adds 1 to k3, not 3)
        for r in de.itertuples():
            if r.user_steamid in res and _ok(r.round_idx, r.user_steamid):
                res[r.user_steamid]["deaths"] += 1
        for (sid, _rd), n in (
            de.groupby(["attacker_steamid", "round_idx"]).size().items()
        ):
            if sid in res and 2 <= int(n) <= 5 and _ok(_rd, sid):
                res[sid][f"k{int(n)}"] += 1
        # ---- trades: for each death, did a teammate kill its attacker
        # within the window (same round)? death_of maps (round, VICTIM) ->
        # (tick, killer): the attacker's own death row is the candidate
        # avenging kill. The attacker dies at most once per round, so one
        # candidate row is enough.
        win = int(trade_s * demo.tickrate)
        death_of: dict[tuple[int, str], tuple[int, object]] = {}
        for r in de.itertuples():
            death_of[(int(getattr(r, "round_idx", 0)), r.user_steamid)] = (
                int(r.tick),
                r.attacker_steamid,
            )
        for r in de.itertuples():
            v, a = r.user_steamid, r.attacker_steamid
            key = (int(getattr(r, "round_idx", 0)), a)
            later = death_of.get(key)
            if later is None:
                continue
            dt, avenger = later
            if not (int(r.tick) < dt <= int(r.tick) + win):
                continue
            if avenger == v or teams.get(avenger) != teams.get(v):
                continue
            if avenger in res and _ok(r.round_idx, avenger):
                res[avenger]["trade_kills"] += 1
            if v in res and _ok(r.round_idx, v):
                res[v]["traded_deaths"] += 1
        # ---- assists
        if "assister_steamid" in de.columns:
            for sid in res:
                res[sid]["assists"] = 0
                res[sid]["flash_assists"] = 0
            ast = de[de["assister_steamid"].isin(names)]
            if keep is not None and len(ast):
                ast = ast[
                    np.array(
                        [
                            _ok(ri, a)
                            for ri, a in zip(ast["round_idx"], ast["assister_steamid"])
                        ],
                        dtype=bool,
                    )
                ]
            for sid, n in ast.groupby("assister_steamid").size().items():
                res[sid]["assists"] = int(n)
            if "assistedflash" in de.columns:
                afl = ast[ast["assistedflash"].fillna(False).astype(bool)]
                for sid, n in afl.groupby("assister_steamid").size().items():
                    res[sid]["flash_assists"] = int(n)
        for sid, s in res.items():
            if s["open_attempts"]:
                s["open_rate"] = round(s["open_wins"] / s["open_attempts"] * 100, 1)
            if s["deaths"]:
                s["traded_pct"] = round(s["traded_deaths"] / s["deaths"] * 100, 1)
    return pd.DataFrame(res[s] for s in names)


def player_summary(
    demo: Demo,
    duels: pd.DataFrame,
    shots: pd.DataFrame,
    thr: float,
    safe: float = SAFE_DIST,
    strict: float = STRICT_DIST,
    hurts: pd.DataFrame | None = None,
    f: "Filters | None" = None,
) -> pd.DataFrame:
    """
    One row per player. Shot / duel metrics come from the (already
    filtered) frames. K/D, opening rate and the impact numbers are round
    structure: with `f` they follow its round range and side (per round,
    so a halftime swap is handled), not the weapon / distance filters.
    """
    res = []
    dth = demo.deaths
    have_d = len(dth) and {"attacker_steamid", "user_steamid"} <= set(dth.columns)
    teams = demo.teams
    # opening-duel win rate (impact_stats, notes s.16): whole-match, like
    # the K/D below - it is round structure, not a filterable shot metric
    open_rate = impact_stats(demo, f=f).set_index("sid")["open_rate"].to_dict()
    keep = _round_side_keep(demo, f)
    kills_by: dict = {}
    deaths_by: dict = {}
    if keep is not None:
        de = _enemy_kill_rows(demo)
        if len(de):
            rd = (de["round_idx"] + 1).astype(int)
            k_ok = np.array(
                [keep(int(r), s) for r, s in zip(rd, de["attacker_steamid"])],
                dtype=bool,
            )
            v_ok = np.array(
                [keep(int(r), s) for r, s in zip(rd, de["user_steamid"])], dtype=bool
            )
            kills_by = de[k_ok].groupby("attacker_steamid").size().to_dict()
            deaths_by = de[v_ok].groupby("user_steamid").size().to_dict()
    for p in demo.players.itertuples():
        sh = shots[shots["sid"] == p.sid]
        hi = _hurts_of(demo, p.sid, hurts)
        dd = duels_for(duels, p.sid) if len(duels) else pd.DataFrame()
        if keep is not None and have_d:
            kills = int(kills_by.get(p.sid, 0))
            deaths = int(deaths_by.get(p.sid, 0))
        elif have_d:
            # K/D like the in-game scoreboard: every ENEMY kill/death with
            # any weapon (knife and grenades included), whole match. Duels
            # above are gun-only, so the two must not be mixed silently.
            # Static teams are fine here: teammates share the label before
            # and after the halftime swap. World/suicide deaths have no
            # known attacker and stay out, teamkills stay out both ways.
            ta = dth["attacker_steamid"].map(teams)
            tu = dth["user_steamid"].map(teams)
            enemy = ta.notna() & tu.notna() & (ta != tu)
            kills = int(
                (
                    (dth["attacker_steamid"] == p.sid)
                    & (dth["attacker_steamid"] != dth["user_steamid"])
                    & enemy
                ).sum()
            )
            deaths = int(
                (
                    (dth["user_steamid"] == p.sid)
                    & (dth["attacker_steamid"] != dth["user_steamid"])
                    & enemy
                ).sum()
            )
        else:
            kills = int((duels["killer"] == p.sid).sum()) if len(duels) else 0
            deaths = int((duels["victim"] == p.sid).sum()) if len(duels) else 0
        kd = dd[dd["result"] == "kill"] if len(dd) else dd
        fs = dd[dd["first"].isin(["me", "opponent"])] if len(dd) else dd
        fsh = sh[sh["first_shot"]] if len(sh) else sh
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
                "kd_ratio": round(kills / max(1, deaths), 2),
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
                # first-shot accuracy: hit rate on first shots of
                # engagements (crosshair placement quality, notes s.15)
                "first_acc": (
                    round(fsh["hit"].mean() * 100, 1) if len(fsh) else np.nan
                ),
                # opening-duel win rate: share of the rounds' first enemy
                # kills won (killer vs victim, notes s.16)
                "open_rate": open_rate.get(p.sid, np.nan),
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
        # first-shot accuracy per weapon: hit rate on the weapon's first
        # shots of engagements (the AWP/USP number that actually matters);
        # NaN for weapons that only appear inside sprays
        fsh = sh[sh["first_shot"]]
        if len(fsh):
            g["first_acc"] = fsh.groupby("weapon")["hit"].mean() * 100
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


def _parse_version() -> str:
    """
    Identity of what load_demo produces: the demoparser2 version plus a hash
    of this file, so a cached parse is never used after the parser or the
    parsing code changed (no version number to forget to bump).
    """
    try:
        with open(__file__, "rb") as fh:
            code = hashlib.sha1(fh.read()).hexdigest()[:8]
    except OSError:
        code = "nocode"
    try:
        dp = importlib.metadata.version("demoparser2")
    except Exception:  # noqa: BLE001
        dp = "x"
    return re.sub(r"[^\w.\-]", "_", f"f1-{dp}-{code}")


PARSE_VERSION = _parse_version()


def load_demo_cached(path: str, progress=None) -> tuple["Demo", bool]:
    """
    load_demo through the disk cache of the data folder: (demo, from_cache).

    Only library demos are cached (their file name carries the content id);
    a demo parsed once opens again in a fraction of a second, also after the
    app was restarted. Any problem with the cache just means parsing again.
    """
    cached = store.read_parsed(path, PARSE_VERSION)
    if isinstance(cached, Demo):
        return cached, True
    demo = load_demo(path, progress=progress)
    store.write_parsed(path, PARSE_VERSION, demo)
    return demo, False


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


def _live_freeze_ends(parser) -> list[int]:
    """
    Sorted ticks of round_freeze_end events of the LIVE match: warmup
    freeze ends and everything before begin_new_match (restarts, the knife
    round) are dropped, so index i maps to live round i+1.
    """
    try:
        ev = parser.parse_events(
            ["round_freeze_end", "begin_new_match"]
        )
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
            ev_ticks += [int(x) for x in pd.to_numeric(d["tick"], errors="coerce").dropna()]
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
            d.drop_duplicates("tick")
            .sort_values("tick")
            .reset_index(drop=True)
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
    if deaths is not None and len(deaths) and {"tick", "attacker_steamid", "user_steamid"} <= set(
        deaths.columns
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
    if hurts is not None and len(hurts) and {"tick", "attacker_steamid", "user_steamid"} <= set(
        hurts.columns
    ):
        h = hurts[hurts["tick"].between(lo, hi)]
        for r in h.itertuples():
            a, u = str(r.attacker_steamid), str(r.user_steamid)
            # every pair of the round: the replay shows the whole round
            # (teammate trades too), not just the selected duelists
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

    # 4. shots with unknown speed (no t-1 tick / death / teleport right
    # before the shot). Better an honest NaN than a garbage number.
    n = int(shots["speed"].isna().sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "no_speed", "level": lvl(f, 0.05, 0.20), "n": n, "pct": f * 100}
    )

    # 5. shots without a living enemy nearby (dist unknown)
    n = int(shots["dist"].isna().sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "no_dist", "level": lvl(f, 0.05, 0.25), "n": n, "pct": f * 100}
    )

    # 6. physically impossible speeds
    n = (
        int((shots["speed"] > IMPOSSIBLE_SPEED).sum())
        if len(shots) and shots["speed"].notna().any()
        else 0
    )
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "fast_speeds", "level": lvl(f, 0.0001, 0.02), "n": n, "pct": f * 100}
    )

    # 7. shots without a round number -> alive-filter unreliable for them
    n = int((shots["round"] <= 0).sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append({"id": "round0", "level": lvl(f, 0.02, 0.20), "n": n, "pct": f * 100})

    # 8. implausible duel distances
    if len(duels) and duels["distance"].notna().any():
        dist = pd.to_numeric(duels["distance"], errors="coerce")
        n = int((dist.isna() | (dist <= 0) | (dist > MAX_DUEL_DIST)).sum())
        f = n / len(duels)
        checks.append(
            {"id": "duel_dist", "level": lvl(f, 0.02, 0.10), "n": n, "pct": f * 100}
        )
    else:
        checks.append({"id": "duel_dist", "level": "ok", "n": 0, "pct": 0.0})

    # 9. aim-error sanity (angle convention / offsets)
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

    # 11. unexpected hitgroup values
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

    # 12. scoreboard cross-check (notes s.14): the game's own
    # kills_total / deaths_total at the last event tick vs our tables.
    # Verified on all three real demos, every player:
    #   kills_total  == our enemy kills (knife/grenade kills included)
    #   deaths_total == our raw death rows (suicides and world deaths
    #                   included; pre-live knife/warmup rows excluded by
    #                   both sides - both aggregates reset at
    #                   begin_new_match)
    # Non-gun kills and teamkills get slack in the kills rule: the three
    # probed demos count both, but other servers/versions may not.
    # game > ours (kills) is always a red flag: we dropped something.
    tot = getattr(demo, "totals", None)
    if tot is not None and len(tot) and len(demo.players):
        dth = demo.deaths
        have_d = len(dth) and {"attacker_steamid", "user_steamid"} <= set(dth.columns)
        teams = demo.teams
        tset = tot.drop_duplicates("sid").set_index("sid")
        n_bad = 0
        n_cmp = 0
        worst: list[str] = []
        if have_d:
            ta = dth["attacker_steamid"].map(teams)
            tu = dth["user_steamid"].map(teams)
            enemy = ta.notna() & tu.notna() & (ta != tu)
        for p in demo.players.itertuples():
            if p.sid not in tset.index:
                continue
            g = tset.loc[p.sid]
            n_cmp += 1
            if have_d:
                mine = dth["attacker_steamid"] == p.sid
                gun_kill = mine & (dth["attacker_steamid"] != dth["user_steamid"])
                ours_k = int((gun_kill & enemy).sum())
                raw_d = int((dth["user_steamid"] == p.sid).sum())
                mk = dth[gun_kill]
                nongun = int((~mk["weapon"].map(is_gun)).sum()) if len(mk) else 0
                tk = (
                    int(
                        (
                            mk["attacker_steamid"].map(teams)
                            == mk["user_steamid"].map(teams)
                        ).sum()
                    )
                    if len(mk)
                    else 0
                )
            else:
                ours_k, raw_d, nongun, tk = 0, 0, 0, 0
            ok_k = ok_d = True
            if "kills" in tset.columns and pd.notna(g["kills"]):
                gk = int(g["kills"])
                ok_k = gk <= ours_k <= gk + nongun + tk
            if "deaths" in tset.columns and pd.notna(g["deaths"]):
                ok_d = int(g["deaths"]) == raw_d
            if not (ok_k and ok_d):
                n_bad += 1
                worst.append(
                    f"{p.name}: K {ours_k} vs {int(g['kills']) if pd.notna(g['kills']) else '?'}, "
                    f"D {raw_d} vs {int(g['deaths']) if pd.notna(g['deaths']) else '?'}"
                )
        f = n_bad / max(n_cmp, 1)
        checks.append(
            {
                "id": "scoreboard",
                "level": lvl(f, 0.05, 0.25),
                "n": n_bad,
                "vals": worst[:3],
            }
        )
    else:
        checks.append({"id": "scoreboard", "level": "ok", "n": 0, "vals": []})
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
    _openings = openings_table(demo)
    _impact = impact_stats(demo)
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
            "excluded": demo.excluded,
            "punch_available": "punch_pitch" in demo.ticks.columns,
            "mouse_rows": 0 if demo.mouse is None else len(demo.mouse),
            "openings": len(_openings),
            "trade_kills": int(_impact["trade_kills"].sum()) if len(_impact) else 0,
            "scoreboard_totals": (
                _records(demo.totals) if demo.totals is not None else None
            ),
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
