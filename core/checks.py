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

from core.metrics import duels_for, impact_stats, openings_table, player_summary


# ---------------------------------------------------------- data quality
def data_checks(demo: Demo, shots: pd.DataFrame, duels: pd.DataFrame) -> list[dict]:
    """
    Heuristic checks for data anomalies. Each dict: id, level
    ('ok'|'warn'|'anomaly'), n, pct (+ extras). The UI translates ids.
    """

    def lvl(frac: float, warn: float, anom: float) -> str:
        return "anomaly" if frac >= anom else "warn" if frac >= warn else "ok"

    checks: list[dict] = []

    # 1. steamids that failed to resolve -> stats missing
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
    checks.append(
        {"id": "na_ids", "level": lvl(f, 0.05, 0.20), "n": n_na, "pct": f * 100}
    )

    # 2. shots from players outside the player table (bots / parse hiccup)
    n = int((~shots["sid"].isin(demo.names)).sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "unknown_shooters", "level": lvl(f, 0.001, 0.05), "n": n, "pct": f * 100}
    )

    # 3. shots without shooter position (dist unknown)
    n = (
        int(shots[["X", "Y"]].isna().any(axis=1).sum())
        if len(shots) and {"X", "Y"}.issubset(shots.columns)
        else 0
    )
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "missing_pos", "level": lvl(f, 0.001, 0.10), "n": n, "pct": f * 100}
    )

    # 4. shots with unknown speed (no t-1 tick / death / teleport right
    # before the shot). NaN, not a garbage number.
    n = int(shots["speed"].isna().sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "no_speed", "level": lvl(f, 0.05, 0.20), "n": n, "pct": f * 100}
    )

    # 5. shots without a living enemy nearby (dist unknown)
    n = int(shots["dist"].isna().sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "no_dist", "level": lvl(f, 0.05, 0.25), "n": n, "pct": f * 100}
    )

    # 6. physically impossible speeds
    n = (
        int((shots["speed"] > IMPOSSIBLE_SPEED).sum())
        if len(shots) and shots["speed"].notna().any()
        else 0
    )
    f = n / max(len(shots), 1)
    checks.append(
        {"id": "fast_speeds", "level": lvl(f, 0.0001, 0.02), "n": n, "pct": f * 100}
    )

    # 7. shots without a round number -> alive-filter unreliable for them
    n = int((shots["round"] <= 0).sum()) if len(shots) else 0
    f = n / max(len(shots), 1)
    checks.append({"id": "round0", "level": lvl(f, 0.02, 0.20), "n": n, "pct": f * 100})

    # 8. implausible duel distances
    if len(duels) and duels["distance"].notna().any():
        dist = pd.to_numeric(duels["distance"], errors="coerce")
        n = int((dist.isna() | (dist <= 0) | (dist > MAX_DUEL_DIST)).sum())
        f = n / len(duels)
        checks.append(
            {"id": "duel_dist", "level": lvl(f, 0.02, 0.10), "n": n, "pct": f * 100}
        )
    else:
        checks.append({"id": "duel_dist", "level": "ok", "n": 0, "pct": 0.0})

    # 9. aim-error sanity (angle convention / offsets)
    if len(duels) and duels["killer_err"].notna().any():
        med = float(duels["killer_err"].median())
        n = int((duels["killer_err"] > 45).sum())
        level = "anomaly" if med > 25 else "warn" if med > 12 else "ok"
        checks.append({"id": "angles", "level": level, "n": n, "med": med})
    else:
        checks.append({"id": "angles", "level": "ok", "n": 0, "med": float("nan")})

    # 10. per-round side data (halftime swap): without it the shown side
    # falls back to the end-of-match team for those rounds
    if len(shots) and demo.sides:
        n = int((~shots["round"].isin(set(demo.sides))).sum())
        f = n / len(shots)
        checks.append(
            {"id": "sides", "level": lvl(f, 0.05, 0.40), "n": n, "pct": f * 100}
        )
    else:
        n = len(shots)
        checks.append(
            {
                "id": "sides",
                "level": "warn" if n else "ok",
                "n": n,
                "pct": (100.0 if n else 0.0),
            }
        )

    # 11. unexpected hitgroup values
    ok_vals = set(HITGROUPS.values()) | {"other"}
    bad = (
        set(demo.hurts["hitgroup"].dropna().unique()) - ok_vals
        if "hitgroup" in demo.hurts.columns and len(demo.hurts)
        else set()
    )
    checks.append(
        {
            "id": "hitgroups",
            "level": "warn" if bad else "ok",
            "n": len(bad),
            "vals": sorted(bad),
        }
    )

    # scoreboard cross-check: the game's own
    # kills_total / deaths_total at the last event tick vs our tables.
    # Verified on all three real demos, every player:
    #   kills_total  == our enemy kills (knife/grenade kills included)
    #   deaths_total == our raw death rows (suicides and world deaths
    #                   included; pre-live knife/warmup rows excluded by
    #                   both sides - both aggregates reset at
    #                   begin_new_match)
    # Non-gun kills and teamkills get slack in the kills rule: the three
    # probed demos count both, but other servers/versions may not.
    # game > ours (kills) is always a red flag: we dropped something.
    tot = getattr(demo, "totals", None)
    if tot is not None and len(tot) and len(demo.players):
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
        checks.append(
            {
                "id": "scoreboard",
                "level": lvl(f, 0.05, 0.25),
                "n": n_bad,
                "vals": worst[:3],
            }
        )
    else:
        checks.append({"id": "scoreboard", "level": "ok", "n": 0, "vals": []})
    return checks


# ------------------------------------------------------------ debug export
def _records(df: pd.DataFrame, cap: int | None = None) -> list[dict]:
    if df is None or not len(df):
        return []
    d = df if cap is None or len(df) <= cap else df.iloc[:cap].copy()
    d = d.replace({np.nan: None})
    return json.loads(d.to_json(orient="records", date_format="iso"))


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

    try:
        import importlib.metadata as im

        dp_ver = im.version("demoparser2")
    except Exception:  # noqa: BLE001
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
        "meta": {
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
        },
        "header": demo.header,
        "players": _records(demo.players),
        "counts": {
            "deaths": len(demo.deaths),
            "fires": len(demo.fires),
            "hurts": len(demo.hurts),
            "ticks_rows": len(demo.ticks),
            "shots": len(shots),
            "duels": len(duels),
            "excluded": demo.excluded,
            "punch_available": "punch_pitch" in demo.ticks.columns,
            "mouse_rows": 0 if demo.mouse is None else len(demo.mouse),
            "openings": len(_openings),
            "trade_kills": int(_impact["trade_kills"].sum()) if len(_impact) else 0,
            "scoreboard_totals": (
                _records(demo.totals) if demo.totals is not None else None
            ),
        },
        "event_columns": {
            "deaths": sorted(map(str, demo.deaths.columns)),
            "fires": sorted(map(str, demo.fires.columns)),
            "hurts": sorted(map(str, demo.hurts.columns)),
        },
        "checks": data_checks(demo, shots, duels),
        "me": {
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
        },
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
