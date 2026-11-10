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


def make_match_sides():
    """
    4 players, 2 rounds with a halftime swap.

    Round 1: S1,S2 = T (team_num 2); S3,S4 = CT (3). Round 2: swapped.
    Static player_info keeps the END-of-match teams (S1,S2 = CT), which is
    exactly the trap the per-round side logic must fix.
    """

    player_info = pd.DataFrame(
        {
            "steamid": [int(s) for s in IDS],
            "name": ["Alpha", "Bravo", "Charlie", "Delta"],
            "team_number": [3, 3, 2, 2],  # end of match: swapped
        }
    )
    events = {
        "weapon_fire": pd.DataFrame(
            {
                "tick": [100, 110, 300],
                "user_steamid": pd.array([int(S1), int(S3), int(S3)], dtype="uint64"),
                "user_name": ["Alpha", "Charlie", "Charlie"],
                "weapon": ["weapon_ak47"] * 3,
                "total_rounds_played": [0, 0, 1],
                "is_warmup_period": [False, False, False],
            }
        ),
        "player_hurt": pd.DataFrame(),
        "player_death": pd.DataFrame(
            {
                "tick": [120, 310],
                "attacker_steamid": pd.array([int(S1), int(S3)], dtype="float64"),
                "attacker_name": ["Alpha", "Charlie"],
                "user_steamid": pd.array([int(S3), int(S1)], dtype="float64"),
                "user_name": ["Charlie", "Alpha"],
                "weapon": ["weapon_ak47", "weapon_ak47"],
                "headshot": [False, True],
                "distance": [500, 500],
                "total_rounds_played": [0, 1],
                "is_warmup_period": [False, False],
            }
        ),
    }
    # team_num per (tick, sid): round 1 (ticks ~100) and round 2 (ticks ~300)
    tn = {}
    rows = []
    req = sorted({99, 100, 109, 110, 119, 120, 299, 300, 309, 310})
    pos = {S1: (0.0, 0.0), S2: (10.0, 10.0), S3: (500.0, 0.0), S4: (510.0, 10.0)}
    yaw = {S1: 0.0, S2: 0.0, S3: 180.0, S4: 180.0}
    for tick in req:
        for sid in IDS:
            first_half = tick < 200
            tn[(tick, sid)] = (
                (2 if sid in (S1, S2) else 3)
                if first_half
                else (3 if sid in (S1, S2) else 2)
            )
            rows.append(
                {
                    "tick": tick,
                    "steamid": f64_sid(sid),
                    "name": {S1: "Alpha", S2: "Bravo", S3: "Charlie", S4: "Delta"}[sid],
                    "X": pos[sid][0],
                    "Y": pos[sid][1],
                    "Z": 0.0,
                    "pitch": 0.0,
                    "yaw": yaw[sid],
                    "is_alive": True,
                    "aim_punch_angle": "0 0 0",
                    "team_num": tn[(tick, sid)],
                }
            )
    return {
        "player_info": player_info,
        "header": {"map_name": "de_mirage"},
        "events": events,
        "ticks": pd.DataFrame(rows),
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

# ------------------------------------------- crouch-aware err + head widths
print("== crouch-aware angle_err / head_ang ==")
from types import SimpleNamespace


def row(x, y, z, pitch=0.0, yaw=0.0, duck=None, punch=None):
    r = SimpleNamespace(X=x, Y=y, Z=z, pitch=pitch, yaw=yaw)
    if duck is not None:
        r.duck_amount = duck
    if punch is not None:
        r.punch_pitch, r.punch_yaw = punch
    return r


check("duck missing -> 0", core._duck_amount(row(0, 0, 0)) == 0.0)
check("duck 0.5", core._duck_amount(row(0, 0, 0, duck=0.5)) == 0.5)
check("duck clamped hi", core._duck_amount(row(0, 0, 0, duck=1.7)) == 1.0)
check("duck clamped lo", core._duck_amount(row(0, 0, 0, duck=-0.2)) == 0.0)
check("duck nan -> 0", core._duck_amount(row(0, 0, 0, duck=np.nan)) == 0.0)

shooter = row(0, 0, 0)
target_stand = row(1000, 0, 0)
err_ss = core.angle_err(shooter, target_stand)  # head 2u below eyes
check(
    "stand/stand err ~ 0.11 deg",
    abs(err_ss - np.degrees(np.arctan(2 / 1000))) < 1e-9,
    err_ss,
)
target_duck = row(1000, 0, 0, duck=1.0)
err_sd = core.angle_err(shooter, target_duck)  # head 20u below eyes
check(
    "target crouched: err ~ 1.15 deg",
    abs(err_sd - np.degrees(np.arctan(20 / 1000))) < 1e-9,
    err_sd,
)
shooter_duck = row(0, 0, 0, duck=1.0)
err_ds = core.angle_err(shooter_duck, target_stand)  # head 16u ABOVE crouched eyes
check(
    "shooter crouched: err ~ 0.92 deg",
    abs(err_ds - np.degrees(np.arctan(16 / 1000))) < 1e-9,
    err_ds,
)
err_dd = core.angle_err(shooter_duck, target_duck)
check("both crouched ~ stand/stand", abs(err_dd - err_ss) < 1e-9, err_dd)

ha = core.head_ang(1000.0)
check("head_ang 1000u ~ 1.031 deg", abs(ha - 1.0313) < 1e-3, ha)
check(
    "head_ang 2000 ≈ half of 1000 (small-angle)",
    abs(core.head_ang(2000.0) - ha / 2) < 1e-3,
)
check("head_ang nan", pd.isna(core.head_ang(np.nan)))
check("head_ang 0 -> nan", pd.isna(core.head_ang(0.0)))
ha_arr = core.head_ang(pd.Series([500.0, 1000.0, np.nan]))
check(
    "head_ang vector",
    abs(ha_arr[0] - 2 * ha) < 1e-3
    and abs(ha_arr[1] - ha) < 1e-9
    and pd.isna(ha_arr[2]),
    ha_arr,
)
# duel-level: killer_err ~5 deg (punch) at dist 800, head_ang(800)=1.289
kerr = duels.iloc[0]["killer_err"]
check(
    "killer would_head False (punch pushes out)",
    not bool(duels.iloc[0]["killer_would_head"]),
)
check(
    "victim would_head True (aimed straight)", bool(duels.iloc[0]["victim_would_head"])
)
check(
    "killer err_hw ~ err/1.289 (3..5)",
    3.0 < duels.iloc[0]["killer_err_hw"] < 5.0,
    duels.iloc[0]["killer_err_hw"],
)
check(
    "victim err_hw <= 0.5",
    duels.iloc[0]["victim_err_hw"] <= 0.5,
    duels.iloc[0]["victim_err_hw"],
)

# duck_amount column flows through load_demo
m_d = make_match()
m_d["ticks"] = m_d["ticks"].assign(duck_amount=0.0)
m_d["ticks"].loc[m_d["ticks"]["steamid"] == f64_sid(S3), "duck_amount"] = 1.0
FakeParser.demos["duck.dem"] = m_d
demo_d = core.load_demo("duck.dem")
check("duck_amount parsed", "duck_amount" in demo_d.ticks.columns)
duels_d = core.build_duels(demo_d, 3.0)
check(
    "crouched shooter raises victim-side err (S3 ducks, S1 stands)",
    duels_d.iloc[0]["victim_err"] > duels.iloc[0]["victim_err"],
    (duels_d.iloc[0]["victim_err"], duels.iloc[0]["victim_err"]),
)
m_d2 = make_match()
m_d2["unsupported"] = {"duck_amount"}
FakeParser.demos["duck2.dem"] = m_d2
demo_d2 = core.load_demo("duck2.dem")
check(
    "duck unsupported: ticks parsed anyway",
    len(demo_d2.ticks) == 22,
    len(demo_d2.ticks),
)
check("duck unsupported: no duck col", "duck_amount" not in demo_d2.ticks.columns)
check("duck unsupported: punch still there", "punch_pitch" in demo_d2.ticks.columns)

# -------------------------------------------------- counter-strafe quality
print("== counter-strafe quality ==")
# unit: add_stop_quality
sq = pd.DataFrame(
    {
        "speed": [300.0, 30.0, 30.0, 0.0, np.nan, 30.0],
        "speed_prev": [250.0, 250.0, 10.0, 250.0, 250.0, np.nan],
    }
)
sq = core.add_stop_quality(sq, 40)
check(
    "stop classes",
    list(sq["stop"]) == ["moving", "stopped", "standing", "stopped", "", "standing"],
    list(sq["stop"]),
)

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
check(
    "fire-tick buttons parsed",
    "BACK" in demo_cs.ticks.columns,
    sorted(c for c in demo_cs.ticks.columns if c.isupper()),
)
shots_cs = core.add_stop_quality(core.shots_table(demo_cs), 40)
s1_cs = shots_cs[shots_cs["sid"] == S1].set_index("tick")
check("moving shot at 120", s1_cs.loc[120, "stop"] == "moving", s1_cs.loc[120, "stop"])
check(
    "stopped shot at 121 (prev 256 -> 32)",
    s1_cs.loc[121, "stop"] == "stopped",
    (s1_cs.loc[121, "speed"], s1_cs.loc[121, "speed_prev"], s1_cs.loc[121, "stop"]),
)
check("speed_prev wired", abs(s1_cs.loc[121, "speed_prev"] - 256.0) < 1e-6)
check(
    "counter-strafe tap with S against +X motion",
    bool(s1_cs.loc[121, "counter_strafe"]),
)
check(
    "no tap when moving (no opposing key)", not bool(s1_cs.loc[120, "counter_strafe"])
)
check("standing shots have no tap", not bool(s1_cs.loc[100, "counter_strafe"]))

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
check(
    "W held (supports motion) -> no tap",
    not bool(s1_cs2.loc[121, "counter_strafe"]),
)

# demo without button data: stop classes still work, tap always False
shots_nb = core.add_stop_quality(core.shots_table(demo), 40)
s1_nb = shots_nb[shots_nb["sid"] == S1].set_index("tick")
check("no buttons: stop classes ok", s1_nb.loc[120, "stop"] == "moving")
check("no buttons: tap False", not bool(s1_nb.loc[120, "counter_strafe"]))

# ------------------------------------- duel detection by crosshair proximity
print("== duel shots by crosshair proximity ==")


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
            "distance": [800],
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
check("one duel", len(duels_a) == 1)
da = duels_a.iloc[0]
check(
    "shots aimed at the opponent count (100,160)",
    da["killer_shots"] == 2,
    (da["killer_shots"], da["killer_shots_raw"]),
)
check("raw counts keep everything", da["killer_shots_raw"] == 3)
check(
    "no-data shot kept for the victim",
    da["victim_shots"] == 2 and da["victim_shots_raw"] == 2,
    (da["victim_shots"], da["victim_shots_raw"]),
)
check("first from filtered shots", da["first"] == "killer")
check(
    "low_conf when a shot went at another enemy", bool(da["low_conf"]), da["low_conf"]
)
check(
    "first tick = first AIMED shot",
    da["killer_first_tick"] == 100,
    da["killer_first_tick"],
)

# S4 far away from the stray shot direction -> clean duel, no flag
FakeParser.demos["aim2.dem"] = make_match_aim(s4_pos=(400.0, -400.0))
demo_a2 = core.load_demo("aim2.dem")
duels_a2 = core.build_duels(demo_a2, 3.0)
check(
    "stray shot still excluded from duel shots",
    duels_a2.iloc[0]["killer_shots"] == 2,
)
check("no second target -> low_conf False", not bool(duels_a2.iloc[0]["low_conf"]))

# the plain synthetic match: every shot is aimed at the opponent -> unchanged
check(
    "plain match duel unchanged",
    duels.iloc[0]["killer_shots"] == 3
    and duels.iloc[0]["killer_shots_raw"] == 3
    and not bool(duels.iloc[0]["low_conf"]),
    duels.iloc[0][["killer_shots", "killer_shots_raw", "low_conf"]].to_dict(),
)

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

print("== corrupt archives ==")
_cd = tempfile.mkdtemp()
_parts = lambda: {
    f
    for f in os.listdir(tempfile.gettempdir())
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
    try:
        core.decompressed_demo(_p)
        check(f"{_name}: raises", False)
    except ValueError:
        check(f"{_name}: ValueError (the app catches it)", True)
    except Exception as _e:  # noqa: BLE001
        check(f"{_name}: ValueError", False, f"got {type(_e).__name__}")
check(
    "no new .part leftovers after failed unpack",
    not (_parts() - _parts_before),
    str(sorted(_parts() - _parts_before)[:2]),
)

print(f"\n{PASS} passed, {FAIL} failed")
if __name__ == "__main__":
    sys.exit(1 if FAIL else 0)
