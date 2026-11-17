"""
Synthetic tests for CS2-AimAudit core.py with a fake demoparser2.

Builds a tiny fake match (4 players, warmup shots, a duel, a teamkill,
a lonely shot) and verifies every core metric without a real .dem file.
Run from the repo root: python tests/test_core.py
(R5 split: the sections live in tests/core_sections/, this module
is the shared header - env, imports, check(), the fake parser.)
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

# parents[2]: this module sits one level deeper than the original test_core.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Windows consoles default to legacy codepages (cp1252) and the output
# below prints → and ≈: force UTF-8 so it does not crash. CI sets
# PYTHONUTF8=1 as well - this covers a local run on a stock console.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
# every file the app stores goes to a throw-away data folder in this run
os.environ["AIMAUDIT_DATA_DIR"] = tempfile.mkdtemp(prefix="cs2aa_data_")
import core  # noqa: E402
import store  # noqa: E402

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
    # at 115 still slowed (0.35) from the hit it took. Everything else
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




# everything below is part of the section files' namespace
__all__ = [
    'FAIL',
    'FakeParser',
    'IDS',
    'PASS',
    'Path',
    'S1',
    'S2',
    'S3',
    'S4',
    'bz2',
    'check',
    'core',
    'f64_sid',
    'gzip',
    'json',
    'make_match',
    'make_match_sides',
    'np',
    'os',
    'pd',
    'store',
    'sys',
    'tempfile',
    'zipfile',
]
