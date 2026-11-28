"""Per-player and per-weapon summaries. K/D and opening rate are round structure (the
Filters argument), shot/duel cells come from filtered frames."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from core.filters import Filters

from core.model import (
    SAFE_DIST,
    STRICT_DIST,
    Demo,
    allowed_speed,
)
from core.duels import duels_for
from core.impact import impact_stats
from core.openings import _enemy_kill_rows, _round_side_keep, _top_weapon

__all__ = ["hitgroup_counts", "hitgroup_damage", "player_summary", "weapon_stats"]


def _hurts_of(demo: Demo, me: str, hurts: pd.DataFrame | None = None) -> pd.DataFrame:
    """hurts rows caused by `me`; `hurts` overrides demo.hurts."""
    h = demo.hurts if hurts is None else hurts
    if h is not None and "attacker_steamid" in getattr(h, "columns", []) and len(h):
        return h[h["attacker_steamid"] == me]
    return pd.DataFrame()


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
            [keep(int(r), s) for r, s in zip(rd, de["attacker_steamid"], strict=True)],
            dtype=bool,
        )
        v_ok = np.array(
            [keep(int(r), s) for r, s in zip(rd, de["user_steamid"], strict=True)], dtype=bool
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
        # K/D like the scoreboard: every ENEMY kill/death with any weapon. Static teams
        # are fine here: teammates share the label on both halves.
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


def _player_frames(demo, shots, duels, hurts, p, thr, safe, strict, side) -> _PlayerFrames:
    """Slice the frames down to one player and derive the risky %."""
    sh = shots[shots["sid"] == p.sid]
    hi = _hurts_of(demo, p.sid, hurts)
    dd = duels_for(duels, p.sid, side) if len(duels) else pd.DataFrame()
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
        # shots that hit, not hurt rows: a shotgun shot hurts once per pellet
        "hits": int(sh["hit"].sum()) if len(sh) else 0,
        "accuracy": round(sh["hit"].mean() * 100, 1) if len(sh) else np.nan,
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
        # hit rate on first shots of engagements
        "first_acc": (
            round(fsh["hit"].mean() * 100, 1) if len(fsh) else np.nan
        ),
        # share of the opening duels won
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
    """One row per player. Round-structure numbers follow the round range and side of `f`,
    not the weapon / distance filters."""
    res = []
    dth = demo.deaths
    have_d = len(dth) and {"attacker_steamid", "user_steamid"} <= set(dth.columns)
    open_rate = impact_stats(demo, f=f).set_index("sid")["open_rate"].to_dict()
    de = _enemy_kill_rows(demo) if have_d else pd.DataFrame()
    keep, kills_by, deaths_by, best_w, died_w = _kd_setup(demo, f, de, have_d)
    for p in demo.players.itertuples():
        fr = _player_frames(
            demo, shots, duels, hurts, p, thr, safe, strict, f.side if f else None
        )
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
        hits=("hit", "sum"),
        median_speed=("speed", "median"),
        median_dist=("dist", "median"),
        moving=("speed", lambda s: (s > thr).mean() * 100),
    )
    if len(sh):
        g["risky"] = sh.groupby("weapon")["_r"].mean() * 100
        # NaN for weapons that only appear inside sprays
        fsh = sh[sh["first_shot"]]
        if len(fsh):
            g["first_acc"] = fsh.groupby("weapon")["hit"].mean() * 100
    hi = _hurts_of(demo, me, hurts)
    h = (
        hi.groupby("weapon").agg(hs=("hitgroup", lambda s: (s == "head").mean() * 100))
        if len(hi) and "weapon" in hi.columns
        else pd.DataFrame({"hs": pd.Series(dtype=float)})
    )
    out = g.join(h, how="left")
    out["accuracy"] = out["hits"] / out["shots"] * 100
    return out.sort_values("shots", ascending=False).round(1).reset_index()


def hitgroup_counts(
    demo: Demo, me: str, hurts: pd.DataFrame | None = None
) -> pd.Series:
    hi = _hurts_of(demo, me, hurts)
    if not len(hi) or "hitgroup" not in hi.columns:
        return pd.Series(dtype="int64")
    return hi["hitgroup"].value_counts()


def hitgroup_damage(
    demo: Demo, me: str, hurts: pd.DataFrame | None = None
) -> pd.Series:
    """Health damage dealt per hitgroup (overkill already clipped by the parser)."""
    hi = _hurts_of(demo, me, hurts)
    if not len(hi) or not {"hitgroup", "dmg_health"} <= set(hi.columns):
        return pd.Series(dtype="float64")
    dmg = pd.to_numeric(hi["dmg_health"], errors="coerce").fillna(0)
    return dmg.groupby(hi["hitgroup"]).sum()
