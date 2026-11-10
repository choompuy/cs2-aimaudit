"""
Synthetic tests for CS2-AimAudit core.py with a fake demoparser2.

Builds a tiny fake match (4 players, warmup shots, a duel, a teamkill,
a lonely shot) and verifies every core metric without a real .dem file.
Run from the repo root: python tests/test_core.py
"""

import bz2
import gzip
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


# ---------------------------------------------------------------- fakes
S1 = "76561111111111111"  # A, T
S2 = "76561111111111112"  # B, T
S3 = "76561111111111113"  # C, CT
S4 = "76561111111111114"  # D, CT
IDS = [S1, S2, S3, S4]


def f64_sid(s):
    """Simulate the polars->pandas float64 conversion quirk (rounds >2^53)."""
    return float(int(s))


def make_match():
    player_info = pd.DataFrame(
        {
            "steamid": [int(s) for s in IDS],
            "name": ["Alpha", "Bravo", "Charlie", "Delta"],
            "team_number": [2, 2, 3, 3],
        }
    )
    events = {
        "weapon_fire": pd.DataFrame(
            {
                "tick": [10, 20, 100, 110, 120, 115, 118, 200],
                "user_steamid": pd.array(
                    [int(S1)] * 5 + [int(S3)] * 2 + [int(S4)], dtype="uint64"
                ),
                "user_name": ["Alpha"] * 5 + ["Charlie"] * 2 + ["Delta"],
                "weapon": ["weapon_ak47"] * 8,
                "total_rounds_played": [0, 0, 0, 0, 0, 0, 0, 1],
                "is_warmup_period": [
                    True,
                    True,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                ],
            }
        ),
        "player_hurt": pd.DataFrame(
            {
                # hurt at 116 = damage from S3's shot at 115 (tick tolerance);
                # hurt at 121 = S4's shot... which does not exist (S4 fires at
                # 200 only) -> must NOT be attributed to anyone.
                "tick": [15, 120, 121, 116],
                "attacker_steamid": pd.array(
                    [int(S1), int(S1), int(S4), int(S3)], dtype="float64"
                ),  # float64 + nulls quirk
                "attacker_name": ["Alpha", "Alpha", "Delta", "Charlie"],
                "user_steamid": pd.array(
                    [int(S2), int(S3), int(S1), int(S1)], dtype="float64"
                ),
                "user_name": ["Bravo", "Charlie", "Alpha", "Alpha"],
                "weapon": ["weapon_ak47"] * 4,
                "hitgroup": [1, 1, 2, 2],
                "total_rounds_played": [0, 0, 0, 0],
                "is_warmup_period": [True, False, False, False],
            }
        ),
        "player_death": pd.DataFrame(
            {
                "tick": [18, 121],
                "attacker_steamid": pd.array([int(S2), int(S1)], dtype="float64"),
                "attacker_name": ["Bravo", "Alpha"],
                "user_steamid": pd.array([int(S4), int(S3)], dtype="float64"),
                "user_name": ["Delta", "Charlie"],
                "weapon": ["weapon_ak47", "weapon_ak47"],
                "headshot": [False, True],
                "distance": [700, 800],
                "total_rounds_played": [0, 0],
                "is_warmup_period": [True, False],
            }
        ),
    }
    # Rows exist at every fire tick AND tick-1 (core requests both). S1 and S3
    # have rows on all such ticks of rounds 1; S4 only around its own shot
    # (simulates the others being "dormant" there -> lonely shot dist NaN).
    # Movement script (speed = |dpos| between t-1 and t, *64):
    #   S1: standing (0) everywhere, but 4 units on the last tick -> 256 u/s
    #       at its shot at 120 ("far fast -> risky").
    #   S3: teleports 300 -> 1200 between 114 and 115 -> 57600 u/s -> must be
    #       NaN (MAX_SPEED cap), then stands still.
    #   S4: 3 units between 199 and 200 -> 192 u/s.
    req = sorted({99, 100, 109, 110, 114, 115, 117, 118, 119, 120, 199, 200})
    pos = {}
    for t in req:
        if t < 199:
            pos[(t, S1)] = (4.0 if t == 120 else 0.0, 0.0, 0.0)
            pos[(t, S3)] = (300.0 if t <= 114 else 1200.0, 0.0, 0.0)
    pos[(199, S4)] = (5000.0, 0.0, 0.0)
    pos[(200, S4)] = (5000.0, 3.0, 0.0)
    yaw = {S1: 0.0, S3: 180.0, S4: 0.0}  # S1 looks +X, S3 looks -X
    punch = {(100, S1): "5.0 0.0 0.0"}
    rows = []
    for (t, sid), (x, y, z) in sorted(pos.items()):
        rows.append(
            {
                "tick": t,
                "steamid": f64_sid(sid),
                "name": {S1: "Alpha", S3: "Charlie", S4: "Delta"}[sid],
                "X": x,
                "Y": y,
                "Z": z,
                "pitch": 0.0,
                "yaw": yaw[sid],
                "is_alive": True,
                "aim_punch_angle": punch.get((t, sid), "0 0 0"),
            }
        )
    ticks = pd.DataFrame(rows)
    return {
        "player_info": player_info,
        "header": {"map_name": "de_dust2", "server_name": "test"},
        "events": events,
        "ticks": ticks,
        "unsupported": set(),
    }


class FakeParser:
    demos: dict = {}
    # Source IN_* bits for the named button props (mirrors real parser)
    NAMED_BITS = {
        "FORWARD": 1 << 3,
        "LEFT": 1 << 9,
        "BACK": 1 << 4,
        "RIGHT": 1 << 10,
        "WALK": 1 << 17,
        "RIGHTCLICK": 1 << 11,
        "FIRE": 1 << 0,
        "RELOAD": 1 << 13,
    }

    def __init__(self, path):
        self.path = path
        self.d = FakeParser.demos[path]

    def parse_player_info(self):
        return self.d["player_info"].copy()

    def parse_header(self):
        return dict(self.d["header"])

    def parse_events(self, names, player=None, other=None):
        return [(n, self.d["events"].get(n, pd.DataFrame()).copy()) for n in names]

    def parse_ticks(self, wanted_props, ticks=None, players=None):
        bad = [p for p in wanted_props if p in self.d.get("unsupported", set())]
        if bad:
            raise RuntimeError(f"unknown props: {bad}")
        df = self.d["ticks"].copy()
        if ticks is not None:
            df = df[df["tick"].isin(set(int(t) for t in ticks))]
        if players is not None:
            ok = {float(int(p)) for p in players}
            df = df[df["steamid"].isin(ok)]
        keep = ["tick", "steamid", "name"]
        for p in wanted_props:
            for c in df.columns:
                if c == p or (
                    p == "aim_punch_angle" and c.startswith("aim_punch_angle_")
                ):
                    keep.append(c)
        out = df[[c for c in dict.fromkeys(keep) if c in df.columns]].copy()
        if "buttons" in wanted_props and "buttons_bit" in self.d:
            m = list(zip(out["tick"], out["steamid"]))
            bits = [self.d["buttons_bit"].get(k, 0) for k in m]
            out["buttons"] = bits
            # real parser derives named button props from the same bitfield
            for nm, bit in core.BTN_BITS.items():
                out[nm] = [(b & bit) != 0 for b in bits]
            for nm, bit in FakeParser.NAMED_BITS.items():
                if nm in wanted_props:
                    out[nm] = [(b & bit) != 0 for b in bits]
        return out.reset_index(drop=True)


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
core.DemoParser = FakeParser  # monkeypatch
demo = core.load_demo("match.dem")
check("warmup fires dropped", len(demo.fires) == 6, f"got {len(demo.fires)}")
check(
    "warmup_dropped counted", demo.warmup_dropped.get("fires") == 2, demo.warmup_dropped
)
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

# ---------------------------------------------- fix_ids: the Heylaurens bug
print("== fix_ids: rounded floats in non-float column ==")
# Only sids that are multiples of 16 survive float64; a column of rounded
# floats in a NON-float dtype used to become Int64->str and lose everyone
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
check("warmup in report", rr["counts"]["warmup_dropped"]["fires"] == 2)
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
check("plain .dem returned as is", core.decompressed_demo(plain) == plain)

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

# ------------------------------------------------------------- app state
print("== app state (recent demos) ==")
with tempfile.TemporaryDirectory(prefix="cs2aa_state_") as td:
    core.STATE_PATH = os.path.join(td, "state.json")
    check("missing state file -> {}", core.load_state() == {})
    st_ = core.remember_demo("/a/b.dem", {})
    st_ = core.remember_demo("/c/d.dem", st_)
    st_ = core.remember_demo("/a/b.dem", st_)  # dedupe + move to front
    check("recent order + dedupe", st_["recent_demos"] == ["/a/b.dem", "/c/d.dem"], st_)
    core.save_state(st_)
    check("state round-trip", core.load_state() == st_)
    st2 = {}
    for i in range(15):
        st2 = core.remember_demo(f"/x/{i}.dem", st2)
    check(
        "recent capped",
        len(st2["recent_demos"]) == core.RECENT_MAX
        and st2["recent_demos"][0] == "/x/14.dem",
        st2["recent_demos"],
    )
    with open(core.STATE_PATH, "w", encoding="utf-8") as f:
        f.write("{ not json")
    check("corrupt state -> {}", core.load_state() == {})
    core.STATE_PATH = os.path.join(td, "ro", "sub", "state.json")
    os.chmod(td, 0o500)  # read-only parent: save must stay silent
    core.save_state({"x": 1})
    check("read-only home -> silent", True)
    os.chmod(td, 0o700)

# ------------------------------------------------------------ no_events
print("== no player_hurt event at all ==")
m8 = make_match()
m8["events"]["player_hurt"] = pd.DataFrame()
FakeParser.demos["nohurt.dem"] = m8
demo8 = core.load_demo("nohurt.dem")
sh8 = core.add_risk(core.shots_table(demo8), 40)
d8 = core.build_duels(demo8, 3.0)
check("no hurts -> load ok", len(demo8.fires) == 6)
check("no hurts -> hit False", not sh8["hit"].any())
check("no hurts -> summary ok", len(core.player_summary(demo8, d8, sh8, 40)) == 4)
check("no hurts -> hitgroup empty", len(core.hitgroup_counts(demo8, S1)) == 0)
check("no hurts -> weapon stats ok", len(core.weapon_stats(demo8, sh8, S1, 40)) == 1)

print("== no_events ==")
m7 = make_match()
m7["events"]["weapon_fire"] = m7["events"]["weapon_fire"].iloc[:2]  # warmup only
FakeParser.demos["warmup_only.dem"] = m7
try:
    core.load_demo("warmup_only.dem")
    check("raises no_events", False)
except ValueError as e:
    check("raises no_events", "no_events" in str(e))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
