"""Duel metrics: one row per enemy kill (build_duels), one player's duels (duels_for)."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd

from core.model import (
    Demo,
    _row_round_sec,
    _safe_int,
    _speed,
    _zone,
    angle_err,
    head_ang,
    is_gun,
    norm_weapon,
    round_of_tick,
    weapon_class,
)

__all__ = ["build_duels", "duels_for"]


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
        # when and where the duel ended (kill-time values)
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
    """One row per enemy kill with the shots both players fired in the window before it,
    aimed within `aim_deg` of each other. Shots without positions are kept. `low_conf`:
    either player also aimed at another enemy in the window."""
    ctx = _DuelCtx(demo, window_s, aim_deg)
    d = demo.deaths
    if not len(d) or "attacker_steamid" not in d.columns:
        return pd.DataFrame()
    d = d[
        d["attacker_steamid"].isin(demo.names)
        & d["user_steamid"].isin(demo.names)
        & (d["attacker_steamid"] != d["user_steamid"])
    ]
    # duels are gun fights: knife / grenade / zeus / world kills have no shots to
    # analyse (the overview K/D still counts them)
    if "weapon" in d.columns and len(d):
        d = d[d["weapon"].map(is_gun)]
    out = []
    for r in d.itertuples():
        k, v, t = r.attacker_steamid, r.user_steamid, int(r.tick)
        if demo.teams.get(k) == demo.teams.get(v):
            continue
        # a demo without the round counter still has the windows
        rd = round_of_tick(getattr(demo, "round_windows", None) or {}, t)
        if rd is None:
            rd = _safe_int(getattr(r, "total_rounds_played", 0)) + 1
        out.append(_duel_row(ctx, r, rd, _duel_shots(ctx, k, v, t)))
    return pd.DataFrame(out)


def duels_for(duels: pd.DataFrame, me: str, side: str | None = None) -> pd.DataFrame:
    """Duels from one player's perspective (kills and deaths). `side` keeps the rounds
    the player spent on it: a raw duel has both sides, so apply_filters cannot."""
    parts = []
    for role, mine, opp in (
        ("kill", "killer", "victim"),
        ("death", "victim", "killer"),
    ):
        df = duels[duels[mine] == me]
        if side is not None:
            df = df[df[f"{mine}_side"] == side]
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
                        lambda f, mine=mine: (
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
