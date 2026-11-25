"""Core metrics on the synthetic match: shots, duels, summaries, context."""

import numpy as np
import pandas as pd

import core
from fakes import FakeParser, IDS, S1, S2, S3, S4, make_match


def test_allowed_speed():
    assert core.allowed_speed(400, 40) == 250.0, "near -> full speed"
    assert core.allowed_speed(2000, 40) == 40.0, "far -> thr"
    mid = core.allowed_speed(1000, 40)  # halfway: 40 + 0.5*(250-40) = 145
    assert abs(mid - 145.0) < 1e-9, "midpoint linear"
    assert np.isnan(core.allowed_speed(np.nan, 40)), "nan dist -> nan"
    arr = core.allowed_speed([400.0, 1000.0, np.nan], 40)
    assert arr[0] == 250.0 and abs(arr[1] - 145) < 1e-9 and np.isnan(arr[2]), "vector input"


def test_load_demo(demo):
    assert len(demo.fires) == 6, "warmup fires dropped"
    assert demo.excluded.get("warmup") == 1, "warmup excluded"
    assert len(demo.deaths) == 1, "warmup death dropped"
    assert len(demo.hurts) == 3, "non-warmup hurts kept (3 of 4)"
    assert (demo.hurts["user_steamid"] == S2).sum() == 0, "teamkill hurt filtered"
    assert "punch_pitch" in demo.ticks.columns, "punch extracted"
    p = demo.ticks[(demo.ticks["steamid"] == S1) & (demo.ticks["tick"] == 100)]
    assert len(p) == 1 and abs(float(p["punch_pitch"].iloc[0]) - 5.0) < 1e-9, "punch value parsed"
    assert set(demo.players["sid"]) == set(IDS), "players resolved"


def test_shots_and_risk(demo, shots):
    s1 = shots[shots["sid"] == S1].sort_values("tick")
    assert len(s1) == 3, "S1 has 3 shots"
    d100 = float(s1[s1["tick"] == 100]["dist"].iloc[0])
    d120 = float(s1[s1["tick"] == 120]["dist"].iloc[0])
    assert abs(d100 - 300) < 1e-6, "dist tick100 = 300"
    assert abs(d120 - 1196.0) < 1e-6, "dist tick120 = 1196 (moved 4u, enemy at 1200)"
    assert bool(s1[s1["tick"] == 120]["hit"].iloc[0]) is True, "hit flagged"
    assert bool(s1[s1["tick"] == 100]["hit"].iloc[0]) is False, "miss not flagged"
    assert bool(s1[s1["tick"] == 110]["hit"].iloc[0]) is False, "old shot 10t before hurt not flagged"
    s3 = shots[shots["sid"] == S3].sort_values("tick")
    assert bool(s3[s3["tick"] == 115]["hit"].iloc[0]) is True, "hurt at t+1 matched (tolerance)"
    assert bool(s3[s3["tick"] == 118]["hit"].iloc[0]) is False, "next shot after hurt not flagged"
    s4 = shots[shots["sid"] == S4]
    assert not bool(s4["hit"].iloc[0]), "hurt without matching fire not counted"
    # own speed: exact 1-tick position delta, capped on teleports
    sp = lambda sid, t: float(
        demo.ticks[(demo.ticks["steamid"] == sid) & (demo.ticks["tick"] == t)][
            "speed"
        ].iloc[0]
    )
    assert abs(sp(S1, 100)) < 1e-9, "speed standing = 0"
    assert abs(sp(S1, 120) - 256.0) < 1e-6, "speed moving = 256"
    assert pd.isna(sp(S3, 115)), "teleport capped to NaN"
    assert abs(sp(S3, 118)) < 1e-9, "speed after teleport ok"
    assert abs(sp(S4, 200) - 192.0) < 1e-6, "speed S4 = 192"
    assert abs(float(s1[s1["tick"] == 120]["speed"].iloc[0]) - 256.0) < 1e-6, "speed at shot col"
    s4 = shots[shots["sid"] == S4]
    assert len(s4) == 1 and pd.isna(s4["dist"].iloc[0]), "lonely shot dist NaN"
    s4 = shots[shots["sid"] == S4]
    r = s1.set_index("tick")["risky"]
    assert not bool(r.loc[100]), "near slow -> not risky"
    assert bool(r.loc[120]), "far fast -> risky"
    assert abs(float(s1[s1["tick"] == 120]["allowed_speed"].iloc[0]) - 103.84) < 0.05, "allowed at 1196 = 103.8"
    assert not bool(s4["risky"].iloc[0]), "lonely shot not risky"
    assert (shots["round"] == 1).sum() == 5, "round col present"


def test_build_duels(duels):
    assert len(duels) == 1, "one duel"
    d0 = duels.iloc[0]
    assert d0["killer"] == S1 and d0["victim"] == S3, "killer/victim"
    assert d0["first"] == "killer", "killer shot first"
    assert d0["killer_shots"] == 3 and d0["victim_shots"] == 2, "killer_shots=3 victim_shots=2"
    assert abs(d0["duration_s"] - round(21 / 64, 2)) < 1e-9, "duration"
    assert 4.0 < d0["killer_err"] < 6.0, "killer_err ~ punch (4..6 deg)"
    assert d0["victim_err"] < 1.0, "victim_err < 1 deg"
    assert float(d0["distance"]) == 800.0, "distance passthrough"


def test_duels_for_and_summary(demo, shots, duels):
    mine = core.duels_for(duels, S1)
    assert len(mine) == 1 and mine.iloc[0]["result"] == "kill", "S1: one kill duel"
    assert mine.iloc[0]["first"] == "me", "first mapped to me"
    summary = core.player_summary(demo, duels, shots, 40, 500, 1500)
    a = summary[summary["sid"] == S1].iloc[0]
    assert a["kills"] == 1 and a["deaths"] == 0, "summary kills"
    assert a["shots"] == 3 and a["hits"] == 1, "summary shots"
    assert abs(a["accuracy"] - 33.3) < 0.05, "summary accuracy"
    assert abs(a["risky"] - 33.3) < 0.05, "summary risky 33.3"
    w = core.weapon_stats(demo, shots, S1, 40, 500, 1500)
    assert len(w) == 1 and w.iloc[0]["weapon"] == "ak47", "weapon_stats ak47"
    assert abs(w.iloc[0]["accuracy"] - 33.3) < 0.05, "weapon accuracy"
    hg = core.hitgroup_counts(demo, S1)
    assert hg.get("head", 0) == 1, "hitgroup head=1"


def test_context(shots, duels):
    s1 = shots[shots["sid"] == S1].sort_values("tick")
    mine = core.duels_for(duels, S1)
    s1c = s1.set_index("tick")
    s3c = shots[shots["sid"] == S3].sort_values("tick").set_index("tick")
    s4c = shots[shots["sid"] == S4].set_index("tick")
    assert (
        list(s1c["zone"]) == ["BombsiteA"] * 3
        and list(s3c["zone"]) == ["Middle"] * 2
        and s4c["zone"].iloc[0] == "Catwalk"
    ), "zones passthrough"
    assert (
        abs(s1c.loc[100, "round_sec"] - 35.3) < 1e-9
        and abs(s1c.loc[110, "round_sec"] - 35.5) < 1e-9
        and abs(s1c.loc[120, "round_sec"] - 35.9) < 1e-9
        and abs(s4c["round_sec"].iloc[0] - 44.0) < 1e-9
    ), "round_sec per shot"
    assert (
        bool(s1c.loc[110, "flashed"]) is True
        and bool(s1c.loc[100, "flashed"]) is False
        and bool(s1c.loc[120, "flashed"]) is False
        and not bool(s3c["flashed"].any())
        and not bool(s4c["flashed"].iloc[0])
    ), "flashed at 110 only"
    # flash_duration holds the WHOLE length of a flash through its fade: only what is
    # left of it, counted from the player_blind event, makes a shot a flashed one
    def _flash_at_110(event):
        m = make_match()
        if event is None:
            m["events"].pop("player_blind")
        else:
            m["events"]["player_blind"] = m["events"]["player_blind"].assign(**event)
        FakeParser.demos["flash.dem"] = m
        f = core.shots_table(core.load_demo("flash.dem"))
        return f[f["sid"] == S1].set_index("tick")["flashed"]
    _fade = _flash_at_110({"tick": [110 - 120]})  # 1.875 s ago: 0.125 s left of 2.0
    assert bool(_fade[110]) is False, "flashed: the tail of the blindness is not a flashed shot"
    _mid = _flash_at_110({"tick": [110 - 64]})  # 1 s left
    assert bool(_mid[110]) is True and bool(_mid[100]) is False, "flashed: a second left is"
    _gone = _flash_at_110({"tick": [110 - 170]})  # the fade (held by the server), long over
    assert bool(_gone[110]) is False, "flashed: the fade after the blindness is not"
    _other = _flash_at_110({"blind_duration": [3.1]})
    assert pd.isna(_other[110]) and bool(_other[100]) is False, "flashed: a value without its own event is unknown, the rest stays known"
    _none = _flash_at_110(None)
    assert _none.isna().all(), "flashed: no player_blind events at all -> unknown, never 'not flashed'"
    assert (
        bool(s1c.loc[120, "slowed"]) is True
        and bool(s1c.loc[100, "slowed"]) is False
        and bool(s3c.loc[115, "slowed"]) is True
        and bool(s3c.loc[118, "slowed"]) is False
        and not bool(s4c["slowed"].iloc[0])
    ), "slowed at 120 (S1) and 115 (S3)"
    assert shots["flashed"].notna().all() and shots["slowed"].notna().all(), "flashed/slowed all known"
    assert (
        abs(duels.iloc[0]["round_sec"] - 37.0) < 1e-9
        and duels.iloc[0]["killer_zone"] == "BombsiteA"
        and duels.iloc[0]["victim_zone"] == "Middle"
    ), "duel context"
    assert (
        abs(mine.iloc[0]["round_sec"] - 37.0) < 1e-9
        and mine.iloc[0]["my_zone"] == "BombsiteA"
        and mine.iloc[0]["opp_zone"] == "Middle"
    ), "duels_for context"
    # unknown ("" / NaN / NA), never a crash and never a fake "False".
    def _strip_ctx(m):
        drop_ev = (
            "user_last_place_name",
            "attacker_last_place_name",
            "game_time",
            "round_start_time",
        )
        for name in ("weapon_fire", "player_hurt", "player_death"):
            df = m["events"][name]
            m["events"][name] = df.drop(
                columns=[c for c in df.columns if c in drop_ev], errors="ignore"
            )
        m["ticks"] = m["ticks"].drop(
            columns=["flash_duration", "velo_modifier"], errors="ignore"
        )
        return m
    FakeParser.demos["old.dem"] = _strip_ctx(make_match())
    old_demo = core.load_demo("old.dem")
    old_shots = core.shots_table(old_demo)
    old_duels = core.build_duels(old_demo)
    assert (old_shots["zone"] == "").all() and old_shots["round_sec"].isna().all(), "no ctx: zone '' + round_sec NaN"
    assert old_shots["flashed"].isna().all() and old_shots["slowed"].isna().all(), "no ctx: flags NA (not False)"
    assert (old_duels["killer_zone"] == "").all() and old_duels["round_sec"].isna().all(), "no ctx: duel zones '' + round_sec NaN"
    # the POS/buttons chain must survive, flags come out NA.
    m_unsup = make_match()
    m_unsup["unsupported"] = {"flash_duration", "velo_modifier"}
    FakeParser.demos["unsup.dem"] = m_unsup
    unsup_demo = core.load_demo("unsup.dem")
    unsup_shots = core.shots_table(unsup_demo)
    assert (
        {"X", "Y", "speed"} <= set(unsup_shots.columns)
        and unsup_shots["speed"].notna().any()
    ), "unsupported state props: POS chain intact"
    assert unsup_shots["flashed"].isna().all() and unsup_shots["slowed"].isna().all(), "unsupported state props: flags NA"
    assert (unsup_shots["zone"] == "BombsiteA").any(), "unsupported state props: zones still present (events unaffected)"
