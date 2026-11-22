"""first shots / mouse flick, data_checks (+ scoreboard cross-check), fix_ids, debug_report, punch variants, timeline buttons, decompressed_demo.

Split out of tests/test_core.py; runs at import in
the runner's order - see core_sections/__init__.py.
"""

# --- shared fixtures and helpers (see _shared.py) ---
from ._shared import (  # noqa: F401
    FakeParser,
    S1,
    S2,
    S3,
    S4,
    bz2,
    check,
    core,
    f64_sid,
    gzip,
    json,
    make_match,
    os,
    pd,
    store,
    tempfile,
    zipfile,
)
from .s01_metrics_tables import demo, duels, shots  # noqa: F401
# ------------------------------------------------ first shots / mouse flick
print("== first shots / mouse flick ==")
# gap semantics: > ENGAGE_GAP_TICKS since the same sid's
# previous shot = fresh aim event; the array is order-independent
_g = core._first_shot_arr([122, 100, 123], ["a", "a", "a"])
check(
    "gap boundary: 22 -> follow-up, 23 -> first, any row order",
    _g.tolist() == [False, True, False],
    _g.tolist(),
)
# the mouse sample covers the window BEFORE each first shot, clipped at 0
_w = core._first_shot_windows(
    pd.DataFrame({"tick": [5, 700], "user_steamid": ["a", "a"]})
)
check(
    "windows: negative ticks clipped, sorted union",
    _w[:5] == [0, 1, 2, 3, 4] and _w[5] == 684 and len(_w) == 21,
    (_w[:6], len(_w)),
)
# base demo: S1 100/110/120 (first + 2 follow-ups), S3 115 first + 118
# follow-up, S4 200 first
check(
    "first_shot flags on the base demo",
    shots["first_shot"].sum() == 3
    and bool(shots[shots["sid"] == S1].sort_values("tick")["first_shot"].iloc[0])
    and not bool(shots[shots["sid"] == S1].sort_values("tick")["first_shot"].iloc[1]),
    shots[["tick", "sid", "first_shot"]].to_dict(),
)
check("no mouse data -> flick_move all NA", shots["flick_move"].isna().all())

# mouse demo: S1's window [84..99] has 12 of 16 ticks (partial sums OK),
# S3's window [99..114] exactly one row, S4's window empty -> NA
m_mouse = make_match()
m_mouse["mouse"] = {
    **{(t, S1): (3, 4) for t in range(88, 100)},
    (110, S3): (10, -5),
}
FakeParser.demos["mouse.dem"] = m_mouse
md = core.load_demo("mouse.dem")
check(
    "demo.mouse: 13 rows, mag |dx|+|dy|",
    md.mouse is not None and len(md.mouse) == 13 and md.mouse["mag"].sum() == 99.0,
    None if md.mouse is None else md.mouse["mag"].sum(),
)
ms = core.shots_table(md)
_s1fm = ms[ms["sid"] == S1].set_index("tick")["flick_move"]
_s3fm = ms[ms["sid"] == S3].set_index("tick")["flick_move"]
check(
    "flick_move: partial window sums the available ticks (12 x 7)",
    abs(float(_s1fm[100]) - 84.0) < 1e-9,
    float(_s1fm[100]),
)
check(
    "flick_move: spray follow-ups stay NA",
    _s1fm[110] != _s1fm[110] and _s1fm[120] != _s1fm[120],
    (_s1fm[110], _s1fm[120]),
)
check(
    "flick_move: single-row window = 15",
    abs(float(_s3fm[115]) - 15.0) < 1e-9,
    float(_s3fm[115]),
)
check(
    "flick_move: empty window -> NA, not 0",
    ms[ms["sid"] == S4]["flick_move"].isna().all(),
    ms[ms["sid"] == S4]["flick_move"].tolist(),
)
# first-shot accuracy: S3's first shot (115) hits (hurt at 116), S1's
# (100) and S4's (200) miss; S2 never fired -> NaN
summ = core.player_summary(md, core.build_duels(md), ms, 40.0)
_fa = summ.set_index("sid")["first_acc"]
check(
    "player_summary first_acc: hit 100.0 / miss 0.0 / no shots NaN",
    _fa[S3] == 100.0 and _fa[S1] == 0.0 and _fa[S4] == 0.0 and _fa.isna().sum() == 1,
    _fa.to_dict(),
)
_ws = core.weapon_stats(md, ms, S3, 40.0)
check(
    "weapon_stats first_acc: ak47 100.0 for S3",
    len(_ws) == 1 and _ws.iloc[0]["first_acc"] == 100.0,
    _ws.to_dict(),
)
_ws1 = core.weapon_stats(md, ms, S1, 40.0)
check(
    "weapon_stats first_acc: 0.0 for S1 (only follow-ups hit)",
    len(_ws1) == 1 and _ws1.iloc[0]["first_acc"] == 0.0,
    _ws1.to_dict(),
)

# parser that RAISES on the usercmd props: first_shot survives (it comes
# from the events), flick_move degrades to NA everywhere
m_unsup_mouse = make_match()
m_unsup_mouse["unsupported"] = {"usercmd_mouse_dx", "usercmd_mouse_dy"}
FakeParser.demos["unsup_mouse.dem"] = m_unsup_mouse
um = core.load_demo("unsup_mouse.dem")
um_shots = core.shots_table(um)
check(
    "unsupported mouse props: first_shot still works",
    um_shots["first_shot"].sum() == 3,
    um_shots["first_shot"].sum(),
)
check(
    "unsupported mouse props: flick_move NA, POS chain intact",
    um_shots["flick_move"].isna().all() and um_shots["speed"].notna().any(),
)

# ------------------------------------------------------------- data_checks
print("== data_checks ==")
checks = {c["id"]: c for c in core.data_checks(demo, shots, duels)}
check("na_ids ok", checks["na_ids"]["level"] == "ok", checks["na_ids"])
check("missing_pos n=0", checks["missing_pos"]["n"] == 0, checks["missing_pos"])
check("no_speed n=1 (teleport)", checks["no_speed"]["n"] == 1, checks["no_speed"])
check(
    "no_speed level ok/warn",
    checks["no_speed"]["level"] in ("ok", "warn"),
    checks["no_speed"],
)
check("fast_speeds ok", checks["fast_speeds"]["level"] == "ok", checks["fast_speeds"])
check("no_dist n=1", checks["no_dist"]["n"] == 1, checks["no_dist"])
check(
    "no_dist level ok/warn",
    checks["no_dist"]["level"] in ("ok", "warn"),
    checks["no_dist"]["level"],
)
check("angles ok", checks["angles"]["level"] == "ok", checks["angles"])
check("hitgroups ok", checks["hitgroups"]["level"] == "ok", checks["hitgroups"])
# no "totals" in the demo config -> the fake returns an empty frame ->
# demo.totals is None and the check reports a skip, never a guess
check(
    "scoreboard skipped without totals",
    checks["scoreboard"] == {"id": "scoreboard", "level": "ok", "n": 0, "vals": []},
    checks["scoreboard"],
)
check("totals None without config", demo.totals is None, type(demo.totals))

# ---------------------------------- scoreboard cross-check
print("== data_checks: scoreboard cross-check ==")
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
check(
    "totals frame columns",
    d_ok.totals is not None
    and {"sid", "kills", "deaths"} <= set(d_ok.totals.columns)
    and len(d_ok.totals) == 4,
    None if d_ok.totals is None else d_ok.totals.to_dict(),
)
ch = {
    c["id"]: c
    for c in core.data_checks(d_ok, core.shots_table(d_ok), core.build_duels(d_ok, 3.0))
}
check(
    "scoreboard ok when totals match",
    ch["scoreboard"]["level"] == "ok",
    ch["scoreboard"],
)
check("scoreboard ok n=0", ch["scoreboard"]["n"] == 0, ch["scoreboard"])

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
check(
    "scoreboard anomaly when game > ours",
    ch["scoreboard"]["level"] == "anomaly" and ch["scoreboard"]["n"] == 1,
    ch["scoreboard"],
)
check(
    "scoreboard names the player",
    ch["scoreboard"]["vals"] and "Alpha" in ch["scoreboard"]["vals"][0],
    ch["scoreboard"].get("vals"),
)

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
check(
    "suicide not an enemy death in summary",
    sm_sui[sm_sui["sid"] == S2].iloc[0]["deaths"] == 0,
    sm_sui[sm_sui["sid"] == S2].to_dict(),
)
ch = {
    c["id"]: c
    for c in core.data_checks(
        d_sui, core.shots_table(d_sui), core.build_duels(d_sui, 3.0)
    )
}
check(
    "scoreboard ok with a suicide death row",
    ch["scoreboard"]["level"] == "ok",
    ch["scoreboard"],
)
# ...but a game that did NOT count the suicide is a mismatch
FakeParser.demos["tot_sui2.dem"] = {**_m_sui, "totals": _tot_ok}
d_sui2 = core.load_demo("tot_sui2.dem")
ch = {
    c["id"]: c
    for c in core.data_checks(
        d_sui2, core.shots_table(d_sui2), core.build_duels(d_sui2, 3.0)
    )
}
check(
    "scoreboard catches an uncounted suicide",
    ch["scoreboard"]["level"] == "anomaly" and ch["scoreboard"]["n"] == 1,
    ch["scoreboard"],
)

# parser that does not know the props -> totals None, check skipped,
# the POS chain untouched (the buttons lesson)
_tn = make_match()
_tn["unsupported"] = {"kills_total", "deaths_total"}
FakeParser.demos["tot_none.dem"] = _tn
d_tn = core.load_demo("tot_none.dem")
check("totals None when parser rejects", d_tn.totals is None, type(d_tn.totals))
check("shots still parsed", len(d_tn.fires) == 6, len(d_tn.fires))
ch = {
    c["id"]: c
    for c in core.data_checks(d_tn, core.shots_table(d_tn), core.build_duels(d_tn, 3.0))
}
check(
    "scoreboard skipped when parser rejects",
    ch["scoreboard"]["level"] == "ok" and ch["scoreboard"]["n"] == 0,
    ch["scoreboard"],
)

# ---------------------------------------------- fix_ids: the Heylaurens bug
print("== fix_ids: rounded floats in non-float column ==")
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
check("object floats resolved with names", list(fixed) == [C, H], list(fixed))
fixed_nn = core.fix_ids(col, fl_h)
check("object floats resolved without names", list(fixed_nn) == [C, H], list(fixed_nn))
# collision: two sids collapse to one float -> name disambiguates, else NA
A1 = "76561111111111001"  # both -> same float64
A2 = "76561111111111003"
info_c = pd.DataFrame(
    {"steamid": [int(A1), int(A2)], "name": ["p1", "p2"], "team_number": [2, 3]}
)
fl_c, n2s_c = core.build_id_lookup(info_c)
col_c = pd.Series([float(int(A1)), float(int(A2))], dtype=object)
fix_c = core.fix_ids(col_c, fl_c, pd.Series(["p1", "p2"]), n2s_c)
check("collision disambiguated by name", list(fix_c) == [A1, A2], list(fix_c))
fix_c2 = core.fix_ids(col_c, fl_c)
check("collision without name -> NA", list(fix_c2) == [core.NA, core.NA], list(fix_c2))
# exact int64 column still works
fix_i = core.fix_ids(pd.Series([int(C), int(H)], dtype="int64"), fl_h)
check("int64 column resolved", list(fix_i) == [C, H], list(fix_i))
# garbage -> NA
fix_g = core.fix_ids(pd.Series(["abc", None, 0], dtype=object), fl_h)
check("garbage -> NA", list(fix_g) == [core.NA] * 3, list(fix_g))

# ------------------------------------------------------------ debug_report
print("== debug_report ==")
rep = core.debug_report(
    demo,
    shots,
    duels,
    S1,
    {"thr": 40, "window_s": 3.0, "safe_dist": 500, "strict_dist": 1500},
)
txt = json.dumps(rep, ensure_ascii=False, default=str)
check("json serializable", len(txt) > 1000)
rr = json.loads(txt)
check("report counts", rr["counts"]["shots"] == 6 and rr["counts"]["duels"] == 1)
check("warmup in report", rr["counts"]["excluded"]["warmup"] == 1)
check("punch flag in report", rr["counts"]["punch_available"] is True)
check("my shots in report", len(rr["me"]["shots"]) == 3)

# ----------------------------------------------------------- punch format 2
print("== punch pre-split columns ==")
m2 = make_match()
tk = m2["ticks"].copy()
tk = tk.drop(columns=["aim_punch_angle"])
tk["aim_punch_angle_0"] = 2.5
tk["aim_punch_angle_1"] = -1.5
m2["ticks"] = tk
FakeParser.demos["match2.dem"] = m2
demo2 = core.load_demo("match2.dem")
row = demo2.ticks[(demo2.ticks["steamid"] == S1) & (demo2.ticks["tick"] == 120)]
check(
    "split cols extracted",
    abs(float(row["punch_pitch"].iloc[0]) - 2.5) < 1e-9
    and abs(float(row["punch_yaw"].iloc[0]) + 1.5) < 1e-9,
)

# -------------------------------------------------- punch unsupported demo
print("== punch unsupported -> fallback ==")
m3 = make_match()
m3["unsupported"] = {"aim_punch_angle"}
FakeParser.demos["match3.dem"] = m3
demo3 = core.load_demo("match3.dem")
check("ticks parsed anyway", len(demo3.ticks) == 22, len(demo3.ticks))
check("punch all-NaN when unsupported", demo3.ticks["punch_pitch"].isna().all())
check("is_alive kept when punch unsupported", "is_alive" in demo3.ticks.columns)
duels3 = core.build_duels(demo3, 3.0)
check(
    "duel err without punch < 1",
    duels3.iloc[0]["killer_err"] < 1.0,
    duels3.iloc[0]["killer_err"],
)

# ----------------------------------------------------------- timeline data
print("== timeline_data buttons ==")
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
check("no missing buttons", missing == [], missing)
check("is_alive in timeline", "is_alive" in df.columns)
alive_part = df[df["tick"] < 512]
dead_part = df[df["tick"] >= 512]
check(
    "timeline speed 256 while alive",
    abs(alive_part["speed"].dropna() - 256.0).max() < 1e-6,
    alive_part["speed"].tolist()[:5],
)
check("timeline speed NaN when dead", dead_part["speed"].isna().all())
check("first tick speed NaN (no history)", pd.isna(df.iloc[0]["speed"]))
check("DUCK decoded", bool(df["DUCK"].all()))
check("JUMP decoded", bool(df["JUMP"].all()))
check("FORWARD present", bool(df["FORWARD"].all()))
check("WALK present", bool(df["WALK"].all()))
check("FIRE present", bool(df["FIRE"].all()))
check("LEFT absent-ish (not pressed)", not bool(df["LEFT"].any()))

print("== timeline_data: buttons unsupported ==")
m5 = make_match()
m5["ticks"] = pd.DataFrame(tl_rows)
m5["unsupported"] = set(core.BTN_NAMED) | {"buttons"}
FakeParser.demos["tl2.dem"] = m5
df2, missing2 = core.timeline_data("tl2.dem", S1, 512, before_s=3.0, after_s=0.25)
check("all buttons missing", missing2 == core.BUTTONS, missing2)
check("POS still parsed", len(df2) > 0 and "yaw" in df2.columns)

print("== timeline_data: buttons raw only partially supported ==")
m6 = make_match()
m6["ticks"] = pd.DataFrame(tl_rows)
m6["unsupported"] = set(core.BTN_NAMED)  # named fail, raw buttons ok
m6["buttons_bit"] = {(t, f64_sid(S1)): bits for t in range(490, 560)}
FakeParser.demos["tl3.dem"] = m6
df3, missing3 = core.timeline_data("tl3.dem", S1, 512, before_s=3.0, after_s=0.25)
check(
    "named missing but DUCK/JUMP decoded",
    "DUCK" in df3.columns and "JUMP" in df3.columns,
)
check("named in missing list", set(core.BTN_NAMED).issubset(set(missing3)))

# ------------------------------------------------- compressed demo unpacking
print("== decompressed_demo ==")
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
check(
    "plain .dem returned as is",
    core.decompressed_demo(plain) == os.path.realpath(plain),
)

bz = os.path.join(tdir, "valve_match.dem.bz2")
with bz2.open(bz, "wb") as f:
    f.write(payload)
out = core.decompressed_demo(bz)
check("bz2 unpacked", _read(out) == payload and out != bz)
check("bz2 cache hit (same path, no re-unpack)", core.decompressed_demo(bz) == out)

gz = os.path.join(tdir, "faceit_match.dem.gz")
with gzip.open(gz, "wb") as f:
    f.write(payload)
check("gz unpacked", _read(core.decompressed_demo(gz)) == payload)

zp = os.path.join(tdir, "match.zip")
with zipfile.ZipFile(zp, "w") as z:
    z.writestr("readme.txt", "ignore me")
    z.writestr("map-de_dust2.dem", payload)
check("zip unpacked (first .dem member)", _read(core.decompressed_demo(zp)) == payload)

zp_bad = os.path.join(tdir, "no_dem.zip")
with zipfile.ZipFile(zp_bad, "w") as z:
    z.writestr("readme.txt", "no demo here")
try:
    core.decompressed_demo(zp_bad)
    check("zip without .dem raises", False)
except ValueError as e:
    check("zip without .dem raises", "No .dem" in str(e))

try:
    import zstandard  # noqa: F401

    zs = os.path.join(tdir, "match.dem.zst")
    with open(zs, "wb") as f:
        f.write(zstandard.compress(payload))
    check("zst unpacked", _read(core.decompressed_demo(zs)) == payload)
except ImportError:
    print("  skip zst (zstandard not installed)")

# broken archives: a truncated or corrupt file can make the decompressor
# throw anything (EOFError, BadZipFile, ZstdError, OSError). The UI only
# catches ValueError, so everything must be converted - and the half-
# written .part must be deleted, not left to eat disk space in temp.
print("== decompressed_demo: broken archives ==")


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
    try:
        core.decompressed_demo(p)
        check(f"{nm} -> ValueError", False, "no exception at all")
    except ValueError:
        check(f"{nm} -> ValueError", True)
    except Exception as e:  # noqa: BLE001
        check(f"{nm} -> ValueError", False, f"escaped as {type(e).__name__}: {e}")
    check(f"{nm} -> no .part left", _parts_in_tmp() == before, _parts_in_tmp())


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
