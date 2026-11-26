"""Knife rounds / live start, halves, openings / trades, kill points."""

import json
import os
import tempfile

import numpy as np
import pandas as pd

import core
from fakes import FakeParser, IDS, S1, S2, S3, S4, _tiny_png, f64_sid


def test_live_match_start():
    NAMES = {S1: "Alpha", S2: "Bravo", S3: "Charlie", S4: "Delta"}
    NAMES_I = {int(s): NAMES[s] for s in IDS}
    def make_live_match(bnm_tick, knife=True, pistol_trp=0, warmup_round=False):
        """
    FACEIT-shaped demo: [knife round] [warmup round] begin_new_match,
    then two live rounds (one duel each, S1 kills S3 at 500u). Knife
    round sits at trp=0 before bnm_tick; `pistol_trp` is the counter
    value of the first live round (0 = the restart reset it, 1 = the
    counter kept counting the knife round).
    """

        def fire(tick, sid, w, trp, warm):
            return (tick, int(sid), NAMES[sid], w, trp, warm)

        def death(tick, a, u, w, trp, warm):
            return (tick, int(a), NAMES[a], int(u), NAMES[u], w, trp, warm, True, 12.7)

        def hurt(tick, a, u, w, trp, warm):
            return (tick, int(a), NAMES[a], int(u), NAMES[u], w, 1, trp, warm)

        ev_fires, ev_deaths, ev_hurts, fe = [], [], [], []
        if knife:
            fe.append((100, False))
            ev_fires += [
                fire(110, S1, "weapon_knife", 0, False),
                fire(120, S3, "weapon_knife_t", 0, False),
            ]
            ev_deaths.append(death(130, S1, S3, "knife", 0, False))
        if warmup_round:
            fe.append((300, True))
            ev_fires.append(fire(310, S1, "weapon_m4a1", 0, True))
        for t0, trp in ((700, pistol_trp), (900, pistol_trp + 1)):
            fe.append((t0 - 50, False))
            ev_fires += [
                fire(t0, S1, "weapon_ak47", trp, False),
                fire(t0 + 2, S1, "weapon_ak47", trp, False),
                fire(t0 + 5, S3, "weapon_ak47", trp, False),
            ]
            ev_hurts.append(hurt(t0 + 2, S1, S3, "weapon_ak47", trp, False))
            ev_deaths.append(death(t0 + 10, S1, S3, "weapon_ak47", trp, False))

        events = {
            "weapon_fire": pd.DataFrame(
                ev_fires,
                columns=[
                    "tick",
                    "user_steamid",
                    "user_name",
                    "weapon",
                    "total_rounds_played",
                    "is_warmup_period",
                ],
            ).assign(user_steamid=lambda d: pd.array(d["user_steamid"], dtype="uint64")),
            "player_hurt": pd.DataFrame(
                ev_hurts,
                columns=[
                    "tick",
                    "attacker_steamid",
                    "attacker_name",
                    "user_steamid",
                    "user_name",
                    "weapon",
                    "hitgroup",
                    "total_rounds_played",
                    "is_warmup_period",
                ],
            ).assign(
                attacker_steamid=lambda d: pd.array(d["attacker_steamid"], dtype="float64"),
                user_steamid=lambda d: pd.array(d["user_steamid"], dtype="float64"),
            ),
            "player_death": pd.DataFrame(
                ev_deaths,
                columns=[
                    "tick",
                    "attacker_steamid",
                    "attacker_name",
                    "user_steamid",
                    "user_name",
                    "weapon",
                    "total_rounds_played",
                    "is_warmup_period",
                    "headshot",
                    "distance",
                ],
            ).assign(
                attacker_steamid=lambda d: pd.array(d["attacker_steamid"], dtype="float64"),
                user_steamid=lambda d: pd.array(d["user_steamid"], dtype="float64"),
            ),
            "round_freeze_end": pd.DataFrame(
                fe, columns=["tick", "is_warmup_period"]
            ).assign(total_rounds_played=0),
        }
        if bnm_tick is not None:
            events["begin_new_match"] = pd.DataFrame(
                {
                    "tick": [bnm_tick],
                    "is_warmup_period": [False],
                    "total_rounds_played": [0],
                }
            )
        # tick rows for the two live duels (S1 vs S3 at 500u, standing still);
        # ticks are requested at every fire tick and fire tick - 1
        rows = []
        for t in list(range(649, 711)) + list(range(849, 911)):
            for sid, (x, yaw) in ((S1, (0.0, 0.0)), (S3, (500.0, 180.0))):
                rows.append(
                    {
                        "tick": t,
                        "steamid": f64_sid(sid),
                        "name": NAMES[sid],
                        "X": x,
                        "Y": 0.0,
                        "Z": 0.0,
                        "pitch": 0.0,
                        "yaw": yaw,
                        "is_alive": True,
                        "aim_punch_angle": "0 0 0",
                    }
                )
        return {
            "player_info": pd.DataFrame(
                {
                    "steamid": [int(s) for s in IDS],
                    "name": [NAMES[s] for s in IDS],
                    "team_number": [2, 2, 3, 3],
                }
            ),
            "header": {"map_name": "de_mirage"},
            "events": events,
            "ticks": pd.DataFrame(rows),
            "unsupported": set(),
        }
    def _extra_death(tick, a, u, w, trp):
        return pd.DataFrame(
            [
                {
                    "tick": tick,
                    "attacker_steamid": int(a),
                    "attacker_name": NAMES[a],
                    "user_steamid": int(u),
                    "user_name": NAMES[u],
                    "weapon": w,
                    "total_rounds_played": trp,
                    "is_warmup_period": False,
                    "headshot": False,
                    "distance": 12.7,
                }
            ],
        ).astype({"attacker_steamid": "float64", "user_steamid": "float64"})
    # 1) knife round + begin_new_match, counter reset by the restart (FACEIT)
    FakeParser.demos["knife_reset.dem"] = make_live_match(500, knife=True, pistol_trp=0)
    dk = core.load_demo("knife_reset.dem")
    assert dk.excluded == {"warmup": 0, "knife_round": 1, "restart": 0}, "knife_reset excluded"
    assert len(dk.deaths) == 2, "knife kill dropped"
    assert len(dk.fires) == 6, "knife_reset 6 gun fires"
    sk = core.add_risk(core.shots_table(dk), 40)
    assert sorted(sk["round"].unique()) == [1, 2], "knife_reset rounds start at 1"
    assert len(core.build_duels(dk, 3.0)) == 2, "knife_reset duels: 2 (no knife)"
    # 2) knife round, no begin_new_match, counter kept counting
    FakeParser.demos["knife_cont.dem"] = make_live_match(None, knife=True, pistol_trp=1)
    dc = core.load_demo("knife_cont.dem")
    assert dc.excluded["knife_round"] == 1, "knife_cont excluded"
    assert len(dc.deaths) == 2, "knife_cont knife dropped"
    sc = core.add_risk(core.shots_table(dc), 40)
    assert sorted(sc["round"].unique()) == [1, 2], "knife_cont pistol renumbered to 1"
    # 3) no knife round at all (Valve-like): nothing excluded
    FakeParser.demos["noknife.dem"] = make_live_match(50, knife=False, pistol_trp=0)
    dn = core.load_demo("noknife.dem")
    assert not any(dn.excluded.values()), "noknife excluded empty"
    sn = core.add_risk(core.shots_table(dn), 40)
    assert sorted(sn["round"].unique()) == [1, 2], "noknife rounds 1-2"
    # 4) knife round + warmup round after it (the faceit2 shape)
    FakeParser.demos["knife_warm.dem"] = make_live_match(
        500, knife=True, warmup_round=True, pistol_trp=0
    )
    dw = core.load_demo("knife_warm.dem")
    assert dw.excluded == {"warmup": 1, "knife_round": 1, "restart": 0}, "knife_warm excluded"
    assert len(dw.fires) == 6, "knife_warm fires are live-only"
    # 5) mid-match knife and grenade kills: not duels, but K/D counts them
    m9 = make_live_match(500, knife=False, pistol_trp=0)
    m9["events"]["player_death"] = pd.concat(
        [
            m9["events"]["player_death"],
            _extra_death(800, S3, S1, "knife", 0),
            _extra_death(801, S3, S2, "hegrenade", 0),
        ],
        ignore_index=True,
    )
    FakeParser.demos["mixed.dem"] = m9
    dm = core.load_demo("mixed.dem")
    dm_duels = core.build_duels(dm, 3.0)
    assert len(dm_duels) == 2, "mixed: knife/nade kills are not duels"
    sm = core.player_summary(dm, dm_duels, core.add_risk(core.shots_table(dm), 40), 40)
    assert sm[sm["sid"] == S3].iloc[0]["kills"] == 2, "mixed: K/D counts knife+nade kills"
    assert sm[sm["sid"] == S1].iloc[0]["deaths"] == 1, "mixed: deaths include knife/nade"
    # scoreboard slack: a server that does NOT count knife/grenade kills in
    # kills_total must still pass - ours may exceed game by exactly the
    # player's non-gun kills (the three probed demos count them,
    # other versions may not)
    m9["totals"] = {
        S1: ("Alpha", 2, 1),
        S2: ("Bravo", 0, 1),
        S3: ("Charlie", 0, 2),
        S4: ("Delta", 0, 0),
    }
    dm2 = core.load_demo("mixed.dem")
    ch = {
        c["id"]: c
        for c in core.data_checks(dm2, core.shots_table(dm2), core.build_duels(dm2, 3.0))
    }
    assert ch["scoreboard"]["level"] == "ok" and ch["scoreboard"]["n"] == 0, "scoreboard: non-gun slack absorbs uncounted knife/nade kills"
    # ...and a server that DOES count them matches exactly
    m9["totals"] = {
        S1: ("Alpha", 2, 1),
        S2: ("Bravo", 0, 1),
        S3: ("Charlie", 2, 2),
        S4: ("Delta", 0, 0),
    }
    dm3 = core.load_demo("mixed.dem")
    ch = {
        c["id"]: c
        for c in core.data_checks(dm3, core.shots_table(dm3), core.build_duels(dm3, 3.0))
    }
    assert ch["scoreboard"]["level"] == "ok", "scoreboard: exact match when the game counts knife/nade"
    # ...but ours beyond the slack is an over-count -> anomaly
    m9["totals"] = {
        S1: ("Alpha", 2, 1),
        S2: ("Bravo", 0, 1),
        S3: ("Charlie", 0, 2),
        S4: ("Delta", 0, 1),
    }
    dm4 = core.load_demo("mixed.dem")
    ch = {
        c["id"]: c
        for c in core.data_checks(dm4, core.shots_table(dm4), core.build_duels(dm4, 3.0))
    }
    assert ch["scoreboard"]["level"] == "anomaly" and ch["scoreboard"]["n"] == 1, "scoreboard: deaths beyond raw rows -> anomaly"


def test_halves_and_presets():
    _s = {
        r: {s: ("T" if (s in (S1, S2)) == (r <= 12 or r >= 25) else "CT") for s in IDS}
        for r in range(1, 27)
    }
    assert core.halves_from_sides(_s, 26) == [(1, 12), (13, 24), (25, 26)], "halves MR12+OT"
    assert core.halves_from_sides({}, 26) == [], "halves empty sides"
    _g = {r: _s[r] for r in (1, 2, 5, 13, 14)}  # gap 5 -> 13: boundary unknowable
    assert core.halves_from_sides(_g, 14) == [], "halves gap not guessed"
    _m15 = {
        r: {s: ("T" if (s in (S1, S2)) == (r <= 15) else "CT") for s in IDS}
        for r in range(1, 19)
    }
    assert core.halves_from_sides(_m15, 18) == [(1, 15), (16, 18)], "halves MR15"
    _h = core.halves_from_sides(_s, 26)
    assert core.round_bounds(26, "first", _h) == (1, 12), "bounds first (data)"
    assert core.round_bounds(26, "second", _h) == (13, 24), "bounds second (data)"
    assert core.round_bounds(26, "ot", _h) == (25, 26), "bounds ot (data)"
    assert core.round_bounds(18, "second", core.halves_from_sides(_m15, 18)) == (16, 18), "bounds MR15 second"
    assert core.pistol_rounds(26, _h) == {1, 13, 25}, "pistols (data)"
    assert core.pistol_rounds(18, core.halves_from_sides(_m15, 18)) == {1, 16}, "pistols MR15"
    assert core.round_bounds(26, "second") == (13, 24), "bounds MR12 fallback"
    assert core.pistol_rounds(26) == {1, 13, 25}, "pistols MR12 fallback"


def make_match_impact():
    T1, T2, T3 = "76561111111111121", "76561111111111122", "76561111111111123"
    C1, C2, C3 = S4, S3, "76561111111111124"  # S3/S4 reused as CT here
    imp_ids = [T1, T2, T3, C1, C2, C3]
    imp_names = {
        T1: "TeeOne",
        T2: "TeeTwo",
        T3: "TeeThree",
        C1: "CeeOne",
        C2: "CeeTwo",
        C3: "CeeThree",
    }
    teams_num = {T1: 2, T2: 2, T3: 2, C1: 3, C2: 3, C3: 3}

    # tick, round(trp), attacker, victim, weapon, assister, flash, round_sec
    D = [
        (100, 0, None, C3, "world", None, False, 35.0),
        (120, 0, T1, C1, "weapon_ak47", None, False, 35.0),
        (300, 0, C2, T1, "weapon_ak47", None, False, 37.0),
        (620, 0, T2, C2, "weapon_ak47", None, False, 40.0),
        (800, 1, T1, T2, "weapon_ak47", T3, False, 22.0),
        (900, 1, C1, T1, "weapon_ak47", None, False, 23.0),
        (1221, 1, T2, C1, "weapon_ak47", None, False, 25.0),
        (2000, 2, T2, C1, "weapon_ak47", None, False, 31.0),
        (2290, 2, T1, C2, "weapon_ak47", None, False, 36.0),
        (2310, 2, T1, C3, "weapon_ak47", None, False, 36.0),
        (2320, 3, T3, C3, "weapon_ak47", T2, False, 5.0),
        (2400, 3, T3, C1, "weapon_ak47", T2, True, 6.0),
        (2410, 3, T3, C2, "weapon_ak47", None, False, 6.0),
    ]
    deaths = pd.DataFrame(
        {
            "tick": [d[0] for d in D],
            "attacker_steamid": pd.array(
                [np.nan if d[2] is None else int(d[2]) for d in D], dtype="float64"
            ),
            "attacker_name": [imp_names.get(d[2]) for d in D],
            "user_steamid": pd.array([int(d[3]) for d in D], dtype="float64"),
            "user_name": [imp_names[d[3]] for d in D],
            "weapon": [d[4] for d in D],
            "headshot": [False] * len(D),
            "distance": [12.7] * len(D),
            "total_rounds_played": [d[1] for d in D],
            "is_warmup_period": [False] * len(D),
            "assister_steamid": pd.array(
                [np.nan if d[5] is None else int(d[5]) for d in D], dtype="float64"
            ),
            "assister_name": [imp_names.get(d[5]) for d in D],
            "assistedflash": [d[6] for d in D],
            "attacker_last_place_name": ["BombsiteA" if d[2] else "Palace" for d in D],
            "user_last_place_name": ["Middle"] * len(D),
            "round_start_time": [100.0] * len(D),
            "game_time": [100.0 + d[7] for d in D],
        }
    )
    # one gun fire per killer around each scripted kill (load_demo needs
    # at least one); tick rows exist for the shooter at t and t+1
    _fk = {}
    for d in D:
        if d[2] is not None:
            _fk.setdefault((d[0] - 1, d[2]), d[1])
    fire_t = sorted(_fk)
    fires = pd.DataFrame(
        {
            "tick": [t for t, _ in fire_t],
            "user_steamid": pd.array([int(s) for _, s in fire_t], dtype="uint64"),
            "user_name": [imp_names[s] for _, s in fire_t],
            "weapon": ["weapon_ak47"] * len(fire_t),
            "total_rounds_played": [_fk[k] for k in fire_t],
            "is_warmup_period": [False] * len(fire_t),
        }
    )
    rows = []
    for t, sid in fire_t:
        for tt in (t, t + 1):
            rows.append(
                {
                    "tick": tt,
                    "steamid": f64_sid(sid),
                    "name": imp_names[sid],
                    "X": 0.0,
                    "Y": 0.0,
                    "Z": 0.0,
                    "pitch": 0.0,
                    "yaw": 0.0,
                    "is_alive": True,
                    "aim_punch_angle": "0 0 0",
                    "team_num": teams_num[sid],
                }
            )
    return {
        "player_info": pd.DataFrame(
            {
                "steamid": [int(s) for s in imp_ids],
                "name": [imp_names[s] for s in imp_ids],
                "team_number": [teams_num[s] for s in imp_ids],
            }
        ),
        "header": {"map_name": "de_mirage", "server_name": "impact"},
        "events": {
            "weapon_fire": fires,
            "player_hurt": pd.DataFrame(),
            "player_death": deaths,
        },
        "ticks": pd.DataFrame(rows),
        "unsupported": set(),
    }
_m_imp = make_match_impact()
FakeParser.demos["impact.dem"] = _m_imp
imp_demo = core.load_demo("impact.dem")


def test_impact(demo):
    op = core.openings_table(imp_demo)
    assert len(op) == 4 and op["round"].tolist() == [1, 2, 3, 4], "openings: one per round, suicides/TK skipped"
    assert (
        op["killer"].tolist()
        == [
            "76561111111111121",
            S4,
            "76561111111111122",
            "76561111111111123",
        ]
    ), "openings: killers"
    assert op["victim"].tolist() == [S4, "76561111111111121", S4, "76561111111111124"], "openings: victims"
    assert op["killer_side"].tolist() == ["T", "CT", "T", "T"], "openings: killer side"
    assert (
        op.loc[0, "killer_zone"] == "BombsiteA"
        and op.loc[0, "victim_zone"] == "Middle"
        and abs(op.loc[0, "round_sec"] - 35.0) < 0.01
    ), "openings: zone + round clock"
    ist = core.impact_stats(imp_demo).set_index("sid")
    _I1 = "76561111111111121"  # TeeOne: opening killer R1, victim R2, 2k round
    _I2 = "76561111111111122"  # TeeTwo: boundary trade, 2 assists (1 flash)
    _I3 = "76561111111111123"  # TeeThree: 3-kill round
    _C2 = S3  # CeeTwo: avenges CeeOne
    _C3 = "76561111111111124"  # CeeThree: opening victim R4, never wins one
    assert (
        ist.loc[_I1, "open_attempts"] == 2
        and ist.loc[S4, "open_attempts"] == 3
        and ist.loc[_I2, "open_attempts"] == 1
        and ist.loc[_C3, "open_attempts"] == 1
    ), "open attempts (killer or victim)"
    assert (
        ist.loc[_I1, "open_wins"] == 1
        and ist.loc[S4, "open_wins"] == 1
        and ist.loc[_I3, "open_wins"] == 1
        and ist.loc[_C3, "open_wins"] == 0
    ), "open wins"
    assert (
        ist.loc[_I1, "open_rate"] == 50.0
        and ist.loc[_I2, "open_rate"] == 100.0
        and ist.loc[_C3, "open_rate"] == 0.0
    ), "open rate"
    assert np.isnan(ist.loc[_C2, "open_rate"]), "open rate NA without attempts"
    assert ist.loc[_C2, "trade_kills"] == 1 and ist.loc[_I2, "trade_kills"] == 1, "trades: avenger + exact +320 boundary"
    assert ist.loc[_I1, "traded_deaths"] == 1 and ist.loc[S4, "traded_deaths"] == 1, "trades: no trade at +321"
    assert ist.loc[_I1, "trade_kills"] == 0 and ist.loc[_C3, "traded_deaths"] == 0, "trades: same-round rule (kill after the round switch)"
    assert (
        ist.loc[S4, "traded_pct"] == 25.0
        and ist.loc[_I1, "traded_pct"] == 50.0
        and ist.loc[_C2, "traded_pct"] == 0.0
        and ist.loc[_C3, "traded_pct"] == 0.0
        and np.isnan(ist.loc[_I2, "traded_pct"])
    ), "traded pct"
    assert ist.loc[_I1, "k2"] == 1 and ist.loc[_I3, "k3"] == 1 and ist.loc[_I2, "k2"] == 0, "multi-kills: 2k and 3k rounds"
    assert ist[["k4", "k5"]].to_numpy().sum() == 0, "multi-kills: no 4k/5k"
    assert (
        ist.loc[_I2, "assists"] == 2
        and ist.loc[_I2, "flash_assists"] == 1
        and ist.loc[_I3, "assists"] == 0
    ), "assists: flash split, teamkill assist ignored"
    # old demo without assister columns -> NA, never 0
    ist_old = core.impact_stats(demo).set_index("sid")
    assert ist_old["assists"].isna().all() and ist_old["flash_assists"].isna().all(), "assists NA without the column"
    # summary + debug report wiring
    summ_imp = core.player_summary(
        imp_demo,
        core.build_duels(imp_demo),
        core.shots_table(imp_demo),
        40.0,
    )
    _r1 = summ_imp[summ_imp["sid"] == _I1].iloc[0]
    assert _r1["open_rate"] == 50.0, "player_summary open_rate"
    rep_imp = core.debug_report(
        imp_demo,
        core.shots_table(imp_demo),
        core.build_duels(imp_demo),
        _I1,
        {"thr": 40.0},
    )
    assert rep_imp["counts"]["openings"] == 4 and rep_imp["counts"]["trade_kills"] == 2, "debug_report counts"
    # demo without any deaths: everything degrades, nothing crashes
    FakeParser.demos["impact_nodeaths.dem"] = {
        **_m_imp,
        "events": {**_m_imp["events"], "player_death": pd.DataFrame()},
    }
    nd = core.load_demo("impact_nodeaths.dem")
    assert len(core.openings_table(nd)) == 0, "no deaths: openings empty"
    ist_nd = core.impact_stats(nd).set_index("sid")
    assert (
        ist_nd["open_attempts"].eq(0).all()
        and ist_nd["open_rate"].isna().all()
        and ist_nd["traded_pct"].isna().all()
        and ist_nd["assists"].isna().all()
    ), "no deaths: impact NA/0"
    summ_nd = core.player_summary(nd, core.build_duels(nd), core.shots_table(nd), 40.0)
    assert summ_nd["open_rate"].isna().all(), "no deaths: summary open_rate NA"


# weapon / distance ones); the sides of one filter partition the match
_d_f, _s_f = core.build_duels(imp_demo), core.shots_table(imp_demo)


def test_impact_follows_filters():
    def _summ(**kw):
        flt = core.Filters(**kw) if kw else None
        return (
            core.player_summary(imp_demo, _d_f, _s_f, 40.0, f=flt)
            .set_index("sid")
            .sort_index()
        )
    _all, _t, _ct = _summ(), _summ(side="T"), _summ(side="CT")
    assert (
        (_t["kills"] + _ct["kills"]).eq(_all["kills"]).all()
        and (_t["deaths"] + _ct["deaths"]).eq(_all["deaths"]).all()
    ), "filter: sides partition kills and deaths"
    assert (_t["kills"] != _ct["kills"]).any() or (_t["deaths"] != _ct["deaths"]).any(), "filter: side really changes K/D for someone"
    _r12, _r34 = _summ(rounds=(1, 2)), _summ(rounds=(3, 4))
    assert (
        (_r12["kills"] + _r34["kills"]).eq(_all["kills"]).all()
        and (_r12["deaths"] + _r34["deaths"]).eq(_all["deaths"]).all()
    ), "filter: round ranges partition kills and deaths"
    assert _summ(weapons={"awp"}, dist=(0.0, 100.0))["kills"].eq(_all["kills"]).all(), "filter: weapon / distance do not touch K/D"
    assert all(r.kd_ratio == round(r.kills / max(1, r.deaths), 2) for r in _all.itertuples()), "filter: kd_ratio = kills / max(1, deaths)"
    _i_all = core.impact_stats(imp_demo).set_index("sid")
    _i_t = core.impact_stats(imp_demo, f=core.Filters(side="T")).set_index("sid")
    _i_ct = core.impact_stats(imp_demo, f=core.Filters(side="CT")).set_index("sid")
    assert all(
            (_i_t[c] + _i_ct[c]).eq(_i_all[c]).all()
            for c in (
                "open_attempts",
                "open_wins",
                "trade_kills",
                "traded_deaths",
                "deaths",
                "k2",
                "k3",
            )
        ), "filter: impact sides partition openings, trades, multi-kills"
    assert (
        core.impact_stats(imp_demo, f=core.Filters(rounds=(1, 1)))["open_attempts"].sum()
        == 2
    ), "filter: round range narrows the opening duels"
    # a deaths frame without a round counter: the filter must not guess rounds
    _nr = core.load_demo("impact.dem")
    _nr.deaths = _nr.deaths.drop(columns=["total_rounds_played"], errors="ignore")
    assert (
        core.player_summary(_nr, _d_f, _s_f, 40.0, f=core.Filters(side="T"))["kills"].sum()
        == core.player_summary(_nr, _d_f, _s_f, 40.0)["kills"].sum()
    ), "filter: no round counter -> unfiltered numbers, no crash"


def test_kill_points_and_radar():
    assert (
        core.kill_points(imp_demo, S4).empty
        and list(core.kill_points(imp_demo, S4).columns) == core.KILL_POINT_COLS
    ), "kill_points: demo without positions -> empty frame with the columns"
    _mp = core.load_demo("impact.dem")
    _nd_ = len(_mp.deaths)
    _mp.deaths = _mp.deaths.assign(
        **{
            f"{who}_{ax}": [base + k * (i + 1) for i in range(_nd_)]
            for who, base in (("attacker", 0.0), ("user", 30.0))
            for ax, k in (("X", 100.0), ("Y", 50.0), ("Z", 0.0))
        }
    )
    _kp = core.kill_points(_mp, S4)
    _sm = core.player_summary(_mp, _d_f, _s_f, 40.0).set_index("sid")
    assert (
        int((_kp["result"] == "kill").sum()) == int(_sm.loc[S4, "kills"])
        and int((_kp["result"] == "death").sum()) == int(_sm.loc[S4, "deaths"])
        and len(_kp) > 0
    ), "kill_points: kills / deaths match the scoreboard numbers"
    _kpt = core.kill_points(_mp, S4, core.Filters(side="T"))
    _smt = core.player_summary(_mp, _d_f, _s_f, 40.0, f=core.Filters(side="T")).set_index(
        "sid"
    )
    assert (
        int((_kpt["result"] == "kill").sum()) == int(_smt.loc[S4, "kills"])
        and int((_kpt["result"] == "death").sum()) == int(_smt.loc[S4, "deaths"])
    ), "kill_points: side filter matches the filtered K/D"
    assert set(core.kill_points(_mp, S4, core.Filters(rounds=(1, 1)))["round"]) <= {1}, "kill_points: round filter"
    _r0 = _kp.iloc[0]
    assert (
        _r0["my_x"] != _r0["opp_x"]
        and abs(
            _r0["distance"]
            - float(
                np.sqrt(
                    (_r0["my_x"] - _r0["opp_x"]) ** 2
                    + (_r0["my_y"] - _r0["opp_y"]) ** 2
                    + (_r0["my_z"] - _r0["opp_z"]) ** 2
                )
            )
        )
        < 1e-6
    ), "kill_points: my / opponent position by role, distance from positions"
    assert (core.kill_points(_mp, S4, core.Filters(dist=(0.0, 1.0)))["distance"] <= 1.0).all(), "kill_points: distance filter"
    _rd = tempfile.mkdtemp(prefix="cs2aa_radar_")
    os.makedirs(os.path.join(_rd, "radars"))
    _tiny_png(os.path.join(_rd, "radars", "de_t.png"), 16)
    _tiny_png(os.path.join(_rd, "radars", "de_l.png"), 16)
    _tiny_png(os.path.join(_rd, "radars", "de_l_lower.png"), 16)
    _tiny_png(os.path.join(_rd, "radars", "de_nopng_meta.png"), 16)
    with open(os.path.join(_rd, "map_data.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "de_t": {"pos_x": -100.0, "pos_y": 200.0, "scale": 2.0},
                "de_l": {
                    "pos_x": 0.0,
                    "pos_y": 0.0,
                    "scale": 4.0,
                    "verticalsections": {
                        "default": {"AltitudeMax": "10000", "AltitudeMin": "-495"},
                        "lower": {"AltitudeMax": "-495", "AltitudeMin": "-10000"},
                    },
                },
                "de_badscale": {"pos_x": 0, "pos_y": 0, "scale": 0},
                "de_noimage": {"pos_x": 0, "pos_y": 0, "scale": 1},
            },
            fh,
        )
    _old_env = os.environ.get("AIMAUDIT_RADAR_DIR")
    os.environ["AIMAUDIT_RADAR_DIR"] = _rd
    try:
        _rt = core.load_radar("de_t")
        assert (
            _rt is not None
            and _rt["size"] == 16
            and _rt["pos_x"] == -100.0
            and _rt["scale"] == 2.0
            and _rt["lower"] is None
        ), "radar: calibration + image size read"
        _px, _py = core.world_to_radar(0.0, 0.0, _rt)
        assert abs(_px - 100 / 2 * 16 / 1024) < 1e-9 and abs(_py - 200 / 2 * 16 / 1024) < 1e-9, "radar: world -> pixel (scaled to the image size, y flipped)"
        _rl = core.load_radar("de_l")
        assert (
            _rl is not None
            and _rl["lower"] == (-10000.0, -495.0)
            and _rl["lower_png"].endswith("de_l_lower.png")
        ), "radar: lower level needs its image and the altitude range"
        assert (
            core.on_lower_level([-600.0, -495.0, 100.0], _rl).tolist()
            == [True, False, False]
            and not core.on_lower_level([-600.0], _rt).any()
        ), "radar: on_lower_level by altitude"
        assert (
            core.load_radar("de_unknown") is None
            and core.load_radar("de_badscale") is None
            and core.load_radar("de_noimage") is None
            and core.load_radar("") is None
        ), "radar: unknown map / bad scale / missing image -> None"
    finally:
        if _old_env is None:
            os.environ.pop("AIMAUDIT_RADAR_DIR", None)
        else:
            os.environ["AIMAUDIT_RADAR_DIR"] = _old_env
