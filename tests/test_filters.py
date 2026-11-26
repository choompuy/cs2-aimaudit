"""App state, tickrate, per-round sides, lobby stats, weapon classes, filters."""

import os

import numpy as np
import pandas as pd

import core
import store
from fakes import FakeParser, S1, S2, S3, S4, make_match, make_match_sides


def test_app_state():
    _sp = store.state_path()
    if os.path.exists(_sp):
        os.remove(_sp)
    assert core.load_state() == {}, "missing state file -> {}"
    core.save_state({"last_player": "p1", "last_demo": "d1"})
    assert core.load_state() == {"last_player": "p1", "last_demo": "d1"}, "state round-trip"
    with open(_sp, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert core.load_state() == {}, "corrupt state -> {}"
    os.remove(_sp)


def test_tickrate_from_header(demo):
    assert core.tickrate_from_header({"playback_ticks": 6400, "playback_time": 50.0}) == 128, "128 from playback fields"
    assert core.tickrate_from_header({"playback_ticks": 64000, "playback_time": 1000.0}) == 64, "64 from playback fields"
    assert core.tickrate_from_header({}) == 64, "missing header -> 64"
    assert core.tickrate_from_header({"playback_ticks": 6400, "playback_time": 0}) == 64, "zero playback_time -> 64"
    assert core.tickrate_from_header({"playback_ticks": 5, "playback_time": 1000.0}) == 64, "insane ratio -> 64"
    m_tr = make_match()
    m_tr["header"] = {
        "map_name": "de_mirage",
        "playback_ticks": 6400,
        "playback_time": 50.0,
    }
    FakeParser.demos["match128.dem"] = m_tr
    demo128 = core.load_demo("match128.dem")
    assert demo128.tickrate == 128, "demo.tickrate == 128"
    sp128 = float(
        demo128.ticks[(demo128.ticks["steamid"] == S1) & (demo128.ticks["tick"] == 120)][
            "speed"
        ].iloc[0]
    )
    assert abs(sp128 - 512.0) < 1e-6, "speed scales with tickrate (4u/tick -> 512 u/s)"
    d128 = core.build_duels(demo128, 3.0)
    assert len(d128) == 1, "duel found at 128 tick"
    assert abs(d128.iloc[0]["duration_s"] - round(21 / 128, 2)) < 1e-9, "duration in seconds scales (21 ticks / 128)"
    assert demo.tickrate == 64, "default fake demo stays 64"


def test_per_round_sides():
    FakeParser.demos["sides.dem"] = make_match_sides()
    demo_s = core.load_demo("sides.dem")
    assert demo_s.sides.get(1) == {S1: "T", S2: "T", S3: "CT", S4: "CT"}, "sides round 1"
    assert demo_s.sides.get(2) == {S1: "CT", S2: "CT", S3: "T", S4: "T"}, "sides round 2 (swapped)"
    shots_s = core.shots_table(demo_s)
    s1s = shots_s[shots_s["sid"] == S1].iloc[0]
    s3s = shots_s[shots_s["sid"] == S3].sort_values("tick").iloc[-1]
    assert s1s["side"] == "T", "S1 round-1 shot side = T (not the static CT)"
    assert s3s["side"] == "T", "S3 round-2 shot side = T (swapped)"
    duels_s = core.build_duels(demo_s, 3.0)
    assert len(duels_s) == 2, "two duels"
    d1 = duels_s[duels_s["round"] == 1].iloc[0]
    d2 = duels_s[duels_s["round"] == 2].iloc[0]
    assert d1["killer_side"] == "T" and d1["victim_side"] == "CT", "duel 1 sides: T killed CT"
    assert d2["killer_side"] == "T" and d2["victim_side"] == "CT", "duel 2 sides: T killed CT after swap"
    mine_s = core.duels_for(duels_s, S1)
    assert list(mine_s["my_side"]) == ["T", "CT"], "duels_for my_side follows rounds"
    # the side is the player's own: the app's path, apply_filters -> duels_for / summary
    f_ct = core.Filters(side="CT")
    sh_ct, du_ct, _ = core.apply_filters(shots_s, duels_s, None, f_ct)
    mine_ct = core.duels_for(du_ct, S1, f_ct.side)
    assert list(mine_ct["my_side"]) == ["CT"], "only the round S1 played as CT"
    opp_ct = core.duels_for(du_ct, S1, "T")
    assert set(opp_ct["round"]).isdisjoint(mine_ct["round"]), "the sides split the duels"
    own_ct = core.player_summary(demo_s, duels_s[duels_s["round"].isin(mine_ct["round"])], sh_ct, 40)
    all_ct = core.player_summary(demo_s, du_ct, sh_ct, 40, f=f_ct)
    cols = ["hs_kills", "first_shot", "aim_err", "duration"]
    pd.testing.assert_frame_equal(
        all_ct.loc[all_ct["sid"] == S1, cols].reset_index(drop=True),
        own_ct.loc[own_ct["sid"] == S1, cols].reset_index(drop=True),
    )
    summ_s = core.player_summary(demo_s, duels_s, shots_s, 40)
    a_row = summ_s[summ_s["sid"] == S1].iloc[0]
    assert a_row["sides"] == "T→CT", "summary sides T→CT"
    chk_s = {c["id"]: c for c in core.data_checks(demo_s, shots_s, duels_s)}
    assert chk_s["sides"]["level"] == "ok", "sides check ok when data present"
    # the hits follow the Rounds and Side filters like the shots they are divided by:
    # S3 (CT, then T) hits in both rounds, in round 2 with two pellets of one shot
    m_h = make_match_sides()
    m_h["events"]["player_hurt"] = pd.DataFrame(
        {
            "tick": [110, 300, 301],
            "attacker_steamid": pd.array([int(S3)] * 3, dtype="float64"),
            "attacker_name": ["Charlie"] * 3,
            "user_steamid": pd.array([int(S1)] * 3, dtype="float64"),
            "user_name": ["Alpha"] * 3,
            "weapon": ["weapon_ak47"] * 3,
            "hitgroup": [1, 2, 2],
            "total_rounds_played": [0, 1, 1],
            "is_warmup_period": [False] * 3,
        }
    )
    FakeParser.demos["sides_hurts.dem"] = m_h
    demo_h = core.load_demo("sides_hurts.dem")
    shots_h, duels_h = core.shots_table(demo_h), core.build_duels(demo_h, 3.0)
    hurts_h = core.hurts_table(demo_h, shots_h)
    assert list(hurts_h["round"]) == [1, 2, 2] and list(hurts_h["side"]) == ["CT", "T", "T"]
    assert "round" not in demo_h.hurts.columns, "the demo's own frame is untouched"
    for flt, n_hits, hs in (
        (None, 2, 33.3),
        (core.Filters(side="CT"), 1, 100.0),
        (core.Filters(side="T"), 1, 0.0),
        (core.Filters(rounds=(1, 1)), 1, 100.0),
        (core.Filters(rounds_set={2}), 1, 0.0),
    ):
        s_h, d_h, h_h = core.apply_filters(shots_h, duels_h, hurts_h, flt)
        row = core.player_summary(demo_h, d_h, s_h, 40, hurts=h_h, f=flt)
        row = row[row["sid"] == S3].iloc[0]
        assert (row["hits"], row["hs_hits"]) == (n_hits, hs), flt
        assert row["accuracy"] == round(n_hits / row["shots"] * 100, 1) <= 100, flt
    # the distance is the one of the shot behind the hit
    by_tick = shots_h.set_index("tick")["dist"]
    assert list(hurts_h["dist"]) == [by_tick[110], by_tick[300], by_tick[300]]
    far = core.Filters(dist=(float(by_tick.max()) + 1, float("inf")))
    assert [len(x) for x in core.apply_filters(shots_h, duels_h, hurts_h, far)] == [0, 0, 0]
    # team_num unavailable -> static fallback + warn check
    m_s2 = make_match_sides()
    m_s2["unsupported"] = {"team_num"}
    FakeParser.demos["sides2.dem"] = m_s2
    demo_s2 = core.load_demo("sides2.dem")
    assert demo_s2.sides == {}, "no team_num -> sides empty"
    shots_s2 = core.shots_table(demo_s2)
    s1s2 = shots_s2[shots_s2["sid"] == S1].sort_values("tick").iloc[0]
    assert s1s2["side"] == "CT", "fallback to static team (S1 end-of-match CT, per-round would be T)"
    chk_s2 = {
        c["id"]: c
        for c in core.data_checks(demo_s2, shots_s2, core.build_duels(demo_s2, 3.0))
    }
    assert chk_s2["sides"]["level"] == "warn", "sides check warns when missing"


def test_wilson_ci():
    lo, hi = core.wilson_ci(0, 10)
    assert abs(lo - 0.0) < 1e-9, "wilson 0/10 lo=0"
    assert abs(hi - 27.7540) < 0.01, "wilson 0/10 hi=27.75"
    lo, hi = core.wilson_ci(5, 10)
    assert abs(lo - 23.6590) < 0.01 and abs(hi - 76.3410) < 0.01, "wilson 5/10"
    lo, hi = core.wilson_ci(10, 10)
    assert abs(lo - 72.2460) < 0.01 and hi == 100.0, "wilson 10/10"
    lo, hi = core.wilson_ci(62, 100)
    assert abs(lo - 52.2096) < 0.01 and abs(hi - 70.9026) < 0.01, "wilson 62/100"
    lo, hi = core.wilson_ci(3, 8)
    assert abs(lo - 13.6842) < 0.01 and abs(hi - 69.4262) < 0.01, "wilson 3/8 wide"
    assert all(pd.isna(x) for x in core.wilson_ci(0, 0)), "wilson n=0 -> nan"
    assert all(pd.isna(x) for x in core.wilson_ci(np.nan, 5)), "wilson nan-safe"
    for k, n in ((0, 10), (5, 10), (62, 100), (99, 100)):
        lo, hi = core.wilson_ci(k, n)
        assert lo - 1e-9 <= k / n * 100 <= hi + 1e-9, f"wilson covers point {k}/{n}"


def test_rank_in_lobby():
    rdf = pd.DataFrame(
        {
            "sid": ["p1", "p2", "p3", "p4"],
            "accuracy": [50.0, 33.3, np.nan, 20.0],
            "risky": [10.0, 30.0, 5.0, np.nan],
        }
    )
    assert core.rank_in_lobby(rdf, "p1", "accuracy") == (1, 3), "rank high best"
    assert core.rank_in_lobby(rdf, "p2", "accuracy") == (2, 3), "rank 2nd"
    assert core.rank_in_lobby(rdf, "p4", "accuracy") == (3, 3), "rank skips NaN players"
    assert core.rank_in_lobby(rdf, "p3", "risky", "low") == (1, 3), "rank low best"
    assert core.rank_in_lobby(rdf, "p2", "risky", "low") == (3, 3), "rank low best 2nd"
    assert core.rank_in_lobby(rdf, "px", "accuracy") is None, "rank unknown player -> None"
    assert core.rank_in_lobby(rdf, "p3", "accuracy") is None, "rank NaN player -> None"
    assert core.rank_in_lobby(rdf.iloc[:1], "p1", "accuracy") is None, "rank single player -> None"
    assert core.rank_in_lobby(rdf, "p1", "nope") is None, "rank missing col -> None"


def test_weapon_class():
    assert core.weapon_class("weapon_ak47") == "rifle", "ak47 -> rifle"
    assert core.weapon_class("m4a1_silencer") == "rifle", "m4a1_silencer -> rifle"
    assert core.weapon_class("awp") == "sniper", "awp -> sniper"
    assert core.weapon_class("weapon_deagle") == "pistol", "deagle -> pistol"
    assert core.weapon_class("negev") == "lmg", "negev -> lmg"
    assert core.weapon_class("mag7") == "shotgun", "mag7 -> shotgun"
    assert core.weapon_class("mp5sd") == "smg", "mp5sd -> smg"
    assert core.weapon_class("something") == "other", "unknown -> other"
    assert (
        {w for ws in core.WEAPON_CLASSES.values() for w in ws}
        == {
            "ak47",
            "m4a1",
            "m4a1_silencer",
            "famas",
            "galilar",
            "aug",
            "sg556",
            "awp",
            "ssg08",
            "scar20",
            "g3sg1",
            "mac10",
            "mp9",
            "mp7",
            "mp5sd",
            "ump45",
            "p90",
            "bizon",
            "glock",
            "hkp2000",
            "usp_silencer",
            "p250",
            "fiveseven",
            "tec9",
            "cz75a",
            "deagle",
            "elite",
            "revolver",
            "nova",
            "xm1014",
            "mag7",
            "sawedoff",
            "m249",
            "negev",
        }
    ), "classes cover the backlog list"


def test_apply_filters():
    fs_shots = pd.DataFrame(
        {
            "tick": [1, 2, 3, 4, 5],
            "sid": ["p1"] * 5,
            "weapon": ["ak47", "awp", "deagle", "ak47", "m4a1"],
            "weapon_class": ["rifle", "sniper", "pistol", "rifle", "rifle"],
            "round": [1, 1, 2, 2, 3],
            "side": ["T", "T", "CT", "T", "CT"],
            "dist": [400.0, 1800.0, 700.0, 1200.0, np.nan],
        }
    )
    fs_duels = pd.DataFrame(
        {
            "round": [1, 2],
            "weapon": ["ak47", "awp"],
            "killer": ["p1", "p2"],
            "victim": ["p2", "p1"],
            "killer_side": ["T", "CT"],
            "victim_side": ["CT", "T"],
            "distance": [800.0, 1600.0],
        }
    )
    fs_hurts = pd.DataFrame({"attacker_steamid": ["p1"], "weapon": ["ak47"]})
    # no filter -> copies, same content
    s0, d0, h0 = core.apply_filters(fs_shots, fs_duels, fs_hurts, None)
    assert len(s0) == 5 and list(s0["weapon"]) == list(fs_shots["weapon"]), "no filter: shots unchanged"
    assert len(d0) == 2, "no filter: duels unchanged"
    assert len(h0) == 1, "no filter: hurts unchanged"
    assert s0 is not fs_shots, "no filter: copies"
    # weapons
    s1, d1, h1 = core.apply_filters(
        fs_shots, fs_duels, fs_hurts, core.Filters(weapons={"ak47"})
    )
    assert list(s1["weapon"]) == ["ak47", "ak47"], "weapons filter shots"
    assert list(d1["weapon"]) == ["ak47"], "weapons filter duels"
    assert list(h1["weapon"]) == ["ak47"], "weapons filter hurts"
    # classes
    s2, d2, _ = core.apply_filters(
        fs_shots, fs_duels, fs_hurts, core.Filters(classes={"sniper", "pistol"})
    )
    assert list(s2["weapon"]) == ["awp", "deagle"], "classes filter shots"
    assert list(d2["weapon"]) == ["awp"], "classes filter duels"
    # weapons + classes = union
    s3, _, _ = core.apply_filters(
        fs_shots, fs_duels, None, core.Filters(weapons={"awp"}, classes={"rifle"})
    )
    assert list(s3["weapon"]) == ["ak47", "awp", "ak47", "m4a1"], "weapons+classes union"
    # rounds
    s4, d4, _ = core.apply_filters(fs_shots, fs_duels, None, core.Filters(rounds=(2, 3)))
    assert list(s4["round"]) == [2, 2, 3], "rounds filter shots"
    assert list(d4["round"]) == [2], "rounds filter duels"
    # side (shots use 'side', raw duels either role)
    s5, d5, _ = core.apply_filters(fs_shots, fs_duels, None, core.Filters(side="T"))
    assert list(s5["side"]) == ["T", "T", "T"], "side filter shots"
    assert list(d5["round"]) == [1, 2], "side filter raw duels (either role)"
    # dist (shots use 'dist', duels 'distance'; NaN dropped)
    s6, d6, _ = core.apply_filters(
        fs_shots, fs_duels, None, core.Filters(dist=(500.0, 1500.0))
    )
    assert list(s6["dist"]) == [700.0, 1200.0], "dist filter shots"
    assert list(d6["distance"]) == [800.0], "dist filter duels"
    # results on duels_for-shaped frames
    fs_mine = pd.DataFrame(
        {
            "round": [1, 2],
            "weapon": ["ak47", "awp"],
            "my_side": ["T", "CT"],
            "result": ["kill", "death"],
        }
    )
    _, d7, _ = core.apply_filters(fs_shots, fs_mine, None, core.Filters(results={"kill"}))
    assert list(d7["result"]) == ["kill"], "results filter"
    _, d8, _ = core.apply_filters(fs_shots, fs_mine, None, core.Filters(side="CT"))
    assert list(d8["my_side"]) == ["CT"], "side filter uses my_side"
    # combined + empty result frames stay frames
    s9, d9, h9 = core.apply_filters(
        fs_shots, fs_duels, fs_hurts, core.Filters(weapons={"deagle"}, rounds=(9, 10))
    )
    assert len(s9) == 0, "combined empty shots"
    assert len(d9) == 0, "combined empty duels"
    assert len(h9) == 0, "combined empty hurts"
    assert core.round_bounds(30, "all") == (1, 30), "bounds all n=30"
    assert core.round_bounds(30, "first") == (1, 12), "bounds first n=30"
    assert core.round_bounds(30, "second") == (13, 24), "bounds second n=30"
    assert core.round_bounds(30, "ot") == (25, 30), "bounds ot n=30"
    assert core.round_bounds(8, "all") == (1, 8), "bounds all n=8"
    assert core.round_bounds(8, "first") == (1, 8), "bounds first clamped n=8"
    assert core.round_bounds(8, "second") == (13, 13), "bounds second beyond match -> empty-ish (lo>=hi)"
    assert core.round_bounds(8, "bogus") == (1, 8), "bounds unknown preset -> all"
    assert core.pistol_rounds(26) == {1, 13, 25}, "pistols n=26 -> {1,13,25}"
    assert core.pistol_rounds(10) == {1}, "pistols n=10 -> {1}"
    _, dp, _ = core.apply_filters(
        fs_shots, fs_duels, None, core.Filters(rounds_set={1, 13})
    )
    # fs_duels has rounds 1,2 -> only round 1 survives
    assert list(dp["round"]) == [1], "rounds_set filter duels"
    dsp, _, _ = core.apply_filters(
        pd.DataFrame({"round": [1, 2, 13, 14]}),
        pd.DataFrame(),
        None,
        core.Filters(rounds_set={1, 13}),
    )
    assert list(dsp["round"]) == [1, 13], "rounds_set filter frames"
