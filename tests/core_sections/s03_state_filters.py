"""app state, tickrate from header, halftime sides, wilson_ci, rank_in_lobby, weapon_class, apply_filters, round presets.

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
    check,
    core,
    make_match,
    make_match_sides,
    np,
    os,
    pd,
    store,
)
from .s01_metrics_tables import demo  # noqa: F401
# ------------------------------------------------------------- app state
print("== app state (data folder) ==")
_sp = store.state_path()
if os.path.exists(_sp):
    os.remove(_sp)
check("missing state file -> {}", core.load_state() == {})
core.save_state({"last_player": "p1", "last_demo": "d1"})
check("state round-trip", core.load_state() == {"last_player": "p1", "last_demo": "d1"})
with open(_sp, "w", encoding="utf-8") as f:
    f.write("{ not json")
check("corrupt state -> {}", core.load_state() == {})
os.remove(_sp)

# -------------------------------------------------- tickrate from header
print("== tickrate from header ==")
check(
    "128 from playback fields",
    core.tickrate_from_header({"playback_ticks": 6400, "playback_time": 50.0}) == 128,
)
check(
    "64 from playback fields",
    core.tickrate_from_header({"playback_ticks": 64000, "playback_time": 1000.0}) == 64,
)
check("missing header -> 64", core.tickrate_from_header({}) == 64)
check(
    "zero playback_time -> 64",
    core.tickrate_from_header({"playback_ticks": 6400, "playback_time": 0}) == 64,
)
check(
    "insane ratio -> 64",
    core.tickrate_from_header({"playback_ticks": 5, "playback_time": 1000.0}) == 64,
)

m_tr = make_match()
m_tr["header"] = {
    "map_name": "de_mirage",
    "playback_ticks": 6400,
    "playback_time": 50.0,
}
FakeParser.demos["match128.dem"] = m_tr
demo128 = core.load_demo("match128.dem")
check("demo.tickrate == 128", demo128.tickrate == 128)
sp128 = float(
    demo128.ticks[(demo128.ticks["steamid"] == S1) & (demo128.ticks["tick"] == 120)][
        "speed"
    ].iloc[0]
)
check(
    "speed scales with tickrate (4u/tick -> 512 u/s)", abs(sp128 - 512.0) < 1e-6, sp128
)
d128 = core.build_duels(demo128, 3.0)
check("duel found at 128 tick", len(d128) == 1)
check(
    "duration in seconds scales (21 ticks / 128)",
    abs(d128.iloc[0]["duration_s"] - round(21 / 128, 2)) < 1e-9,
    d128.iloc[0]["duration_s"],
)
check("default fake demo stays 64", demo.tickrate == 64)

dftl, _ = core.timeline_data(
    "tl.dem", S1, 512, before_s=3.0, after_s=0.25, tickrate=128
)
check(
    "timeline window in ticks scales (first row 490)",
    len(dftl) and int(dftl.iloc[0]["tick"]) == 490,
    dftl["tick"].tolist()[:3],
)
sp_tl = dftl[(dftl["tick"] > 490) & (dftl["tick"] < 512)]["speed"].dropna()
check("timeline speed at 128 tick = 512", abs(sp_tl.max() - 512.0) < 1e-6, sp_tl.max())

# --------------------------------------------------- halftime side swap
print("== per-round sides (halftime swap) ==")
FakeParser.demos["sides.dem"] = make_match_sides()
demo_s = core.load_demo("sides.dem")
check(
    "sides round 1",
    demo_s.sides.get(1) == {S1: "T", S2: "T", S3: "CT", S4: "CT"},
    demo_s.sides,
)
check(
    "sides round 2 (swapped)",
    demo_s.sides.get(2) == {S1: "CT", S2: "CT", S3: "T", S4: "T"},
    demo_s.sides,
)
shots_s = core.shots_table(demo_s)
s1s = shots_s[shots_s["sid"] == S1].iloc[0]
s3s = shots_s[shots_s["sid"] == S3].sort_values("tick").iloc[-1]
check("S1 round-1 shot side = T (not the static CT)", s1s["side"] == "T", s1s["side"])
check("S3 round-2 shot side = T (swapped)", s3s["side"] == "T", s3s["side"])
duels_s = core.build_duels(demo_s, 3.0)
check("two duels", len(duels_s) == 2, len(duels_s))
d1 = duels_s[duels_s["round"] == 1].iloc[0]
d2 = duels_s[duels_s["round"] == 2].iloc[0]
check(
    "duel 1 sides: T killed CT",
    d1["killer_side"] == "T" and d1["victim_side"] == "CT",
    (d1["killer_side"], d1["victim_side"]),
)
check(
    "duel 2 sides: T killed CT after swap",
    d2["killer_side"] == "T" and d2["victim_side"] == "CT",
    (d2["killer_side"], d2["victim_side"]),
)
mine_s = core.duels_for(duels_s, S1)
check(
    "duels_for my_side follows rounds",
    list(mine_s["my_side"]) == ["T", "CT"],
    list(mine_s["my_side"]),
)
summ_s = core.player_summary(demo_s, duels_s, shots_s, 40)
a_row = summ_s[summ_s["sid"] == S1].iloc[0]
check("summary sides T→CT", a_row["sides"] == "T→CT", a_row["sides"])
chk_s = {c["id"]: c for c in core.data_checks(demo_s, shots_s, duels_s)}
check(
    "sides check ok when data present", chk_s["sides"]["level"] == "ok", chk_s["sides"]
)

# team_num unavailable -> static fallback + warn check
m_s2 = make_match_sides()
m_s2["unsupported"] = {"team_num"}
FakeParser.demos["sides2.dem"] = m_s2
demo_s2 = core.load_demo("sides2.dem")
check("no team_num -> sides empty", demo_s2.sides == {})
shots_s2 = core.shots_table(demo_s2)
s1s2 = shots_s2[shots_s2["sid"] == S1].sort_values("tick").iloc[0]
check(
    "fallback to static team (S1 end-of-match CT, per-round would be T)",
    s1s2["side"] == "CT",
    s1s2["side"],
)
chk_s2 = {
    c["id"]: c
    for c in core.data_checks(demo_s2, shots_s2, core.build_duels(demo_s2, 3.0))
}
check(
    "sides check warns when missing",
    chk_s2["sides"]["level"] == "warn",
    chk_s2["sides"],
)

# ------------------------------------------------- stats: wilson + ranks
print("== wilson_ci ==")
lo, hi = core.wilson_ci(0, 10)
check("wilson 0/10 lo=0", abs(lo - 0.0) < 1e-9, lo)
check("wilson 0/10 hi=27.75", abs(hi - 27.7540) < 0.01, hi)
lo, hi = core.wilson_ci(5, 10)
check("wilson 5/10", abs(lo - 23.6590) < 0.01 and abs(hi - 76.3410) < 0.01, (lo, hi))
lo, hi = core.wilson_ci(10, 10)
check("wilson 10/10", abs(lo - 72.2460) < 0.01 and hi == 100.0, (lo, hi))
lo, hi = core.wilson_ci(62, 100)
check("wilson 62/100", abs(lo - 52.2096) < 0.01 and abs(hi - 70.9026) < 0.01, (lo, hi))
lo, hi = core.wilson_ci(3, 8)
check(
    "wilson 3/8 wide", abs(lo - 13.6842) < 0.01 and abs(hi - 69.4262) < 0.01, (lo, hi)
)
check("wilson n=0 -> nan", all(pd.isna(x) for x in core.wilson_ci(0, 0)))
check("wilson nan-safe", all(pd.isna(x) for x in core.wilson_ci(np.nan, 5)))
for k, n in ((0, 10), (5, 10), (62, 100), (99, 100)):
    lo, hi = core.wilson_ci(k, n)
    check(
        f"wilson covers point {k}/{n}",
        lo - 1e-9 <= k / n * 100 <= hi + 1e-9,
        (lo, hi),
    )

print("== rank_in_lobby ==")
rdf = pd.DataFrame(
    {
        "sid": ["p1", "p2", "p3", "p4"],
        "accuracy": [50.0, 33.3, np.nan, 20.0],
        "risky": [10.0, 30.0, 5.0, np.nan],
    }
)
check("rank high best", core.rank_in_lobby(rdf, "p1", "accuracy") == (1, 3))
check("rank 2nd", core.rank_in_lobby(rdf, "p2", "accuracy") == (2, 3))
check("rank skips NaN players", core.rank_in_lobby(rdf, "p4", "accuracy") == (3, 3))
check("rank low best", core.rank_in_lobby(rdf, "p3", "risky", "low") == (1, 3))
check("rank low best 2nd", core.rank_in_lobby(rdf, "p2", "risky", "low") == (3, 3))
check("rank unknown player -> None", core.rank_in_lobby(rdf, "px", "accuracy") is None)
check("rank NaN player -> None", core.rank_in_lobby(rdf, "p3", "accuracy") is None)
check(
    "rank single player -> None",
    core.rank_in_lobby(rdf.iloc[:1], "p1", "accuracy") is None,
)
check("rank missing col -> None", core.rank_in_lobby(rdf, "p1", "nope") is None)

# ------------------------------------------------- weapon classes / filters
print("== weapon_class ==")
check("ak47 -> rifle", core.weapon_class("weapon_ak47") == "rifle")
check("m4a1_silencer -> rifle", core.weapon_class("m4a1_silencer") == "rifle")
check("awp -> sniper", core.weapon_class("awp") == "sniper")
check("deagle -> pistol", core.weapon_class("weapon_deagle") == "pistol")
check("negev -> lmg", core.weapon_class("negev") == "lmg")
check("mag7 -> shotgun", core.weapon_class("mag7") == "shotgun")
check("mp5sd -> smg", core.weapon_class("mp5sd") == "smg")
check("unknown -> other", core.weapon_class("something") == "other")
check(
    "classes cover the backlog list",
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
    },
)

print("== apply_filters ==")
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
check(
    "no filter: shots unchanged",
    len(s0) == 5 and list(s0["weapon"]) == list(fs_shots["weapon"]),
)
check("no filter: duels unchanged", len(d0) == 2)
check("no filter: hurts unchanged", len(h0) == 1)
check("no filter: copies", s0 is not fs_shots)

# weapons
s1, d1, h1 = core.apply_filters(
    fs_shots, fs_duels, fs_hurts, core.Filters(weapons={"ak47"})
)
check("weapons filter shots", list(s1["weapon"]) == ["ak47", "ak47"])
check("weapons filter duels", list(d1["weapon"]) == ["ak47"])
check("weapons filter hurts", list(h1["weapon"]) == ["ak47"])

# classes
s2, d2, _ = core.apply_filters(
    fs_shots, fs_duels, fs_hurts, core.Filters(classes={"sniper", "pistol"})
)
check("classes filter shots", list(s2["weapon"]) == ["awp", "deagle"])
check("classes filter duels", list(d2["weapon"]) == ["awp"])

# weapons + classes = union
s3, _, _ = core.apply_filters(
    fs_shots, fs_duels, None, core.Filters(weapons={"awp"}, classes={"rifle"})
)
check("weapons+classes union", list(s3["weapon"]) == ["ak47", "awp", "ak47", "m4a1"])

# rounds
s4, d4, _ = core.apply_filters(fs_shots, fs_duels, None, core.Filters(rounds=(2, 3)))
check("rounds filter shots", list(s4["round"]) == [2, 2, 3])
check("rounds filter duels", list(d4["round"]) == [2])

# side (shots use 'side', raw duels either role)
s5, d5, _ = core.apply_filters(fs_shots, fs_duels, None, core.Filters(side="T"))
check("side filter shots", list(s5["side"]) == ["T", "T", "T"])
check(
    "side filter raw duels (either role)",
    list(d5["round"]) == [1, 2],  # r1: killer T; r2: victim T
    d5,
)

# dist (shots use 'dist', duels 'distance'; NaN dropped)
s6, d6, _ = core.apply_filters(
    fs_shots, fs_duels, None, core.Filters(dist=(500.0, 1500.0))
)
check("dist filter shots", list(s6["dist"]) == [700.0, 1200.0])
check("dist filter duels", list(d6["distance"]) == [800.0])

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
check("results filter", list(d7["result"]) == ["kill"])
_, d8, _ = core.apply_filters(fs_shots, fs_mine, None, core.Filters(side="CT"))
check("side filter uses my_side", list(d8["my_side"]) == ["CT"])

# combined + empty result frames stay frames
s9, d9, h9 = core.apply_filters(
    fs_shots, fs_duels, fs_hurts, core.Filters(weapons={"deagle"}, rounds=(9, 10))
)
check("combined empty shots", len(s9) == 0)
check("combined empty duels", len(d9) == 0)
check("combined empty hurts", len(h9) == 0)

print("== round presets ==")
check("bounds all n=30", core.round_bounds(30, "all") == (1, 30))
check("bounds first n=30", core.round_bounds(30, "first") == (1, 12))
check("bounds second n=30", core.round_bounds(30, "second") == (13, 24))
check("bounds ot n=30", core.round_bounds(30, "ot") == (25, 30))
check("bounds all n=8", core.round_bounds(8, "all") == (1, 8))
check("bounds first clamped n=8", core.round_bounds(8, "first") == (1, 8))
check(
    "bounds second beyond match -> empty-ish (lo>=hi)",
    core.round_bounds(8, "second") == (13, 13),
    core.round_bounds(8, "second"),
)
check("bounds unknown preset -> all", core.round_bounds(8, "bogus") == (1, 8))
check("pistols n=26 -> {1,13,25}", core.pistol_rounds(26) == {1, 13, 25})
check("pistols n=10 -> {1}", core.pistol_rounds(10) == {1})
_, dp, _ = core.apply_filters(
    fs_shots, fs_duels, None, core.Filters(rounds_set={1, 13})
)
# fs_duels has rounds 1,2 -> only round 1 survives
check("rounds_set filter duels", list(dp["round"]) == [1])
dsp, _, _ = core.apply_filters(
    pd.DataFrame({"round": [1, 2, 13, 14]}),
    pd.DataFrame(),
    None,
    core.Filters(rounds_set={1, 13}),
)
check("rounds_set filter frames", list(dsp["round"]) == [1, 13])
