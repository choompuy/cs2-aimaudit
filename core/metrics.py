"""
Metric tables built from a parsed Demo: shots, duels, openings,
impact, per-player and per-weapon stats.
"""

from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

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


def round_winners(demo: Demo) -> dict[int, str]:
    """
    Round winners ('CT'/'T') for the replay's round bar, inferred from
    eliminations: CS2 demos expose no round_end/winner event, so the
    only honest signal is "a side lost every player in
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
        d["is_warmup_period"].astype(bool)
        if "is_warmup_period" in d.columns
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
    # opening-duel win rate (impact_stats): whole-match, like
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
