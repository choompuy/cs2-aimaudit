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
# Windows consoles default to legacy codepages (cp1252) and the output
# below prints → and ≈: force UTF-8 instead of crashing. CI sets
# PYTHONUTF8=1 as well - this covers a local run on a stock console.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
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
                # context: zone + round clock. Warmup rows get a
                # NEGATIVE round_second (before round start) - dropped with
                # the warmup rows anyway, but exercises the NaN clamp.
                "user_last_place_name": [
                    "TSpawn",
                    "TSpawn",
                    "BombsiteA",
                    "BombsiteA",
                    "BombsiteA",
                    "Middle",
                    "Middle",
                    "Catwalk",
                ],
                "round_start_time": [100.0] * 7 + [400.0],
                "game_time": [50.0, 55.0, 135.3, 135.5, 135.9, 136.2, 136.4, 444.0],
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
                "attacker_last_place_name": [
                    "TSpawn",
                    "BombsiteA",
                    "Catwalk",
                    "Middle",
                ],
                "user_last_place_name": ["TSpawn", "Middle", "Palace", "Palace"],
                "round_start_time": [100.0] * 4,
                "game_time": [60.0, 135.9, 136.5, 136.3],
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
                "attacker_last_place_name": ["TSpawn", "BombsiteA"],
                "user_last_place_name": ["Catwalk", "Middle"],
                "round_start_time": [100.0, 100.0],
                "game_time": [70.0, 137.0],
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
    # shooter state at the fire ticks: S1 is flashed (2.0s left) at
    # its 110 shot and damage-slowed (velo 0.4) at the 120 one; S3 answers
    # at 115 still slowed (0.35) from the hit it just took. Everything else
    # is the clean state (0.0 / 1.0).
    flash = {(110, S1): 2.0}
    velo = {(120, S1): 0.4, (115, S3): 0.35}
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
                "flash_duration": flash.get((t, sid), 0.0),
                "velo_modifier": velo.get((t, sid), 1.0),
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
        # usercmd mouse deltas: config {"mouse": {(tick, sid): (dx, dy)}};
        # without the config the fake returns no usercmd columns (an old
        # parser/demo) and the app must degrade to NA, never crash
        if "usercmd_mouse_dx" in wanted_props or "usercmd_mouse_dy" in wanted_props:
            mo = self.d.get("mouse")
            # name lookup from player_info (the module-level NAMES constant
            # is only defined further down the file)
            nm = {
                int(s): n
                for s, n in zip(
                    self.d["player_info"]["steamid"], self.d["player_info"]["name"]
                )
            }
            rows = []
            for t in ticks or []:
                for (mt, sid), (dx, dy) in (mo or {}).items():
                    if mt == int(t):
                        rows.append(
                            {
                                "tick": int(t),
                                "steamid": f64_sid(sid),
                                "name": nm.get(int(sid), "?"),
                                "usercmd_mouse_dx": dx,
                                "usercmd_mouse_dy": dy,
                            }
                        )
            return pd.DataFrame(
                rows,
                columns=[
                    "tick",
                    "steamid",
                    "name",
                    "usercmd_mouse_dx",
                    "usercmd_mouse_dy",
                ],
            )
        # scoreboard aggregates: the game's kills_total / deaths_total at
        # the requested tick(s). Config: {"totals": {sid: (name, k, d)}};
        # without it the "parser" knows nothing -> empty frame (the real
        # parser always returns the columns; both paths must be safe).
        if "kills_total" in wanted_props or "deaths_total" in wanted_props:
            tot = self.d.get("totals")
            rows = []
            for t in ticks or []:
                for sid, (nm, k, d) in (tot or {}).items():
                    rows.append(
                        {
                            "tick": int(t),
                            "steamid": f64_sid(sid),
                            "name": nm,
                            "kills_total": k,
                            "deaths_total": d,
                        }
                    )
            return pd.DataFrame(
                rows,
                columns=["tick", "steamid", "name", "kills_total", "deaths_total"],
            )
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

# ------------------------------------------------ first shots / mouse flick
print("== first shots / mouse flick ==")
# gap semantics (notes s.15): > ENGAGE_GAP_TICKS since the same sid's
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

# ---------------------------------- scoreboard cross-check (notes s.14)
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
    d = tempfile.gettempdir()
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

# ------------------------------------------------- input path normalization
print("== normalize_input_path ==")
check(
    "plain path unchanged",
    core.normalize_input_path("/a/b/match.dem") == "/a/b/match.dem",
)
check("None -> empty", core.normalize_input_path(None) == "")
check("empty -> empty", core.normalize_input_path("") == "")
check("whitespace stripped", core.normalize_input_path("  /a/b.dem \n") == "/a/b.dem")
check(
    "double quotes stripped (Explorer 'Copy as path')",
    core.normalize_input_path('"/a/b/match.dem"') == "/a/b/match.dem",
)
check(
    "single quotes stripped (shell drop)",
    core.normalize_input_path("'/a/b/match.dem'") == "/a/b/match.dem",
)
check(
    "nested quotes stripped",
    core.normalize_input_path("\"'/a/b.dem'\"") == "/a/b.dem",
)
check(
    "only leading quote stripped",
    core.normalize_input_path('"/a/b.dem') == "/a/b.dem",
)
check(
    "~ expands",
    core.normalize_input_path("~/x/match.dem")
    == os.path.join(os.path.expanduser("~"), "x", "match.dem"),
)
os.environ["CS2AA_TEST_DIR"] = "/env/demo/dir"
check(
    "%VAR% expands (Windows style, on any OS)",
    core.normalize_input_path("%CS2AA_TEST_DIR%/x.dem") == "/env/demo/dir/x.dem",
)
check(
    "unknown %VAR% kept as is",
    core.normalize_input_path("%CS2AA_NOPE%/x.dem") == "%CS2AA_NOPE%/x.dem",
)
with tempfile.TemporaryDirectory(prefix="cs2aa_path_") as td:
    fp = os.path.join(td, "match.dem")
    with open(fp, "wb") as f:
        f.write(b"x")
    check(
        "quoted real file passes isfile()",
        os.path.isfile(core.normalize_input_path(f'"{fp}"')),
    )
del os.environ["CS2AA_TEST_DIR"]

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

# ------------------------------------------- knife round / live match start
print("== knife round / live match start ==")

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
        return (tick, int(a), NAMES[a], int(u), NAMES[u], w, trp, warm, True, 500)

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
                "distance": 500,
            }
        ],
    ).astype({"attacker_steamid": "float64", "user_steamid": "float64"})


# 1) knife round + begin_new_match, counter reset by the restart (FACEIT)
FakeParser.demos["knife_reset.dem"] = make_live_match(500, knife=True, pistol_trp=0)
dk = core.load_demo("knife_reset.dem")
check(
    "knife_reset excluded",
    dk.excluded == {"warmup": 0, "knife_round": 1, "restart": 0},
    dk.excluded,
)
check("knife kill dropped", len(dk.deaths) == 2, f"got {len(dk.deaths)}")
check("knife_reset 6 gun fires", len(dk.fires) == 6, f"got {len(dk.fires)}")
sk = core.add_risk(core.shots_table(dk), 40)
check(
    "knife_reset rounds start at 1",
    sorted(sk["round"].unique()) == [1, 2],
    sorted(sk["round"].unique()),
)
check("knife_reset duels: 2 (no knife)", len(core.build_duels(dk, 3.0)) == 2)

# 2) knife round, no begin_new_match, counter kept counting
FakeParser.demos["knife_cont.dem"] = make_live_match(None, knife=True, pistol_trp=1)
dc = core.load_demo("knife_cont.dem")
check("knife_cont excluded", dc.excluded["knife_round"] == 1, dc.excluded)
check("knife_cont knife dropped", len(dc.deaths) == 2, f"got {len(dc.deaths)}")
sc = core.add_risk(core.shots_table(dc), 40)
check(
    "knife_cont pistol renumbered to 1",
    sorted(sc["round"].unique()) == [1, 2],
    sorted(sc["round"].unique()),
)

# 3) no knife round at all (Valve-like): nothing excluded
FakeParser.demos["noknife.dem"] = make_live_match(50, knife=False, pistol_trp=0)
dn = core.load_demo("noknife.dem")
check("noknife excluded empty", not any(dn.excluded.values()), dn.excluded)
sn = core.add_risk(core.shots_table(dn), 40)
check("noknife rounds 1-2", sorted(sn["round"].unique()) == [1, 2])

# 4) knife round + warmup round after it (the faceit2 shape)
FakeParser.demos["knife_warm.dem"] = make_live_match(
    500, knife=True, warmup_round=True, pistol_trp=0
)
dw = core.load_demo("knife_warm.dem")
check(
    "knife_warm excluded",
    dw.excluded == {"warmup": 1, "knife_round": 1, "restart": 0},
    dw.excluded,
)
check("knife_warm fires are live-only", len(dw.fires) == 6, f"got {len(dw.fires)}")

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
check(
    "mixed: knife/nade kills are not duels", len(dm_duels) == 2, f"got {len(dm_duels)}"
)
sm = core.player_summary(dm, dm_duels, core.add_risk(core.shots_table(dm), 40), 40)
check(
    "mixed: K/D counts knife+nade kills",
    sm[sm["sid"] == S3].iloc[0]["kills"] == 2,
    sm[sm["sid"] == S3].iloc[0]["kills"],
)
check(
    "mixed: deaths include knife/nade",
    sm[sm["sid"] == S1].iloc[0]["deaths"] == 1,
    sm[sm["sid"] == S1].iloc[0]["deaths"],
)

# scoreboard slack: a server that does NOT count knife/grenade kills in
# kills_total must still pass - ours may exceed game by exactly the
# player's non-gun kills (notes s.14; the three probed demos count them,
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
check(
    "scoreboard: non-gun slack absorbs uncounted knife/nade kills",
    ch["scoreboard"]["level"] == "ok" and ch["scoreboard"]["n"] == 0,
    ch["scoreboard"],
)
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
check(
    "scoreboard: exact match when the game counts knife/nade",
    ch["scoreboard"]["level"] == "ok",
    ch["scoreboard"],
)
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
check(
    "scoreboard: deaths beyond raw rows -> anomaly",
    ch["scoreboard"]["level"] == "anomaly" and ch["scoreboard"]["n"] == 1,
    ch["scoreboard"],
)

# 6) halves from sides: MR12 with overtime, MR15, gaps, fallbacks
print("== halves / presets from sides ==")
_s = {
    r: {s: ("T" if (s in (S1, S2)) == (r <= 12 or r >= 25) else "CT") for s in IDS}
    for r in range(1, 27)
}
check(
    "halves MR12+OT",
    core.halves_from_sides(_s, 26) == [(1, 12), (13, 24), (25, 26)],
    core.halves_from_sides(_s, 26),
)
check("halves empty sides", core.halves_from_sides({}, 26) == [])
_g = {r: _s[r] for r in (1, 2, 5, 13, 14)}  # gap 5 -> 13: boundary unknowable
check("halves gap not guessed", core.halves_from_sides(_g, 14) == [])
_m15 = {
    r: {s: ("T" if (s in (S1, S2)) == (r <= 15) else "CT") for s in IDS}
    for r in range(1, 19)
}
check(
    "halves MR15",
    core.halves_from_sides(_m15, 18) == [(1, 15), (16, 18)],
    core.halves_from_sides(_m15, 18),
)
_h = core.halves_from_sides(_s, 26)
check("bounds first (data)", core.round_bounds(26, "first", _h) == (1, 12))
check("bounds second (data)", core.round_bounds(26, "second", _h) == (13, 24))
check("bounds ot (data)", core.round_bounds(26, "ot", _h) == (25, 26))
check(
    "bounds MR15 second",
    core.round_bounds(18, "second", core.halves_from_sides(_m15, 18)) == (16, 18),
)
check("pistols (data)", core.pistol_rounds(26, _h) == {1, 13, 25})
check(
    "pistols MR15", core.pistol_rounds(18, core.halves_from_sides(_m15, 18)) == {1, 16}
)
check("bounds MR12 fallback", core.round_bounds(26, "second") == (13, 24))
check("pistols MR12 fallback", core.pistol_rounds(26) == {1, 13, 25})

# ------------------------------------------- impact: openings / trades
# 6 players (3v3, no side swap). Script (trade window 5 s = 320 ticks
# at tickrate 64):
#   R1 (trp 0): 100 suicide C3 (attacker NA - must be skipped for the
#                   opening); 120 T1 kills C1  <- opening 1 (T1 wins)
#               300 C2 kills T1  <- avenges C1 within 180 t: trade (C2)
#               620 T2 kills C2  <- exactly +320 after T1's death:
#                                   boundary trade (T2), T1's death traded
#   R2 (trp 1): 800 TEAMKILL T1 kills T2 (assister T3 - must not count)
#               900 C1 kills T1  <- opening 2 (C1 wins)
#              1221 T2 kills C1  <- +321 after T1's death: NOT a trade
#   R3 (trp 2): 2000 T2 kills C1 <- opening 3 (T2 wins)
#              2290 T1 kills C2, 2310 T1 kills C3  <- T1 2-kill round
#   R4 (trp 3): 2320 T3 kills C3 (assister T2, no flash) <- opening 4
#              2400 T3 kills C1 (assister T2, flash) ; 2410 T3 kills C2
#                                   <- T3 3-kill round (k3)
# The 2310/2320 pair is 10 ticks apart but in different rounds: a kill
# right after the round switch is never a trade (same-round rule).
print("== impact: openings / trades / multi-kills ==")


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
            "distance": [500] * len(D),
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
op = core.openings_table(imp_demo)
check(
    "openings: one per round, suicides/TK skipped",
    len(op) == 4 and op["round"].tolist() == [1, 2, 3, 4],
    op.get("round").tolist() if len(op) else "empty",
)
check(
    "openings: killers",
    op["killer"].tolist()
    == [
        "76561111111111121",
        S4,
        "76561111111111122",
        "76561111111111123",
    ],
)
check(
    "openings: victims",
    op["victim"].tolist() == [S4, "76561111111111121", S4, "76561111111111124"],
)
check(
    "openings: killer side",
    op["killer_side"].tolist() == ["T", "CT", "T", "T"],
)
check(
    "openings: zone + round clock",
    op.loc[0, "killer_zone"] == "BombsiteA"
    and op.loc[0, "victim_zone"] == "Middle"
    and abs(op.loc[0, "round_sec"] - 35.0) < 0.01,
)

ist = core.impact_stats(imp_demo).set_index("sid")
_I1 = "76561111111111121"  # TeeOne: opening killer R1, victim R2, 2k round
_I2 = "76561111111111122"  # TeeTwo: boundary trade, 2 assists (1 flash)
_I3 = "76561111111111123"  # TeeThree: 3-kill round
_C2 = S3  # CeeTwo: avenges CeeOne
_C3 = "76561111111111124"  # CeeThree: opening victim R4, never wins one
check(
    "open attempts (killer or victim)",
    ist.loc[_I1, "open_attempts"] == 2
    and ist.loc[S4, "open_attempts"] == 3
    and ist.loc[_I2, "open_attempts"] == 1
    and ist.loc[_C3, "open_attempts"] == 1,
)
check(
    "open wins",
    ist.loc[_I1, "open_wins"] == 1
    and ist.loc[S4, "open_wins"] == 1
    and ist.loc[_I3, "open_wins"] == 1
    and ist.loc[_C3, "open_wins"] == 0,
)
check(
    "open rate",
    ist.loc[_I1, "open_rate"] == 50.0
    and ist.loc[_I2, "open_rate"] == 100.0
    and ist.loc[_C3, "open_rate"] == 0.0,
)
check(
    "open rate NA without attempts",
    np.isnan(ist.loc[_C2, "open_rate"]),
)
check(
    "trades: avenger + exact +320 boundary",
    ist.loc[_C2, "trade_kills"] == 1 and ist.loc[_I2, "trade_kills"] == 1,
)
check(
    "trades: no trade at +321",
    ist.loc[_I1, "traded_deaths"] == 1 and ist.loc[S4, "traded_deaths"] == 1,
)
check(
    "trades: same-round rule (kill after the round switch)",
    ist.loc[_I1, "trade_kills"] == 0 and ist.loc[_C3, "traded_deaths"] == 0,
)
check(
    "traded pct",
    ist.loc[S4, "traded_pct"] == 25.0
    and ist.loc[_I1, "traded_pct"] == 50.0
    and ist.loc[_C2, "traded_pct"] == 0.0
    and ist.loc[_C3, "traded_pct"] == 0.0
    and np.isnan(ist.loc[_I2, "traded_pct"]),
)
check(
    "multi-kills: 2k and 3k rounds",
    ist.loc[_I1, "k2"] == 1 and ist.loc[_I3, "k3"] == 1 and ist.loc[_I2, "k2"] == 0,
)
check("multi-kills: no 4k/5k", ist[["k4", "k5"]].to_numpy().sum() == 0)
check(
    "assists: flash split, teamkill assist ignored",
    ist.loc[_I2, "assists"] == 2
    and ist.loc[_I2, "flash_assists"] == 1
    and ist.loc[_I3, "assists"] == 0,
)
# old demo without assister columns -> NA, never 0
ist_old = core.impact_stats(demo).set_index("sid")
check(
    "assists NA without the column",
    ist_old["assists"].isna().all() and ist_old["flash_assists"].isna().all(),
)
# summary + debug report wiring
summ_imp = core.player_summary(
    imp_demo,
    core.build_duels(imp_demo),
    core.shots_table(imp_demo),
    40.0,
)
_r1 = summ_imp[summ_imp["sid"] == _I1].iloc[0]
check("player_summary open_rate", _r1["open_rate"] == 50.0)
rep_imp = core.debug_report(
    imp_demo,
    core.shots_table(imp_demo),
    core.build_duels(imp_demo),
    _I1,
    {"thr": 40.0},
)
check(
    "debug_report counts",
    rep_imp["counts"]["openings"] == 4 and rep_imp["counts"]["trade_kills"] == 2,
)
# demo without any deaths: everything degrades, nothing crashes
FakeParser.demos["impact_nodeaths.dem"] = {
    **_m_imp,
    "events": {**_m_imp["events"], "player_death": pd.DataFrame()},
}
nd = core.load_demo("impact_nodeaths.dem")
check("no deaths: openings empty", len(core.openings_table(nd)) == 0)
ist_nd = core.impact_stats(nd).set_index("sid")
check(
    "no deaths: impact NA/0",
    ist_nd["open_attempts"].eq(0).all()
    and ist_nd["open_rate"].isna().all()
    and ist_nd["traded_pct"].isna().all()
    and ist_nd["assists"].isna().all(),
)
summ_nd = core.player_summary(nd, core.build_duels(nd), core.shots_table(nd), 40.0)
check("no deaths: summary open_rate NA", summ_nd["open_rate"].isna().all())

# ---- K/D, openings and impact follow the Side / Rounds filter (not the
# weapon / distance ones); the sides of one filter partition the match
_d_f, _s_f = core.build_duels(imp_demo), core.shots_table(imp_demo)


def _summ(**kw):
    flt = core.Filters(**kw) if kw else None
    return (
        core.player_summary(imp_demo, _d_f, _s_f, 40.0, f=flt)
        .set_index("sid")
        .sort_index()
    )


_all, _t, _ct = _summ(), _summ(side="T"), _summ(side="CT")
check(
    "filter: sides partition kills and deaths",
    (_t["kills"] + _ct["kills"]).eq(_all["kills"]).all()
    and (_t["deaths"] + _ct["deaths"]).eq(_all["deaths"]).all(),
)
check(
    "filter: side really changes K/D for someone",
    (_t["kills"] != _ct["kills"]).any() or (_t["deaths"] != _ct["deaths"]).any(),
)
_r12, _r34 = _summ(rounds=(1, 2)), _summ(rounds=(3, 4))
check(
    "filter: round ranges partition kills and deaths",
    (_r12["kills"] + _r34["kills"]).eq(_all["kills"]).all()
    and (_r12["deaths"] + _r34["deaths"]).eq(_all["deaths"]).all(),
)
check(
    "filter: weapon / distance do not touch K/D",
    _summ(weapons={"awp"}, dist=(0.0, 100.0))["kills"].eq(_all["kills"]).all(),
)
check(
    "filter: kd_ratio = kills / deaths (NaN without deaths)",
    all(
        (
            np.isnan(r.kd_ratio)
            if r.deaths == 0
            else r.kd_ratio == round(r.kills / r.deaths, 2)
        )
        for r in _all.itertuples()
    ),
)
_i_all = core.impact_stats(imp_demo).set_index("sid")
_i_t = core.impact_stats(imp_demo, f=core.Filters(side="T")).set_index("sid")
_i_ct = core.impact_stats(imp_demo, f=core.Filters(side="CT")).set_index("sid")
check(
    "filter: impact sides partition openings, trades, multi-kills",
    all(
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
    ),
)
check(
    "filter: round range narrows the opening duels",
    core.impact_stats(imp_demo, f=core.Filters(rounds=(1, 1)))["open_attempts"].sum()
    == 2,
)
# a deaths frame without a round counter: the filter must not guess rounds
_nr = core.load_demo("impact.dem")
_nr.deaths = _nr.deaths.drop(columns=["total_rounds_played"], errors="ignore")
check(
    "filter: no round counter -> unfiltered numbers, no crash",
    core.player_summary(_nr, _d_f, _s_f, 40.0, f=core.Filters(side="T"))["kills"].sum()
    == core.player_summary(_nr, _d_f, _s_f, 40.0)["kills"].sum(),
)

# ---- Map page: kill_points (positions of kills / deaths), radar calibration
import json as _json
import struct as _struct
import tempfile as _tempfile
import zlib as _zlib


def _tiny_png(path, n):
    """A valid n x n PNG without any imaging library."""
    raw = b"".join(b"\x00" + b"\x20\x20\x30" * n for _ in range(n))

    def chunk(tag, data):
        c = _struct.pack(">I", len(data)) + tag + data
        return c + _struct.pack(">I", _zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as fh:
        fh.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", _struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", _zlib.compress(raw))
            + chunk(b"IEND", b"")
        )


check(
    "kill_points: demo without positions -> empty frame with the columns",
    core.kill_points(imp_demo, S4).empty
    and list(core.kill_points(imp_demo, S4).columns) == core.KILL_POINT_COLS,
)
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
check(
    "kill_points: kills / deaths match the scoreboard numbers",
    int((_kp["result"] == "kill").sum()) == int(_sm.loc[S4, "kills"])
    and int((_kp["result"] == "death").sum()) == int(_sm.loc[S4, "deaths"])
    and len(_kp) > 0,
    (_kp["result"].value_counts().to_dict(), _sm.loc[S4, ["kills", "deaths"]].tolist()),
)
_kpt = core.kill_points(_mp, S4, core.Filters(side="T"))
_smt = core.player_summary(_mp, _d_f, _s_f, 40.0, f=core.Filters(side="T")).set_index(
    "sid"
)
check(
    "kill_points: side filter matches the filtered K/D",
    int((_kpt["result"] == "kill").sum()) == int(_smt.loc[S4, "kills"])
    and int((_kpt["result"] == "death").sum()) == int(_smt.loc[S4, "deaths"]),
)
check(
    "kill_points: round filter",
    set(core.kill_points(_mp, S4, core.Filters(rounds=(1, 1)))["round"]) <= {1},
)
_r0 = _kp.iloc[0]
check(
    "kill_points: my / opponent position by role, distance from positions",
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
    < 1e-6,
)
check(
    "kill_points: distance filter",
    (core.kill_points(_mp, S4, core.Filters(dist=(0.0, 1.0)))["distance"] <= 1.0).all(),
)

_rd = _tempfile.mkdtemp(prefix="cs2aa_radar_")
os.makedirs(os.path.join(_rd, "radars"))
_tiny_png(os.path.join(_rd, "radars", "de_t.png"), 16)
_tiny_png(os.path.join(_rd, "radars", "de_l.png"), 16)
_tiny_png(os.path.join(_rd, "radars", "de_l_lower.png"), 16)
_tiny_png(os.path.join(_rd, "radars", "de_nopng_meta.png"), 16)
with open(os.path.join(_rd, "map_data.json"), "w", encoding="utf-8") as fh:
    _json.dump(
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
    check(
        "radar: calibration + image size read",
        _rt is not None
        and _rt["size"] == 16
        and _rt["pos_x"] == -100.0
        and _rt["scale"] == 2.0
        and _rt["lower"] is None,
    )
    _px, _py = core.world_to_radar(0.0, 0.0, _rt)
    check(
        "radar: world -> pixel (scaled to the image size, y flipped)",
        abs(_px - 100 / 2 * 16 / 1024) < 1e-9 and abs(_py - 200 / 2 * 16 / 1024) < 1e-9,
        (_px, _py),
    )
    _rl = core.load_radar("de_l")
    check(
        "radar: lower level needs its image and the altitude range",
        _rl is not None
        and _rl["lower"] == (-10000.0, -495.0)
        and _rl["lower_png"].endswith("de_l_lower.png"),
    )
    check(
        "radar: on_lower_level by altitude",
        core.on_lower_level([-600.0, -495.0, 100.0], _rl).tolist()
        == [True, False, False]
        and not core.on_lower_level([-600.0], _rt).any(),
    )
    check(
        "radar: unknown map / bad scale / missing image -> None",
        core.load_radar("de_unknown") is None
        and core.load_radar("de_badscale") is None
        and core.load_radar("de_noimage") is None
        and core.load_radar("") is None,
    )
finally:
    if _old_env is None:
        os.environ.pop("AIMAUDIT_RADAR_DIR", None)
    else:
        os.environ["AIMAUDIT_RADAR_DIR"] = _old_env

# ---- 2D duel replay: replay_data (two duellists, one parse) + the figure
print("== replay_data / build_replay_figure ==")
import mapview  # noqa: E402

_rp_rows = []
for _sid, _nm, _x0, _vx, _alive_to in (
    (S1, "Alpha", 0.0, 4.0, 512),
    (S2, "Bravo", 900.0, -2.0, 99999),
):
    for _tk in range(490, 560):
        _rp_rows.append(
            {
                "tick": _tk,
                "steamid": f64_sid(_sid),
                "name": _nm,
                "X": _x0 + (_tk - 490) * _vx,
                "Y": 0.0 if _sid == S1 else 50.0,
                "Z": 0.0,
                "pitch": 1.0,
                "yaw": 2.0 if _sid == S1 else 182.0,
                "is_alive": _tk < _alive_to,
            }
        )
_rp = make_match()
_rp["ticks"] = pd.DataFrame(_rp_rows)
_rbits = (1 << 3) | (1 << 0)  # FORWARD + FIRE for Alpha only
_rp["buttons_bit"] = {(_t, f64_sid(S1)): _rbits for _t in range(490, 560)}
FakeParser.demos["rp.dem"] = _rp
_fr, _miss = core.replay_data("rp.dem", [S1, S2], 512, before_s=0.5, after_s=0.25)
check("replay_data: one frame per duellist", set(_fr) == {S1, S2}, list(_fr))
check(
    "replay_data: frames are the right player each (positions differ)",
    abs(_fr[S1]["X"].iloc[-1] - _fr[S2]["X"].iloc[-1]) > 100,
)
check(
    "replay_data: same tick window for both",
    _fr[S1]["tick"].tolist() == _fr[S2]["tick"].tolist() and len(_fr[S1]) > 20,
)
check(
    "replay_data: speed per player (256 vs 128 u/s), dead -> NaN",
    abs(_fr[S1][_fr[S1]["tick"] < 512]["speed"].dropna() - 256.0).max() < 1e-6
    and abs(_fr[S2]["speed"].dropna() - 128.0).max() < 1e-6
    and _fr[S1][_fr[S1]["tick"] >= 512]["speed"].isna().all(),
)
check(
    # (the fake keys its button bits by float64 SteamID, where S1 and S2
    # collide, so only the decoding itself is asserted here)
    "replay_data: keys decoded, nothing missing",
    bool(_fr[S1]["FORWARD"].all()) and bool(_fr[S1]["FIRE"].all()) and _miss == [],
    _miss,
)
_rp2 = make_match()
_rp2["ticks"] = pd.DataFrame(_rp_rows)
_rp2["unsupported"] = set(core.BTN_NAMED) | {"buttons"}
FakeParser.demos["rp2.dem"] = _rp2
_fr2, _miss2 = core.replay_data("rp2.dem", [S1, S2], 512, before_s=0.5, after_s=0.25)
check(
    "replay_data: buttons unsupported -> positions only, all keys missing",
    len(_fr2[S1]) > 0 and _miss2 == core.BUTTONS,
)

_rf = {"me": _fr[S1], "opp": _fr[S2]}
_rlab = {"me": "Alpha", "opp": "Bravo", "keys": {k: k for k in core.BUTTONS}}
_ff = mapview.build_replay_figure(
    _rf,
    None,
    512,
    64,
    shots={"me": [500]},
    hits=[(505, "me")],
    keys=core.BUTTONS,
    labels=_rlab,
)
check(
    "replay figure: frames update the dynamic traces, slider has a step per frame",
    _ff is not None
    and len(_ff.data) == 14
    and len(_ff.frames) > 3
    and all(
        list(fr.traces) == list(range(2, 14)) and len(fr.data) == 12
        for fr in _ff.frames
    )
    and len(_ff.layout.sliders[0].steps) == len(_ff.frames)
    and str(512) in [fr.name for fr in _ff.frames],
)
check(
    "replay figure: dead player gets a cross after the death tick",
    any(len(fr.data[9].x) == 1 for fr in _ff.frames if int(fr.name) > 512)
    and all(len(fr.data[9].x) == 0 for fr in _ff.frames if int(fr.name) < 512),
)
check(
    "replay figure: a shot shows as an aim line only just after it",
    any(len(fr.data[4].x) > 0 for fr in _ff.frames if 500 <= int(fr.name) < 510)
    and all(len(fr.data[4].x) == 0 for fr in _ff.frames if int(fr.name) > 520),
)
_nk = mapview.build_replay_figure(_rf, None, 512, 64, keys=[], labels=_rlab)
check(
    "replay figure: no key columns -> no key panel (12 traces)",
    _nk is not None
    and len(_nk.data) == 12
    and all(len(fr.data) == 10 for fr in _nk.frames),
)
check(
    "replay figure: missing duellist -> None",
    mapview.build_replay_figure(
        {"me": _fr[S1], "opp": _fr[S2].iloc[0:0]}, None, 512, 64
    )
    is None,
)
_rd2 = _tempfile.mkdtemp(prefix="cs2aa_radar2_")
os.makedirs(os.path.join(_rd2, "radars"))
_tiny_png(os.path.join(_rd2, "radars", "de_r.png"), 16)
with open(os.path.join(_rd2, "map_data.json"), "w", encoding="utf-8") as fh:
    _json.dump({"de_r": {"pos_x": -100.0, "pos_y": 100.0, "scale": 2.0}}, fh)
_old_env2 = os.environ.get("AIMAUDIT_RADAR_DIR")
os.environ["AIMAUDIT_RADAR_DIR"] = _rd2
try:
    _rr = mapview.build_replay_figure(
        _rf, core.load_radar("de_r"), 512, 64, keys=core.BUTTONS, labels=_rlab
    )
    check(
        "replay figure: radar image drawn below, view box set",
        _rr is not None
        and len(_rr.layout.images) == 1
        and _rr.layout.xaxis.range[0] < _rr.layout.xaxis.range[1],
    )
finally:
    if _old_env2 is None:
        os.environ.pop("AIMAUDIT_RADAR_DIR", None)
    else:
        os.environ["AIMAUDIT_RADAR_DIR"] = _old_env2

print(f"\n{PASS} passed, {FAIL} failed")
if __name__ == "__main__":
    sys.exit(1 if FAIL else 0)
