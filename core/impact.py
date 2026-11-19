"""
Per-player round impact: opening duels, trades, multi-kills and assists,
all from the enemy-kill deaths (round structure, not a per-shot metric).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from core.filters import Filters

from core.model import TRADE_S, Demo
from core.openings import _enemy_kill_rows, _round_side_keep


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
                    for ri, a in zip(ast["round_idx"], ast["assister_steamid"], strict=True)
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
