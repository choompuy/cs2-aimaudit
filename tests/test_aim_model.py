"""Crouch-aware aim error, counter-strafes, duel detection, broken demos."""

import bz2
import os
import tempfile
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import core
import store
from fakes import FakeParser, IDS, S1, S2, S3, S4, f64_sid, make_match


def test_crouch_aware_error(duels):
    def row(x, y, z, pitch=0.0, yaw=0.0, duck=None, punch=None):
        r = SimpleNamespace(X=x, Y=y, Z=z, pitch=pitch, yaw=yaw)
        if duck is not None:
            r.duck_amount = duck
        if punch is not None:
            r.punch_pitch, r.punch_yaw = punch
        return r
    assert core.model._duck_amount(row(0, 0, 0)) == 0.0, "duck missing -> 0"
    assert core.model._duck_amount(row(0, 0, 0, duck=0.5)) == 0.5, "duck 0.5"
    assert core.model._duck_amount(row(0, 0, 0, duck=1.7)) == 1.0, "duck clamped hi"
    assert core.model._duck_amount(row(0, 0, 0, duck=-0.2)) == 0.0, "duck clamped lo"
    assert core.model._duck_amount(row(0, 0, 0, duck=np.nan)) == 0.0, "duck nan -> 0"
    shooter = row(0, 0, 0)
    target_stand = row(1000, 0, 0)
    err_ss = core.angle_err(shooter, target_stand)  # head 2u below eyes
    assert abs(err_ss - np.degrees(np.arctan(2 / 1000))) < 1e-9, "stand/stand err ~ 0.11 deg"
    target_duck = row(1000, 0, 0, duck=1.0)
    err_sd = core.angle_err(shooter, target_duck)  # head 20u below eyes
    assert abs(err_sd - np.degrees(np.arctan(20 / 1000))) < 1e-9, "target crouched: err ~ 1.15 deg"
    shooter_duck = row(0, 0, 0, duck=1.0)
    err_ds = core.angle_err(shooter_duck, target_stand)  # head 16u ABOVE crouched eyes
    assert abs(err_ds - np.degrees(np.arctan(16 / 1000))) < 1e-9, "shooter crouched: err ~ 0.92 deg"
    err_dd = core.angle_err(shooter_duck, target_duck)
    assert abs(err_dd - err_ss) < 1e-9, "both crouched ~ stand/stand"
    ha = core.head_ang(1000.0)
    assert abs(ha - 1.0313) < 1e-3, "head_ang 1000u ~ 1.031 deg"
    assert abs(core.head_ang(2000.0) - ha / 2) < 1e-3, "head_ang 2000 ≈ half of 1000 (small-angle)"
    assert pd.isna(core.head_ang(np.nan)), "head_ang nan"
    assert pd.isna(core.head_ang(0.0)), "head_ang 0 -> nan"
    ha_arr = core.head_ang(pd.Series([500.0, 1000.0, np.nan]))
    assert (
        abs(ha_arr[0] - 2 * ha) < 1e-3
        and abs(ha_arr[1] - ha) < 1e-9
        and pd.isna(ha_arr[2])
    ), "head_ang vector"
    # duel-level: killer_err ~5 deg (punch) at dist 800, head_ang(800)=1.289
    kerr = duels.iloc[0]["killer_err"]
    assert not bool(duels.iloc[0]["killer_would_head"]), "killer would_head False (punch pushes out)"
    assert bool(duels.iloc[0]["victim_would_head"]), "victim would_head True (aimed straight)"
    assert 3.0 < duels.iloc[0]["killer_err_hw"] < 5.0, "killer err_hw ~ err/1.289 (3..5)"
    assert duels.iloc[0]["victim_err_hw"] <= 0.5, "victim err_hw <= 0.5"
    # duck_amount column flows through load_demo
    m_d = make_match()
    m_d["ticks"] = m_d["ticks"].assign(duck_amount=0.0)
    m_d["ticks"].loc[m_d["ticks"]["steamid"] == f64_sid(S3), "duck_amount"] = 1.0
    FakeParser.demos["duck.dem"] = m_d
    demo_d = core.load_demo("duck.dem")
    assert "duck_amount" in demo_d.ticks.columns, "duck_amount parsed"
    duels_d = core.build_duels(demo_d, 3.0)
    assert duels_d.iloc[0]["victim_err"] > duels.iloc[0]["victim_err"], "crouched shooter raises victim-side err (S3 ducks, S1 stands)"
    m_d2 = make_match()
    m_d2["unsupported"] = {"duck_amount"}
    FakeParser.demos["duck2.dem"] = m_d2
    demo_d2 = core.load_demo("duck2.dem")
    assert len(demo_d2.ticks) == 22, "duck unsupported: ticks parsed anyway"
    assert "duck_amount" not in demo_d2.ticks.columns, "duck unsupported: no duck col"
    assert "punch_pitch" in demo_d2.ticks.columns, "duck unsupported: punch still there"


def test_counter_strafe_quality(demo):
    # unit: add_stop_quality
    sq = pd.DataFrame(
        {
            "speed": [300.0, 30.0, 30.0, 0.0, np.nan, 30.0],
            "speed_prev": [250.0, 250.0, 10.0, 250.0, 250.0, np.nan],
        }
    )
    sq = core.add_stop_quality(sq, 40)
    assert list(sq["stop"]) == ["moving", "stopped", "standing", "stopped", "", "standing"], "stop classes"
    # integration: fake with buttons at fire ticks. S1 runs +X (yaw 0 = forward),
    # decelerates to 32 u/s at tick 121 and fires there while holding S (BACK).
    BACK_BIT = 1 << 4  # IN_BACK
    m_cs = make_match()
    m_cs["events"]["weapon_fire"] = pd.concat(
        [
            m_cs["events"]["weapon_fire"],
            pd.DataFrame(
                {
                    "tick": [121],
                    "user_steamid": pd.array([int(S1)], dtype="uint64"),
                    "user_name": ["Alpha"],
                    "weapon": ["weapon_ak47"],
                    "total_rounds_played": [0],
                    "is_warmup_period": [False],
                }
            ),
        ],
        ignore_index=True,
    )
    tk_cs = m_cs["ticks"]
    tk_cs = pd.concat(
        [
            tk_cs,
            pd.DataFrame(
                [
                    {
                        "tick": 121,
                        "steamid": f64_sid(S1),
                        "name": "Alpha",
                        "X": 4.5,  # 0.5u from t=120 -> 32 u/s at 64 tick
                        "Y": 0.0,
                        "Z": 0.0,
                        "pitch": 0.0,
                        "yaw": 0.0,
                        "is_alive": True,
                        "aim_punch_angle": "0 0 0",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    m_cs["ticks"] = tk_cs
    m_cs["buttons_bit"] = {
        (121, f64_sid(S1)): BACK_BIT,  # S held at the stop-and-shoot tick
        (120, f64_sid(S1)): 0,
    }
    FakeParser.demos["cs.dem"] = m_cs
    demo_cs = core.load_demo("cs.dem")
    assert "BACK" in demo_cs.ticks.columns, "fire-tick buttons parsed"
    shots_cs = core.add_stop_quality(core.shots_table(demo_cs), 40)
    s1_cs = shots_cs[shots_cs["sid"] == S1].set_index("tick")
    assert s1_cs.loc[120, "stop"] == "moving", "moving shot at 120"
    assert s1_cs.loc[121, "stop"] == "stopped", "stopped shot at 121 (prev 256 -> 32)"
    assert abs(s1_cs.loc[121, "speed_prev"] - 256.0) < 1e-6, "speed_prev wired"
    assert bool(s1_cs.loc[121, "counter_strafe"]), "counter-strafe tap with S against +X motion"
    assert not bool(s1_cs.loc[120, "counter_strafe"]), "no tap when moving (no opposing key)"
    assert not bool(s1_cs.loc[100, "counter_strafe"]), "standing shots have no tap"
    # same geometry but holding W (FORWARD, supports the motion) -> no tap
    FWD_BIT = 1 << 3
    m_cs2 = make_match()
    m_cs2["ticks"] = tk_cs
    m_cs2["events"] = m_cs["events"]
    m_cs2["buttons_bit"] = {(121, f64_sid(S1)): FWD_BIT, (120, f64_sid(S1)): 0}
    FakeParser.demos["cs2.dem"] = m_cs2
    demo_cs2 = core.load_demo("cs2.dem")
    shots_cs2 = core.add_stop_quality(core.shots_table(demo_cs2), 40)
    s1_cs2 = shots_cs2[shots_cs2["sid"] == S1].set_index("tick")
    assert not bool(s1_cs2.loc[121, "counter_strafe"]), "W held (supports motion) -> no tap"
    # demo without button data: stop classes still work, tap always False
    shots_nb = core.add_stop_quality(core.shots_table(demo), 40)
    s1_nb = shots_nb[shots_nb["sid"] == S1].set_index("tick")
    assert s1_nb.loc[120, "stop"] == "moving", "no buttons: stop classes ok"
    assert not bool(s1_nb.loc[120, "counter_strafe"]), "no buttons: tap False"


def test_duel_shots_by_proximity(duels):
    def make_match_aim(s4_pos=(0.0, 800.0)):
        """
    S1 (T) kills S3 (CT) at tick 200. In the window S1 fires:
    t=100 aimed at S3 (yaw 0), t=150 aimed 90 deg away (yaw 90 - at S4
    when S4 sits north, at empty space otherwise), t=160 aimed at S3
    again. S3 fires t=120 at S1 and t=130 without a position row.
    """

        player_info = pd.DataFrame(
            {
                "steamid": [int(s) for s in IDS],
                "name": ["Alpha", "Bravo", "Charlie", "Delta"],
                "team_number": [2, 2, 3, 3],
            }
        )
        fires = pd.DataFrame(
            {
                "tick": [100, 150, 160, 120, 130],
                "user_steamid": pd.array([int(S1)] * 3 + [int(S3)] * 2, dtype="uint64"),
                "user_name": ["Alpha"] * 3 + ["Charlie"] * 2,
                "weapon": ["weapon_ak47"] * 5,
                "total_rounds_played": [0] * 5,
                "is_warmup_period": [False] * 5,
            }
        )
        deaths = pd.DataFrame(
            {
                "tick": [200],
                "attacker_steamid": pd.array([int(S1)], dtype="float64"),
                "attacker_name": ["Alpha"],
                "user_steamid": pd.array([int(S3)], dtype="float64"),
                "user_name": ["Charlie"],
                "weapon": ["weapon_ak47"],
                "headshot": [False],
                "distance": [20.32],
                "total_rounds_played": [0],
                "is_warmup_period": [False],
            }
        )
        # S2 is kept far off every sightline so it never trips the mixed-target check
        pos = {S1: (0.0, 0.0), S2: (0.0, 1200.0), S3: (800.0, 0.0), S4: s4_pos}
        yaw_at = {100: 0.0, 150: 90.0, 160: 0.0}  # S1's yaw per fire tick
        rows = []
        for t in sorted({99, 100, 119, 120, 129, 130, 149, 150, 159, 160}):
            for sid in IDS:
                x, y = pos[sid]
                rows.append(
                    {
                        "tick": t,
                        "steamid": f64_sid(sid),
                        "name": {S1: "Alpha", S2: "Bravo", S3: "Charlie", S4: "Delta"}[sid],
                        "X": x,
                        "Y": y,
                        "Z": 0.0,
                        "pitch": 0.0,
                        "yaw": yaw_at.get(t, 180.0) if sid == S1 else 180.0,
                        "is_alive": True,
                        "aim_punch_angle": "0 0 0",
                        "team_num": 2 if sid in (S1, S2) else 3,
                    }
                )
        # drop S3's rows at 130/129: the shot without position data must be kept
        ticks = pd.DataFrame(rows)
        ticks = ticks[
            ~((ticks["steamid"] == f64_sid(S3)) & (ticks["tick"].isin([129, 130])))
        ]
        return {
            "player_info": player_info,
            "header": {"map_name": "de_mirage"},
            "events": {
                "weapon_fire": fires,
                "player_hurt": pd.DataFrame(),
                "player_death": deaths,
            },
            "ticks": ticks,
            "unsupported": set(),
        }
    FakeParser.demos["aim.dem"] = make_match_aim(s4_pos=(0.0, 800.0))
    demo_a = core.load_demo("aim.dem")
    duels_a = core.build_duels(demo_a, 3.0)
    assert len(duels_a) == 1, "one duel"
    da = duels_a.iloc[0]
    assert da["killer_shots"] == 2, "shots aimed at the opponent count (100,160)"
    assert da["killer_shots_raw"] == 3, "raw counts keep everything"
    assert da["victim_shots"] == 2 and da["victim_shots_raw"] == 2, "no-data shot kept for the victim"
    assert da["first"] == "killer", "first from filtered shots"
    assert bool(da["low_conf"]), "low_conf when a shot went at another enemy"
    assert da["killer_first_tick"] == 100, "first tick = first AIMED shot"
    # S4 far away from the stray shot direction -> clean duel, no flag
    FakeParser.demos["aim2.dem"] = make_match_aim(s4_pos=(400.0, -400.0))
    demo_a2 = core.load_demo("aim2.dem")
    duels_a2 = core.build_duels(demo_a2, 3.0)
    assert duels_a2.iloc[0]["killer_shots"] == 2, "stray shot still excluded from duel shots"
    assert not bool(duels_a2.iloc[0]["low_conf"]), "no second target -> low_conf False"
    # the plain synthetic match: every shot is aimed at the opponent -> unchanged
    assert (
        duels.iloc[0]["killer_shots"] == 3
        and duels.iloc[0]["killer_shots_raw"] == 3
        and not bool(duels.iloc[0]["low_conf"])
    ), "plain match duel unchanged"


def test_no_player_hurt():
    m8 = make_match()
    m8["events"]["player_hurt"] = pd.DataFrame()
    FakeParser.demos["nohurt.dem"] = m8
    demo8 = core.load_demo("nohurt.dem")
    sh8 = core.add_risk(core.shots_table(demo8), 40)
    d8 = core.build_duels(demo8, 3.0)
    assert len(demo8.fires) == 6, "no hurts -> load ok"
    assert not sh8["hit"].any(), "no hurts -> hit False"
    assert len(core.player_summary(demo8, d8, sh8, 40)) == 4, "no hurts -> summary ok"
    assert len(core.hitgroup_counts(demo8, S1)) == 0, "no hurts -> hitgroup empty"
    assert len(core.weapon_stats(demo8, sh8, S1, 40)) == 1, "no hurts -> weapon stats ok"


def test_no_events():
    m7 = make_match()
    m7["events"]["weapon_fire"] = m7["events"]["weapon_fire"].iloc[:2]  # warmup only
    FakeParser.demos["warmup_only.dem"] = m7
    with pytest.raises(ValueError) as exc_info:
        core.load_demo("warmup_only.dem")
    e = exc_info.value
    assert "no_events" in str(e), "raises no_events"


def test_corrupt_archives():
    _cd = tempfile.mkdtemp()
    _parts = lambda: {
        f
        for f in os.listdir(store.unpacked_dir())
        if f.startswith("cs2_aimaudit_") and f.endswith(".part")
    }  # noqa: E731
    _parts_before = _parts()
    _good = bz2.compress(b"PBDEMS2\0" + os.urandom(4000))
    for _name, _data in (
        ("trunc.dem.bz2", _good[: len(_good) // 2]),
        ("garbage.dem.bz2", b"not a bz2 file"),
        ("garbage.dem.gz", b"not a gzip file"),
        ("garbage.zip", b"PK not really a zip"),
        ("empty.dem.bz2", b""),
    ):
        _p = os.path.join(_cd, _name)
        with open(_p, "wb") as _f:
            _f.write(_data)
        with pytest.raises(ValueError):
            core.decompressed_demo(_p)
    assert not (_parts() - _parts_before), "no new .part leftovers after failed unpack"
