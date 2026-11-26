"""Constants, the Demo dataclass and pure helpers. No parsing, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Sequence
import numpy as np
import pandas as pd

__all__ = [
    "BTN_BITS", "BTN_NAMED", "BUTTONS", "Demo", "NA", "ROUND_REASONS", "SAFE_DIST",
    "STRICT_DIST", "WEAPON_CLASSES", "allowed_speed", "angle_err", "build_round_info",
    "build_round_windows", "classify_round_end", "head_ang", "norm_weapon",
    "rank_in_lobby", "round_of_tick", "weapon_class", "wilson_ci",
]

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


# CS2 internal names after norm_weapon; everything unmapped is "other".
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
# LEFT/RIGHT are strafe A/D
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


# not exposed as named props: decoded from the raw bitfield (Source IN_* codes)
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


# movement keys and the direction they push (deg relative to yaw)
STRAFE_DIRS = {"FORWARD": 0.0, "LEFT": 90.0, "BACK": 180.0, "RIGHT": -90.0}


# velocity_X/Y are NOT requested: demoparser2 derives them from the REQUESTED tick rows,
# so a sparse tick list inflates every movement (see _add_speed).
POS = ["X", "Y", "Z", "pitch", "yaw"]


# drops garbage rows of a dead pawn (stale buttons, frozen positions)
ALIVE_PROPS = ["is_alive"]


# recoil punch; unknown to the parser -> a plain POS parse
PUNCH_PROPS = ["aim_punch_angle"]


# Crouch amount (m_flDuckAmount, 0..1): shifts eye/head height down ~18u.
DUCK_PROPS = ["duck_amount"]


TEAMS = {2: "T", 3: "CT"}


SIDE_PROPS = ["team_num"]  # m_iTeamNum per tick: the real per-round side


# ------------------------------------------------- distance-aware risk model
# A heuristic, NOT the real spread table: any speed is accepted up to SAFE_DIST, only
# speed <= thr from STRICT_DIST, linear in between.
SAFE_DIST = 500.0


STRICT_DIST = 1500.0


FULL_SPEED = 250.0  # ~ running speed with a rifle


IMPOSSIBLE_SPEED = 350.0  # above this the speed data itself looks broken


# above this a position delta is a teleport / respawn / dormancy artifact -> NaN
MAX_SPEED = 600.0


# a hurt belongs to the shooter's latest same-weapon shot at most this many ticks
# earlier
HIT_WINDOW = 3


MAX_DUEL_DIST = 10000.0
# player_death.distance is in metres, everything else in game units (inches)
UNITS_PER_M = 1 / 0.0254


# ------------------------------------------------- shot context
# blindness left for a flashed shot, the slowed threshold, the valid round clock
FLASH_SHOT_S = 0.5


SLOWED_MAX = 0.99


ROUND_SEC_MAX = 300.0


# first shot of an engagement: no shot by the player in this many ticks (~350 ms)
ENGAGE_GAP_TICKS = 22


# mouse movement is summed over this many ticks (~250 ms) before a first shot
FLICK_WIN_TICKS = 16


# a death avenged by a teammate within this is a trade
TRADE_S = 5.0


# eye / head heights above the pawn origin; crouching lowers both by DUCK_DROP
EYE_STAND, HEAD_STAND, DUCK_DROP = 64.0, 62.0, 18.0


# approximate head hitbox width (units): the aim error in "head widths"
HEAD_WIDTH = 18.0


# Below these counts a rate is noise: the UI shows a small-sample hint.
MIN_N_SHOTS = 10


MIN_N_DUELS = 5


def wilson_ci(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval (%) of a binomial rate k/n; NaNs when n <= 0."""

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
    """(position, n) of player `sid` in a summary column, 1 = best; NaN players are
    skipped. None when there is nothing to compare."""

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
    """Max acceptable speed of a shot at `dist`: vmax up to `safe`, thr from `strict`,
    linear in between. NaN distance -> NaN."""

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
    """DUCK / JUMP from the raw `buttons` bitfield (far below 2^53: float64 is safe)."""

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
    """True when every held movement key at the shot tick (or the one before) pushed
    AGAINST the movement: an active counter-strafe, not just releasing keys."""

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
    """Seconds into the live round (freeze time excluded); NaN when the fields are absent
    or the clock is bad."""

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
    """Angle (deg) between a's bullet direction (view + recoil punch) and t's head;
    crouch-aware, approximate heights."""

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
    """Angular size (deg) of the head at `dist` units; NaN for dist <= 0."""

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
    # excluded rounds: {warmup, knife_round, restart: count}
    tickrate: int = TICKRATE  # from the demo header (fallback: 64)
    sides: dict = field(default_factory=dict)  # round(1-based) -> sid -> T/CT
    totals: pd.DataFrame | None = None
    # scoreboard kills / deaths per player at the last event tick (None: unknown)
    mouse: pd.DataFrame | None = None
    # usercmd mouse deltas around the first-shot windows (None: not in the demo)
    round_windows: dict = field(default_factory=dict)
    # {round: (t0, t1)} tick windows of the played rounds
    round_info: dict = field(default_factory=dict)
    # {round: how it ended}, see build_round_info

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
        return dict(zip(self.players["sid"], self.players["name"], strict=True))

    @cached_property
    def teams(self) -> dict[str, str]:
        return dict(zip(self.players["sid"], self.players["team"], strict=True))


# How a round ended (the key of Demo.round_info[n]["reason"]).
ROUND_REASONS = ("bomb", "defuse", "elim", "time", "surrender", "hostage", "draw", "other")
# Three spellings: the event's `message`, demoparser2's word in `reason` when it builds
# round_end itself, and the numeric code.
_REASON_BY_MESSAGE = (
    ("Target_Bombed", "bomb"),
    ("Bomb_Defused", "defuse"),
    ("Target_Saved", "time"),
    ("Surrender", "surrender"),
    ("Hostages", "hostage"),
    ("Round_Draw", "draw"),
    ("Terrorists_Win", "elim"),
    ("CTs_Win", "elim"),
)
_REASON_BY_CODE = {
    1: "bomb",
    7: "defuse",
    8: "elim",
    9: "elim",
    10: "draw",
    11: "hostage",
    12: "time",
    13: "hostage",
    17: "surrender",
    18: "surrender",
}
# escape / VIP words and t_saved stay "other"
_REASON_BY_WORD = {
    "bomb_exploded": "bomb",
    "bomb_defused": "defuse",
    "t_killed": "elim",
    "ct_killed": "elim",
    "time_ran_out": "time",
    "t_surrender": "surrender",
    "ct_surrender": "surrender",
    "hostage_rescued": "hostage",
    "ct_reached_hostage": "hostage",
    "RoundEndReasonHostagesNotRescued": "hostage",
    "draw": "draw",
}


def classify_round_end(message, code=None) -> str:
    """round_end -> one of ROUND_REASONS (message, then the word, then the number)."""
    msg = str(message or "")
    for needle, key in _REASON_BY_MESSAGE:
        if needle in msg:
            return key
    if isinstance(code, str) and code in _REASON_BY_WORD:
        return _REASON_BY_WORD[code]
    try:
        return _REASON_BY_CODE.get(int(code), "other")
    except (TypeError, ValueError):
        return "other"


def _side_of_winner(w) -> str:
    """round_end.winner (team number 2 = T, 3 = CT, or a name) -> 'T'/'CT'/''."""
    s = str(w).strip().upper()
    if s in ("2", "2.0", "T", "TERRORIST", "TERRORISTS"):
        return "T"
    if s in ("3", "3.0", "CT", "COUNTER-TERRORIST", "COUNTERTERRORIST"):
        return "CT"
    return ""


def build_round_windows(
    freeze_ends,
    officially_ended,
    events,
    tickrate: int = TICKRATE,
    max_s: float = 180.0,
    marks=(),
) -> dict[int, tuple[int, int]]:
    """{round: (t0, t1)} tick windows of the played rounds.

    Round i starts at freeze_ends[i]; it ends one tick before its round_officially_ended
    (on that tick everybody is respawned), else 5 s after its last event / mark, capped
    by the next freeze end and start + max_s. Windows without events are skipped. The
    round NUMBER comes from the events inside (a demo may start mid-match)."""
    fe = sorted(int(x) for x in (freeze_ends or []))
    if not fe or events is None or not len(events):
        return {}
    off = sorted(int(x) for x in (officially_ended or []))
    marks = [int(x) for x in marks]
    cap = int(max_s * tickrate)
    five_s = 5 * tickrate
    ev = events
    if "tick" not in ev.columns:
        return {}
    ev = ev.assign(
        _t=pd.to_numeric(ev["tick"], errors="coerce"),
        _r=(
            pd.to_numeric(ev["round"], errors="coerce").fillna(0).astype(int)
            if "round" in ev.columns
            else 0
        ),
    ).dropna(subset=["_t"])
    out: dict[int, tuple[int, int]] = {}
    used: set[int] = set()
    for i, start in enumerate(fe):
        hard = fe[i + 1] - 1 if i + 1 < len(fe) else start + cap
        hard = min(hard, start + cap)
        inside = ev[(ev["_t"] >= start) & (ev["_t"] <= hard)]
        named = inside[inside["_r"] >= 1]
        if not len(named):
            continue
        n0 = int(named.sort_values("_t")["_r"].iloc[0])
        # looked up inside the window: pairing by position breaks on warmup / restarts
        cands = [o for o in off if start < o <= hard]
        if cands:
            end = max(min(cands) - 1, start + 1)
        else:
            last = max([int(inside["_t"].max()), *(m for m in marks if start <= m <= hard)])
            end = min(hard, last + five_s)
        n = n0
        while n in used:
            n += 1
        used.add(n)
        out[n] = (start, end)
    return out


def round_of_tick(windows, tick) -> int | None:
    """Round number whose window contains ``tick``; None outside all."""
    try:
        t = int(tick)
    except (TypeError, ValueError):
        return None
    for n, (t0, t1) in sorted((windows or {}).items()):
        if t0 <= t <= t1:
            return n
    return None


def build_round_info(
    windows: dict[int, tuple[int, int]],
    round_end: pd.DataFrame | None,
    bombs: Sequence[tuple[int, str, str]],
    bomb_known: bool = False,
) -> dict[int, dict]:
    """round -> how it ended, from the demo's ``round_end`` rows and bomb events [(tick,
    plant / defuse_start / defuse / boom, sid)]. Without a ``round_end`` (many demos)
    winner and reason are '' and round_outcomes infers them. ``bomb_known``: the demo
    reports bomb events at all."""
    out: dict[int, dict] = {}
    ends: list[tuple[int, str, str]] = []
    if round_end is not None and len(round_end) and "tick" in round_end.columns:
        for r in round_end.itertuples(index=False):
            tk = pd.to_numeric(getattr(r, "tick", None), errors="coerce")
            if pd.isna(tk):
                continue
            ends.append(
                (
                    int(tk),
                    _side_of_winner(getattr(r, "winner", "")),
                    classify_round_end(getattr(r, "message", ""), getattr(r, "reason", None)),
                )
            )
    for n, (lo, hi) in windows.items():
        hit = [e for e in ends if lo <= e[0] <= hi + 1]
        # the round_end of the round is its LAST one inside the window
        end = max(hit, key=lambda e: e[0]) if hit else None
        out[int(n)] = {
            "winner": end[1] if end else "",
            "reason": end[2] if end else "",
            "end": end[0] if end else None,
            "bomb": sorted(b for b in bombs if lo <= int(b[0]) <= hi),
            "bomb_known": bool(bomb_known),
        }
    return out


def _first_shot_arr(ticks, sids) -> np.ndarray:
    """True where a row is the player's FIRST shot of an engagement (no shot in the
    previous ENGAGE_GAP_TICKS). Rows may arrive in any order."""

    n = len(ticks)
    if not n:
        return np.zeros(0, dtype=bool)
    # plain values: a filtered table carries a holey index
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
    """Sorted ticks of the FLICK_WIN_TICKS window before every first shot."""

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
