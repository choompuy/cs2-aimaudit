"""Round structure: how rounds ended, the openings table and the shared round/side
predicate + enemy-kill rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    # for the annotations only
    from core.filters import Filters

from core.model import (
    NA,
    Demo,
    _round_col,
    _row_round_sec,
    _safe_int,
    _zone,
    norm_weapon,
    weapon_class,
)

__all__ = ["openings_table", "round_outcomes", "round_winners"]


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
    return dict(zip(top[who], top["weapon"], strict=True))


def _enemy_kill_rows(demo: Demo) -> pd.DataFrame:
    """Deaths by an ENEMY (the scoreboard K/D rule) with a 0-based `round_idx`."""
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
    """keep(round_no, sid) -> bool by round range and the side of that round; None when no
    filter applies or the deaths carry no round counter."""
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
    """How every round ended: {"winner", "reason", "end", "inferred"}.

    The demo's ``round_end`` when it has one, else inferred in this order: the bomb
    exploded -> T; defused -> CT; a side lost every player -> the other; bomb events
    known but no plant and nobody eliminated -> CT on time. Anything else stays blank."""
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
    """{round: 'CT'/'T'}; rounds whose result cannot be told are absent."""
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
    """The round's deaths by its tick window when known (a post-round death carries the
    NEXT round's number), else by the round column."""
    if win is not None:
        return d[d["tick"].between(win[0], win[1]) & ~warm]
    return d[(dr == r - 1) & ~warm]


def _guess_round_winners(demo: Demo) -> dict[int, str]:
    """Elimination guess behind round_outcomes: a side that lost every player lost the
    round. T eliminated post-plant is decided by the explosion first."""
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
    """The first enemy kill of every round, any weapon (suicides / teamkills skipped)."""
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
