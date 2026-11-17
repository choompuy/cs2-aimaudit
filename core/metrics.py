"""
Metric tables built from a parsed Demo: shots, duels, openings,
impact, per-player and per-weapon stats.
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    # annotation-only (F821): the `f: "Filters | None"` hints below resolve
    # without importing core.filters at runtime here
    from core.filters import Filters

from core.model import (
    Demo,
    FLASH_SHOT_S,
    FLICK_WIN_TICKS,
    HIT_WINDOW,
    NA,
    ROUND_SEC_MAX,
    SAFE_DIST,
    SLOWED_MAX,
    STRICT_DIST,
    TRADE_S,
    _counter_strafe_tap,
    _first_shot_arr,
    _round_col,
    _row_round_sec,
    _safe_int,
    _speed,
    _zone,
    allowed_speed,
    angle_err,
    head_ang,
    is_gun,
    norm_weapon,
    round_of_tick,
    weapon_class,
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


def _fire_rows(demo: Demo) -> pd.DataFrame:
    """The fire events with round / named zone / seconds-into-round."""
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
    return s.drop(
        columns=["user_last_place_name", "game_time", "round_start_time"],
        errors="ignore",
    )


def _shot_frames(demo: Demo, s: pd.DataFrame) -> pd.DataFrame:
    """Fire rows joined with the shooter's tick frame (speed, position,
    state), the tick-before speed, counter-strafe flags, dist and hit."""
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
        _counter_strafe_tap(rows.get((int(tk), sid)), rows.get((int(tk) - 1, sid)))
        for tk, sid in zip(out["tick"], out["sid"])
    ]
    out["dist"] = _nearest_enemy_dist(demo, out)
    out["hit"] = _match_hits(out, demo.hurts)
    return out


def _state_flags(out: pd.DataFrame) -> None:
    """flashed / slowed: True/False when the prop is known, NA when the
    demo/parser does not provide it (an old demo must not look like
    "0% flashed")."""
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


def _flick_moves(demo: Demo, out: pd.DataFrame) -> None:
    """first shot of an engagement (gap > ENGAGE_GAP_TICKS since the same
    sid's previous shot) and the mouse movement that preceded it: the
    sum of |dx|+|dy| over the FLICK_WIN_TICKS ticks before the shot.
    first_shot is always known; flick_move is NA for spray follow-ups
    (recoil control is not aim) and when the demo/parser has no usercmd
    props (missing is never 0, the buttons lesson)."""
    out["first_shot"] = _first_shot_arr(out["tick"], out["sid"])
    out["flick_move"] = np.nan
    if demo.mouse is None or not len(demo.mouse):
        return
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
    out = _shot_frames(demo, _fire_rows(demo))
    _state_flags(out)
    _flick_moves(demo, out)
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


class _DuelCtx:
    """Per-demo context the duel helpers share: fires, enemies, aim rule."""

    __slots__ = ("demo", "rows", "fires_by", "enemies_of", "win", "aim_deg")

    def __init__(self, demo: Demo, window_s: float, aim_deg: float):
        self.demo = demo
        self.rows = demo.rows
        self.fires_by = {
            k: g["tick"].astype(int).to_numpy()
            for k, g in demo.fires.groupby("user_steamid")
        }
        self.enemies_of = {
            sid: [
                o
                for o in demo.names
                if o != sid
                and demo.teams.get(o) not in (None, "?")
                and demo.teams.get(o) != demo.teams.get(sid)
            ]
            for sid in demo.names
        }
        self.win = int(window_s * demo.tickrate)
        self.aim_deg = aim_deg


def _aimed_at(ctx: _DuelCtx, ts: int, sh: str, opp: str) -> bool:
    """Was the shooter's crosshair within aim_deg of that opponent?"""
    ra, ro = ctx.rows.get((ts, sh)), ctx.rows.get((ts, opp))
    if ra is None or ro is None:
        return True  # no data -> keep the shot (old behaviour)
    e = angle_err(ra, ro)
    return not np.isfinite(e) or e <= ctx.aim_deg


def _aimed_elsewhere(ctx: _DuelCtx, ts: int, sh: str, opp: str) -> bool:
    """Did the shooter rather aim at a DIFFERENT enemy at that tick?"""
    ra = ctx.rows.get((ts, sh))
    if ra is None:
        return False
    for other in ctx.enemies_of.get(sh, []):
        if other == opp:
            continue
        ro = ctx.rows.get((ts, other))
        if ro is None:
            continue
        e = angle_err(ra, ro)
        if np.isfinite(e) and e <= ctx.aim_deg:
            return True
    return False


class DuelShots(NamedTuple):
    """The window shots of one duel: aimed / raw, first ticks, confidence."""

    kw: np.ndarray
    vw: np.ndarray
    kw_raw: np.ndarray
    vw_raw: np.ndarray
    kf: int | None
    vf: int | None
    low_conf: bool


def _shots_in_window(ctx: _DuelCtx, sid: str, t: int) -> np.ndarray:
    """The shots `sid` fired in the window before tick t."""
    a = ctx.fires_by.get(sid, np.array([], dtype=int))
    return a[(a > t - ctx.win) & (a <= t)]


def _duel_shots(ctx: _DuelCtx, k: str, v: str, t: int) -> DuelShots:
    """Both sides' aimed window shots + who fired first."""
    kw_raw, vw_raw = _shots_in_window(ctx, k, t), _shots_in_window(ctx, v, t)
    kw = np.array([int(ts) for ts in kw_raw if _aimed_at(ctx, int(ts), k, v)], dtype=int)
    vw = np.array([int(ts) for ts in vw_raw if _aimed_at(ctx, int(ts), v, k)], dtype=int)
    low_conf = any(_aimed_elsewhere(ctx, int(ts), k, v) for ts in kw_raw) or any(
        _aimed_elsewhere(ctx, int(ts), v, k) for ts in vw_raw
    )
    return DuelShots(
        kw,
        vw,
        kw_raw,
        vw_raw,
        int(kw.min()) if len(kw) else None,
        int(vw.min()) if len(vw) else None,
        bool(low_conf),
    )


def _duel_dist(ctx: _DuelCtx, r, fa: str, fb: str, f_tick) -> float:
    """Distance for head-size maths: the game value, else positions."""
    d = getattr(r, "distance", np.nan)
    if pd.notna(d) and d > 0:
        return float(d)
    a, b = ctx.rows.get((f_tick, fa)), ctx.rows.get((f_tick, fb))
    if a is not None and b is not None:
        return float(
            np.sqrt((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2 + (a.Z - b.Z) ** 2)
        )
    return float("nan")


def _first_shooter(kf: int | None, vf: int | None) -> str:
    """Who fired first: killer / victim / simultaneous / none."""
    if kf is None and vf is None:
        return "none"
    if vf is None or (kf is not None and kf < vf):
        return "killer"
    if kf is None or vf < kf:
        return "victim"
    return "simultaneous"


def _err_hw(err: float, ang: float) -> float:
    """Aim error in head widths, NaN when either value is unknown."""
    if np.isfinite(err) and np.isfinite(ang) and ang > 0:
        return float(err / ang)
    return np.nan


def _would_head(err: float, ang: float) -> bool:
    """Crosshair inside the head box (within half a head width of
    its center) at the moment of the first shot."""
    return bool(np.isfinite(err) and np.isfinite(ang) and err <= ang / 2)


def _duel_row(ctx: _DuelCtx, r, rd: int, s: DuelShots) -> dict:
    """The build_duels output row for one enemy-kill event."""
    demo = ctx.demo
    k, v, t = r.attacker_steamid, r.user_steamid, int(r.tick)
    kf, vf = s.kf, s.vf
    k_err = (
        angle_err(ctx.rows.get((kf, k)), ctx.rows.get((kf, v)))
        if kf is not None
        else np.nan
    )
    v_err = (
        angle_err(ctx.rows.get((vf, v)), ctx.rows.get((vf, k)))
        if vf is not None
        else np.nan
    )
    k_ang = head_ang(_duel_dist(ctx, r, k, v, kf))
    v_ang = head_ang(_duel_dist(ctx, r, v, k, vf))
    start = (
        min(x for x in (kf, vf) if x is not None)
        if (kf is not None or vf is not None)
        else t
    )
    return {
        "round": rd,
        "tick": t,
        "killer": k,
        "victim": v,
        "killer_side": demo.side_at(rd, k),
        "victim_side": demo.side_at(rd, v),
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
        "first": _first_shooter(kf, vf),
        "killer_shots": len(s.kw),
        "victim_shots": len(s.vw),
        "killer_shots_raw": len(s.kw_raw),
        "victim_shots_raw": len(s.vw_raw),
        "low_conf": s.low_conf,
        "killer_first_tick": kf,
        "victim_first_tick": vf,
        "duration_s": round((t - start) / demo.tickrate, 2),
        "killer_speed": _speed(ctx.rows.get((kf, k))) if kf is not None else np.nan,
        "victim_speed": _speed(ctx.rows.get((vf, v))) if vf is not None else np.nan,
        "killer_err": k_err,
        "victim_err": v_err,
        "killer_err_hw": _err_hw(k_err, k_ang),
        "victim_err_hw": _err_hw(v_err, v_ang),
        "killer_would_head": _would_head(k_err, k_ang),
        "victim_would_head": _would_head(v_err, v_ang),
    }


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
    ctx = _DuelCtx(demo, window_s, aim_deg)
    d = demo.deaths
    if not len(d) or "attacker_steamid" not in d.columns:
        return pd.DataFrame()
    d = d[
        d["attacker_steamid"].isin(demo.names)
        & d["user_steamid"].isin(demo.names)
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
        if demo.teams.get(k) == demo.teams.get(v):
            continue
        # the round follows the demo's round windows (a post-round kill
        # already counted in the next round still belongs to this one),
        # total_rounds_played is the fallback when there are no windows
        rd = round_of_tick(getattr(demo, "round_windows", None) or {}, t)
        if rd is None:
            rd = _safe_int(getattr(r, "total_rounds_played", 0)) + 1
        out.append(_duel_row(ctx, r, rd, _duel_shots(ctx, k, v, t)))
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
                    "round_sec": df["round_sec"],
                    "my_zone": df[f"{mine}_zone"],
                    "opp_zone": df[f"{opp}_zone"],
                }
            )
        )
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts).sort_values("tick").reset_index(drop=True)


def _top_weapon(de: pd.DataFrame, ok: np.ndarray, who: str) -> dict[str, str]:
    """sid -> the weapon carried by the most of this player's rows in
    de[ok] (enemy kills / deaths); ties break alphabetically."""
    if "weapon" not in de.columns or not len(de):
        return {}
    sub = de.loc[ok, [who, "weapon"]]
    sub = sub[sub["weapon"].astype(str).str.len() > 0]
    if not len(sub):
        return {}
    top = (
        sub.groupby([who, "weapon"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values([who, "n", "weapon"], ascending=[True, False, True])
        .drop_duplicates(who)
    )
    return dict(zip(top[who], top["weapon"]))


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


def _round_side_keep(demo: Demo, f: "Filters | None"):
    """
    keep(round_no, sid) -> bool for the round-structure metrics (K/D,
    openings, trades, multi-kills, assists), or None when no filter applies.

    Only the filters that mean something for a PLAYER in a ROUND are used:
    the round range / pistol preset and the side the player had in that
    round (Demo.side_at, so a halftime swap is handled per round). Weapon,
    class and distance are shot-level filters and stay out of it. None is
    also returned when the deaths carry no round counter - guessing rounds
    would drop or keep the wrong kills.
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


def round_outcomes(demo: Demo) -> dict[int, dict]:
    """
    How every round of the replay ended: {"winner": 'CT'/'T'/'', "reason":
    one of core.ROUND_REASONS or '', "end": tick | None, "inferred": bool}.

    The demo's own ``round_end`` event is the source whenever it is there
    (Demo.round_info). Many demos do not carry it, so a round without one is
    INFERRED from what the demo does tell, in this order:

    1. the bomb exploded -> T won by the bomb (even if every T died after the
       plant: the explosion decides the round);
    2. the bomb was defused -> CT won by the defuse;
    3. a side lost every player -> the other side won by elimination;
    4. the demo reports bomb events but this round had no plant and nobody
       was eliminated -> CT won on time.

    Anything else stays blank (winner '') and the UI shows no result for it.
    ``inferred`` marks 1-4 so the UI can say it is a reading of the events.
    """
    guessed = _guess_round_winners(demo)
    out: dict[int, dict] = {}
    for n in sorted(demo.round_windows or {}):
        info = (demo.round_info or {}).get(n) or {}
        if info.get("winner") in ("CT", "T"):
            out[int(n)] = {
                "winner": info["winner"],
                "reason": info.get("reason") or "",
                "end": info.get("end"),
                "inferred": False,
            }
            continue
        bombs = info.get("bomb") or []
        kinds = {b[1]: b[0] for b in bombs}
        if "boom" in kinds:
            res = ("T", "bomb", kinds["boom"])
        elif "defuse" in kinds:
            res = ("CT", "defuse", kinds["defuse"])
        elif n in guessed:
            res = (guessed[n], "elim", None)
        elif info.get("bomb_known") and "plant" not in kinds:
            res = ("CT", "time", None)
        else:
            res = ("", "", None)
        out[int(n)] = {
            "winner": res[0],
            "reason": res[1],
            "end": res[2],
            "inferred": bool(res[0]),
        }
    return out


def round_winners(demo: Demo) -> dict[int, str]:
    """
    Round winners ('CT'/'T') for the replay's round bar (see round_outcomes:
    the demo's own round_end first, then the bomb events and the kills).
    A round whose result cannot be told stays absent from the dict.
    """
    return {n: o["winner"] for n, o in round_outcomes(demo).items() if o["winner"]}


def _round_fielded(sides: dict, r: int) -> dict[str, int] | None:
    """Players each side fielded in round r, None when a side is unknown
    (nothing to compare)."""
    fielded = {"T": 0, "CT": 0}
    for s in sides[r].values():
        if s in fielded:
            fielded[s] += 1
    if fielded["T"] < 1 or fielded["CT"] < 1:
        return None
    return fielded


def _round_death_rows(d, dr, warm, r: int, win) -> pd.DataFrame:
    """The round's death rows: its own tick window when known, else the
    round column (a death after the round ended carries the NEXT round's
    number but belongs to this one)."""
    if win is not None:
        return d[d["tick"].between(win[0], win[1]) & ~warm]
    return d[(dr == r - 1) & ~warm]


def _guess_round_winners(demo: Demo) -> dict[int, str]:
    """
    The elimination-based guess behind round_outcomes: "a side lost every
    player in that round" - then the other side won it. Every death counts
    towards the VICTIM's side loss count (kills, teamkills, suicides/world,
    the bomb), because the team did lose that player either way. Halftime
    swaps are per round (demo.sides), a missing side for a round skips it.
    Known wrong edge case, accepted: T eliminated post-plant while the bomb
    still explodes - round_outcomes decides that one by the explosion first.
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
        d["is_warmup_period"].astype(bool)
        if "is_warmup_period" in d.columns
        else pd.Series(False, index=d.index)
    )
    out: dict[int, str] = {}
    for r in rounds:
        fielded = _round_fielded(sides, r)
        if fielded is None:
            continue
        rows = _round_death_rows(d, dr, warm, r, (demo.round_windows or {}).get(r))
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


def openings_table(demo: Demo) -> pd.DataFrame:
    """
    The first enemy kill of every round - both teams, match context.

    An 'opening duel' is the round's first death caused by an enemy:
    suicides / world damage / teamkill rows are skipped and the next valid
    death of that round becomes the opening (on the real demos 2-3 rounds
    per match start with such a row). ANY weapon counts,
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


def _keep_ok(keep, round_idx, sid) -> bool:
    """The _round_side_keep predicate: no filter -> everything counts."""
    return keep is None or keep(int(round_idx) + 1, sid)


def _impact_openings(de: pd.DataFrame, res: dict, keep) -> None:
    """Opening duels: attempts (killer or victim of the first enemy kill)
    and wins (the killer)."""
    op = de.groupby("round_idx", sort=True).head(1)
    for r in op.itertuples():
        k, v = r.attacker_steamid, r.user_steamid
        for sid in (k, v):
            if sid in res and _keep_ok(keep, r.round_idx, sid):
                res[sid]["open_attempts"] += 1
        if k in res and _keep_ok(keep, r.round_idx, k):
            res[k]["open_wins"] += 1


def _impact_multikills(de: pd.DataFrame, res: dict, keep) -> None:
    """Deaths + k2..k5: ROUNDS with exactly that many enemy kills - one
    3-kill round adds 1 to k3, not 3."""
    for r in de.itertuples():
        if r.user_steamid in res and _keep_ok(keep, r.round_idx, r.user_steamid):
            res[r.user_steamid]["deaths"] += 1
    for (sid, _rd), n in (
        de.groupby(["attacker_steamid", "round_idx"]).size().items()
    ):
        if sid in res and 2 <= int(n) <= 5 and _keep_ok(keep, _rd, sid):
            res[sid][f"k{int(n)}"] += 1


def _impact_trades(
    demo: Demo, de: pd.DataFrame, res: dict, keep, trade_s: float
) -> None:
    """Trades: for each death, did a teammate kill its attacker within the
    window (same round)? death_of maps (round, VICTIM) -> (tick, killer):
    the attacker's own death row is the candidate avenging kill. The
    attacker dies at most once per round, so one candidate row is enough."""
    teams = demo.teams
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
        if avenger in res and _keep_ok(keep, r.round_idx, avenger):
            res[avenger]["trade_kills"] += 1
        if v in res and _keep_ok(keep, r.round_idx, v):
            res[v]["traded_deaths"] += 1


def _impact_assists(de: pd.DataFrame, res: dict, keep, names) -> None:
    """Assists (any form) and the assistedflash subset; NaN when the
    demo/parser has no assister column."""
    if "assister_steamid" not in de.columns:
        return
    for sid in res:
        res[sid]["assists"] = 0
        res[sid]["flash_assists"] = 0
    ast = de[de["assister_steamid"].isin(names)]
    if keep is not None and len(ast):
        ast = ast[
            np.array(
                [
                    _keep_ok(keep, ri, a)
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
        _impact_openings(de, res, keep)
        _impact_multikills(de, res, keep)
        _impact_trades(demo, de, res, keep, trade_s)
        _impact_assists(de, res, keep, names)
        for sid, s in res.items():
            if s["open_attempts"]:
                s["open_rate"] = round(s["open_wins"] / s["open_attempts"] * 100, 1)
            if s["deaths"]:
                s["traded_pct"] = round(s["traded_deaths"] / s["deaths"] * 100, 1)
    return pd.DataFrame(res[s] for s in names)


def _kd_setup(demo: Demo, f, de: pd.DataFrame, have_d: bool):
    """Round/side-filtered K/D tallies and top weapons from the enemy-kill
    rows (``de`` empty -> no tallies, every player falls back to 0/0)."""
    keep = _round_side_keep(demo, f)
    kills_by: dict = {}
    deaths_by: dict = {}
    best_w: dict[str, str] = {}  # sid -> the weapon the most kills came from
    died_w: dict[str, str] = {}  # sid -> the weapon the most deaths came from
    if keep is not None and len(de):
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
        best_w = _top_weapon(de, k_ok, "attacker_steamid")
        died_w = _top_weapon(de, v_ok, "user_steamid")
    elif len(de):  # no round/side filter: every enemy kill counts
        k_ok = np.ones(len(de), dtype=bool)
        best_w = _top_weapon(de, k_ok, "attacker_steamid")
        died_w = _top_weapon(de, k_ok, "user_steamid")
    return keep, kills_by, deaths_by, best_w, died_w


def _player_kd(
    p_sid: str,
    demo: Demo,
    duels: pd.DataFrame,
    have_d: bool,
    keep,
    kills_by: dict,
    deaths_by: dict,
) -> tuple[int, int]:
    """One player's kills/deaths, round structure (not shot filters)."""
    if keep is not None and have_d:
        return int(kills_by.get(p_sid, 0)), int(deaths_by.get(p_sid, 0))
    if have_d:
        # K/D like the in-game scoreboard: every ENEMY kill/death with
        # any weapon (knife and grenades included), whole match. Duels
        # above are gun-only, so the two must not be mixed.
        # Static teams are fine here: teammates share the label before
        # and after the halftime swap. World/suicide deaths have no
        # known attacker and stay out, teamkills stay out both ways.
        dth, teams = demo.deaths, demo.teams
        ta = dth["attacker_steamid"].map(teams)
        tu = dth["user_steamid"].map(teams)
        enemy = ta.notna() & tu.notna() & (ta != tu)
        kills = int(
            (
                (dth["attacker_steamid"] == p_sid)
                & (dth["attacker_steamid"] != dth["user_steamid"])
                & enemy
            ).sum()
        )
        deaths = int(
            (
                (dth["user_steamid"] == p_sid)
                & (dth["attacker_steamid"] != dth["user_steamid"])
                & enemy
            ).sum()
        )
        return kills, deaths
    return (
        int((duels["killer"] == p_sid).sum()) if len(duels) else 0,
        int((duels["victim"] == p_sid).sum()) if len(duels) else 0,
    )


class _PlayerFrames(NamedTuple):
    """One player's filtered shot/duel slices + the risky-shot percentage."""

    sh: pd.DataFrame
    hi: pd.DataFrame
    dd: pd.DataFrame
    kd: pd.DataFrame
    fs: pd.DataFrame
    fsh: pd.DataFrame
    risky: float


def _player_frames(demo, shots, duels, hurts, p, thr, safe, strict) -> _PlayerFrames:
    """Slice the frames down to one player and derive the risky %."""
    sh = shots[shots["sid"] == p.sid]
    hi = _hurts_of(demo, p.sid, hurts)
    dd = duels_for(duels, p.sid) if len(duels) else pd.DataFrame()
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
    return _PlayerFrames(sh, hi, dd, kd, fs, fsh, risky)


def _player_row(demo: Demo, p, fr: _PlayerFrames, kills: int, deaths: int, thr: float, open_rate: dict, best_w: dict, died_w: dict) -> dict:
    """player_summary's output row: one player's metric cells (NaN when
    the player has no data for a metric)."""
    sh, hi, dd, kd, fs, fsh = fr.sh, fr.hi, fr.dd, fr.kd, fr.fs, fr.fsh
    return {
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
        "risky": fr.risky,
        "first_shot": (
            round((fs["first"] == "me").mean() * 100, 1) if len(fs) else np.nan
        ),
        # first-shot accuracy: hit rate on first shots of
        # engagements (crosshair placement quality)
        "first_acc": (
            round(fsh["hit"].mean() * 100, 1) if len(fsh) else np.nan
        ),
        # opening-duel win rate: share of the rounds' first enemy
        # kills won (killer vs victim)
        "open_rate": open_rate.get(p.sid, np.nan),
        "aim_err": (
            round(dd["my_err"].median(), 1)
            if len(dd) and dd["my_err"].notna().any()
            else np.nan
        ),
        "duration": round(dd["duration_s"].median(), 2) if len(dd) else np.nan,
        # icon columns: the favorite weapon and the one dying to
        "best_weapon": best_w.get(p.sid, ""),
        "died_to": died_w.get(p.sid, ""),
    }


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
    # opening-duel win rate (impact_stats): whole-match, like
    # the K/D below - it is round structure, not a filterable shot metric
    open_rate = impact_stats(demo, f=f).set_index("sid")["open_rate"].to_dict()
    de = _enemy_kill_rows(demo) if have_d else pd.DataFrame()
    keep, kills_by, deaths_by, best_w, died_w = _kd_setup(demo, f, de, have_d)
    for p in demo.players.itertuples():
        fr = _player_frames(demo, shots, duels, hurts, p, thr, safe, strict)
        kills, deaths = _player_kd(
            p.sid, demo, duels, have_d, keep, kills_by, deaths_by
        )
        res.append(
            _player_row(demo, p, fr, kills, deaths, thr, open_rate, best_w, died_w)
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
        # shots of engagements (the AWP/USP number that matters);
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
