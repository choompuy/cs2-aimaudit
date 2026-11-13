"""
Constants, the Demo dataclass and pure helpers (weapons, angles,
speed, first-shot windows). No parsing, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
import numpy as np
import pandas as pd

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


# Weapon classes (CS2 internal names after norm_weapon).
# Everything unmapped is "other".
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
# Named button props supported by demoparser2 (README props table).
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
# Per-shot context from server fields:
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
# the player committed.
FLICK_WIN_TICKS = 16


# Trade window: a teammate avenging your death within this many seconds
# counts as a trade (the usual community definition).
TRADE_S = 5.0


# Eye / head heights (units, above the pawn origin). Crouching lowers both
# by ~18 units; we scale linearly with duck_amount (0..1).
EYE_STAND, HEAD_STAND, DUCK_DROP = 64.0, 62.0, 18.0


# Eye / head heights (units, above the pawn origin). Crouching lowers both
# by ~18 units; we scale linearly with duck_amount (0..1).
EYE_STAND, HEAD_STAND, DUCK_DROP = 64.0, 62.0, 18.0


# Eye / head heights (units, above the pawn origin). Crouching lowers both
# by ~18 units; we scale linearly with duck_amount (0..1).
EYE_STAND, HEAD_STAND, DUCK_DROP = 64.0, 62.0, 18.0


# Approximate horizontal size of the head hitbox (units). Used to express
# the aim error in "head widths": 5° at 200 units is a miss, 5° at 2000
# units can still be a headshot.
HEAD_WIDTH = 18.0


# Below these counts a rate is noise: the UI shows a small-sample hint.
MIN_N_SHOTS = 10


MIN_N_DUELS = 5


def wilson_ci(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score interval (in %) for a binomial rate k/n.

    With 8 duels a '62%' means almost nothing; a rate means little next
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


def weapon_class(w) -> str:
    """Class of a weapon name ('rifle', 'sniper', ..., 'other')."""
    return WEAPON_TO_CLASS.get(norm_weapon(w), "other")


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
    #                      knife_round, restart: round counts}
    tickrate: int = TICKRATE  # from the demo header (fallback: 64)
    sides: dict = field(default_factory=dict)  # round(1-based) -> sid -> T/CT
    totals: pd.DataFrame | None = None
    #                      game kills_total / deaths_total per player,
    #                      sampled at the last event tick (None when the
    #                      parser or props are unavailable) - input of the
    #                      data_checks scoreboard cross-check
    mouse: pd.DataFrame | None = None
    #                      usercmd mouse deltas (|dx|+|dy| per tick) around
    #                      first-shot windows, None when the demo/parser
    #                      does not provide them - input of flick_move

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
    the previous ENGAGE_GAP_TICKS ticks (~350 ms). A spray's
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
