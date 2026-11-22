"""Data-quality checks and the debug report."""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

from core.model import (
    Demo,
    FULL_SPEED,
    HITGROUPS,
    IMPOSSIBLE_SPEED,
    MAX_DUEL_DIST,
    NA,
    SAFE_DIST,
    STRICT_DIST,
    is_gun,
)

from core.duels import duels_for
from core.impact import impact_stats
from core.openings import openings_table
from core.summary import player_summary


# ---------------------------------------------------------- data quality
def _lvl(frac: float, warn: float, anom: float) -> str:
    return "anomaly" if frac >= anom else "warn" if frac >= warn else "ok"


def _frac_check(
    checks: list[dict], cid: str, n: int, total: int, warn: float, anom: float
) -> None:
    """Append the standard {id, level, n, pct} row for a share-of-total."""
    f = n / max(total, 1)
    checks.append({"id": cid, "level": _lvl(f, warn, anom), "n": n, "pct": f * 100})


def _na_ids_check(demo: Demo) -> dict:
    """1. steamids that failed to resolve -> stats missing."""
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
    return {"id": "na_ids", "level": _lvl(f, 0.05, 0.20), "n": n_na, "pct": f * 100}


def _duel_checks(duels: pd.DataFrame, checks: list[dict]) -> None:
    """8. implausible duel distances; 9. aim-error sanity."""
    if len(duels) and duels["distance"].notna().any():
        dist = pd.to_numeric(duels["distance"], errors="coerce")
        n = int((dist.isna() | (dist <= 0) | (dist > MAX_DUEL_DIST)).sum())
        _frac_check(checks, "duel_dist", n, len(duels), 0.02, 0.10)
    else:
        checks.append({"id": "duel_dist", "level": "ok", "n": 0, "pct": 0.0})
    if len(duels) and duels["killer_err"].notna().any():
        med = float(duels["killer_err"].median())
        n = int((duels["killer_err"] > 45).sum())
        level = "anomaly" if med > 25 else "warn" if med > 12 else "ok"
        checks.append({"id": "angles", "level": level, "n": n, "med": med})
    else:
        checks.append({"id": "angles", "level": "ok", "n": 0, "med": float("nan")})


def _sides_check(demo: Demo, shots: pd.DataFrame) -> dict:
    """10. per-round side data (halftime swap): without it the shown side
    falls back to the end-of-match team for those rounds."""
    if len(shots) and demo.sides:
        n = int((~shots["round"].isin(set(demo.sides))).sum())
        f = n / len(shots)
        return {
            "id": "sides",
            "level": _lvl(f, 0.05, 0.40),
            "n": n,
            "pct": f * 100,
        }
    n = len(shots)
    return {
        "id": "sides",
        "level": "warn" if n else "ok",
        "n": n,
        "pct": (100.0 if n else 0.0),
    }


def _hitgroups_check(demo: Demo) -> dict:
    """11. unexpected hitgroup values."""
    ok_vals = set(HITGROUPS.values()) | {"other"}
    bad = (
        set(demo.hurts["hitgroup"].dropna().unique()) - ok_vals
        if "hitgroup" in demo.hurts.columns and len(demo.hurts)
        else set()
    )
    return {
        "id": "hitgroups",
        "level": "warn" if bad else "ok",
        "n": len(bad),
        "vals": sorted(bad),
    }


def _scoreboard_check(demo: Demo) -> dict:
    """Scoreboard cross-check: the game's own
    kills_total / deaths_total at the last event tick vs our tables.
    Verified on all three real demos, every player:
      kills_total  == our enemy kills (knife/grenade kills included)
      deaths_total == our raw death rows (suicides and world deaths
                      included; pre-live knife/warmup rows excluded by
                      both sides - both aggregates reset at
                      begin_new_match)
    Non-gun kills and teamkills get slack in the kills rule: the three
    probed demos count both, but other servers/versions may not.
    game > ours (kills) is always a red flag: we dropped something."""
    tot = getattr(demo, "totals", None)
    if tot is None or not len(tot) or not len(demo.players):
        return {"id": "scoreboard", "level": "ok", "n": 0, "vals": []}
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
    return {
        "id": "scoreboard",
        "level": _lvl(f, 0.05, 0.25),
        "n": n_bad,
        "vals": worst[:3],
    }


def data_checks(demo: Demo, shots: pd.DataFrame, duels: pd.DataFrame) -> list[dict]:
    """
    Heuristic checks for data anomalies. Each dict: id, level
    ('ok'|'warn'|'anomaly'), n, pct (+ extras). The UI translates ids.
    """
    checks: list[dict] = [_na_ids_check(demo)]

    # 2. shots from players outside the player table (bots / parse hiccup)
    n = int((~shots["sid"].isin(demo.names)).sum()) if len(shots) else 0
    _frac_check(checks, "unknown_shooters", n, len(shots), 0.001, 0.05)

    # 3. shots without shooter position (dist unknown)
    n = (
        int(shots[["X", "Y"]].isna().any(axis=1).sum())
        if len(shots) and {"X", "Y"}.issubset(shots.columns)
        else 0
    )
    _frac_check(checks, "missing_pos", n, len(shots), 0.001, 0.10)

    # 4. shots with unknown speed (no t-1 tick / death / teleport right
    # before the shot). NaN, not a garbage number.
    n = int(shots["speed"].isna().sum()) if len(shots) else 0
    _frac_check(checks, "no_speed", n, len(shots), 0.05, 0.20)

    # 5. shots without a living enemy nearby (dist unknown)
    n = int(shots["dist"].isna().sum()) if len(shots) else 0
    _frac_check(checks, "no_dist", n, len(shots), 0.05, 0.25)

    # 6. physically impossible speeds
    n = (
        int((shots["speed"] > IMPOSSIBLE_SPEED).sum())
        if len(shots) and shots["speed"].notna().any()
        else 0
    )
    _frac_check(checks, "fast_speeds", n, len(shots), 0.0001, 0.02)

    # 7. shots without a round number -> alive-filter unreliable for them
    n = int((shots["round"] <= 0).sum()) if len(shots) else 0
    _frac_check(checks, "round0", n, len(shots), 0.02, 0.20)

    _duel_checks(duels, checks)
    checks.append(_sides_check(demo, shots))
    checks.append(_hitgroups_check(demo))
    checks.append(_scoreboard_check(demo))
    return checks


# ------------------------------------------------------------ debug export
def _records(df: pd.DataFrame, cap: int | None = None) -> list[dict]:
    if df is None or not len(df):
        return []
    d = df if cap is None or len(df) <= cap else df.iloc[:cap].copy()
    d = d.replace({np.nan: None})
    return json.loads(d.to_json(orient="records", date_format="iso"))


def _debug_meta(demo: Demo, params: dict, dp_ver: str, size) -> dict:
    """The report's meta block: versions, models, the file."""
    return {
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
    }


def _debug_counts(demo: Demo, shots: pd.DataFrame, duels: pd.DataFrame, openings, impact) -> dict:
    """The report's counts block: raw sizes and a few derived totals."""
    return {
        "deaths": len(demo.deaths),
        "fires": len(demo.fires),
        "hurts": len(demo.hurts),
        "ticks_rows": len(demo.ticks),
        "shots": len(shots),
        "duels": len(duels),
        "excluded": demo.excluded,
        "punch_available": "punch_pitch" in demo.ticks.columns,
        "mouse_rows": 0 if demo.mouse is None else len(demo.mouse),
        "openings": len(openings),
        "trade_kills": int(impact["trade_kills"].sum()) if len(impact) else 0,
        "scoreboard_totals": (
            _records(demo.totals) if demo.totals is not None else None
        ),
    }


def _debug_me(demo: Demo, shots: pd.DataFrame, me: str, my, shot_note: str, shots_cap: int) -> dict:
    """The report's `me` block: the selected player's shots and duels."""
    return {
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
    }


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

    import importlib.metadata as im

    try:
        dp_ver = im.version("demoparser2")
    except im.PackageNotFoundError:
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
        "meta": _debug_meta(demo, params, dp_ver, size),
        "header": demo.header,
        "players": _records(demo.players),
        "counts": _debug_counts(demo, shots, duels, _openings, _impact),
        "event_columns": {
            "deaths": sorted(map(str, demo.deaths.columns)),
            "fires": sorted(map(str, demo.fires.columns)),
            "hurts": sorted(map(str, demo.hurts.columns)),
        },
        "checks": data_checks(demo, shots, duels),
        "me": _debug_me(demo, shots, me, my, shot_note, shots_cap),
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
