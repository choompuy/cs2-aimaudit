"""allowed_speed, load_demo, shots_table, build_duels, summary, shot/duel context (old + unsupported demos).

Split out of tests/test_core.py; runs at import in
the runner's order - see core_sections/__init__.py.
"""

# --- shared fixtures and helpers (see _shared.py) ---
from ._shared import (  # noqa: F401
    FakeParser,
    IDS,
    S1,
    S2,
    S3,
    S4,
    check,
    core,
    make_match,
    np,
    pd,
)
# ------------------------------------------------------- allowed_speed unit
print("== allowed_speed ==")
check("near -> full speed", core.allowed_speed(400, 40) == 250.0)
check("far -> thr", core.allowed_speed(2000, 40) == 40.0)
mid = core.allowed_speed(1000, 40)  # halfway: 40 + 0.5*(250-40) = 145
check("midpoint linear", abs(mid - 145.0) < 1e-9, f"got {mid}")
check("nan dist -> nan", np.isnan(core.allowed_speed(np.nan, 40)))
arr = core.allowed_speed([400.0, 1000.0, np.nan], 40)
check("vector input", arr[0] == 250.0 and abs(arr[1] - 145) < 1e-9 and np.isnan(arr[2]))

# ------------------------------------------------------------- load_demo
print("== load_demo (match) ==")
FakeParser.demos["match.dem"] = make_match()
# monkeypatch: DemoParser is used by core.loader (load_demo) and
# core.replay (timeline_data / replay_data); the facade copy is patched
# too for direct core.DemoParser(...) calls.
core.DemoParser = core.loader.DemoParser = core.replay.DemoParser = FakeParser
demo = core.load_demo("match.dem")
check("warmup fires dropped", len(demo.fires) == 6, f"got {len(demo.fires)}")
check("warmup excluded", demo.excluded.get("warmup") == 1, demo.excluded)
check("warmup death dropped", len(demo.deaths) == 1, f"got {len(demo.deaths)}")
check("non-warmup hurts kept (3 of 4)", len(demo.hurts) == 3, f"got {len(demo.hurts)}")
check("teamkill hurt filtered", (demo.hurts["user_steamid"] == S2).sum() == 0)
check("punch extracted", "punch_pitch" in demo.ticks.columns)
p = demo.ticks[(demo.ticks["steamid"] == S1) & (demo.ticks["tick"] == 100)]
check(
    "punch value parsed",
    len(p) == 1 and abs(float(p["punch_pitch"].iloc[0]) - 5.0) < 1e-9,
    p.get("punch_pitch"),
)
check("players resolved", set(demo.players["sid"]) == set(IDS))

# ------------------------------------------------------------ shots_table
print("== shots_table / risk ==")
shots = core.shots_table(demo)
s1 = shots[shots["sid"] == S1].sort_values("tick")
check("S1 has 3 shots", len(s1) == 3, f"got {len(s1)}")
d100 = float(s1[s1["tick"] == 100]["dist"].iloc[0])
d120 = float(s1[s1["tick"] == 120]["dist"].iloc[0])
check("dist tick100 = 300", abs(d100 - 300) < 1e-6, d100)
check("dist tick120 = 1196 (moved 4u, enemy at 1200)", abs(d120 - 1196.0) < 1e-6, d120)
check("hit flagged", bool(s1[s1["tick"] == 120]["hit"].iloc[0]) is True)
check("miss not flagged", bool(s1[s1["tick"] == 100]["hit"].iloc[0]) is False)
check(
    "old shot 10t before hurt not flagged",
    bool(s1[s1["tick"] == 110]["hit"].iloc[0]) is False,
)
s3 = shots[shots["sid"] == S3].sort_values("tick")
check(
    "hurt at t+1 matched (tolerance)",
    bool(s3[s3["tick"] == 115]["hit"].iloc[0]) is True,
)
check(
    "next shot after hurt not flagged",
    bool(s3[s3["tick"] == 118]["hit"].iloc[0]) is False,
)
s4 = shots[shots["sid"] == S4]
check("hurt without matching fire not counted", not bool(s4["hit"].iloc[0]))

# own speed: exact 1-tick position delta, capped on teleports
sp = lambda sid, t: float(
    demo.ticks[(demo.ticks["steamid"] == sid) & (demo.ticks["tick"] == t)][
        "speed"
    ].iloc[0]
)
check("speed standing = 0", abs(sp(S1, 100)) < 1e-9, sp(S1, 100))
check("speed moving = 256", abs(sp(S1, 120) - 256.0) < 1e-6, sp(S1, 120))
check("teleport capped to NaN", pd.isna(sp(S3, 115)), sp(S3, 115))
check("speed after teleport ok", abs(sp(S3, 118)) < 1e-9, sp(S3, 118))
check("speed S4 = 192", abs(sp(S4, 200) - 192.0) < 1e-6, sp(S4, 200))
check(
    "speed at shot col",
    abs(float(s1[s1["tick"] == 120]["speed"].iloc[0]) - 256.0) < 1e-6,
)
s4 = shots[shots["sid"] == S4]
check("lonely shot dist NaN", len(s4) == 1 and pd.isna(s4["dist"].iloc[0]))
shots = core.add_risk(shots, 40)
s1 = shots[shots["sid"] == S1].sort_values("tick")
s4 = shots[shots["sid"] == S4]
r = s1.set_index("tick")["risky"]
check("near slow -> not risky", not bool(r.loc[100]))
check("far fast -> risky", bool(r.loc[120]))
check(
    "allowed at 1196 = 103.8",
    abs(float(s1[s1["tick"] == 120]["allowed_speed"].iloc[0]) - 103.84) < 0.05,
)
check("lonely shot not risky", not bool(s4["risky"].iloc[0]))
check("round col present", (shots["round"] == 1).sum() == 5, shots["round"].tolist())

# ------------------------------------------------------------- build_duels
print("== build_duels ==")
duels = core.build_duels(demo, 3.0)
check("one duel", len(duels) == 1, f"got {len(duels)}")
d0 = duels.iloc[0]
check("killer/victim", d0["killer"] == S1 and d0["victim"] == S3)
check("killer shot first", d0["first"] == "killer")
check(
    "killer_shots=3 victim_shots=2", d0["killer_shots"] == 3 and d0["victim_shots"] == 2
)
check("duration", abs(d0["duration_s"] - round(21 / 64, 2)) < 1e-9, d0["duration_s"])
check("killer_err ~ punch (4..6 deg)", 4.0 < d0["killer_err"] < 6.0, d0["killer_err"])
check("victim_err < 1 deg", d0["victim_err"] < 1.0, d0["victim_err"])
check("distance passthrough", float(d0["distance"]) == 800.0)

# ---------------------------------------------------- duels_for / summary
print("== duels_for / player_summary ==")
mine = core.duels_for(duels, S1)
check("S1: one kill duel", len(mine) == 1 and mine.iloc[0]["result"] == "kill")
check("first mapped to me", mine.iloc[0]["first"] == "me")
summary = core.player_summary(demo, duels, shots, 40, 500, 1500)
a = summary[summary["sid"] == S1].iloc[0]
check("summary kills", a["kills"] == 1 and a["deaths"] == 0)
check(
    "summary shots", a["shots"] == 3 and a["hits"] == 1, f'got {a["shots"]}/{a["hits"]}'
)
check("summary accuracy", abs(a["accuracy"] - 33.3) < 0.05, a["accuracy"])
check("summary risky 33.3", abs(a["risky"] - 33.3) < 0.05, a["risky"])
w = core.weapon_stats(demo, shots, S1, 40, 500, 1500)
check("weapon_stats ak47", len(w) == 1 and w.iloc[0]["weapon"] == "ak47")
check(
    "weapon accuracy", abs(w.iloc[0]["accuracy"] - 33.3) < 0.05, w.iloc[0]["accuracy"]
)
hg = core.hitgroup_counts(demo, S1)
check("hitgroup head=1", hg.get("head", 0) == 1)

# ------------------------------------------------- shot/duel context
print("== shot/duel context ==")
s1c = s1.set_index("tick")
s3c = shots[shots["sid"] == S3].sort_values("tick").set_index("tick")
s4c = shots[shots["sid"] == S4].set_index("tick")
check(
    "zones passthrough",
    list(s1c["zone"]) == ["BombsiteA"] * 3
    and list(s3c["zone"]) == ["Middle"] * 2
    and s4c["zone"].iloc[0] == "Catwalk",
    s1c["zone"].tolist(),
)
check(
    "round_sec per shot",
    abs(s1c.loc[100, "round_sec"] - 35.3) < 1e-9
    and abs(s1c.loc[110, "round_sec"] - 35.5) < 1e-9
    and abs(s1c.loc[120, "round_sec"] - 35.9) < 1e-9
    and abs(s4c["round_sec"].iloc[0] - 44.0) < 1e-9,
    s1c["round_sec"].tolist(),
)
check(
    "flashed at 110 only",
    bool(s1c.loc[110, "flashed"]) is True
    and bool(s1c.loc[100, "flashed"]) is False
    and bool(s1c.loc[120, "flashed"]) is False
    and not bool(s3c["flashed"].any())
    and not bool(s4c["flashed"].iloc[0]),
    s1c["flashed"].tolist(),
)
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
check("flashed: the tail of the blindness is not a flashed shot", bool(_fade[110]) is False, _fade.tolist())
_mid = _flash_at_110({"tick": [110 - 64]})  # 1 s left
check("flashed: a second left is", bool(_mid[110]) is True and bool(_mid[100]) is False, _mid.tolist())
_gone = _flash_at_110({"tick": [110 - 170]})  # the fade (held by the server), long over
check("flashed: the fade after the blindness is not", bool(_gone[110]) is False, _gone.tolist())
_other = _flash_at_110({"blind_duration": [3.1]})
check(
    "flashed: a value without its own event is unknown, the rest stays known",
    pd.isna(_other[110]) and bool(_other[100]) is False,
    _other.tolist(),
)
_none = _flash_at_110(None)
check("flashed: no player_blind events at all -> unknown, never 'not flashed'", _none.isna().all(), _none.tolist())
check(
    "slowed at 120 (S1) and 115 (S3)",
    bool(s1c.loc[120, "slowed"]) is True
    and bool(s1c.loc[100, "slowed"]) is False
    and bool(s3c.loc[115, "slowed"]) is True
    and bool(s3c.loc[118, "slowed"]) is False
    and not bool(s4c["slowed"].iloc[0]),
    list(s1c["slowed"]) + list(s3c["slowed"]),
)
check(
    "flashed/slowed all known",
    shots["flashed"].notna().all() and shots["slowed"].notna().all(),
)
check(
    "duel context",
    abs(duels.iloc[0]["round_sec"] - 37.0) < 1e-9
    and duels.iloc[0]["killer_zone"] == "BombsiteA"
    and duels.iloc[0]["victim_zone"] == "Middle",
    duels.iloc[0][["round_sec", "killer_zone", "victim_zone"]].to_dict(),
)
check(
    "duels_for context",
    abs(mine.iloc[0]["round_sec"] - 37.0) < 1e-9
    and mine.iloc[0]["my_zone"] == "BombsiteA"
    and mine.iloc[0]["opp_zone"] == "Middle",
    mine.iloc[0][["round_sec", "my_zone", "opp_zone"]].to_dict(),
)


# --- old demo without the context fields: columns exist, values are
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
check(
    "no ctx: zone '' + round_sec NaN",
    (old_shots["zone"] == "").all() and old_shots["round_sec"].isna().all(),
    old_shots["zone"].head().tolist(),
)
check(
    "no ctx: flags NA (not False)",
    old_shots["flashed"].isna().all() and old_shots["slowed"].isna().all(),
    old_shots[["flashed", "slowed"]].head(2).to_dict(),
)
check(
    "no ctx: duel zones '' + round_sec NaN",
    (old_duels["killer_zone"] == "").all() and old_duels["round_sec"].isna().all(),
    old_duels[["round_sec", "killer_zone"]].to_dict(),
)

# --- parser that RAISES on the state props (unknown on old versions):
# the POS/buttons chain must survive, flags come out NA.
m_unsup = make_match()
m_unsup["unsupported"] = {"flash_duration", "velo_modifier"}
FakeParser.demos["unsup.dem"] = m_unsup
unsup_demo = core.load_demo("unsup.dem")
unsup_shots = core.shots_table(unsup_demo)
check(
    "unsupported state props: POS chain intact",
    {"X", "Y", "speed"} <= set(unsup_shots.columns)
    and unsup_shots["speed"].notna().any(),
)
check(
    "unsupported state props: flags NA",
    unsup_shots["flashed"].isna().all() and unsup_shots["slowed"].isna().all(),
    unsup_shots[["flashed", "slowed"]].head(2).to_dict(),
)
check(
    "unsupported state props: zones still present (events unaffected)",
    (unsup_shots["zone"] == "BombsiteA").any(),
    unsup_shots["zone"].head().tolist(),
)
