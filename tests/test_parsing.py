"""First shots, data checks, id repair, punch variants, buttons, archives."""

import bz2
import gzip
import json
import os
import tempfile
import zipfile

import pandas as pd
import pytest

import core
import store
from fakes import FakeParser, S1, S2, S3, S4, f64_sid, make_match


def test_hurt_weapon_is_the_fired_one():
    """A hurt names the base item: m4a1 for the M4A1-S."""
    m = make_match()
    fires, hurts = m["events"]["weapon_fire"], m["events"]["player_hurt"]
    by_s1 = fires["user_name"] == "Alpha"
    m["events"]["weapon_fire"] = fires.assign(
        weapon=fires["weapon"].where(~by_s1, "weapon_m4a1_silencer")
    )
    m["events"]["player_hurt"] = hurts.assign(
        weapon=hurts["weapon"].where(hurts["attacker_name"] != "Alpha", "weapon_m4a1")
    )
    FakeParser.demos["m4a1s.dem"] = m
    d = core.load_demo("m4a1s.dem")
    weapon = d.hurts.set_index("attacker_steamid")["weapon"]
    assert weapon[S1] == "m4a1_silencer"
    assert weapon[S3] == "ak47" and weapon[S4] == "ak47", "no shot behind the hit: its own name"
    sh = core.shots_table(d)
    assert sh.loc[sh["tick"] == 120, "hit"].all(), "the shot knows it hit"
    ws = core.weapon_stats(d, sh, S1, 40.0).set_index("weapon")
    assert list(ws.index) == ["m4a1_silencer"]
    assert ws.loc["m4a1_silencer", "hits"] == 1 and ws.loc["m4a1_silencer", "accuracy"] > 0


def test_round_by_window():
    """The counter steps at the round end, the round lasts until the respawn."""
    df = pd.DataFrame({"tick": [150, 190, 250, 350], "total_rounds_played": [0, 1, 1, 1]})
    core.phases._round_by_window({1: (100, 200), 2: (300, 400)}, df)
    assert df["total_rounds_played"].tolist() == [0, 0, 1, 1]
    core.phases._round_by_window({}, df)
    assert df["total_rounds_played"].tolist() == [0, 0, 1, 1], "no windows: untouched"


def test_cap_damage():
    hurts = pd.DataFrame(
        {
            "tick": [10, 20, 15, 99],
            "user_steamid": ["a", "a", "b", "a"],
            "health": [40, 0, 0, 0],
            "dmg_health": [60, 431, 120, 431],
            "total_rounds_played": [0, 0, 0, 1],
        },
        index=[5, 2, 9, 4],
    )
    core.phases._cap_damage(hurts)
    assert hurts["dmg_health"].tolist() == [60, 40, 100, 100]


def test_first_shots_and_flick(shots):
    # gap semantics: > ENGAGE_GAP_TICKS since the same sid's
    # previous shot = fresh aim event; the array is order-independent
    _g = core.model._first_shot_arr([122, 100, 123], ["a", "a", "a"])
    assert _g.tolist() == [False, True, False], "gap boundary: 22 -> follow-up, 23 -> first, any row order"
    # the mouse sample covers the window BEFORE each first shot, clipped at 0
    _w = core.model._first_shot_windows(
        pd.DataFrame({"tick": [5, 700], "user_steamid": ["a", "a"]})
    )
    assert _w[:5] == [0, 1, 2, 3, 4] and _w[5] == 684 and len(_w) == 21, "windows: negative ticks clipped, sorted union"
    # base demo: S1 100/110/120 (first + 2 follow-ups), S3 115 first + 118
    # follow-up, S4 200 first
    assert (
        shots["first_shot"].sum() == 3
        and bool(shots[shots["sid"] == S1].sort_values("tick")["first_shot"].iloc[0])
        and not bool(shots[shots["sid"] == S1].sort_values("tick")["first_shot"].iloc[1])
    ), "first_shot flags on the base demo"
    assert shots["flick_move"].isna().all(), "no mouse data -> flick_move all NA"
    # mouse demo: S1's window [84..99] has 12 of 16 ticks (partial sums OK),
    # S3's window [99..114] exactly one row, S4's window empty -> NA
    m_mouse = make_match()
    m_mouse["mouse"] = {
        **{(t, S1): (3, 4) for t in range(88, 100)},
        (110, S3): (10, -5),
    }
    FakeParser.demos["mouse.dem"] = m_mouse
    md = core.load_demo("mouse.dem")
    assert md.mouse is not None and len(md.mouse) == 13 and md.mouse["mag"].sum() == 99.0, "demo.mouse: 13 rows, mag |dx|+|dy|"
    ms = core.shots_table(md)
    _s1fm = ms[ms["sid"] == S1].set_index("tick")["flick_move"]
    _s3fm = ms[ms["sid"] == S3].set_index("tick")["flick_move"]
    assert abs(float(_s1fm[100]) - 84.0) < 1e-9, "flick_move: partial window sums the available ticks (12 x 7)"
    assert _s1fm[110] != _s1fm[110] and _s1fm[120] != _s1fm[120], "flick_move: spray follow-ups stay NA"
    assert abs(float(_s3fm[115]) - 15.0) < 1e-9, "flick_move: single-row window = 15"
    assert ms[ms["sid"] == S4]["flick_move"].isna().all(), "flick_move: empty window -> NA, not 0"
    # first-shot accuracy: S3's first shot (115) hits (hurt at 116), S1's
    # (100) and S4's (200) miss; S2 never fired -> NaN
    summ = core.player_summary(md, core.build_duels(md), ms, 40.0)
    _fa = summ.set_index("sid")["first_acc"]
    assert _fa[S3] == 100.0 and _fa[S1] == 0.0 and _fa[S4] == 0.0 and _fa.isna().sum() == 1, "player_summary first_acc: hit 100.0 / miss 0.0 / no shots NaN"
    _ws = core.weapon_stats(md, ms, S3, 40.0)
    assert len(_ws) == 1 and _ws.iloc[0]["first_acc"] == 100.0, "weapon_stats first_acc: ak47 100.0 for S3"
    _ws1 = core.weapon_stats(md, ms, S1, 40.0)
    assert len(_ws1) == 1 and _ws1.iloc[0]["first_acc"] == 0.0, "weapon_stats first_acc: 0.0 for S1 (only follow-ups hit)"
    # parser that RAISES on the usercmd props: first_shot survives (it comes
    # from the events), flick_move degrades to NA everywhere
    m_unsup_mouse = make_match()
    m_unsup_mouse["unsupported"] = {"usercmd_mouse_dx", "usercmd_mouse_dy"}
    FakeParser.demos["unsup_mouse.dem"] = m_unsup_mouse
    um = core.load_demo("unsup_mouse.dem")
    um_shots = core.shots_table(um)
    assert um_shots["first_shot"].sum() == 3, "unsupported mouse props: first_shot still works"
    assert um_shots["flick_move"].isna().all() and um_shots["speed"].notna().any(), "unsupported mouse props: flick_move NA, POS chain intact"


def test_data_checks(demo, shots, duels):
    checks = {c["id"]: c for c in core.data_checks(demo, shots, duels)}
    assert checks["na_ids"]["level"] == "ok", "na_ids ok"
    assert checks["missing_pos"]["n"] == 0, "missing_pos n=0"
    assert checks["no_speed"]["n"] == 1, "no_speed n=1 (teleport)"
    assert checks["no_speed"]["level"] in ("ok", "warn"), "no_speed level ok/warn"
    assert checks["fast_speeds"]["level"] == "ok", "fast_speeds ok"
    assert checks["no_dist"]["n"] == 1, "no_dist n=1"
    assert checks["no_dist"]["level"] in ("ok", "warn"), "no_dist level ok/warn"
    assert checks["angles"]["level"] == "ok", "angles ok"
    assert checks["hitgroups"]["level"] == "ok", "hitgroups ok"
    # no "totals" in the demo config -> the fake returns an empty frame ->
    # demo.totals is None and the check reports a skip, never a guess
    assert checks["scoreboard"] == {"id": "scoreboard", "level": "ok", "n": 0, "vals": []}, "scoreboard skipped without totals"
    assert demo.totals is None, "totals None without config"


def test_scoreboard_cross_check():
    # match.dem live facts: S1 kills S3 once; raw death rows: S3 x1.
    # Warmup kill/death (S2->S4) dropped by our filter AND not counted by
    # the game (both aggregates reset when the live match starts).
    _tot_ok = {
        S1: ("Alpha", 1, 0),
        S2: ("Bravo", 0, 0),
        S3: ("Charlie", 0, 1),
        S4: ("Delta", 0, 0),
    }
    FakeParser.demos["tot_ok.dem"] = {**make_match(), "totals": _tot_ok}
    d_ok = core.load_demo("tot_ok.dem")
    assert (
        d_ok.totals is not None
        and {"sid", "kills", "deaths"} <= set(d_ok.totals.columns)
        and len(d_ok.totals) == 4
    ), "totals frame columns"
    ch = {
        c["id"]: c
        for c in core.data_checks(d_ok, core.shots_table(d_ok), core.build_duels(d_ok, 3.0))
    }
    assert ch["scoreboard"]["level"] == "ok", "scoreboard ok when totals match"
    assert ch["scoreboard"]["n"] == 0, "scoreboard ok n=0"
    # game claims MORE kills than we found -> we dropped something -> anomaly
    FakeParser.demos["tot_bad.dem"] = {
        **make_match(),
        "totals": {**_tot_ok, S1: ("Alpha", 2, 0)},
    }
    d_bad = core.load_demo("tot_bad.dem")
    ch = {
        c["id"]: c
        for c in core.data_checks(
            d_bad, core.shots_table(d_bad), core.build_duels(d_bad, 3.0)
        )
    }
    assert ch["scoreboard"]["level"] == "anomaly" and ch["scoreboard"]["n"] == 1, "scoreboard anomaly when game > ours"
    assert ch["scoreboard"]["vals"] and "Alpha" in ch["scoreboard"]["vals"][0], "scoreboard names the player"
    # deaths must equal RAW death rows: a live suicide is not an enemy death
    # (player_summary skips it) but the game counts it
    _m_sui = make_match()
    _m_sui["events"]["player_death"] = pd.concat(
        [
            _m_sui["events"]["player_death"],
            pd.DataFrame(
                {
                    "tick": [122],
                    "attacker_steamid": pd.array([int(S2)], dtype="float64"),
                    "attacker_name": ["Bravo"],
                    "user_steamid": pd.array([int(S2)], dtype="float64"),
                    "user_name": ["Bravo"],
                    "weapon": ["world"],
                    "headshot": [False],
                    "distance": [0],
                    "total_rounds_played": [0],
                    "is_warmup_period": [False],
                    "attacker_last_place_name": ["TSpawn"],
                    "user_last_place_name": ["TSpawn"],
                    "round_start_time": [100.0],
                    "game_time": [137.5],
                }
            ),
        ],
        ignore_index=True,
    )
    FakeParser.demos["tot_sui.dem"] = {
        **_m_sui,
        "totals": {**_tot_ok, S2: ("Bravo", 0, 1)},
    }
    d_sui = core.load_demo("tot_sui.dem")
    sm_sui = core.player_summary(
        d_sui, core.build_duels(d_sui, 3.0), core.shots_table(d_sui), 40
    )
    assert sm_sui[sm_sui["sid"] == S2].iloc[0]["deaths"] == 0, "suicide not an enemy death in summary"
    ch = {
        c["id"]: c
        for c in core.data_checks(
            d_sui, core.shots_table(d_sui), core.build_duels(d_sui, 3.0)
        )
    }
    assert ch["scoreboard"]["level"] == "ok", "scoreboard ok with a suicide death row"
    # ...but a game that did NOT count the suicide is a mismatch
    FakeParser.demos["tot_sui2.dem"] = {**_m_sui, "totals": _tot_ok}
    d_sui2 = core.load_demo("tot_sui2.dem")
    ch = {
        c["id"]: c
        for c in core.data_checks(
            d_sui2, core.shots_table(d_sui2), core.build_duels(d_sui2, 3.0)
        )
    }
    assert ch["scoreboard"]["level"] == "anomaly" and ch["scoreboard"]["n"] == 1, "scoreboard catches an uncounted suicide"
    # parser that does not know the props -> totals None, check skipped,
    # the POS chain untouched (the buttons lesson)
    _tn = make_match()
    _tn["unsupported"] = {"kills_total", "deaths_total"}
    FakeParser.demos["tot_none.dem"] = _tn
    d_tn = core.load_demo("tot_none.dem")
    assert d_tn.totals is None, "totals None when parser rejects"
    assert len(d_tn.fires) == 6, "shots still parsed"
    ch = {
        c["id"]: c
        for c in core.data_checks(d_tn, core.shots_table(d_tn), core.build_duels(d_tn, 3.0))
    }
    assert ch["scoreboard"]["level"] == "ok" and ch["scoreboard"]["n"] == 0, "scoreboard skipped when parser rejects"


def test_fix_ids_rounded_floats():
    # Only sids that are multiples of 16 survive float64; a column of rounded
    # floats in a NON-float dtype lose everyone
    # else (in a real 10-player match exactly one player kept his hits).
    H = "76561199129512544"  # % 16 == 0 -> float-exact
    C = "76561198323851243"  # rounds to ...248
    info_h = pd.DataFrame(
        {
            "steamid": [int(H), int(C)],
            "name": ["Heylaurens", "choompuy"],
            "team_number": [3, 2],
        }
    )
    fl_h, n2s_h = core.build_id_lookup(info_h)
    col = pd.Series([float(int(C)), float(int(H))], dtype=object)  # rounded, object dtype
    fixed = core.fix_ids(col, fl_h, pd.Series(["choompuy", "Heylaurens"]), n2s_h)
    assert list(fixed) == [C, H], "object floats resolved with names"
    fixed_nn = core.fix_ids(col, fl_h)
    assert list(fixed_nn) == [C, H], "object floats resolved without names"
    # collision: two sids collapse to one float -> name disambiguates, else NA
    A1 = "76561111111111001"  # both -> same float64
    A2 = "76561111111111003"
    info_c = pd.DataFrame(
        {"steamid": [int(A1), int(A2)], "name": ["p1", "p2"], "team_number": [2, 3]}
    )
    fl_c, n2s_c = core.build_id_lookup(info_c)
    col_c = pd.Series([float(int(A1)), float(int(A2))], dtype=object)
    fix_c = core.fix_ids(col_c, fl_c, pd.Series(["p1", "p2"]), n2s_c)
    assert list(fix_c) == [A1, A2], "collision disambiguated by name"
    fix_c2 = core.fix_ids(col_c, fl_c)
    assert list(fix_c2) == [core.NA, core.NA], "collision without name -> NA"
    # exact int64 column still works
    fix_i = core.fix_ids(pd.Series([int(C), int(H)], dtype="int64"), fl_h)
    assert list(fix_i) == [C, H], "int64 column resolved"
    # garbage -> NA
    fix_g = core.fix_ids(pd.Series(["abc", None, 0], dtype=object), fl_h)
    assert list(fix_g) == [core.NA] * 3, "garbage -> NA"


def test_debug_report(demo, shots, duels):
    rep = core.debug_report(
        demo,
        shots,
        duels,
        S1,
        {"thr": 40, "window_s": 3.0, "safe_dist": 500, "strict_dist": 1500},
    )
    txt = json.dumps(rep, ensure_ascii=False, default=str)
    assert len(txt) > 1000, "json serializable"
    rr = json.loads(txt)
    assert rr["counts"]["shots"] == 6 and rr["counts"]["duels"] == 1, "report counts"
    assert rr["counts"]["excluded"]["warmup"] == 1, "warmup in report"
    assert rr["counts"]["punch_available"] is True, "punch flag in report"
    assert len(rr["me"]["shots"]) == 3, "my shots in report"


def test_punch_presplit_columns():
    m2 = make_match()
    tk = m2["ticks"].copy()
    tk = tk.drop(columns=["aim_punch_angle"])
    tk["aim_punch_angle_0"] = 2.5
    tk["aim_punch_angle_1"] = -1.5
    m2["ticks"] = tk
    FakeParser.demos["match2.dem"] = m2
    demo2 = core.load_demo("match2.dem")
    row = demo2.ticks[(demo2.ticks["steamid"] == S1) & (demo2.ticks["tick"] == 120)]
    assert (
        abs(float(row["punch_pitch"].iloc[0]) - 2.5) < 1e-9
        and abs(float(row["punch_yaw"].iloc[0]) + 1.5) < 1e-9
    ), "split cols extracted"


def test_punch_unsupported():
    m3 = make_match()
    m3["unsupported"] = {"aim_punch_angle"}
    FakeParser.demos["match3.dem"] = m3
    demo3 = core.load_demo("match3.dem")
    assert len(demo3.ticks) == 22, "ticks parsed anyway"
    assert demo3.ticks["punch_pitch"].isna().all(), "punch all-NaN when unsupported"
    assert "is_alive" in demo3.ticks.columns, "is_alive kept when punch unsupported"
    duels3 = core.build_duels(demo3, 3.0)
    assert duels3.iloc[0]["killer_err"] < 1.0, "duel err without punch < 1"


def test_timeline_buttons():
    m4 = make_match()
    tl_rows = []
    for tick in range(490, 560):
        tl_rows.append(
            {
                "tick": tick,
                "steamid": f64_sid(S1),
                "name": "Alpha",
                "X": (tick - 490) * 4.0,
                "Y": 0.0,
                "Z": 0.0,
                "pitch": 1.0,
                "yaw": 2.0,
                "is_alive": tick < 512,  # dies at 512 = the event tick
            }
        )
    m4["ticks"] = pd.DataFrame(tl_rows)
    # FORWARD(bit3)+DUCK(bit2)+JUMP(bit1)+WALK(bit17)+FIRE(bit0)
    bits = (1 << 3) | (1 << 2) | (1 << 1) | (1 << 17) | (1 << 0)
    m4["buttons_bit"] = {(t, f64_sid(S1)): bits for t in range(490, 560)}
    FakeParser.demos["tl.dem"] = m4
    df, missing = core.timeline_data("tl.dem", S1, 512, before_s=3.0, after_s=0.25)
    assert missing == [], "no missing buttons"
    assert "is_alive" in df.columns, "is_alive in timeline"
    alive_part = df[df["tick"] < 512]
    dead_part = df[df["tick"] >= 512]
    assert abs(alive_part["speed"].dropna() - 256.0).max() < 1e-6, "timeline speed 256 while alive"
    assert dead_part["speed"].isna().all(), "timeline speed NaN when dead"
    assert pd.isna(df.iloc[0]["speed"]), "first tick speed NaN (no history)"
    assert bool(df["DUCK"].all()), "DUCK decoded"
    assert bool(df["JUMP"].all()), "JUMP decoded"
    assert bool(df["FORWARD"].all()), "FORWARD present"
    assert bool(df["WALK"].all()), "WALK present"
    assert bool(df["FIRE"].all()), "FIRE present"
    assert not bool(df["LEFT"].any()), "LEFT absent-ish (not pressed)"
    m5 = make_match()
    m5["ticks"] = pd.DataFrame(tl_rows)
    m5["unsupported"] = set(core.BTN_NAMED) | {"buttons"}
    FakeParser.demos["tl2.dem"] = m5
    df2, missing2 = core.timeline_data("tl2.dem", S1, 512, before_s=3.0, after_s=0.25)
    assert missing2 == core.BUTTONS, "all buttons missing"
    assert len(df2) > 0 and "yaw" in df2.columns, "POS still parsed"
    m6 = make_match()
    m6["ticks"] = pd.DataFrame(tl_rows)
    m6["unsupported"] = set(core.BTN_NAMED)  # named fail, raw buttons ok
    m6["buttons_bit"] = {(t, f64_sid(S1)): bits for t in range(490, 560)}
    FakeParser.demos["tl3.dem"] = m6
    df3, missing3 = core.timeline_data("tl3.dem", S1, 512, before_s=3.0, after_s=0.25)
    assert "DUCK" in df3.columns and "JUMP" in df3.columns, "named missing but DUCK/JUMP decoded"
    assert set(core.BTN_NAMED).issubset(set(missing3)), "named in missing list"
    dftl, _ = core.timeline_data(
        "tl.dem", S1, 512, before_s=3.0, after_s=0.25, tickrate=128
    )
    assert len(dftl) and int(dftl.iloc[0]["tick"]) == 490, "timeline window in ticks scales (first row 490)"
    sp_tl = dftl[(dftl["tick"] > 490) & (dftl["tick"] < 512)]["speed"].dropna()
    assert abs(sp_tl.max() - 512.0) < 1e-6, "timeline speed at 128 tick = 512"


def test_keys_from_pawn_state():
    """Recent CS2 demos carry neither `buttons` nor WALK: Shift / crouch / jump come from
    the pawn state instead; a real bitfield still wins."""
    from core.model import _decode_buttons

    df = _decode_buttons(
        pd.DataFrame(
            {
                "FORWARD": [True, False, False],
                "is_walking": [True, False, None],
                "duck_amount": [0.0, 0.7, 1.0],
                "is_airborne": [False, True, False],
            }
        )
    )
    assert list(df["WALK"]) == [True, False, False], "WALK = is_walking"
    assert list(df["DUCK"]) == [False, True, True], "DUCK = crouched past half way"
    assert list(df["JUMP"]) == [False, True, False], "JUMP = in the air"
    bits = _decode_buttons(pd.DataFrame({"buttons": [1 << 2], "is_airborne": [True]}))
    assert bool(bits["DUCK"].iloc[0]) and not bool(bits["JUMP"].iloc[0]), "the bitfield wins"


def test_decompressed_demo():
    payload = b"FAKEDEMO-PAYLOAD-" * 256
    tdir = tempfile.mkdtemp(prefix="cs2aa_test_")
    def _read(p):
        with open(p, "rb") as f:
            return f.read()
    plain = os.path.join(tdir, "match.dem")
    with open(plain, "wb") as f:
        f.write(payload)
    # decompressed_demo returns os.path.realpath(path): on Windows CI the
    # temp dir contains an 8.3 short name (RUNNER~1) that realpath expands,
    # so compare against the resolved path, not the mkdtemp string.
    assert core.decompressed_demo(plain) == os.path.realpath(plain), "plain .dem returned as is"
    bz = os.path.join(tdir, "valve_match.dem.bz2")
    with bz2.open(bz, "wb") as f:
        f.write(payload)
    out = core.decompressed_demo(bz)
    assert _read(out) == payload and out != bz, "bz2 unpacked"
    assert core.decompressed_demo(bz) == out, "bz2 cache hit (same path, no re-unpack)"
    gz = os.path.join(tdir, "faceit_match.dem.gz")
    with gzip.open(gz, "wb") as f:
        f.write(payload)
    assert _read(core.decompressed_demo(gz)) == payload, "gz unpacked"
    zp = os.path.join(tdir, "match.zip")
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("readme.txt", "ignore me")
        z.writestr("map-de_dust2.dem", payload)
    assert _read(core.decompressed_demo(zp)) == payload, "zip unpacked (first .dem member)"
    zp_bad = os.path.join(tdir, "no_dem.zip")
    with zipfile.ZipFile(zp_bad, "w") as z:
        z.writestr("readme.txt", "no demo here")
    with pytest.raises(ValueError) as exc_info:
        core.decompressed_demo(zp_bad)
    e = exc_info.value
    assert "No .dem" in str(e), "zip without .dem raises"
    try:
        import zstandard  # noqa: F401

        zs = os.path.join(tdir, "match.dem.zst")
        with open(zs, "wb") as f:
            f.write(zstandard.compress(payload))
        assert _read(core.decompressed_demo(zs)) == payload, "zst unpacked"
    except ImportError:
        print("  skip zst (zstandard not installed)")
    def _parts_in_tmp():
        d = store.unpacked_dir()
        return sorted(
            f
            for f in os.listdir(d)
            if f.startswith("cs2_aimaudit_") and f.endswith(".part")
        )
    def _expect_value_error(p):
        nm = os.path.basename(p)
        before = _parts_in_tmp()
        with pytest.raises(ValueError):
            core.decompressed_demo(p)
        assert _parts_in_tmp() == before, f"{nm} -> no .part left"
    bz_trunc = os.path.join(tdir, "truncated.dem.bz2")
    _blob = bz2.compress(payload)
    with open(bz_trunc, "wb") as f:
        f.write(_blob[: len(_blob) // 2])
    gz_trunc = os.path.join(tdir, "truncated.dem.gz")
    _blob = gzip.compress(payload)
    with open(gz_trunc, "wb") as f:
        f.write(_blob[: len(_blob) // 2])
    zip_fake = os.path.join(tdir, "fake.zip")
    with open(zip_fake, "wb") as f:
        f.write(b"this is not a zip file at all, just garbage bytes")
    for p in (bz_trunc, gz_trunc, zip_fake):
        _expect_value_error(p)
    try:
        import zstandard

        zs_garbage = os.path.join(tdir, "garbage.dem.zst")
        with open(zs_garbage, "wb") as f:
            f.write(b"\x28\xb5\x2f\xfd not really a zstd frame")
        zs_trunc = os.path.join(tdir, "truncated.dem.zst")
        _blob = zstandard.compress(payload)
        with open(zs_trunc, "wb") as f:
            f.write(_blob[: len(_blob) // 2])
        for p in (zs_garbage, zs_trunc):
            _expect_value_error(p)
    except ImportError:
        print("  skip broken zst (zstandard not installed)")
