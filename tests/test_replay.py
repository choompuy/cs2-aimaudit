"""The 2D replay: round windows, replay data, duels, the payload."""

import copy as _copy
import json
import os
import tempfile
from types import SimpleNamespace as _NS

import numpy as np
import pandas as pd
import pytest

import core
from core.replay import _kd_before
from fakes import FakeParser, IDS, S1, S2, S3, S4, _tiny_png, f64_sid, make_match
from ui.roundrep import round_payload


def test_round_windows():
    _bw = core.build_round_windows
    _ev = lambda rows: pd.DataFrame(rows, columns=["tick", "round"])
    # bug 1: the first event comes < 3 s after the freeze end. The old heuristic
    # started the window 3 s before that event, found the round's OWN freeze end
    # "ahead" of it and cut the round at that freeze end: a 1-second round.
    _w = _bw([10000, 20000], [], _ev([(10100, 1), (10900, 1), (20100, 2), (20800, 2)]), 64)
    assert _w[1][0] == 10000 and _w[1][1] - _w[1][0] > 5 * 64 and _w[2][0] == 20000, "windows: first event right after the freeze end -> the round is NOT collapsed"
    # bug 2: a kill AFTER the round ended carries the next round's number
    # (total_rounds_played already counted it); by tick it still belongs to round 1
    _w = _bw([10000, 20000], [], _ev([(10200, 1), (10900, 1), (10950, 2), (20300, 2), (20600, 2)]), 64)
    assert core.round_of_tick(_w, 10950) == 1 and _w[1][0] <= 10950 <= _w[1][1], "windows: a post-round kill (next round's number) stays in the round it happened in"
    assert sorted(_w) == [1, 2] and _w[1][1] < _w[2][0] and core.round_of_tick(_w, 5) is None, "windows: every tick belongs to exactly one round, numbers come from the events"
    _w = _bw([10000, 20000], [10800, 20900], _ev([(10200, 1), (10900, 1), (10950, 2), (20300, 2), (20600, 2)]), 64)
    assert _w == {1: (10000, 10799), 2: (20000, 20899)}, ("windows: the round ends on the tick before round_officially_ended (the win panel is part of it,"
        " the respawn of the next round is not)")
    _w = _bw([10000, 20000, 30000], [], _ev([(10200, 1), (30200, 3)]), 64)
    assert sorted(_w) == [1, 3], "windows: a window without any event is not a played round (restart / knife) - skipped"
    _w = _bw([10000, 20000], [], _ev([(10200, 6), (20300, 7)]), 64)
    assert sorted(_w) == [6, 7], "windows: a demo starting mid-match keeps the app's round numbers (6, 7)"
    _w = _bw([10000], [], _ev([(10200, 1), (10600, 1)]), 64, max_s=10)
    assert (
        _w[1][1] == 10000 + 640
        and _bw([10000, 10400], [], _ev([(10100, 1), (10150, 1), (10500, 2)]), 64)[1][1] == 10399
    ), "windows: the last round is capped at max_s, an earlier next freeze end caps the end"
    _w = _bw([10000, 20000], [], _ev([(10200, 1), (20300, 1)]), 64)
    assert sorted(_w) == [1, 2], "windows: two windows claiming one number -> the later one gets the next number"
    assert _bw([], [], _ev([(1, 1)]), 64) == {} and _bw([100], [], None, 64) == {}, "windows: no freeze ends / no events -> nothing"


_rr_rows = []
for _tk in range(100, 640):
    for _sid, _nm in (
        (S1, "Alpha"),
        (S2, "Bravo"),
        (S3, "Charlie"),
        (S4, "Delta"),
    ):
        _alive = not (
            (_sid == S1 and _tk >= 600) or (_sid == S3 and _tk >= 510)
        )
        _hp = 100
        if _sid == S1:
            _hp = 0 if _tk >= 600 else (34 if _tk >= 505 else 100)
        elif _sid == S3:
            _hp = 0 if _tk >= 510 else 100
        _rr_rows.append(
            {
                "tick": _tk,
                "steamid": f64_sid(_sid),
                "name": _nm,
                "X": (10.0 + (_tk - 100)) if _sid == S1 else (510.0 if _sid == S3 else 0.0),
                "Y": 0.0 if _sid in (S1, S3) else 1500.0,
                "Z": 0.0,
                "pitch": 0.0,
                "yaw": 0.0 if _sid in (S1, S2) else 180.0,
                "is_alive": _alive,
                "team_num": 2 if _sid in (S1, S2) else 3,
                "health": _hp,
                "active_weapon_name": (
                    ("weapon_awp" if _tk >= 400 else "weapon_ak47")
                    if _sid == S1
                    else "weapon_ak47"
                ),
                "flash_duration": 0.0,
            }
        )
_rr_events = {
    "round_freeze_end": pd.DataFrame(
        {
            "tick": [100, 1000],
            "is_warmup_period": [False, False],
        }
    ),
    "begin_new_match": pd.DataFrame({"tick": [10]}),
    "weapon_fire": pd.DataFrame(
        {
            "tick": [500, 505],
            "user_steamid": pd.array([int(S1), int(S3)], dtype="uint64"),
            "user_name": ["Alpha", "Charlie"],
            "weapon": ["weapon_ak47", "weapon_ak47"],
            "total_rounds_played": [0, 0],
            "is_warmup_period": [False, False],
        }
    ),
    "player_hurt": pd.DataFrame(
        {
            "tick": [505],
            "attacker_steamid": pd.array([int(S1)], dtype="float64"),
            "attacker_name": ["Alpha"],
            "user_steamid": pd.array([int(S3)], dtype="float64"),
            "user_name": ["Charlie"],
            "weapon": ["weapon_ak47"],
            "hitgroup": [1],
            "total_rounds_played": [0],
            "is_warmup_period": [False],
        }
    ),
    "player_death": pd.DataFrame(
        {
            "tick": [510],
            "attacker_steamid": pd.array([int(S1)], dtype="float64"),
            "attacker_name": ["Alpha"],
            "user_steamid": pd.array([int(S3)], dtype="float64"),
            "user_name": ["Charlie"],
            "weapon": ["weapon_ak47"],
            "headshot": [True],
            "distance": [12.7],
            "total_rounds_played": [0],
            "is_warmup_period": [False],
        }
    ),
}
_rr = make_match()
_rr["ticks"] = pd.DataFrame(_rr_rows)
_rr["events"].update(_rr_events)
_rr["buttons_bit"] = {
    (t, f64_sid(S1)): ((1 << 3) | (1 << 0) if t == 500 else (1 << 3))
    for t in range(100, 640)
}
FakeParser.demos["rr.dem"] = _rr
_rrdemo = core.load_demo("rr.dem")


def test_round_windows_of_a_demo():
    assert _rrdemo.round_windows == {1: (100, 830)}, "load_demo: round window = freeze end .. last event + 5 s (round 2 has no events: not a round)"
    # the round_officially_ended event, when the demo has it, ends the round
    _rr_end = make_match()
    _rr_end["ticks"] = pd.DataFrame(_rr_rows)
    _rr_end["events"].update(_rr_events)
    _rr_end["events"]["round_officially_ended"] = pd.DataFrame({"tick": [900, 1900]})
    FakeParser.demos["rr_end.dem"] = _rr_end
    assert core.load_demo("rr_end.dem").round_windows == {1: (100, 899)}, "load_demo: round_officially_ended is the end of the round"
    # no round_freeze_end at all: windows from the events' own round numbers
    _rr_nofe = make_match()
    _rr_nofe["ticks"] = pd.DataFrame(_rr_rows)
    _rr_nofe["events"].update(_rr_events)
    _rr_nofe["events"]["round_freeze_end"] = pd.DataFrame(columns=["tick", "is_warmup_period"])
    FakeParser.demos["rr_nofe.dem"] = _rr_nofe
    _wn = core.load_demo("rr_nofe.dem").round_windows
    assert list(_wn) == [1] and _wn[1][0] < 500 < 510 < _wn[1][1], "load_demo: no freeze ends -> the round still gets a window from its events"
    # a kill AFTER the round ended carries the NEXT round's number
    # (total_rounds_played is already counted): the duel table follows the window
    _rr_post = make_match()
    _rr_post["ticks"] = pd.DataFrame(_rr_rows)
    _post_events = dict(_rr_events)
    _post_events["player_death"] = _rr_events["player_death"].assign(total_rounds_played=[1])
    _post_events["weapon_fire"] = pd.concat(
        [
            _rr_events["weapon_fire"],
            pd.DataFrame(
                {
                    "tick": [1100],
                    "user_steamid": pd.array([int(S2)], dtype="uint64"),
                    "user_name": ["Bravo"],
                    "weapon": ["weapon_ak47"],
                    "total_rounds_played": [1],
                    "is_warmup_period": [False],
                }
            ),
        ],
        ignore_index=True,
    )
    _rr_post["events"].update(_post_events)
    FakeParser.demos["rr_post.dem"] = _rr_post
    _dpost = core.load_demo("rr_post.dem")
    assert (
        core.round_of_tick(_dpost.round_windows, 510) == 1
        and list(core.build_duels(_dpost, 3.0)["round"]) == [1]
    ), "build_duels: a post-round kill (next round's number) is a duel of the round it happened in"
    _ws = core.load_demo("sides.dem") if "sides.dem" in FakeParser.demos else None
    if _ws is not None:
        assert (
            core.round_of_tick(_ws.round_windows, 120) == 1
            and core.round_of_tick(_ws.round_windows, 310) == 2
        ), "windows (no freeze ends): late events of a round never leak into the next window"


_rr_info = make_match()
_rr_info["ticks"] = pd.DataFrame(_rr_rows)
_rr_info["events"].update(_rr_events)
_rr_info["events"]["round_end"] = pd.DataFrame(
    {"tick": [700], "winner": [3], "reason": [7], "message": ["#SFUI_Notice_Bomb_Defused"]}
)
_rr_info["events"]["bomb_planted"] = pd.DataFrame(
    {"tick": [300], "user_steamid": pd.array([int(S1)], dtype="uint64")}
)
_rr_info["events"]["bomb_defused"] = pd.DataFrame(
    {"tick": [690], "user_steamid": pd.array([int(S2)], dtype="uint64")}
)
FakeParser.demos["rr_info.dem"] = _rr_info
_di = core.load_demo("rr_info.dem")
# a demo WITHOUT round_end (the FACEIT / Valve demos of docs/DEMOPARSER_NOTES.md
# have none): the result is read from the bomb events and the kills


def test_round_end(demo):
    assert (
        core.classify_round_end("#SFUI_Notice_Target_Bombed", 1) == "bomb"
        and core.classify_round_end("#SFUI_Notice_Bomb_Defused", 7) == "defuse"
        and core.classify_round_end("#SFUI_Notice_Terrorists_Win", 9) == "elim"
        and core.classify_round_end("#SFUI_Notice_CTs_Win", 8) == "elim"
        and core.classify_round_end("#SFUI_Notice_Target_Saved", 12) == "time"
        and core.classify_round_end("#SFUI_Notice_CTs_Surrender", 18) == "surrender"
        and core.classify_round_end("", 12) == "time"
        and core.classify_round_end("", None) == "other"
        and core.classify_round_end(None, "x") == "other"
    ), "classify_round_end: the message is the signal, the numeric code the fallback"
    # the last round of a match has no officially_ended: it lasts to its own end
    _lw_ev = pd.DataFrame({"tick": [200, 1000], "round": [1, 1]})
    assert (
        core.build_round_windows([100], [], _lw_ev, 64)[1] == (100, 1320)
        and core.build_round_windows([100], [], _lw_ev, 64, marks=[50, 1400])[1] == (100, 1720)
        and core.build_round_windows([100], [], _lw_ev, 64, marks=[99999])[1] == (100, 1320)
        and core.build_round_windows([100], [1200], _lw_ev, 64, marks=[1400])[1] == (100, 1199)
    ), "round windows: without officially_ended a late defuse / round_end stays inside"
    # the round_end demoparser2 0.42.2 builds itself: no message, the reason is a word
    _words = pd.DataFrame(
        {
            "reason": ["bomb_defused", "ct_killed", "t_killed", "bomb_exploded", "time_ran_out", "vip_killed"],
            "round": [1, 2, 3, 4, 5, 6],
            "tick": [150, 250, 350, 450, 550, 650],
            "winner": ["CT", "T", "CT", "T", "CT", "T"],
        }
    )
    _wi = core.build_round_info({n: (n * 100, n * 100 + 90) for n in range(1, 7)}, _words, [])
    assert (
        [_wi[n]["reason"] for n in range(1, 7)] == ["defuse", "elim", "elim", "bomb", "time", "other"]
        and [_wi[n]["winner"] for n in range(1, 7)] == ["CT", "T", "CT", "T", "CT", "T"]
    ), "round_info: the words demoparser2 puts into round_end.reason"
    assert (
        core.classify_round_end("", "ct_surrender") == "surrender"
        and core.classify_round_end("", "hostage_rescued") == "hostage"
        and core.classify_round_end("", "draw") == "draw"
        and core.classify_round_end("", "12") == "time"
    ), "classify_round_end: surrender / hostage / draw words, a number as text still works"
    _re = pd.DataFrame(
        {
            "tick": [700, 5000],
            "winner": [3, 2],
            "reason": [7, 18],
            "message": ["#SFUI_Notice_Bomb_Defused", "#SFUI_Notice_CTs_Surrender"],
        }
    )
    _ri = core.build_round_info(
        {1: (100, 830)},
        _re,
        [(300, "plant", S1), (650, "defuse_start", S2), (690, "defuse", S2), (9000, "plant", S1)],
    )
    assert (
        _ri == {1: {"winner": "CT", "reason": "defuse", "end": 700,
                    "bomb": [(300, "plant", S1), (650, "defuse_start", S2), (690, "defuse", S2)],
                    "bomb_known": False}}
    ), "round_info: winner + reason + end tick, bomb events of the window only"
    assert core.build_round_info({1: (100, 830)}, None, [], bomb_known=True)[1]["bomb_known"] is True, "round_info: bomb_known is carried per round"
    assert (
        core.build_round_info({1: (100, 830)}, _re.iloc[1:], [])[1]["winner"] == ""
        and core.build_round_info({1: (100, 830)}, None, [])[1]["reason"] == ""
        and core.build_round_info({}, _re, []) == {}
    ), "round_info: a match-ending round_end past the last window belongs to nobody; no round_end -> blank"
    assert (
        _di.round_info.get(1, {}).get("winner") == "CT"
        and _di.round_info[1]["reason"] == "defuse"
        and _di.round_info[1]["end"] == 700
        and [b[1] for b in _di.round_info[1]["bomb"]] == ["plant", "defuse"]
    ), "load_demo: round_info carries the winner, how (defuse) and the bomb story"
    assert core.round_winners(_di) == {1: "CT"}, "round_winners: the demo's own round_end wins over the elimination guess"
    def _no_round_end(demo, bombs, known=True):
        d = _copy.copy(demo)
        d.round_info = {1: {"winner": "", "reason": "", "end": None, "bomb": bombs, "bomb_known": known}}
        return d
    assert (
        core.round_outcomes(_no_round_end(_di, [(300, "plant", S1), (750, "boom", S1)]))[1]
        == {"winner": "T", "reason": "bomb", "end": 750, "inferred": True}
    ), "round_outcomes: the bomb exploded -> T won by the bomb (read from the events)"
    assert (
        core.round_outcomes(_no_round_end(_di, [(300, "plant", S1), (690, "defuse", S2)]))[1]
        == {"winner": "CT", "reason": "defuse", "end": 690, "inferred": True}
    ), "round_outcomes: the bomb was defused -> CT won by the defuse"
    assert (
        core.round_outcomes(_no_round_end(_di, []))[1]
        == {"winner": "CT", "reason": "time", "end": None, "inferred": True}
    ), "round_outcomes: no plant, nobody eliminated, bomb events known -> CT won on time"
    assert (
        core.round_outcomes(_no_round_end(_di, [(300, "plant", S1)]))[1]["winner"] == ""
        and core.round_outcomes(_no_round_end(_di, [], known=False))[1]["winner"] == ""
    ), "round_outcomes: a planted bomb nobody resolved, or bomb events not reported -> stays blank"
    assert core.round_outcomes(_di)[1] == {"winner": "CT", "reason": "defuse", "end": 700, "inferred": False}, "round_outcomes: the demo's own round_end is never second-guessed"
    assert (
        core.load_demo("rr.dem").round_info.get(1)
        == {"winner": "", "reason": "", "end": None, "bomb": [], "bomb_known": False}
    ), "load_demo: a demo without round_end / bomb events still loads (blank outcome)"


def test_replay_span():
    _span = core.replay._replay_span
    def _span_demo(end, deaths=(), bombs=()):
        return type("D", (), {
            "round_info": {2: {"end": end, "bomb": list(bombs)}},
            "deaths": pd.DataFrame({"tick": list(deaths)}),
        })()
    _sw = {1: (1000, 4000), 2: (5000, 9000)}
    assert _span(_span_demo(8000), 2, _sw, 5000, 9000, 64) == (5000 - 128, 8000 + 320), "replay span: 2 s before the freeze ends .. 5 s after the round is decided"
    assert (
        _span(_span_demo(8000, deaths=[8500]), 2, _sw, 5000, 9000, 64)[1] == 8564
        and _span(_span_demo(8000, bombs=[(8990, "plant", S1)]), 2, _sw, 5000, 9000, 64)[1] == 9000
    ), "replay span: a late kill / plant keeps a second after it, never past the window"
    assert _span(_span_demo(None), 2, {1: (1000, 4950), 2: (5000, 9000)}, 5000, 9000, 64) == (4951, 9000), "replay span: the lead stays out of the round before, an unknown end keeps the window"


_rrd = core.round_replay_data(_rrdemo, 1)
# the match's kills / deaths before a round: enemy kills only (no suicide, no team kill)


def test_round_replay_data():
    assert (_rrd["t0"], _rrd["t1"]) == (100, 830), "round_replay_data: span = the demo's round window"
    assert set(_rrd["frames"]) == set(IDS), "round_replay_data: every player of the match gets a frame"
    assert "FORWARD" in _rrd["keys"] and "FIRE" in _rrd["keys"], "round_replay_data: keys decoded from the bitfield"
    assert _rrd["round"] == 1 and _rrd["tickrate"] == 64, "round_replay_data: round number and tickrate"
    assert (
        _rrd["shots"] == [(500, S1, "ak47"), (505, S3, "ak47")]
        and _rrd["kills"] == [(510, S1, S3, "ak47", True)]
        and _rrd["hits"] == [(505, S1, S3)]
    ), "round_replay_data: shots/kills/hits of the round come through"
    _kdd = _NS(
        deaths=pd.DataFrame(
            {
                "tick": [10, 20, 30, 40, 50, 900],
                "attacker_steamid": ["a", "a", "b", "a", "0", "a"],
                "user_steamid": ["b", "c", "b", "d", "a", "b"],
            }
        ),
        teams={"a": "X", "b": "Y", "c": "X", "d": "Y"},
    )
    assert _kd_before(_kdd, 100) == {"b": [0, 2], "a": [2, 1], "c": [0, 1], "d": [0, 1]}, "_kd_before: enemy kills / all deaths before the round; suicide, team kill, world, later rounds out"
    assert _kd_before(_kdd, 5) == {}, "_kd_before: nothing before the first tick"
    assert isinstance(_rrd.get("kd0"), dict), "round_replay_data: carries the match kills / deaths before the round (kd0)"
    _rr_small = core.round_replay_data(_rrdemo, 1, max_samples=50)
    assert (
        _rr_small["step"] > 1
        and len(range(_rr_small["t0"], _rr_small["t1"] + 1, _rr_small["step"])) <= 50
    ), "round_replay_data: grid is downsampled to <= max_samples"
    # degradation: an old parser without hp/weapon/button props
    _rr2 = make_match()
    _rr2["ticks"] = pd.DataFrame(_rr_rows)
    _rr2["events"].update(_rr_events)
    _rr2["unsupported"] = {"health", "active_weapon_name"} | set(core.BTN_NAMED) | {"buttons"}
    FakeParser.demos["rr2.dem"] = _rr2
    _rrd3 = core.round_replay_data(core.load_demo("rr2.dem"), 1)
    assert _rrd3["keys"] == [] and "health" not in _rrd3["frames"][S1].columns, "round_replay_data: hp/weapon/buttons unsupported -> positions only"
    # a round that cannot be placed at all
    for _bad_round in (2, 7):
        with pytest.raises(ValueError) as exc_info:
            core.round_replay_data(_rrdemo, _bad_round)
        e = exc_info.value
        assert str(e) == "no_round_data", f"round_replay_data: unplaceable round {_bad_round} raises"


_dcols = ["tick", "killer", "victim", "weapon", "headshot", "duration_s",
          "killer_first_tick", "victim_first_tick", "first", "round"]
_dframe = pd.DataFrame(
    [
        (510, S1, S3, "ak47", True, 0.16, 500, 505, "killer", 1),
        (700, S2, S4, "awp", False, 0.4, np.nan, 690, "victim", 1),
        # a kill after the round ended carries the NEXT round's number but
        # its tick is still inside the window: it belongs here
        (820, S4, S2, "ak47", False, 0.1, 815, np.nan, "killer", 2),
        (2500, S1, S2, "ak47", False, 0.1, 2495, 2496, "killer", 2),  # another round
    ],
    columns=_dcols,
)
_rdu = core.round_duels(_dframe, (100, 830), 64)


def test_round_duels():
    assert [(z["killer"], z["victim"]) for z in _rdu] == [(S1, S3), (S2, S4), (S4, S2)], "round_duels: all duels of ALL players inside the window, earliest first"
    assert (
        all(z["tick"] != 2500 for z in _rdu)
        and core.round_duels(_dframe, (2000, 3000), 64)[0]["tick"] == 2500
        and len(core.round_duels(_dframe, (2000, 3000), 64)) == 1
    ), "round_duels: a duel outside the window never shows up (no duels from another round)"
    assert (
        _rdu[0]["a"] == 500 - 48 and _rdu[0]["b"] == 510 + 64
        and _rdu[1]["a"] == 690 - 48  # only the victim shot: his first shot counts
        and _rdu[1]["b"] == 700 + 64
        and _rdu[2]["a"] == 815 - 48
        and _rdu[2]["b"] == 830
    ), "round_duels: the segment starts at the FIRST shot of either duelist (-0.75 s), ends 1 s after the kill"
    assert (
        _rdu[0]["weapon"] == "ak47" and _rdu[0]["hs"] is True and _rdu[0]["dur"] == 0.16
        and core.round_duels(pd.DataFrame(), (0, 1), 64) == []
        and core.round_duels(None, (0, 1), 64) == []
    ), "round_duels: weapon / headshot / duration pass through, empty input is safe"


def test_round_winners():
    # rr.dem: only Charlie (CT) dies, Delta survives -> no winner guessed
    assert core.round_winners(core.load_demo("rr.dem")) == {}, "round_winners: no full elimination -> no winner"
    # rrw.dem: Delta (CT) dies too -> CT is eliminated in round 1 -> T won
    _rrw = make_match()
    _rrw["ticks"] = pd.DataFrame(_rr_rows)
    _rrw["events"].update(_rr_events)
    _rrw["events"]["player_death"] = pd.concat(
        [
            _rrw["events"]["player_death"],
            pd.DataFrame(
                {
                    "tick": [520],
                    "attacker_steamid": pd.array([int(S2)], dtype="float64"),
                    "attacker_name": ["Bravo"],
                    "user_steamid": pd.array([int(S4)], dtype="float64"),
                    "user_name": ["Delta"],
                    "weapon": ["weapon_ak47"],
                    "headshot": [False],
                    "distance": [12.7],
                    "total_rounds_played": [0],
                    "is_warmup_period": [False],
                }
            ),
        ],
        ignore_index=True,
    )
    FakeParser.demos["rrw.dem"] = _rrw
    assert core.round_winners(core.load_demo("rrw.dem")) == {1: "T"}, "round_winners: side fully eliminated -> the other side won"
    # a world death (attacker unknown) also counts as a lost player
    _rrw2 = make_match()
    _rrw2["ticks"] = pd.DataFrame(_rr_rows)
    _rrw2["events"].update(_rr_events)
    _rrw2["events"]["player_death"] = pd.concat(
        [
            _rrw2["events"]["player_death"],
            pd.DataFrame(
                {
                    "tick": [520],
                    "attacker_steamid": pd.array([0], dtype="float64"),
                    "attacker_name": ["world"],
                    "user_steamid": pd.array([int(S4)], dtype="float64"),
                    "user_name": ["Delta"],
                    "weapon": ["world"],
                    "headshot": [False],
                    "distance": [12.7],
                    "total_rounds_played": [0],
                    "is_warmup_period": [False],
                }
            ),
        ],
        ignore_index=True,
    )
    FakeParser.demos["rrw2.dem"] = _rrw2
    assert core.round_winners(core.load_demo("rrw2.dem")) == {1: "T"}, "round_winners: world/suicide deaths still eliminate the victim's side"


_rnames = {S1: "alpha", S2: "Bravo", S3: "charlie", S4: "Delta"}
_rside = lambda s: "T" if s in (S1, S2) else "CT"
_rpl = round_payload(
    _rrd,
    names=_rnames,
    side_of=_rside,
    radar=None,
    duels=_rdu[:1],
    rounds=[{"n": 1, "win": "CT"}, {"n": 2, "win": "unknown"}],
    at=460,
    sel=0,
)
_dz = _rpl["duels"][0]


def test_payload():
    assert [p["name"] for p in _rpl["players"]] == ["charlie", "Delta", "alpha", "Bravo"], "payload: split by side (CT first, then T), by nickname (case-insensitive) inside a team"
    _rrd_rev = dict(_rrd, frames=dict(reversed(list(_rrd["frames"].items()))))
    assert (
        [p["sid"] for p in round_payload(_rrd_rev, names=_rnames, side_of=_rside).get("players")]
        == [p["sid"] for p in _rpl["players"]]
        and [p["name"] for p in round_payload(_rrd, names=_rnames, side_of=lambda s: "CT" if s == S2 else "?")["players"]][0]
        == "Bravo"
    ), "payload: the order never depends on the frame order; a side with nobody on it goes last"
    assert (
        all("role" not in p for p in _rpl["players"])
        and "myIdx" not in _rpl
        and "marks" not in _rpl
    ), "payload: nobody is special - no role / me / marks in the model"
    _p1 = next(p for p in _rpl["players"] if p["sid"] == S1)
    assert (
        _p1["side"] == "T"
        and next(p for p in _rpl["players"] if p["sid"] == S3)["side"] == "CT"
    ), "payload: sides passed through (team colours + kill-mark colouring)"
    assert (
        all(isinstance(v, int) for v in _p1["x"][:20])
        and _p1["hp"][0] == 100
        and _p1["hp"][405] == 34
        and _p1["al"][410] == 1
        and _p1["al"][500] == 0
        and _p1["w"] == [[100, "ak47"], [400, "awp"]]
    ), "payload: positions are whole units, hp / alive arrays, weapon changes (short names)"
    _fw, _fire = _rpl["keys"].index("FORWARD"), _rpl["keys"].index("FIRE")
    assert (
        (_p1["kb"][0] >> _fw) & 1 == 1
        and (_p1["kb"][400] >> _fire) & 1 == 1  # tick 500
        and (_p1["kb"][0] >> _fire) & 1 == 0
        and "keys" not in _p1
    ), "payload: held keys are ONE bitmask per sample, for every player"
    assert _p1["kb"][500] == 0 and _p1["kb"][100] != 0, "payload: input of a dead player is zeroed (parser artifact), alive input is kept"
    _pi = {p["sid"]: i for i, p in enumerate(_rpl["players"])}
    assert (
        _rpl["shots"][0][1] == _pi[S1]
        and _rpl["kills"] == [[510, _pi[S1], _pi[S3], "ak47", True]]
        and _rpl["hits"] == [[505, _pi[S1], _pi[S3]]]
        and _rpl["at"] == 460
    ), "payload: events are player indices; start tick passes through"
    assert (
        {k: _dz[k] for k in ("a", "b", "tk", "ki", "vi", "kn", "vn", "w", "hs", "dur", "first", "kft", "vft")}
        == {
            "a": 452,
            "b": 574,
            "tk": 510,
            "ki": _pi[S1],
            "vi": _pi[S3],
            "kn": "alpha",
            "vn": "charlie",
            "w": "ak47",
            "hs": True,
            "dur": 0.2,
            "first": "killer",
            "kft": 500,
            "vft": 505,
        }
    ), "payload: a duel card carries killer / victim (index + name), segment, weapon"
    assert _rpl["rounds"] == [[1, "CT", ""], [2, "", ""]], "payload: rounds row for the match bar (unknown winner -> '')"
    _du1 = [{"a": 400, "b": 700, "tick": 510, "killer": S1, "victim": S3, "weapon": "ak47"}]
    assert (
        _rpl["sel"] == 0
        and round_payload(_rrd, names=_rnames, side_of=_rside, duels=_du1, sel=5)["sel"] == -1
        and round_payload(_rrd, names=_rnames, side_of=_rside, duels=_du1, sel=0)["at"] == 400
    ), "payload: sel clamped, at defaults to the selected duel's segment start"
    assert (
        round_payload(
            _rrd, names=_rnames, side_of=_rside,
            duels=[{"a": -1, "b": 99999, "tick": 510, "killer": S1, "victim": S3}],
        )["duels"][0]["a"] == _rrd["t0"]
    ), "payload: duel segment clamped into the round window"
    assert (
        round_payload(
            _rrd, names={**_rnames, "999": "Ghost"}, side_of=_rside,
            duels=[{"a": 400, "b": 700, "tick": 510, "killer": "999", "victim": S3}],
        )["duels"][0]["ki"] == -1
    ), "payload: a duelist outside the frames keeps his name, index -1"
    assert (
        _rpl["radar"] is None
        and _rpl["gridb"]["minx"] < _rpl["gridb"]["maxx"]
    ), "payload: no radar -> unit-grid bounds instead"
    assert (
        round_payload({"t0": 0, "t1": 1, "step": 1, "tickrate": 64, "round": 1,
                        "frames": {}, "keys": [], "shots": [], "kills": [],
                        "hits": []}, names={}, side_of=_rside)["players"] == []
    ), "payload: no players at all -> empty list (UI shows a hint)"


def test_duel_telemetry():
    _tel = core.duel_telemetry(
        _rrd["frames"][S1], _rrd["frames"][S3], (400, 500), _rrd["t0"], _rrd["step"], 64
    )
    _n_tel = len(_tel["a"]["sp"])
    assert _tel["i0"] == 300 and _n_tel == 101 and len(_tel["d"]) == _n_tel, "duel_telemetry: one value per replay sample of the window, i0 = first sample"
    assert _tel["a"]["sp"][5] == 64 and _tel["b"]["sp"][5] == 0, "duel_telemetry: speed = the position delta (1 unit/tick = 64 u/s), a standing player 0"
    assert _tel["a"]["tn"][5] == 0 and _tel["d"][0] > _tel["d"][-1], "duel_telemetry: a steady crosshair turns 0 deg/s; the distance shrinks as S1 closes in"
    assert _tel["a"]["er"][5] is not None and _tel["a"]["er"][5] < 2.0, "duel_telemetry: aiming straight at the enemy -> a small crosshair-to-head angle"
    _tel2 = core.duel_telemetry(
        _rrd["frames"][S1], _rrd["frames"][S3], (600, 700), _rrd["t0"], _rrd["step"], 64
    )
    assert _tel2["a"]["sp"][5] is None and _tel2["a"]["er"][5] is None, "duel_telemetry: a dead player has no speed / turn / aim error (parser artifact masked)"
    assert (
        core.duel_telemetry(_rrd["frames"][S1], _rrd["frames"][S3], (5000, 6000), _rrd["t0"], _rrd["step"], 64) == {}
        and core.duel_telemetry(None, None, (0, 1), 0, 1, 64) == {}
    ), "duel_telemetry: nothing to show -> empty"
    assert (
        "tel" in _dz
        and _dz["tel"]["i0"] == _dz["a"] - _rrd["t0"]
        and set(_dz["tel"]) == {"i0", "k", "v", "d"}
        and set(_dz["tel"]["k"]) == {"sp", "tn", "er"}
    ), "payload: every duel carries its telemetry for killer and victim"
    assert _rpl["out"] == {"win": "CT", "why": "", "end": None, "inferred": False} and _rpl["bomb"] == [], "payload: no outcome in the demo -> the rounds bar's winner fills the gap, no reason"
    _rrd_i = core.round_replay_data(_di, 1)
    _rpl_i = round_payload(_rrd_i, names=_rnames, side_of=_rside)
    _pi_i = {p["sid"]: i for i, p in enumerate(_rpl_i["players"])}
    assert (
        _rpl_i["out"] == {"win": "CT", "why": "defuse", "end": 700, "inferred": False}
        and _rpl_i["bomb"] == [[300, "plant", _pi_i[S1]], [690, "defuse", _pi_i[S2]]]
    ), "payload: how the round ended (winner, reason, end tick) + the bomb events with player indices"
    assert (
        round_payload(_rrd, names=_rnames, side_of=_rside,
                      rounds=[{"n": 1, "win": "T", "why": "bomb"}, {"n": 2, "win": "CT", "why": "nonsense"}])["rounds"]
        == [[1, "T", "bomb"], [2, "CT", ""]]
    ), "payload: a round bar chip carries how the round ended (unknown reason -> '')"
    assert "NaN" not in json.dumps(_rpl), "payload: JSON-serialisable (NaN never leaks into the model)"


def test_economy():
    _eco_rows = []
    for _r in _rr_rows:
        _t = _r["tick"]
        _eco_rows.append(
            {
                **_r,
                "armor_value": 100 if _t < 300 else 40,
                "has_helmet": _r["name"] == "Alpha",   # (steam ids are float-rounded: tell players apart by name)
                "has_defuser": _r["name"] == "Bravo",
                "is_defusing": False,
                "balance": 3000 if _t < 200 else 150,
                "current_equip_value": 4750,
                "inventory": ["knife", "AK-47", "Flashbang", "Flashbang", "Smoke Grenade"]
                + (["C4 Explosive"] if _r["name"] == "Charlie" else []),
            }
        )
    _eco = make_match()
    _eco["ticks"] = pd.DataFrame(_eco_rows)
    _eco["events"].update(_rr_events)
    FakeParser.demos["rr_eco.dem"] = _eco
    _red = core.round_replay_data(core.load_demo("rr_eco.dem"), 1)
    assert all(c in _red["frames"][S1].columns for c in core.ECONOMY_PROPS), "round_replay_data: the economy columns come through when the parser has them"
    _epl = round_payload(_red, names=_rnames, side_of=_rside, duels=_rdu[:1], thr=34.0)
    _ep = {p["sid"]: p for p in _epl["players"]}
    assert (
        _ep[S1]["ar"] == [[100, 100, 1], [300, 40, 1]]
        and _ep[S2]["ar"][0][2] == 0 and _ep[S2]["kit"] == [[100, 1]] and "kit" not in _ep[S1]
        and _ep[S1]["mo"] == [[100, 3000], [200, 150]] and _ep[S1]["ev"] == [[100, 4750]]
    ), "payload: armor + helmet, money, gear value, defuse kit as change lists (a run per change, not per sample)"
    assert (
        _ep[S1]["inv"] == [[100, ["knife", "ak47", "flashbang", "flashbang", "smokegrenade"]]]
        and "c4" in _ep[S3]["inv"][0][1] and _ep[S1]["w"][0][1] == "ak47"
    ), "payload: the inventory as icon keys (duplicates kept), the bomb carrier has the c4; the weapon in hands as a key"
    assert (
        all("dg" not in p for p in _epl["players"])
        and _epl["thr"] == 34.0
        and round_payload(_rrd, names=_rnames, side_of=_rside)["thr"] is None
    ), "payload: is_defusing stays off for everyone -> no dg runs; the moving threshold passes through"
    assert all(k not in p for p in _rpl["players"] for k in ("ar", "mo", "ev", "inv", "kit", "dg")), "payload: a demo without the economy props has no ar / mo / ev / inv / kit / dg arrays"
    # with a two-level radar the payload carries both images + level bits
    _rd1 = tempfile.mkdtemp(prefix="cs2aa_radar1_")
    os.makedirs(os.path.join(_rd1, "radars"))
    _tiny_png(os.path.join(_rd1, "radars", "de_r.png"), 16)
    _tiny_png(os.path.join(_rd1, "radars", "de_r_lower.png"), 16)
    with open(os.path.join(_rd1, "map_data.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "de_r": {
                    "pos_x": -100.0,
                    "pos_y": 100.0,
                    "scale": 2.0,
                    "verticalsections": {
                        "lower": {"AltitudeMin": -500.0, "AltitudeMax": -100.0}
                    },
                }
            },
            fh,
        )
    _old_env1 = os.environ.get("AIMAUDIT_RADAR_DIR")
    os.environ["AIMAUDIT_RADAR_DIR"] = _rd1
    try:
        _rrad = core.load_radar("de_r")
        _rpl2 = round_payload(
            _rrd, names=_rnames, side_of=_rside, radar=_rrad
        )
        assert (
            _rpl2["radar"] is not None
            and _rpl2["radar"]["png"].startswith("data:image/png;base64,")
            and _rpl2["radar"]["lo"] is not None
            and _rpl2["gridb"] is None
            and _rpl2["radar"]["size"] > 0
        ), "payload: radar -> image data URIs + calibration, no grid"
        _p1b = next(p for p in _rpl2["players"] if p["sid"] == S1)
        assert (
            all("lvl" in p for p in _rpl2["players"])
            and all(v == 0 for v in _p1b["lvl"][:5])
        ), "payload: two-level radar -> every player has level bits"
    finally:
        if _old_env1 is None:
            os.environ.pop("AIMAUDIT_RADAR_DIR", None)
        else:
            os.environ["AIMAUDIT_RADAR_DIR"] = _old_env1
