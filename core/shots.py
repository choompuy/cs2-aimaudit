"""The per-shot table plus the stop-quality and risk annotations."""

from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from core.model import (
    FLASH_SHOT_S,
    FLICK_WIN_TICKS,
    HIT_WINDOW,
    NA,
    ROUND_SEC_MAX,
    SAFE_DIST,
    SLOWED_MAX,
    STRICT_DIST,
    Demo,
    _counter_strafe_tap,
    _first_shot_arr,
    _round_col,
    _zone,
    allowed_speed,
    weapon_class,
)

__all__ = ["add_risk", "add_stop_quality", "shots_table"]


def _nearest_enemy_dist(demo: Demo, shots: pd.DataFrame) -> pd.Series:
    """Distance from each shot to the nearest LIVING enemy at that tick (approximate; NaN
    when none). A death at the shot's own tick counts as alive: it may be this shot's
    victim."""

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
    """hit[i] = this shot damaged an enemy: each hurt goes to the shooter's LATEST
    same-weapon shot within HIT_WINDOW ticks before it."""

    hit = np.zeros(len(shots), dtype=bool)
    if not len(hurts) or not {"attacker_steamid", "weapon", "tick"}.issubset(
        hurts.columns
    ):
        return hit
    by_key: dict[tuple, list[tuple[int, int]]] = {}
    for i, (sid, w, t) in enumerate(
        zip(shots["sid"], shots["weapon"], shots["tick"].astype(int), strict=True)
    ):
        by_key.setdefault((sid, w), []).append((int(t), i))
    for lst in by_key.values():
        lst.sort()
    for sid, w, th in zip(
        hurts["attacker_steamid"], hurts["weapon"], hurts["tick"].astype(int)
    , strict=True):
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
        c for c in ("flash_left", "velo_modifier") if c in demo.ticks.columns
    ]
    t = demo.ticks[tcols].rename(columns={"steamid": "sid"})
    out = s.merge(t, on=["tick", "sid"], how="left").reset_index(drop=True)
    # speed on the tick BEFORE the shot
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
        for tk, sid in zip(out["tick"], out["sid"], strict=True)
    ]
    out["dist"] = _nearest_enemy_dist(demo, out)
    out["hit"] = _match_hits(out, demo.hurts)
    return out


def _state_flags(out: pd.DataFrame) -> None:
    """flashed / slowed: NA when the demo does not provide the prop, never False."""
    if "flash_left" in out.columns:
        fd = pd.to_numeric(out["flash_left"], errors="coerce")
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
    """first_shot (a new engagement) and flick_move, the mouse movement over
    FLICK_WIN_TICKS before it (NA for follow-ups and without usercmd props)."""
    out["first_shot"] = _first_shot_arr(out["tick"], out["sid"])
    out["flick_move"] = np.nan
    if demo.mouse is None or not len(demo.mouse):
        return
    _by = {sid: g.set_index("tick")["mag"] for sid, g in demo.mouse.groupby("sid")}
    _fm = []
    for t, first, sid in zip(out["tick"], out["first_shot"], out["sid"], strict=True):
        if not first or sid not in _by:
            _fm.append(np.nan)
            continue
        got = _by[sid].reindex(range(int(t) - FLICK_WIN_TICKS, int(t)))
        v = got.sum(min_count=1)
        _fm.append(float(v) if pd.notna(v) else np.nan)
    out["flick_move"] = _fm


def shots_table(demo: Demo) -> pd.DataFrame:
    """One row per gun shot: position, speed, distance to the nearest living enemy, hit,
    zone, round_sec, flashed / slowed."""
    out = _shot_frames(demo, _fire_rows(demo))
    _state_flags(out)
    _flick_moves(demo, out)
    out = out.drop(columns=["flash_left", "velo_modifier"], errors="ignore")
    out["side"] = [
        demo.side_at(int(r), sid) for r, sid in zip(out["round"], out["sid"], strict=True)
    ]
    out["weapon_class"] = out["weapon"].map(weapon_class)
    return out


def add_stop_quality(shots: pd.DataFrame, thr: float) -> pd.DataFrame:
    """Adds `stop`: 'stopped' (crossed under `thr` exactly on this tick), 'standing',
    'moving' or '' (unknown)."""

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
    """Adds `allowed_speed` and `risky` (unknown speed/distance -> not risky)."""

    out = shots.copy()
    out["allowed_speed"] = allowed_speed(out["dist"], thr, safe, strict)
    out["risky"] = (out["speed"] > out["allowed_speed"]).fillna(False)
    return out
