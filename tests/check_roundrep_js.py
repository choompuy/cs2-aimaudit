"""
Optional headless check of the 2D replay front end (ui/roundrep.js).

Builds a synthetic 10-player round with the REAL ui.roundrep.round_payload,
then runs the JS under jsdom with a real canvas (tests/js/roundrep.test.mjs):
nickname order, every duel as a card, the eye (hide a player), the duel lock,
the rounds bar trigger, playback without reruns.

    cd tests/js && npm install     # once: jsdom + @napi-rs/canvas
    python tests/check_roundrep_js.py
    RR_SHOT_DIR=/tmp/rr python tests/check_roundrep_js.py   # + PNG screenshots

Skipped (exit 0) when node or the npm packages are missing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import core  # noqa: E402
from ui.roundrep import round_payload  # noqa: E402

KEYS = ["FORWARD", "LEFT", "BACK", "RIGHT", "WALK", "DUCK", "JUMP", "FIRE"]
NAMES = ["zeta", "Alpha", "bravo", "Charlie", "delta", "Echo", "foxtrot", "Golf", "hotel", "India"]


def build_payload() -> dict:
    tr, t0, step, n = 64, 1000, 4, 600
    t1 = t0 + step * (n - 1)
    grid = np.arange(t0, t1 + 1, step)
    sids = [f"7656119800000000{i}" for i in range(len(NAMES))]
    names = dict(zip(sids, NAMES))
    frames = {}
    for k, sid in enumerate(sids):
        alive = np.ones(len(grid), bool)
        hp = np.full(len(grid), 100)
        if k in (1, 6):  # two players die during the round
            alive[250 + 80 * k :] = False
            hp[250 + 80 * k :] = 0
        keys = {b: np.sin(grid / (20 + 3 * i) + k) > 0.3 for i, b in enumerate(KEYS)}
        frames[sid] = pd.DataFrame(
            {
                "tick": grid,
                "steamid": sid,
                "X": -800 + 150 * k + 200 * np.cos(grid / 90 + k * 0.6),
                "Y": 600 - 120 * k + 200 * np.sin(grid / 70 + k * 0.6),
                "Z": 0.0,
                "pitch": 0.0,
                "yaw": (grid * 0.7 + k * 40) % 360 - 180,
                "is_alive": alive,
                "health": hp,
                "active_weapon_name": "weapon_ak47",
                **keys,
            }
        )
    data = {
        "t0": t0, "t1": t1, "step": step, "tickrate": tr, "round": 3,
        "frames": frames, "keys": KEYS,
        "shots": [(t0 + 4 * i, sids[i % 5], "ak47") for i in range(300, 340, 3)],
        "kills": [(t0 + 4 * 330, sids[0], sids[1], "ak47", True),
                  (t0 + 4 * 480, sids[5], sids[6], "awp", False)],
        "hits": [(t0 + 4 * 325, sids[0], sids[1])],
    }
    duel_rows = pd.DataFrame(
        [
            {"tick": t0 + 4 * 330, "killer": sids[0], "victim": sids[1], "weapon": "ak47",
             "headshot": True, "duration_s": 1.4, "killer_first_tick": t0 + 4 * 300,
             "victim_first_tick": None, "first": "killer", "distance": 740.0,
             "killer_speed": 12.0, "victim_speed": 180.0, "killer_err": 1.8,
             "victim_err": 9.4, "killer_shots": 3, "victim_shots": 0},
            {"tick": t0 + 4 * 480, "killer": sids[5], "victim": sids[6], "weapon": "awp",
             "headshot": False, "duration_s": 0.6, "killer_first_tick": None,
             "victim_first_tick": t0 + 4 * 470, "first": "victim", "distance": 420.0,
             "killer_speed": 90.0, "victim_speed": 5.0, "killer_err": 6.1,
             "victim_err": 2.2, "killer_shots": 1, "victim_shots": 2},
        ]
    )
    data["info"] = {
        "winner": "T", "reason": "bomb", "end": t0 + 4 * 560,
        "bomb": [(t0 + 4 * 200, "plant", sids[2]), (t0 + 4 * 560, "boom", "")],
    }
    return round_payload(
        data,
        names=names,
        side_of=lambda s: "CT" if sids.index(s) % 2 == 0 else "T",
        duels=core.round_duels(duel_rows, (t0, t1), tr),
        rounds=[{"n": i, "win": ["CT", "T", ""][i % 3], "why": ["defuse", "bomb", ""][i % 3]}
                for i in range(1, 25)],
        labels={"players": "Players", "rounds": "Rounds", "locked": "Duel", "exit": "Exit duel",
                "round_prev": "Previous round", "round_next": "Next round", "round": "Round",
                "won_ct": "Counter-Terrorists won", "won_t": "Terrorists won",
                "why_bomb": "Bomb exploded", "why_defuse": "Bomb defused",
                "b_plant": "planted", "b_boom": "exploded", "u_s": "u/s",
                "speed": "Speed", "tel_speed": "Speed", "tel_turn": "Crosshair turn",
                "tel_err": "Aim error", "tel_first": "first shot", "tel_shots": "shots",
                "tel_moving": "moving", "tel_standing": "standing", "duel_panel": "Duel aim",
                "role_killer": "killer", "role_victim": "victim", "first_killer": "The killer shot first"},
        colors={"ct": "#4f9dff", "t": "#f2b134", "ctb": "#17304f", "ttb": "#4a3a14",
                "dead": "#8b8f98", "hit": "#ffd24d", "flash": "#ffffff", "good": "#3ecf8e",
                "bad": "#ef5350", "warn": "#f2b134", "bg": "#0e1116", "grid": "#2a2f38",
                "text": "#e8eaf0", "text2": "#9aa1ad", "acc": "#7c8cff"},
    )


def main() -> int:
    node = shutil.which("node")
    js_dir = os.path.join(HERE, "js")
    if not node or not os.path.isdir(os.path.join(js_dir, "node_modules", "jsdom")):
        print("skipped: needs node and `npm install` in tests/js (jsdom, @napi-rs/canvas)")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "payload.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(build_payload(), fh)
        res = subprocess.run(
            [node, os.path.join(js_dir, "roundrep.test.mjs"), path],
            capture_output=True, text=True, cwd=js_dir,
        )
    sys.stdout.write(res.stdout)
    if res.returncode:
        sys.stdout.write(res.stderr[-2000:])
        print("FAIL: roundrep.js")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
