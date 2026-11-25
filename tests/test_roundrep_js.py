"""
Headless check of the 2D replay front end: the REAL round_payload of a
synthetic round runs through ui/roundrep.js under jsdom (tests/js). Needs
node and `npm ci` in tests/js; skipped without them.

    RR_SHOT_DIR=/tmp/rr pytest tests/test_roundrep_js.py   # + PNG screenshots
"""

import hashlib
import json
import os
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

import core
import store
from ui import icons as ui_icons
from ui.icons import sanitize_svg
from ui.roundrep import round_payload


HERE = os.path.dirname(os.path.abspath(__file__))


KEYS = ["FORWARD", "LEFT", "BACK", "RIGHT", "WALK", "DUCK", "JUMP", "FIRE"]
NAMES = ["zeta", "Alpha", "bravo", "Charlie", "delta", "Echo", "foxtrot", "Golf", "hotel", "India"]

# the icon fixture: sanitized SVGs + a manifest pinning their hashes (the
# same shape tools/fetch_icons.py writes) - enough keys for every icon slot
# of this round: the weapons, the bomb story, the headshot, the skull, the
# clock and the kill modifiers of duel 1
ICON_FIXTURE = {
    "ak47": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 32" width="100" height="32"><path fill="white" d="M0 10h100v12H0z"/></svg>',
    "awp": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 32" width="100" height="32"><path fill="white" d="M4 12h92v8H4z"/></svg>',
    "headshot": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><circle fill="white" cx="12" cy="12" r="9"/></svg>',
    "skull": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path d="M12 2l4 8-4 12-4-12z"/></svg>',
    "bomb": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><circle fill="white" cx="12" cy="14" r="8"/></svg>',
    "bomb_explode": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z"/></svg>',
    "defuse": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="4" y="8" width="16" height="8"/></svg>',
    "timer": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><circle fill="white" cx="12" cy="12" r="10"/></svg>',
    "hourglass": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M6 3h12l-6 9zM6 21h12l-6-9z"/></svg>',
    "wallbang": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M2 11h20v2H2z"/></svg>',
    "noscope": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><circle fill="white" cx="12" cy="12" r="7"/></svg>',
    "smoke_kill": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M4 16a6 6 0 0 1 12-6 5 5 0 0 1 4 5z"/></svg>',
    "flashed_kill": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M12 4l8 14H4z"/></svg>',
    # the economy row: armor states, the utility grenades, the bomb, the kit
    "armor_full": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="4" y="4" width="16" height="16"/></svg>',
    "armor_half": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="4" y="4" width="8" height="16"/></svg>',
    "armor_half_helmet": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="4" y="4" width="8" height="16"/><circle fill="white" cx="18" cy="6" r="3"/></svg>',
    "armor_helmet": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="4" y="4" width="16" height="16"/><circle fill="white" cx="18" cy="6" r="3"/></svg>',
    "armor_none": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="4" y="4" width="16" height="16" opacity="0.3"/></svg>',
    "flashbang": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><circle fill="white" cx="12" cy="12" r="8"/></svg>',
    "smokegrenade": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="6" y="6" width="12" height="12"/></svg>',
    "c4": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><rect fill="white" x="4" y="4" width="16" height="16"/></svg>',
    "defuse": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M5 6h14v4H5zM10 10h4v8h-4z"/></svg>',
    "blind": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M12 3l7 12H5z"/></svg>',
}


def write_icon_fixture(dest: str) -> str:
    """The verified fixture set + manifest; AIMAUDIT_ICON_DIR must point
    here BEFORE the payload is built (ui.icons caches per folder)."""
    os.makedirs(dest, exist_ok=True)
    manifest = {}
    for key, svg in ICON_FIXTURE.items():
        text = sanitize_svg(svg)
        with open(os.path.join(dest, f"{key}.svg"), "w", encoding="utf-8") as fh:
            fh.write(text)
        manifest[key] = {
            "file": f"{key}.svg", "w": 100 if key in ("ak47", "awp") else 24,
            "h": 32 if key in ("ak47", "awp") else 24,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source": "mirror",
        }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return dest


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
                "armor_value": np.where(grid < 1900, 100, 40),
                "has_helmet": k % 2 == 0,
                "has_defuser": k == 2,
                "is_defusing": False,
                "balance": np.where(grid < 1300, 3000 + 100 * k, 200 + 100 * k),
                "current_equip_value": 4750,
                "inventory": [["knife", "AK-47", "Flashbang", "Smoke Grenade"]
                              + (["C4 Explosive"] if k == 3 else [])] * len(grid),
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
        # the match's kills / deaths before this round (the player cards add the live ones)
        "kd0": {sids[0]: [7, 12], sids[1]: [3, 4], sids[5]: [0, 0], sids[6]: [10, 9]},
    }
    duel_rows = pd.DataFrame(
        [
            {"tick": t0 + 4 * 330, "killer": sids[0], "victim": sids[1], "weapon": "ak47",
             "headshot": True, "duration_s": 1.4, "killer_first_tick": t0 + 4 * 300,
             "victim_first_tick": None, "first": "killer", "distance": 740.0,
             "killer_speed": 12.0, "victim_speed": 180.0, "killer_err": 1.8,
             "victim_err": 9.4, "killer_shots": 3, "victim_shots": 0,
             "noscope": True, "thrusmoke": False, "attackerblind": True, "penetrated": True},
            {"tick": t0 + 4 * 480, "killer": sids[5], "victim": sids[6], "weapon": "awp",
             "headshot": False, "duration_s": 0.6, "killer_first_tick": None,
             "victim_first_tick": t0 + 4 * 470, "first": "victim", "distance": 420.0,
             "killer_speed": 90.0, "victim_speed": 5.0, "killer_err": 6.1,
             "victim_err": 2.2, "killer_shots": 1, "victim_shots": 2,
             "noscope": False, "thrusmoke": False, "attackerblind": False, "penetrated": False},
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
        thr=34.0,
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


@pytest.mark.parametrize("script", ["roundrep.test.mjs", "icons.test.mjs"])
def test_js(script, tmp_path, monkeypatch):
    node = shutil.which("node")
    js_dir = os.path.join(HERE, "js")
    if not node or not os.path.isdir(os.path.join(js_dir, "node_modules", "jsdom")):
        pytest.skip("needs node and `npm ci` in tests/js")
    icon_dir = write_icon_fixture(str(tmp_path / "icons"))
    monkeypatch.setenv("AIMAUDIT_ICON_DIR", icon_dir)
    ui_icons.reset_cache()
    try:
        arg = icon_dir
        if script == "roundrep.test.mjs":
            arg = str(tmp_path / "payload.json")
            store.write_json(arg, build_payload())
        res = subprocess.run(
            [node, os.path.join(js_dir, script), arg],
            capture_output=True, text=True, encoding="utf-8", cwd=js_dir,
        )
    finally:
        ui_icons.reset_cache()
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
