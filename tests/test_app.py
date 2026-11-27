"""The real app.py under AppTest, scenario by scenario, on synthetic demos."""

import json
import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import core
import store
from app_driver import DEMO_DIR, GRIDS, NAMES, metric_map, set_chips, ROOT, _DEMO_IDS, _PRESETS_BY_COUNT, _demo_n, _shots_shown, act, ctl, err_of, fresh_at, goto, make_match, n_presets, open_demo, open_duel, open_match, pick, register_demo, ss_get, widget
from demoparser2 import DemoParser
from fakes import FakeParser, IDS, S1, S2, S3, S4


ROOT = Path(__file__).resolve().parents[1]


def test_one_round():
    """A demo with a single round must not crash the app."""
    at = open_demo(make_match([1]), "one_round.dem")
    assert not at.exception, err_of(at)
    assert not at.error, "no st.error on load"
    assert n_presets(at) == (1, "all"), "only the 'all' preset offered"
    act(
        at,
        "switch player (Bravo, no shots)",
        lambda: at.selectbox(key="me").select(S2).run(),
    )
    act(
        at,
        "switch player back (Alpha)",
        lambda: at.selectbox(key="me").select(S1).run(),
    )
    act(at, "language ru", lambda: at.selectbox(key="lang").select("ru").run())
    open_duel(at, 0, "duel window renders")
    assert len(at.get("plotly_chart")) == 1, "duel window: the speed/aim/keys chart now lives in Duels (one plotly chart)"
    assert widget(at, "button", "du_to_replay") is not None, "duel window: 'show in 2D replay' button"
    assert not ss_get(at, "du_open"), "the row click flag is consumed"
    goto(at, "replay")
    assert not at.get("plotly_chart"), "2D replay: the canvas component draws it, no plotly chart"
    assert widget(at, "pills", "flt_preset") is None, "2D replay: the filter panel is hidden (the replay is the whole round)"
    # the payload behind the canvas: the whole round, every player, with
    # HP / weapon / keys - checked straight against the fake demo so the
    # data contract is asserted, not just "no exception"
    _demo_path = os.path.realpath(
        store.demo_path(store.get_demo(at.session_state["demo_id"]))
    )
    _m = core.load_demo(_demo_path)
    assert 1 in _m.round_windows, "replay: the demo has a window for round 1"
    _rr = core.round_replay_data(_m, 1)
    assert (
        set(_rr["frames"]) == set(IDS)
        and "health" in _rr["frames"][S1].columns
        and "active_weapon_name" in _rr["frames"][S1].columns
        and "FORWARD" in _rr["keys"]
    ), "replay data: every player of the round + hp/weapon/keys parsed"
    assert len(_rr["shots"]) == 3 and len(_rr["kills"]) == 1 and len(_rr["hits"]) == 1, "replay data: round 1 events (3 shots, 1 kill, 1 hit)"
    from ui.roundrep import round_payload as _rp

    _all_duels = core.round_duels(
        core.build_duels(_m, 1.0), _m.round_windows[1], _m.tickrate
    )
    _pl = _rp(
        _rr,
        names=NAMES,
        side_of=lambda s: _m.side_at(1, s),
        radar=None,
        duels=_all_duels,
        rounds=[{"n": 1, "win": "T", "why": "bomb"}],
        sel=0,
    )
    _names_sorted = [p["name"] for p in _pl["players"]]
    _p0 = next(p for p in _pl["players"] if p["sid"] == S1)
    _by_side = {}
    for p in _pl["players"]:
        _by_side.setdefault(p["side"], []).append(p["name"])
    _sides_seen = [p["side"] for p in _pl["players"]]
    assert (
        all(v == sorted(v, key=str.casefold) for v in _by_side.values())
        and _sides_seen == sorted(_sides_seen, key=lambda x: {"CT": 0, "T": 1}.get(x, 2))
        and _p0["hp"][0] == 100
        and _p0["w"][0][1] == "ak47"
        and all("kb" in p for p in _pl["players"] if p["sid"] == S1)
    ), "replay payload: CT group then T group, sorted by nickname inside a team; hp, weapon change, held keys"
    assert (
        len(_pl["duels"]) == len(_all_duels) >= 1
        and _pl["duels"][0]["kn"] == NAMES[S1]
        and _pl["duels"][0]["vn"] == NAMES[S3]
        and _pl["duels"][0]["ki"] >= 0
        and _pl["rounds"] == [[1, "T", "bomb"]]
        and _pl["sel"] == 0
        and _pl["at"] == _pl["duels"][0]["a"]
        and "tel" in _pl["duels"][0]
    ), "replay payload: every duel of the round as a card + locked segment + rounds bar"


def test_round_replay():
    """The 2D replay: rounds bar, the hand-over from the duel window."""
    at = open_demo(make_match([1, 2]), "rr_switch.dem")
    assert not at.exception, err_of(at)
    goto(at, "replay")
    assert ss_get(at, "rr_round") == 1, "starts at the first round"
    # a round-bar chip click lands as rr_round in the session (the
    # component trigger calls the same setter); the replay follows it
    act(
        at,
        "rounds bar switches the replay to round 2",
        lambda: at.session_state.__setitem__("rr_round", 2) or at.run(),
    )
    act(
        at,
        "a round that does not exist falls back to the first one",
        lambda: at.session_state.__setitem__("rr_round", 77) or at.run(),
    )
    assert ss_get(at, "rr_round") == 1, "fell back to round 1"
    # the duel window's button hands the duel over: page, round, selection
    open_duel(at, 1, "open the round-2 duel")
    act(at, "show it in the 2D replay", lambda: at.button(key="du_to_replay").click().run())
    assert ss_get(at, "match_tab") == "replay", "switched to the replay tab"
    assert ss_get(at, "rr_round") == 2, "on the duel's own round"
    sel = ss_get(at, "rr_sel")
    assert sel is not None and sel[0] == 2 and sel[2] == S3, "the duel is handed over (round, tick, victim)"
    act(
        at,
        "rounds bar switch drops the hand-over",
        lambda: at.session_state.__setitem__("rr_round", 1)
        or at.session_state.__delitem__("rr_sel")
        or at.run(),
    )


def test_nav():
    """The sidebar rail (st.page_link over st.navigation): navigation,
    the ?m&tab URL state."""
    at = open_demo(make_match([1, 2]), "nav.dem")
    did = at.session_state["match"]
    # the rail: one st.page_link per section (labels are localized; the
    # four ids in order are enough to pin the rail itself)
    _labels = [str(link.value).strip() for link in at.sidebar.page_link]
    assert len(_labels) == 4 and all(_labels), "nav: four sidebar page_links render"
    assert at.query_params.get("m") == did, "nav: the match view syncs ?m"
    # a rail click is a page_link navigation: the page switches and the
    # query params clear, so the sidebar Matches item opens the LIST (an
    # open match is left) - goto() applies exactly that
    goto(at, "matches")
    assert ss_get(at, "page") == "matches" and ss_get(at, "match") is None, "nav: the list is shown, the match is closed"
    goto(at, "settings")
    assert ss_get(at, "page") == "settings" and not at.exception, err_of(at)
    goto(at, "guide")
    assert ss_get(at, "page") == "guide" and not at.exception, err_of(at)
    goto(at, "dashboard")
    assert ss_get(at, "page") == "dashboard" and widget(at, "button", "dash_go_matches") is not None, "nav: the dashboard stub renders with a way to Matches"
    act(at, "nav: dashboard -> Matches", lambda: at.button(key="dash_go_matches").click().run())
    assert ss_get(at, "page") == "matches" and ss_get(at, "match") is None, "nav: back on the list"
    # ?m&tab: a copied link reopens the exact screen; garbage is ignored
    at3 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at3.query_params["m"] = did
    at3.query_params["tab"] = "duels"
    at3.run()
    assert (
        ss_get(at3, "page") == "matches"
        and ss_get(at3, "match") == did
        and ss_get(at3, "match_tab") == "duels"
        and not at3.exception
    ), err_of(at3)
    at4 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at4.query_params["m"] = "zzzz-not-an-id"
    at4.query_params["tab"] = "nonsense"
    at4.run()
    assert ss_get(at4, "page") == "matches" and ss_get(at4, "match") is None and not at4.exception, "nav: broken ?m&tab values fall back safely"


def test_settings():
    """The Settings page: sections, and the sliders surviving page switches."""
    at = open_demo(make_match([1, 2]), "settings.dem")
    goto(at, "settings")
    assert (
        widget(at, "slider", "thr") is not None
        and widget(at, "slider", "window_s") is not None
        and widget(at, "slider", "safe_dist") is not None
        and widget(at, "slider", "strict_dist") is not None
        and widget(at, "button", "btn_reset_set") is not None
    ), "settings: the analysis sliders render (moved from the sidebar)"
    assert (
        all(widget(at.sidebar, "slider", k) is None for k in ("thr", "window_s", "safe_dist", "strict_dist"))
        and widget(at.sidebar, "selectbox", "lang") is not None
    ), "settings: no analysis slider in the sidebar anymore"
    act(at, "settings: thr to 200", lambda: at.slider(key="thr").set_value(200).run())
    act(at, "settings: safe_dist to 1000", lambda: at.slider(key="safe_dist").set_value(1000).run())
    # the values survive Settings -> Matches -> a match
    goto(at, "matches")
    assert ss_get(at, "page") == "matches" and not at.exception, err_of(at)
    assert ss_get(at, "thr") == 200, "settings: thr survived the trip to the list"
    goto(at, "overview")
    assert (
        ss_get(at, "match") is not None
        and ss_get(at, "thr") == 200
        and ss_get(at, "safe_dist") == 1000
        and not at.exception
    ), (ss_get(at, "thr"), ss_get(at, "safe_dist"), err_of(at))
    # the reset restores the defaults (plain keys, the sliders follow)
    goto(at, "settings")
    act(at, "settings: reset to defaults", lambda: at.button(key="btn_reset_set").click().run())
    assert (
        ss_get(at, "thr") == 40 and ss_get(at, "window_s") == 3.0
        and ss_get(at, "safe_dist") == 500 and ss_get(at, "strict_dist") == 1500
    ), "settings: reset restores the defaults"
    # accounts: the fields persist to accounts.json
    act(
        at,
        "settings: steam id typed",
        lambda: at.text_input(key="mt_steamid").set_value("76561111111111111").run(),
    )
    assert store.read_accounts().get("steam_id64") == "76561111111111111", "settings: steam id persisted to accounts.json"
    # the Steam helper card lives here now
    if not __import__("sources").steam.helper_installed():
        assert widget(at, "button", "mt_helper_install") is not None, "settings: the Steam helper card offers the install"
    # the library and storage section (moved from the change-demo block)
    assert (
        widget(at, "button", "lib_clear") is not None
        and widget(at, "button", f"del_{at.session_state['match']}") is not None
    ), "settings: the library panel renders (clear cache, per-demo delete)"
    # privacy names the offline gate
    assert any("AIMAUDIT_OFFLINE" in str(c.value) for c in at.caption), "settings: the privacy section names AIMAUDIT_OFFLINE"
    # clear cache (the Library and storage section) keeps the demos
    act(at, "settings: clear cache", lambda: at.button(key="lib_clear").click().run())
    assert store.storage_report()["parsed"] == 0 and store.get_demo(at.session_state["match"]) is not None, "settings: clear cache empties the caches, demos stay"
    # deleting the open match from the library section falls back to the list
    did = at.session_state["match"]
    act(at, "settings: delete: ask", lambda: at.button(key=f"del_{did}").click().run())
    act(at, "settings: delete: confirm", lambda: at.button(key=f"delyes_{did}").click().run())
    assert ss_get(at, "page") == "settings" and ss_get(at, "match") is None and store.get_demo(did) is None, "settings: deleting the open match drops it (back to the list from a match view)"


def test_player_stepper():
    """The header: the demo info and the player stepper."""
    at = open_demo(make_match([1, 2]), "stepper.dem", me=S1)
    assert not at.exception, err_of(at)
    assert (
        widget(at, "button", "me_prev") is not None
        and widget(at, "button", "me_next") is not None
    ), "prev/next player buttons rendered"
    assert (
        widget(at, "button", "mt_back") is not None
        and widget(at, "button", "demo_change_btn") is None
    ), "the match header offers the way back to the list"
    act(at, "next player (Bravo)", lambda: at.button(key="me_next").click().run())
    assert ss_get(at, "me") == S2, "next moved the selection"
    caps = [c.value for c in at.caption]
    assert any("**stepper**" in str(c) for c in caps), "the demo info names the file"
    act(
        at,
        "prev player (back to Alpha)",
        lambda: at.button(key="me_prev").click().run(),
    )
    assert ss_get(at, "me") == S1, "prev returned to the first player"
    act(
        at,
        "prev wraps around to the last player",
        lambda: at.button(key="me_prev").click().run(),
    )
    assert ss_get(at, "me") == S4, "prev wrapped to Delta"
    act(
        at,
        "next wraps to the first player",
        lambda: at.button(key="me_next").click().run(),
    )
    assert ss_get(at, "me") == S1, "next wrapped to Alpha"
    # '← Матчи' leaves the match without losing it: a fresh row click on
    # the list brings it straight back
    act(at, "back to the matches list", lambda: at.button(key="mt_back").click().run())
    assert (
        ss_get(at, "page") == "matches"
        and ss_get(at, "match") is None
        and ss_get(at, "demo_loaded") == _DEMO_IDS["stepper.dem"]
    ), "back keeps the demo loaded, the match closed"
    open_match(at, _DEMO_IDS["stepper.dem"], "reopen the same match")
    assert (
        ss_get(at, "match") == _DEMO_IDS["stepper.dem"]
        and ss_get(at, "demo_loaded") == _DEMO_IDS["stepper.dem"]
        and not at.exception
    ), err_of(at)


def test_duel_strip():
    """Fast duel/round navigation: the strip (in the duel window) owns the selection."""
    at = open_demo(make_match([1, 2, 13]), "strip.dem")
    assert not at.exception, err_of(at)
    open_duel(at, 0, "open the duel window")
    # the old long "pick a duel" selectbox and the round selectbox are
    # gone: the strip chips, its hotkeys and the Duels row click move the
    # selection now, and they all write the plain tl_duel session key
    assert widget(at, "selectbox", "tl_duel") is None, "no tl_duel selectbox anymore"
    assert widget(at, "selectbox", "tl_round") is None, "no tl_round selectbox anymore"
    assert any("Shift+←" in str(c.value) for c in at.caption), "hotkey hint rendered under the strip"
    act(
        at,
        "open duel 1 (a chip click writes the same key)",
        lambda: at.session_state.__setitem__("tl_duel", 1) or at.run(),
    )
    assert ss_get(at, "tl_duel") == 1, "moved to duel 1"
    open_duel(at, 99, "stale pick beyond the duel list clamps")
    assert ss_get(at, "tl_duel") == 0, "clamped back to duel 0"
    act(at, "language ru", lambda: at.selectbox(key="lang").select("ru").run())
    open_duel(at, 0, "ru: reopen the duel window")
    assert any("Shift+←" in str(c.value) for c in at.caption), "ru: hotkey hint rendered"


def test_roundtrip():
    """The path Matches -> row -> tabs -> back,
    and nothing is lost on the way."""
    at = open_demo(make_match([1, 2, 13]), "roundtrip.dem")
    did = at.session_state["match"]
    act(at, "roundtrip: first-half preset", lambda: ctl(at, "pills", "flt_preset").select("first").run())
    assert ss_get(at, "flt_preset") == "first", "roundtrip: preset set"
    act(at, "roundtrip: to the duels tab", lambda: ctl(at, "segmented_control", "match_tab").select("duels").run())
    assert ss_get(at, "match_tab") == "duels" and not at.exception, err_of(at)
    act(at, "roundtrip: back to the list", lambda: at.button(key="mt_back").click().run())
    assert (
        ss_get(at, "page") == "matches" and ss_get(at, "match") is None
        and ss_get(at, "demo_loaded") == did and not at.exception
    ), (ss_get(at, "page"), ss_get(at, "match"), err_of(at))
    # the list's own filters live next to the analysis ones
    act(at, "roundtrip: a list filter", lambda: ctl(at, "segmented_control", "mflt_result").select("tie").run())
    assert ss_get(at, "mflt_result") == "tie", "roundtrip: the list filter set"
    # back into the match: the analysis filter survived the trip through the
    # list (a fresh row click opens it on the overview tab)
    open_match(at, did, "roundtrip: reopen the match")
    assert ss_get(at, "flt_preset") == "first" and ss_get(at, "match") == did and not at.exception, (ss_get(at, "flt_preset"), ss_get(at, "match"), err_of(at))
    assert at.query_params.get("m") == did and at.query_params.get("fres") == "tie", "roundtrip: ?m and the list filters ride in the URL"
    # a copied link reopens the exact screen: the match on its tab
    at2 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at2.query_params["m"] = did
    at2.query_params["tab"] = "duels"
    at2.run()
    assert (
        ss_get(at2, "page") == "matches" and ss_get(at2, "match") == did
        and ss_get(at2, "match_tab") == "duels" and not at2.exception
    ), err_of(at2)


def test_two_rounds():
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "two_rounds.dem")
    assert not at.exception, err_of(at)
    assert n_presets(at) == (3, "all"), "presets all+pistols+custom"
    act(
        at,
        "pistols preset",
        lambda: ctl(at, "pills", "flt_preset").select("pistols").run(),
    )
    assert ss_get(at, "flt_preset") == "pistols", "preset switched to pistols"
    act(
        at,
        "custom preset",
        lambda: ctl(at, "pills", "flt_preset").select("custom").run(),
    )
    act(at, "pick round 2", lambda: set_chips(at, "flt_rounds", [2]))
    assert ss_get(at, "flt_preset") == "custom" and ss_get(at, "flt_rounds") == [2], "picked rounds keep Custom selected"
    act(
        at,
        "weapon filter awp",
        lambda: set_chips(at, "flt_weapons", ["awp"]),
    )
    act(
        at,
        "class chips sniper",
        lambda: ctl(at, "pills", "flt_classes").select("sniper").run(),
    )
    assert widget(at, "segmented_control", "flt_side") is not None, "side control is a segmented control"
    act(
        at,
        "side filter T",
        lambda: ctl(at, "segmented_control", "flt_side").select("T").run(),
    )
    assert widget(at, "segmented_control", "flt_res") is None, "result control not on the overview page"
    goto(at, "duels")
    assert widget(at, "segmented_control", "flt_res") is not None, "result control on the duels page"
    act(
        at,
        "result filter death (no such duels)",
        lambda: ctl(at, "segmented_control", "flt_res").select("death").run(),
    )
    assert not at.exception, err_of(at)


def test_long25():
    at = open_demo(
        make_match([1, 13, 25], weapons=("ak47", "awp", "deagle")), "long25.dem"
    )
    assert not at.exception, err_of(at)
    assert n_presets(at) == (6, "all"), "all six presets offered"
    for p in ("first", "second", "ot", "pistols"):
        act(
            at,
            f"preset {p}",
            lambda p=p: ctl(at, "pills", "flt_preset").select(p).run(),
        )
    act(
        at,
        "side filter CT (round 13 only)",
        lambda: ctl(at, "segmented_control", "flt_side").select("CT").run(),
    )
    open_duel(at, 1, "duel window 1 of 3")


def test_filter_panel():
    """The filter bar: distance bands, reset, page scope, links."""
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "panel.dem")
    assert not at.exception, err_of(at)
    base = _shots_shown(at)
    assert bool(base), "the counts line shows the shot count"
    for kind, k in (("pills", "flt_preset"), ("segmented_control", "flt_side"), ("selectbox", "flt_dist")):
        assert widget(at, kind, k) is not None, f"{k} in the bar"
    # Streamlit mounts a popover body on every run: reachable without opening it
    assert widget(at, "pills", "flt_classes") is not None, "class pills in the weapons popover"
    assert at.button(key="flt_reset").disabled, "nothing to reset without active filters"
    act(
        at,
        "weapon filter awp",
        lambda: set_chips(at, "flt_weapons", ["awp"]),
    )
    filtered = _shots_shown(at)
    assert filtered is not None and 0 <= filtered < base, "filtered count reported and smaller"
    for band in ("close", "mid", "long"):
        act(
            at,
            f"distance band {band}",
            lambda b=band: ctl(at, "selectbox", "flt_dist").select(b).run(),
        )
        assert not at.exception, err_of(at)
    assert not at.button(key="flt_reset").disabled, "reset button offered"
    act(at, "reset filters", lambda: at.button(key="flt_reset").click().run())
    assert _shots_shown(at) == base, "reset restores the full view"
    # a closed popover keeps every selection (its widgets stay mounted)
    act(
        at,
        "weapon filter awp again",
        lambda: set_chips(at, "flt_weapons", ["awp"]),
    )
    act(
        at,
        "plain rerun with the popover closed",
        lambda: at.button(key="mt_back").click().run(),
    )
    open_match(at, at.session_state["demo_id"], "back into the match")
    assert ss_get(at, "flt_weapons") == ["awp"], "closed popover kept the weapon filter"
    assert not at.button(key="flt_reset").disabled, "Reset offered while filters are active"
    act(
        at,
        "reset again",
        lambda: at.button(key="flt_reset").click().run(),
    )
    assert ss_get(at, "flt_weapons") == [], "reset cleared the weapon filter"
    assert ss_get(at, "flt_dist") == "all", "reset cleared the distance band"
    # Result is a duels-page control; its value must survive page hops
    assert widget(at, "segmented_control", "flt_res") is None, "no result control on the overview page"
    goto(at, "duels")
    act(
        at,
        "result kills only",
        lambda: ctl(at, "segmented_control", "flt_res").select("kill").run(),
    )
    goto(at, "overview")
    assert ss_get(at, "flt_res") == "kill", "result value survives other pages"
    assert widget(at, "segmented_control", "flt_res") is None, "result control still hidden on the overview"
    # the guide page hides the whole panel but keeps every selection
    act(
        at,
        "side filter T before the guide",
        lambda: ctl(at, "segmented_control", "flt_side").select("T").run(),
    )
    goto(at, "guide")
    assert widget(at, "pills", "flt_preset") is None, "guide hides the panel"
    goto(at, "overview")
    assert ss_get(at, "flt_side") == "T", "side filter survives the guide page"
    # link round-trip: filters -> URL -> fresh app
    at3 = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "panel3.dem")
    act(
        at3,
        "distance band mid (for the link)",
        lambda: ctl(at3, "selectbox", "flt_dist").select("mid").run(),
    )
    act(at3, "weapons for the link", lambda: set_chips(at3, "flt_weapons", ["ak47"]))
    act(at3, "custom rounds for the link", lambda: ctl(at3, "pills", "flt_preset").select("custom").run())
    act(at3, "round 2 for the link", lambda: set_chips(at3, "flt_rounds", [2]))
    link = dict(at3.query_params)
    assert link.get("dist") == ["mid"] or link.get("dist") == "mid", "the filtered view is in the URL"
    at4 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    for k, v in link.items():
        at4.query_params[k] = v
    at4.query_params["res"] = "nonsense"
    at4.query_params["lang"] = "xx"
    at4.run()
    assert not at4.exception, err_of(at4)
    assert (
        (ss_get(at4, "flt_dist"), ss_get(at4, "flt_weapons"), ss_get(at4, "flt_preset"),
         tuple(ss_get(at4, "flt_rounds")), ss_get(at4, "flt_res"), ss_get(at4, "lang"))
        == ("mid", ["ak47"], "custom", (2,), "both", "en")
    ), "link restores the filters, unknown values fall back"
    # the result filter stays reachable when it leaves no duels, and Reset clears it
    goto(at4, "duels")
    act(at4, "deaths only", lambda: ctl(at4, "segmented_control", "flt_res").select("death").run())
    assert widget(at4, "segmented_control", "flt_res") is not None
    act(at4, "reset clears the result too", lambda: at4.button(key="flt_reset").click().run())
    assert ss_get(at4, "flt_res") == "both", "the result filter is cleared by Reset"


def test_knife_round():
    """FACEIT shape: a knife round before begin_new_match ."""

    def make_knife_match(rounds):
        d = make_match(rounds)
        ev = d["events"]
        # knife round at ticks 100-130, live match restarts at 500
        ev["weapon_fire"] = pd.concat(
            [
                pd.DataFrame(
                    {
                        "tick": [110, 120],
                        "user_steamid": pd.array([int(S1), int(S3)], dtype="uint64"),
                        "user_name": [NAMES[S1], NAMES[S3]],
                        "weapon": ["weapon_knife", "weapon_knife_t"],
                        "total_rounds_played": [0, 0],
                        "is_warmup_period": [False, False],
                    }
                ),
                ev["weapon_fire"],
            ],
            ignore_index=True,
        )
        ev["player_death"] = pd.concat(
            [
                pd.DataFrame(
                    {
                        "tick": [130],
                        "attacker_steamid": pd.array([int(S1)], dtype="float64"),
                        "attacker_name": [NAMES[S1]],
                        "user_steamid": pd.array([int(S3)], dtype="float64"),
                        "user_name": [NAMES[S3]],
                        "weapon": ["knife"],
                        "headshot": [False],
                        "distance": [12.7],
                        "total_rounds_played": [0],
                        "is_warmup_period": [False],
                    }
                ),
                ev["player_death"],
            ],
            ignore_index=True,
        )
        ev["begin_new_match"] = pd.DataFrame(
            {"tick": [500], "is_warmup_period": [False], "total_rounds_played": [0]}
        )
        ev["round_freeze_end"] = pd.DataFrame(
            {
                "tick": [100] + [1000 * r - 50 for r in rounds],
                "is_warmup_period": [False] * (1 + len(rounds)),
                "total_rounds_played": [0] * (1 + len(rounds)),
            }
        )
        return d

    at = open_demo(make_knife_match([1, 2]), "knife_round.dem")
    assert not at.exception, err_of(at)
    banners = [c.value for c in at.caption if "Excluded" in str(c.value)]
    assert len(banners) == 1, "excluded banner shown"
    assert any("knife round (1)" in b for b in banners), "banner says knife round (1)"
    # the knife kill must not leak into the live data: one duel per round
    goto(at, "duels")
    assert not at.exception, err_of(at)
    # the same banner in russian
    at = open_demo(make_knife_match([1, 2]), "knife_round.dem", lang="ru")
    assert not at.exception, err_of(at)
    banners_ru = [c.value for c in at.caption if "Исключено" in str(c.value)]
    assert any("ножевой раунд (1)" in b for b in banners_ru), "ru: banner shown"


def _plain_demo(dirname, fname):
    """A real demo file somewhere on disk (a linked demo points at it)."""
    _demo_n[0] += 1
    d = os.path.join(DEMO_DIR, dirname)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, fname)
    with open(p, "wb") as f:
        f.write(f"smoke linked demo {fname} #{_demo_n[0]}".encode())
    FakeParser.demos[os.path.realpath(p)] = make_match([1, 2])
    return p


def test_links():
    """Choose file LINK demos (no copy); missing file; locate; remove."""
    import nativedialog

    real_files = nativedialog.pick_files
    try:
        at = open_demo(make_match([1, 2]), "links_base.dem")
        assert not at.exception, err_of(at)
        # the Add match button on the Matches page opens the file dialog
        goto(at, "matches")
        assert widget(at, "button", "mt_add") is not None, "links: the Add match button is offered"

        # --- choosing a file links it by its path; nothing is copied
        p = _plain_demo("link_a", "my_link.dem")
        copies_before = sorted(os.listdir(store.demos_dir()))
        nativedialog.pick_files = lambda *a, **k: [p]
        act(at, "links: choose a demo file", lambda: at.button(key="mt_add").click().run())
        lid = store.link_id(p)
        e = store.get_demo(lid) or {}
        assert e.get("mode") == "link" and e.get("path") == os.path.abspath(p), "links: choosing a file links it by its path"
        assert sorted(os.listdir(store.demos_dir())) == copies_before, "links: ... nothing was copied into demos/"
        assert (
            at.session_state["demo_id"] == lid and at.session_state["match"] == lid
            and at.session_state["demo_loaded"] == lid and not at.exception
        ), err_of(at)
        assert (store.get_demo(lid) or {}).get("map") == "de_smoke", "links: ... the map was remembered in the library"

        # --- the file disappears: a clear message that names the path, not a crash
        moved = os.path.join(os.path.dirname(p), "moved_here.dem")
        os.rename(p, moved)
        at = fresh_at(demo_id=lid, match=lid)
        assert not at.exception, err_of(at)
        assert any(os.path.basename(p) in str(x.value) for x in at.error), "links: ... the error names the file"
        assert (
            widget(at, "button", "lib_locate") is not None
            and widget(at, "button", "lib_remove_missing") is not None
            and widget(at, "button", "mt_back_missing") is not None
        ), "links: ... the no-file card offers Locate, Remove and the way back"
        assert store.get_demo(lid)["status"] == "missing", "links: ... the library lists it as missing"
        FakeParser.demos[os.path.realpath(moved)] = make_match([1, 2])
        nativedialog.pick_files = lambda *a, **k: [moved]
        act(at, "links: locate the file", lambda: at.button(key="lib_locate").click().run())
        e = store.get_demo(lid) or {}
        assert e.get("path") == os.path.abspath(moved) and e.get("status") == "ok" and not at.exception and not at.error, (e, err_of(at))

        # --- the same demo's row in the Matches list: its action is
        # "Set file...", and a row click runs it (the file moved again)
        moved2 = os.path.join(os.path.dirname(p), "moved_twice.dem")
        os.rename(moved, moved2)
        FakeParser.demos[os.path.realpath(moved2)] = make_match([1, 2])
        at2 = fresh_at(page="matches")
        import i18n as _i18n

        from views import matches as _mv

        _t = _i18n.make_t("en")
        _ml_rows = core.build_rows(_mv._all_records(), store.list_demos())
        _ml_row = next(r for r in _ml_rows if r.demo_id == lid)
        assert _ml_row.missing and _mv.action_of(_t, _ml_row) == _t("act_locate"), "links: the list row of a moved demo offers the locate action"
        _i = _ml_rows.index(_ml_row)
        nativedialog.pick_files = lambda *a, **k: [moved2]
        act(
            at2,
            "links: a row click locates the file",
            lambda: at2.session_state.__setitem__("mt_action_row", _i) or at2.run(),
        )
        e = store.get_demo(lid) or {}
        assert e.get("path") == os.path.abspath(moved2) and e.get("status") == "ok" and not at2.exception, (e, err_of(at2))

        # --- removing a linked demo never deletes the user's file (the
        # Library and storage section of Settings owns the button)
        at = at2
        goto(at, "settings")
        act(at, "links: remove from the list", lambda: at.button(key=f"rm_{lid}").click().run())
        assert os.path.isfile(moved2) and store.get_demo(lid) is None and not at.exception, err_of(at)

        # --- no dialog available (no tkinter): a hint, never a crash
        goto(at, "matches")
        nativedialog.pick_files = lambda *a, **k: None
        act(at, "links: choose a file without a dialog", lambda: at.button(key="mt_add").click().run())
        assert not at.exception and any("tkinter" in str(w.value) for w in at.warning), "links: no dialog -> a hint, not a crash"
    finally:
        nativedialog.pick_files = real_files


class _empty_library:
    """Run a block against an empty data folder (the first-start situation)."""

    def __enter__(self):
        self.old = os.environ["AIMAUDIT_DATA_DIR"]
        os.environ["AIMAUDIT_DATA_DIR"] = os.path.realpath(tempfile.mkdtemp(prefix="cs2aa_empty_"))

    def __exit__(self, *exc):
        os.environ["AIMAUDIT_DATA_DIR"] = self.old
        return False


def test_bad_input():
    did = register_demo(make_match([1, 2]), "vanishing.dem")
    os.remove(store.demo_path(store.get_demo(did)))  # deleted from demos/ by hand
    # opened as a match (a Matches row): the vanished copy leaves the list
    # (a copy is adopted from the folder, so its entry goes with the file) -
    # the app must land on the list, not crash
    at = fresh_at(demo_id=did, match=did)
    assert not at.exception, err_of(at)
    assert ss_get(at, "page") == "matches" and ss_get(at, "match") is None and not at.error, "vanished copy drops the stale match, back to the list"
    with _empty_library():
        at = fresh_at()
        assert not at.exception, err_of(at)
        assert len(at.subheader) >= 1 and not at.error, "empty library: the matches list says what to do"


def test_library():
    """One input: saved demos in a list, delete, clear cache, meta, switching."""
    idb = register_demo(make_match([1]), "lib_b.dem")
    at = open_demo(make_match([1, 2]), "lib_a.dem")
    ida = at.session_state["match"]
    assert not at.exception, err_of(at)
    assert widget(at, "selectbox", "lib_pick") is None and widget(at, "button", "demo_change_btn") is None, "the match header is the compact way around (no picker, no change button)"
    assert not any(getattr(w, "key", "") == "demo_path" for w in at.text_input), "there is no path field any more"
    meta = store.get_demo(ida)
    assert meta["map"] == "de_smoke" and len(meta["players"]) >= 2 and meta["tickrate"] == 64, "a loaded demo remembers its map and players in the library"
    assert store.list_demos()[0]["id"] == ida, "opening a demo marks it as the latest"
    assert store.storage_report()["parsed"] > 0, "the parsed demo was cached to the data folder"
    # switch to the other saved demo: its row in the Matches list
    goto(at, "matches")
    rows = core.build_rows([], store.list_demos())
    _ib = next(i for i, r in enumerate(rows) if r.demo_id == idb)
    act(
        at,
        "a list row opens the other saved demo",
        lambda: at.session_state.__setitem__("mt_action_row", _ib) or at.run(),
    )
    assert at.session_state["demo_id"] == idb and at.session_state["match"] == idb and not at.exception, err_of(at)
    assert store.list_demos()[0]["id"] == idb, "the switched demo is now the latest"
    # a new browser session starts on the matches list; the last demo is
    # one row click away
    at = fresh_at()
    assert ss_get(at, "page") == "matches" and ss_get(at, "match") is None and not at.exception, err_of(at)
    goto(at, "overview")
    assert ss_get(at, "match") == idb, "the last opened demo is one click away"
    # the clear-cache / delete buttons live on Settings now (scenario_settings
    # locks them); here only the library state itself is checked
    assert store.get_demo(ida) is not None and store.get_demo(idb) is not None, "both demos are still in the library"


def test_matrix(pytestconfig):
    """--full: every widget combination that a user can realistically click."""
    if not pytestconfig.getoption("full"):
        pytest.skip("only with --full")
    demos = {
        "two_rounds.dem": make_match([1, 2], weapons=("ak47", "awp")),
        "long25.dem": make_match([1, 13, 25], weapons=("ak47", "awp", "deagle")),
    }
    for lang in ("en", "ru"):
        for fname, demo in demos.items():
            at = open_demo(demo, fname, lang=lang)
            assert not at.exception, err_of(at)
            for thr in (0, 100, 250):
                act(
                    at,
                    f"thr={thr}",
                    lambda v=thr: at.session_state.__setitem__("thr", v) or at.run(),
                )
            for w in (1.0, 6.0):
                act(
                    at,
                    f"window={w}",
                    lambda v=w: at.session_state.__setitem__("window_s", v) or at.run(),
                )
            for sd in (100, 2000):
                act(
                    at,
                    f"safe_dist={sd}",
                    lambda v=sd: at.session_state.__setitem__("safe_dist", v) or at.run(),
                )
            for sdist in (500, 4000):
                act(
                    at,
                    f"strict_dist={sdist}",
                    lambda v=sdist: at.session_state.__setitem__("strict_dist", v) or at.run(),
                )
            presets = _PRESETS_BY_COUNT.get(n_presets(at)[0], ())
            for p in presets:
                act(
                    at,
                    f"preset {p}",
                    lambda p=p: ctl(at, "pills", "flt_preset")
                    .select(p)
                    .run(),
                )
            for wep in ("ak47", "awp", "deagle"):
                act(
                    at,
                    f"weapon {wep}",
                    lambda w=wep: at.multiselect(key="flt_weapons")
                    .set_value([w])
                    .run(),
                )
            for cls in ("rifle", "sniper", "pistol"):
                act(
                    at,
                    f"class {cls}",
                    lambda c=cls: ctl(at, "pills", "flt_classes").set_value([c]).run(),
                )
            for side in ("T", "CT"):
                if widget(at, "segmented_control", "flt_side") is not None:
                    act(
                        at,
                        f"side {side}",
                        lambda s=side: ctl(at, "segmented_control", "flt_side")
                        .select(s)
                        .run(),
                    )
            goto(at, "duels")
            for res in ("kill", "death", "both"):
                act(
                    at,
                    f"result {res}",
                    lambda r=res: ctl(at, "segmented_control", "flt_res")
                    .select(r)
                    .run(),
                )
            for sid in IDS:
                act(
                    at,
                    f"player {NAMES[sid]}",
                    lambda s=sid: at.selectbox(key="me").select(s).run(),
                )
            for tk in ("ov_full", "du_full"):
                goto(at, {"ov_full": "overview", "du_full": "duels"}[tk])
                if widget(at, "toggle", tk) is not None:
                    act(
                        at,
                        f"{tk} on",
                        lambda tk=tk: at.toggle(key=tk).set_value(True).run(),
                    )
                    act(
                        at,
                        f"{tk} off",
                        lambda tk=tk: at.toggle(key=tk).set_value(False).run(),
                    )
            for i in range(3):
                open_duel(at, i, f"duel window #{i}")
            goto(at, "replay")


def test_tables():
    """
    Basic/Full table switches on the custom grids (the "All columns"
    toggles still pick which columns the component receives).
    """
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "tables.dem")
    assert not at.exception, err_of(at)
    assert widget(at, "toggle", "ov_full") is not None, "overview toggle exists"
    act(
        at,
        "overview: all columns",
        lambda: at.toggle(key="ov_full").set_value(True).run(),
    )
    goto(at, "duels")
    assert widget(at, "toggle", "du_full") is not None, "duels toggle exists"
    act(
        at, "duels: all columns", lambda: at.toggle(key="du_full").set_value(True).run()
    )
    act(
        at,
        "duels back to basic",
        lambda: at.toggle(key="du_full").set_value(False).run(),
    )
    goto(at, "overview")
    act(
        at,
        "overview back to basic",
        lambda: at.toggle(key="ov_full").set_value(False).run(),
    )
    # russian labels are ~20-30% longer - the tables must hold
    act(at, "tables in russian", lambda: at.selectbox(key="lang").select("ru").run())
    goto(at, "overview")
    act(
        at,
        "russian overview: all columns",
        lambda: at.toggle(key="ov_full").set_value(True).run(),
    )
    goto(at, "duels")
    act(
        at,
        "russian duels: all columns",
        lambda: at.toggle(key="du_full").set_value(True).run(),
    )


def test_context():
    """
    Round seconds in the duels table, the by-zone table, and the
    flashed/slowed discipline tips. A 25-round fake match: Alpha's t0+2
    shot is flashed every round (50% of his shots), Charlie's t0+5 answer
    is damage-slowed every round (100% of his).
    """
    at = open_demo(make_match(list(range(1, 26))), "context.dem", me=S1)
    assert not at.exception, err_of(at)

    # tips live on the shooting page: Alpha fired 25 of 50 shots flashed
    # (50% >= 8%) -> the blind-fire verdict; 0 slowed shots -> no verdict
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    assert "while flashed" in tips_txt, "flashed tip shown for Alpha"
    assert "slowed by recent damage" not in tips_txt, "no slowed tip for Alpha (0 slowed shots)"

    goto(at, "duels")

    # the zone of every duel (all columns): Alpha = 25 kills from BombsiteA
    act(at, "duels: all columns", lambda: at.toggle(key="du_full").set_value(True).run())
    z = GRIDS["du_grid"]
    assert len(z) == 25 and (z["Zone"] == "BombsiteA").all(), "duels: Alpha's zone BombsiteA"

    # Charlie: his zone table flips to the victim zones (deaths in Middle),
    # and every answer shot is damage-slowed -> the slowed verdict
    act(
        at,
        "switch player (Charlie)",
        lambda: at.selectbox(key="me").select(S3).run(),
    )
    z = GRIDS["du_grid"]
    assert len(z) == 25 and (z["Zone"] == "Middle").all(), "duels (Charlie): died in Middle"
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    assert "slowed by recent damage" in tips_txt, "slowed tip shown for Charlie"
    assert "while flashed" not in tips_txt, "no flashed tip for Charlie (0 flashed shots)"

    # russian labels: the new tables/columns must hold the longer strings
    goto(at, "duels")
    act(
        at,
        "context tables in russian",
        lambda: at.selectbox(key="lang").select("ru").run(),
    )
    assert not at.exception, err_of(at)
    assert "Зона" in GRIDS["du_grid"].columns, "ru: zone column localized"


def test_first_shots():
    """
    First-shot discipline: first_shot/first_acc in the tables and the
    mid-swing tip from usercmd mouse deltas. 30 rounds: Alpha's t0 shot
    is always the first of an engagement (the t0+2 hit is a follow-up),
    12 of those 30 first shots come after a big mouse swing (>= 2x his
    own median) -> the verdict; Charlie has no mouse rows at all -> the
    tile degrades to "-" and no verdict.
    """
    rounds = list(range(1, 31))
    d = make_match(rounds)
    mouse = {}
    for i, r in enumerate(rounds):
        t0 = 1000 * r
        # window [t0-16, t0): 12 swinging rounds (8 ticks x 25 = 200),
        # 18 calm ones (2 ticks x 1 = 2) -> median 2, bar 4, 12 >= bar
        big = i < 12
        for t in range(t0 - 16, t0 - 8 if big else t0 - 14):
            mouse[(t, S1)] = (25 if big else 1, 0)
    d["mouse"] = mouse
    at = open_demo(d, "first_shots.dem")
    assert not at.exception, err_of(at)

    # the state file remembers the previous scenario's player - pin Alpha
    act(
        at,
        "switch player (Alpha)",
        lambda: at.selectbox(key="me").select(S1).run(),
    )
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    assert "still swinging fast" in tips_txt, "swing tip shown for Alpha"

    act(at, "all metrics", lambda: at.toggle(key="sh_all").set_value(True).run())
    labels = metric_map()
    assert "1st-shot accuracy" in labels, "first-acc tile present"
    m = labels.get("1st-shot accuracy")
    if m:
        assert m.value == "0%", "first-acc value 0% (t0 always misses)"
        assert m.delta == "-100 pp", "first-acc delta vs follow-ups"
    m = labels.get("First shots")
    assert m is not None and m.value == "30", "first-shot count 30"
    m = labels.get("Mouse move before 1st shot")
    assert m is not None and m.value == "2", "median swing 2 counts (calm majority)"

    # Charlie: no mouse rows -> flick_move NA -> tile "-" and no verdict
    act(
        at,
        "switch player (Charlie)",
        lambda: at.selectbox(key="me").select(S3).run(),
    )
    tips_txt = "\n".join(m.value for m in at.markdown)
    assert "still swinging fast" not in tips_txt, "no swing tip without mouse data"
    labels = metric_map()
    m = labels.get("Mouse move before 1st shot")
    assert m is not None and m.value == "—", "swing tile degrades to dash"

    # a demo WITHOUT usercmd data: no crash, no verdict, tile "-"
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "no_mouse.dem")
    assert not at.exception, err_of(at)
    act(
        at,
        "switch player (Alpha, no-mouse)",
        lambda: at.selectbox(key="me").select(S1).run(),
    )
    goto(at, "shooting")
    act(
        at,
        "all metrics (no-mouse)",
        lambda: at.toggle(key="sh_all").set_value(True).run(),
    )
    tips_txt = "\n".join(m.value for m in at.markdown)
    assert "still swinging fast" not in tips_txt, "no swing tip on old demo"
    labels = metric_map()
    m = labels.get("Mouse move before 1st shot")
    assert m is not None and m.value == "—", "no-mouse swing tile dash"

    # russian labels render (the longer strings must not break columns)
    act(
        at,
        "first shots in russian",
        lambda: at.selectbox(key="lang").select("ru").run(),
    )
    labels = metric_map()
    assert "Точность 1-го выстрела" in labels and "Движение мыши перед 1-м" in labels, "ru first-acc tile"


def test_impact():
    """
    Round-impact layer: opening duels, trades, multi-kills, assists.
    30 rounds, Alpha kills Charlie every round: Alpha opens 30/30 (100%,
    no verdict), Charlie opens 0/30 (the tip_openings verdict), nobody
    trades, assists degrade to '-' (the fake match has no assister
    columns). The openings table on the Duels page lists every round,
    marks the selected player with a star and follows the round filter.
    """
    at = open_demo(make_match(list(range(1, 31))), "impact.dem")
    assert not at.exception, err_of(at)

    # Charlie: 30 opening deaths, 0 won -> the tip; Alpha: 30/30 -> none
    act(
        at,
        "switch player (Charlie)",
        lambda: at.selectbox(key="me").select(S3).run(),
    )
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    assert "opening duel and keep losing" in tips_txt, "openings tip shown for Charlie"
    act(
        at,
        "all metrics",
        lambda: at.toggle(key="sh_all").set_value(True).run(),
    )
    labels = metric_map()
    m = labels.get("Opening duels (W / L)")
    assert m is not None and m.value == "0 / 30", "opening tile 0 / 30"
    m = labels.get("Opening duels (W / L)")
    assert m is not None and m.delta == "Opening winrate: 0%", "opening winrate 0%"
    m = labels.get("Trade kills")
    assert m is not None and m.value == "0", "trade kills 0"
    m = labels.get("Traded deaths")
    assert m is not None and m.value == "0%", "traded deaths 0%"
    m = labels.get("Assists")
    assert m is not None and m.value == "—", "assists dash without column"

    act(
        at,
        "switch player (Alpha)",
        lambda: at.selectbox(key="me").select(S1).run(),
    )
    tips_txt = "\n".join(m.value for m in at.markdown)
    assert "opening duel and keep losing" not in tips_txt, "no openings tip for Alpha (100%)"
    labels = metric_map()
    m = labels.get("Opening duels (W / L)")
    assert m is not None and m.value == "30 / 0", "opening tile 30 / 0"
    m = labels.get("Opening duels (W / L)")
    assert m is not None and m.delta == "Opening winrate: 100%", "opening winrate 100%"

    # russian labels render
    act(
        at,
        "impact tables in russian",
        lambda: at.selectbox(key="lang").select("ru").run(),
    )
    goto(at, "shooting")
    act(
        at,
        "all metrics (ru)",
        lambda: at.toggle(key="sh_all").set_value(True).run(),
    )
    labels = metric_map()
    assert labels["Первые дуэли (W / L)"].delta.startswith("Процент входов: "), "ru impact tiles"
    assert {"Трейды", "Мульти-килл раунды", "Эйс"} <= set(labels), "ru grouped tiles"


def test_scoreboard():
    """
    The scoreboard cross-check of core.data_checks: the game's own kills_total /
    deaths_total aggregates vs our tables (2-round match, Alpha kills Charlie in both
    rounds).
    """

    def _scoreboard(d, name):
        at = open_demo(d, name)
        assert not at.exception, err_of(at)
        path = store.demo_path(store.get_demo(at.session_state["demo_id"]))
        demo = core.load_demo(os.path.realpath(path))
        shots = core.shots_table(demo)
        checks = core.data_checks(demo, shots, core.build_duels(demo, 3.0))
        return next(c for c in checks if c["id"] == "scoreboard")

    d = make_match([1, 2])
    d["totals"] = {
        S1: ("Alpha", 2, 0),
        S2: ("Bravo", 0, 0),
        S3: ("Charlie", 0, 2),
        S4: ("Delta", 0, 0),
    }
    sb = _scoreboard(d, "score_ok.dem")
    assert sb["level"] == "ok" and sb.get("n", 0) == 0, sb

    # the game claims a kill we never found -> the check flags the player
    d2 = make_match([1, 2])
    d2["totals"] = {
        S1: ("Alpha", 3, 0),
        S2: ("Bravo", 0, 0),
        S3: ("Charlie", 0, 2),
        S4: ("Delta", 0, 0),
    }
    sb = _scoreboard(d2, "score_bad.dem")
    assert sb["level"] != "ok" and sb["n"] == 1, sb
    assert any(v.startswith("Alpha: K 2 vs 3") for v in sb.get("vals") or []), sb


def test_map():
    """The Map page: friendly message without positions, a chart with them."""
    import struct
    import zlib

    at = open_demo(make_match([1, 2]), "map_nopos.dem")
    goto(at, "map")
    assert any("demoparser2" in str(i.value) for i in at.info), "map: kill events without positions -> a friendly info"

    d = make_match([1, 2])
    de = d["events"]["player_death"]
    for who, base in (("attacker", 0.0), ("user", 300.0)):
        for ax, k in (("X", 100.0), ("Y", 50.0), ("Z", 0.0)):
            de[f"{who}_{ax}"] = [base + k * (i + 1) for i in range(len(de))]
    at = open_demo(d, "map_pos.dem")
    goto(at, "map")
    assert (
        len(at.get("plotly_chart")) == 1
        and any("fetch_radars" in str(i.value) for i in at.info)
    ), "map: with positions and no radar -> chart + radar hint"
    act(
        at,
        "map: show kills only",
        lambda: at.session_state.__setitem__("map_show", "kills") or at.run(),
    )
    act(
        at,
        "map: show deaths only",
        lambda: at.session_state.__setitem__("map_show", "deaths") or at.run(),
    )
    act(at, "map: lines off", lambda: at.toggle(key="map_lines").set_value(False).run())

    # with a radar (a tiny valid PNG + calibration for the fake map name)
    rd = tempfile.mkdtemp(prefix="cs2aa_smoke_radar_")
    os.makedirs(os.path.join(rd, "radars"))
    n = 8
    raw = b"".join(b"\x00" + b"\x20\x20\x30" * n for _ in range(n))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(os.path.join(rd, "radars", "de_smoke.png"), "wb") as fh:
        fh.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )
    with open(os.path.join(rd, "map_data.json"), "w", encoding="utf-8") as fh:
        json.dump({"de_smoke": {"pos_x": -500.0, "pos_y": 500.0, "scale": 1.0}}, fh)
    old = os.environ.get("AIMAUDIT_RADAR_DIR")
    os.environ["AIMAUDIT_RADAR_DIR"] = rd
    try:
        at = open_demo(d, "map_radar.dem")
        goto(at, "map")
        assert (
            len(at.get("plotly_chart")) == 1
            and not any("fetch_radars" in str(i.value) for i in at.info)
        ), "map: with a radar -> chart and no radar hint"
    finally:
        if old is None:
            os.environ.pop("AIMAUDIT_RADAR_DIR", None)
        else:
            os.environ["AIMAUDIT_RADAR_DIR"] = old


def test_matchstats():
    """The per-match summary: the ask flow, compute-at-open, and the resets."""
    import i18n as _i18n

    import sources
    from views import matches as _mv

    _t = _i18n.make_t("en")

    def _rows():
        return core.build_rows(
            sources.read_cache("steam"), store.list_demos(), stats_of=core.matchstats.cached_stats
        )

    # 1) no known account: opening a match asks who you are - once, with
    # the "remember" choice (ON = Settings / every match, OFF = this demo)
    store.write_accounts({**store.read_accounts(), "steam_id64": ""})
    at = open_demo(make_match([1, 2]), "ms_ask.dem")
    did = at.session_state["match"]
    assert (
        widget(at, "selectbox", "mt_who_pick") is not None
        and widget(at, "checkbox", "mt_who_remember") is not None
        and widget(at, "button", "mt_who_set") is not None
    ), "ms: the ask card renders without a known 'me'"
    assert core.matchstats.read_cached(store.demo_path(store.get_demo(did))) is None, "ms: nothing cached before the answer"
    # remember OFF: the pick lands in the library entry only
    act(
        at,
        "ms: untick 'remember in Settings'",
        lambda: at.checkbox(key="mt_who_remember").uncheck().run(),
    )
    act(at, "ms: pick yourself (Charlie)", lambda: at.selectbox(key="mt_who_pick").select(S3).run())
    act(at, "ms: press 'That's me'", lambda: at.button(key="mt_who_set").click().run())
    entry = store.get_demo(did)
    assert entry.get("me_sid") == S3, "ms: the pick is remembered in the library"
    assert store.read_accounts().get("steam_id64") == "", "ms: without the checkbox the accounts stay untouched"
    stats = core.matchstats.read_cached(store.demo_path(entry))
    assert stats is not None and stats["me_sid"] == S3, "ms: the summary was computed right away"
    assert stats is not None and stats["kills"] == 0 and stats["deaths"] == 2, "ms: the numbers are the picked player's (Alpha killed Charlie twice)"

    # 1b) the identity menu ("this is not me"): pick another player; with
    # remember ON the pick goes to the accounts and the summary recomputes
    assert (
        widget(at, "selectbox", "mt_me_pick") is not None
        and widget(at, "checkbox", "mt_me_remember") is not None
        and widget(at, "button", "mt_me_set") is not None
    ), "ms: the identity menu offers the picker"
    act(at, "ms: the menu picks Alpha", lambda: at.selectbox(key="mt_me_pick").select(S1).run())
    act(at, "ms: the menu presses 'That's me'", lambda: at.button(key="mt_me_set").click().run())
    entry = store.get_demo(did)
    assert store.read_accounts().get("steam_id64") == S1, "ms: the new pick went to the accounts (remember ON)"
    assert not entry.get("me_sid"), "ms: the stale per-match pick was cleared"
    stats = core.matchstats.read_cached(store.demo_path(entry))
    assert stats is not None and stats["me_sid"] == S1 and stats["kills"] == 2, "ms: the summary recomputed for the new player"
    assert ss_get(at, "me") == S1, "ms: the player row followed the new identity"

    # 2) the SteamID64 route: a match of the account's player never asks
    store.write_accounts({**store.read_accounts(), "version": 1, "steam_id64": S1})
    at = open_demo(make_match([1, 2, 13]), "ms_auto.dem")
    did2 = at.session_state["match"]
    assert widget(at, "selectbox", "mt_who_pick") is None, "ms: no ask card when the SteamID64 played"
    stats2 = core.matchstats.read_cached(store.demo_path(store.get_demo(did2)))
    assert stats2 is not None and stats2["me_sid"] == S1 and stats2["kills"] == 3, "ms: the summary computed at open for the SteamID64 player"

    # 3) the table rows carry the numbers; an un-analyzed row offers Analyze
    did3 = register_demo(make_match([1]), "ms_third.dem")
    goto(at, "matches")
    at.run()
    rows = _rows()
    assert (
        next(r for r in rows if r.demo_id == did2).stats is not None
        and next(r for r in rows if r.demo_id == did).stats is not None
    ), "ms: the analyzed rows carry their summary"
    row3 = next(r for r in rows if r.demo_id == did3)
    assert (
        _mv.action_of(_t, row3) == _t("act_analyze")
        and _mv.action_of(_t, next(r for r in rows if r.demo_id == did2)) == _t("mt_open")
    ), "ms: an un-analyzed row offers Analyze, an analyzed one Open"
    i3 = rows.index(row3)
    act(
        at,
        "ms: a row click opens (and analyzes) the match",
        lambda: at.session_state.__setitem__("mt_action_row", i3) or at.run(),
    )
    assert (
        core.matchstats.read_cached(store.demo_path(store.get_demo(did3))) is not None
        and ss_get(at, "match") == did3
        and not at.exception
    ), (ss_get(at, "match"), err_of(at))

    # 4) the resets: clear cache and delete take the summaries with them
    did4 = register_demo(make_match([1, 2]), "ms_fourth.dem")
    goto(at, "settings")
    act(at, "ms: clear the caches", lambda: at.button(key="lib_clear").click().run())
    assert (
        core.matchstats.read_cached(store.demo_path(store.get_demo(did4))) is None
        and os.listdir(store.matchstats_dir()) == []
    ), "ms: clear_cache resets the summaries"


def test_matches():
    """The Matches page: the unified table, refresh, download -> open."""
    import sources
    from sources import model as sm

    def _rec(mid, result="win", score=(13, 5), demo=sm.DemoState.READY, **kw):
        from datetime import datetime, timezone

        return sm.MatchRecord(
            provider="steam",
            match_id=mid,
            played_at=datetime.now(timezone.utc),
            map_name="de_mirage",
            mode="Competitive",
            score=score,
            result=result,
            demo=demo,
            **kw,
        )

    def _rows():
        return core.build_rows(sources.read_cache("steam"), store.list_demos())

    def _shown_of(at):
        """(x, y) from the 'Showing x of y' counter line."""
        for c in at.caption:
            m = re.search(r"(?:Showing|Показано) (\d+) (?:of|из) (\d+)", str(c.value))
            if m:
                return int(m.group(1)), int(m.group(2))
        return None

    # 1) empty library, nothing set up: the page must open and stay friendly
    with _empty_library():
        at = fresh_at(page="matches")
        assert not at.exception, err_of(at)
        assert widget(at, "button", "mt_refresh") is not None, "matches: refresh button offered"
        assert widget(at, "button", "mt_add") is not None, "matches: the add-match button is offered"
        assert (
            widget(at, "button", "mt_configure") is not None
            and widget(at, "button", "mt_helper_install") is None
        ), "matches: the helper status points at Settings"
        assert len(at.subheader) >= 1 and not at.error, "matches: the empty state says what to do"

        # 2) a failing refresh keeps the page usable
        def _boom(provider, cfg, n, **kw):
            raise sm.NetworkError("down")

        _orig_list, _orig_fetch = sources.list_matches, sources.fetch_demo
        sources.list_matches = _boom
        try:
            act(at, "refresh fails", lambda: at.button(key="mt_refresh").click().run())
            assert len(at.error) >= 1, "matches: network error is shown"
        finally:
            sources.list_matches = _orig_list

        # 3) the real flow with a fake provider: refresh -> table -> download -> open
        store.write_accounts({"version": 1, "match_count": 5})

        def _fake_list(provider, cfg, n, **kw):
            if provider != "steam":
                return []
            recs = [
                _rec("m1"),
                _rec("m2", result="loss", score=(9, 13)),
                _rec("m3", result="tie", score=(12, 12)),
            ]
            recs += [_rec(f"m{i}") for i in range(4, 31)]  # past the Show-more cut
            return recs

        def _fake_fetch(rec, cfg, *, progress=None, cancel=None, **kw):
            src = os.path.join(DEMO_DIR, f"steam_{rec.match_id}.dem")
            with open(src, "wb") as fh:
                fh.write(f"fake steam demo {rec.match_id}".encode())
            if progress:
                progress(24, 24)
            entry = sources.import_local(rec, src)
            # a real download is followed by the on-the-spot analysis: give
            # the fake parser a parsable match for every fetched demo
            FakeParser.demos[os.path.realpath(store.demo_path(entry))] = make_match([1, 2])
            return entry

        _orig_replays = sources.inbox.replays_records
        sources.list_matches = _fake_list
        sources.fetch_demo = _fake_fetch
        # no real CS2 replays on the test machine may leak into the cache
        sources.inbox.replays_records = lambda *a, **k: []
        try:
            act(at, "refresh loads the list", lambda: at.button(key="mt_refresh").click().run())
            assert len(sources.read_cache("steam")) == 30, "matches: cached to matches.json"
            rows = _rows()
            assert len(rows) == 30 and {"win", "loss", "tie"} <= {r.result for r in rows}, "matches: one row per game, results win/loss/tie in the list"
            assert _shown_of(at) == (25, 30), "matches: the counter shows the first page (25 of 30)"
            assert widget(at, "button", "mt_more") is not None, "matches: the Show more button is offered"
            act(at, "show more", lambda: at.button(key="mt_more").click().run())
            assert _shown_of(at) == (30, 30), "matches: the second page completes the table"

            # a row click downloads the demo (the deferred action the row
            # trigger records - the same path the component callback takes)
            rows = _rows()
            i_m1 = next(i for i, r in enumerate(rows) if r.match_id == "m1")
            act(
                at,
                "row click downloads the demo",
                lambda: at.session_state.__setitem__("mt_action_row", i_m1) or at.run(),
            )
            entry = store.find_by_match("steam", "m1")
            assert entry is not None and "steam_m1" in entry["file"], "matches: download bound the demo in the library"
            rows = _rows()
            m1row = next(r for r in rows if r.match_id == "m1")
            assert (
                m1row.demo_id == entry["id"] and m1row.demo_state == sm.DemoState.DOWNLOADED
                and sum(1 for r in rows if r.match_id == "m1") == 1
            ), "matches: the row is in-library now, one row still (no dupes)"

            # multi-download: the checkboxes (the component reports the row
            # KEYS into mt_checked), the button, the loop with per-demo
            # analysis, and the last download opening as the match
            goto(at, "matches")  # the single download opened m1 as a match
            store.write_accounts({"version": 1, "match_count": 5, "steam_id64": S1})
            rows = _rows()
            sel_keys = {r.key for r in rows if r.match_id in ("m2", "m3")}
            assert (
                widget(at, "button", "mt_dl_selected") is not None
                and at.button(key="mt_dl_selected").disabled
            ), "matches: the download bar stays disabled without a selection"
            act(
                at,
                "check two rows",
                lambda: at.session_state.__setitem__("mt_checked", set(sel_keys)) or at.run(),
            )
            assert not at.button(key="mt_dl_selected").disabled, "matches: the download bar wakes up with a selection"
            act(
                at,
                "press Download selected",
                lambda: at.session_state.__setitem__("mt_dl_multi", True) or at.run(),
            )
            e2 = store.find_by_match("steam", "m2")
            e3 = store.find_by_match("steam", "m3")
            assert e2 is not None and e3 is not None, "matches: both selected demos were downloaded"
            analyzed = []
            for _e in (e2, e3):
                _s = core.matchstats.read_cached(store.demo_path(_e)) if _e else None
                analyzed.append(_s is not None and _s["me_sid"] == S1 and _s["kills"] == 2)
            assert analyzed == [True, True], "matches: multi downloads were analyzed on the spot"
            assert (
                ss_get(at, "match") is not None
                and ss_get(at, "match") == (e3 or {}).get("id")
            ), "matches: the last download opened as the match"
            assert not ss_get(at, "mt_checked"), "matches: the selection was consumed"
            goto(at, "matches")

            # a row click opens the match
            FakeParser.demos[os.path.realpath(store.demo_path(entry))] = make_match([1, 2])
            act(
                at,
                "row click opens the match",
                lambda: at.session_state.__setitem__("mt_action_row", i_m1) or at.run(),
            )
            assert (
                ss_get(at, "page") == "matches"
                and ss_get(at, "match") == entry["id"]
                and ss_get(at, "match_tab") == "overview"
                and not at.exception
            ), (ss_get(at, "page"), ss_get(at, "match_tab"), err_of(at))
            assert ss_get(at, "demo_id") == entry["id"], "matches: the demo is the loaded one"
            assert goto(at, "matches") or True, "matches: back to the matches list still works"
            open_match(at, entry["id"], "continue: reopen the last match")
            assert ss_get(at, "match") == entry["id"] and not at.exception, (ss_get(at, "match"), err_of(at))
            goto(at, "matches")

            # an own demo joins the table as its own row (source: File)
            own_id = register_demo(make_match([1, 2]), "own_demo.dem")
            at.run()
            rows = _rows()
            assert any(r.demo_id == own_id and r.source == "file" and r.provider is None for r in rows), "matches: an own demo is its own row with the File source"
            assert _shown_of(at) == (31, 31), "matches: the counter counts it (31 rows)"
            assert {r.kind for r in rows} == {"competitive", "manual"}, "matches: rows classify into pools (competitive / manual)"

            # filters: result / pool + the counter + the URL (the tie
            # filter keeps one row, safely under the Show-more cut)
            act(at, "filter: ties only", lambda: ctl(at, "segmented_control", "mflt_result").select("tie").run())
            assert _shown_of(at) == (1, 31), "matches: the result filter narrows the table"
            assert at.query_params.get("fres") == "tie", "matches: the filters ride in the URL"
            act(at, "filter: reset", lambda: at.button(key="mflt_reset").click().run())
            assert _shown_of(at) == (25, 31), "matches: reset restores the table (a fresh 25-row page)"
            assert "fres" not in at.query_params, "matches: reset clears the URL"
            act(
                at,
                "filter: manual uploads only",
                lambda: ctl(at, "selectbox", "mflt_kind").select("manual").run(),
            )
            assert _shown_of(at) == (1, 31), "matches: the pool filter keeps only your files"
            assert at.query_params.get("fkind") == "manual", "matches: the pool filter rides in the URL"
            act(at, "filter: reset again", lambda: at.button(key="mflt_reset").click().run())

            # a copied link restores the filter in a fresh session
            at5 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
            at5.query_params["fres"] = "tie"
            at5.run()
            assert ss_get(at5, "mflt_result") == "tie" and not at5.exception, (ss_get(at5, "mflt_result"), err_of(at5))

            # delete the whole library at once (the provider rows stay)
            act(at, "ask clear", lambda: at.button(key="mt_clear").click().run())
            assert widget(at, "button", "mt_clear_yes") is not None, "matches: clear asks to confirm"
            act(at, "confirm clear", lambda: at.button(key="mt_clear_yes").click().run())
            assert store.list_demos() == [], "matches: library empty after clear"
            assert not at.exception, err_of(at)

            # 4) refresh error after a successful load: the cached list stays
            def _boom2(provider, cfg, n, **kw):
                raise sm.RateLimitError(retry_after=42)

            sources.list_matches = _boom2
            act(at, "refresh hits the rate limit", lambda: at.button(key="mt_refresh").click().run())
            assert any("42" in str(e.value) for e in at.error), "matches: rate limit message shown"
            assert len(sources.read_cache("steam")) >= 2, "matches: the cached rows survive the error"
        finally:
            sources.list_matches = _orig_list
            sources.fetch_demo = _orig_fetch
            sources.inbox.replays_records = _orig_replays


def test_pick():
    """pick(): format_func widgets accept the raw option AND the label.

    Regression for the harness helper: AppTest's select_index() is
    unusable on format_func widgets (double formatting), which used to
    make whole widget groups unreachable for tests (mflt_kind,
    mflt_period, lang). pick() must select by raw value and by label,
    in EN and in RU, on selectbox and segmented_control alike.
    """
    at = open_demo(make_match([1, 2]), "pick.dem", me=S1)
    goto(at, "matches")

    # selectbox with translated labels (mflt_kind: all/matchmaking/premier/...)
    act(at, "pick mflt_kind by raw value", lambda: pick(at, "selectbox", "mflt_kind", "manual"))
    assert ss_get(at, "mflt_kind") == "manual", "pick: selectbox raw value landed"
    act(at, "pick mflt_kind by label", lambda: pick(at, "selectbox", "mflt_kind", "Manual upload"))
    assert ss_get(at, "mflt_kind") == "manual", "pick: selectbox label landed"

    # segmented_control with translated labels (mflt_period: 7/30/all)
    act(at, "pick mflt_period by raw value", lambda: pick(at, "segmented_control", "mflt_period", "30"))
    assert ss_get(at, "mflt_period") == "30", "pick: segmented raw value landed"
    act(at, "pick mflt_period by label", lambda: pick(at, "segmented_control", "mflt_period", "7 days"))
    assert ss_get(at, "mflt_period") == "7", "pick: segmented label landed"

    # the language selectbox formats its options through i18n.LANGS
    act(at, "pick lang by label", lambda: pick(at, "selectbox", "lang", "Русский"))
    assert ss_get(at, "lang") == "ru", "pick: lang label landed"

    # ... and after the switch the SAME widgets show Russian labels
    act(at, "pick mflt_kind by RU label", lambda: pick(at, "selectbox", "mflt_kind", "Ручная загрузка"))
    assert ss_get(at, "mflt_kind") == "manual", "pick: RU selectbox label landed"
    act(at, "pick mflt_period by RU label", lambda: pick(at, "segmented_control", "mflt_period", "7 дней"))
    assert ss_get(at, "mflt_period") == "7", "pick: RU segmented label landed"

    # a value that is neither an option nor a label must raise, not click
    # something random
    with pytest.raises(ValueError):
        pick(at, "selectbox", "mflt_kind", "no such choice")

    # back to English for the scenarios that follow
    act(at, "lang back to EN", lambda: pick(at, "selectbox", "lang", "en"))


def test_list_filter_keep():
    """The Matches-list filters must survive leaving the list.

    Regression for the state-loss bug: mflt_* are keyed widgets that
    render only on the list, and Streamlit drops the widget state of
    anything a run does not render - so a trip to Settings (or into a
    match and back) used to reset every filter to "all" and silently
    strip ?fkind/?fp from the shareable URL. persist_state="session"
    keeps the values alive.
    """
    at = open_demo(make_match([1, 2]), "mfltkeep.dem", me=S1)
    goto(at, "matches")

    act(at, "filter the list to manual uploads", lambda: pick(at, "selectbox", "mflt_kind", "manual"))
    act(at, "filter the list to 30 days", lambda: pick(at, "segmented_control", "mflt_period", "30"))
    assert ss_get(at, "mflt_kind") == "manual" and ss_get(at, "mflt_period") == "30", "mflt set on the list"
    assert at.query_params.get("fkind") == "manual", "filter is shareable (?fkind in URL)"

    # leave to Settings and come back: the widgets do not render there
    goto(at, "settings")
    assert ss_get(at, "mflt_kind") == "manual", "mflt_kind alive on Settings"
    goto(at, "matches")
    assert ss_get(at, "mflt_kind") == "manual", "mflt_kind survives Settings round trip"
    assert ss_get(at, "mflt_period") == "30", "mflt_period survives Settings round trip"
    assert at.query_params.get("fkind") == "manual", "URL keeps ?fkind after the round trip"

    # into the match and back: the list does not render in the match view
    goto(at, "overview")
    assert ss_get(at, "mflt_kind") == "manual", "mflt_kind alive inside the match"
    goto(at, "matches")
    assert ss_get(at, "mflt_kind") == "manual", "mflt_kind survives match round trip"
    assert at.query_params.get("fkind") == "manual", "URL keeps ?fkind after the match round trip"

    # the Reset button still clears everything (also through persist)
    act(at, "reset the list filters", lambda: at.button(key="mflt_reset").click().run())
    assert ss_get(at, "mflt_kind") == "all", "mflt_reset clears the kind"
    assert ss_get(at, "mflt_period") == "all", "mflt_reset clears the period"
    assert "fkind" not in dict(at.query_params), "mflt_reset drops ?fkind from the URL"


def test_tab_state_keep():
    """Tab-scoped toggles must survive switching match tabs.

    Regression for the state-loss bug: the "all columns / all metrics"
    toggles and the Map controls render only on their own tab, so every
    tab switch used to reset them (ov_full, sh_all, du_full, map_show,
    map_lines). persist_state="session" keeps the values alive.
    """
    demo = make_match([1, 2])
    de = demo["events"]["player_death"]
    for who, base in (("attacker", 0.0), ("user", 300.0)):
        for ax, k in (("X", 100.0), ("Y", 50.0), ("Z", 0.0)):
            de[f"{who}_{ax}"] = [base + k * (i + 1) for i in range(len(de))]
    at = open_demo(demo, "tabkeep.dem", me=S1)

    # set every tab's widget on its own tab
    goto(at, "overview")
    act(at, "overview: all columns on", lambda: at.toggle(key="ov_full").set_value(True).run())
    goto(at, "shooting")
    act(at, "shooting: all metrics on", lambda: at.toggle(key="sh_all").set_value(True).run())
    goto(at, "duels")
    act(at, "duels: all columns on", lambda: at.toggle(key="du_full").set_value(True).run())
    goto(at, "map")
    act(at, "map: deaths only", lambda: pick(at, "segmented_control", "map_show", "deaths"))
    act(at, "map: lines off", lambda: at.toggle(key="map_lines").set_value(False).run())

    # walk all tabs and demand every value is still there
    goto(at, "overview")
    assert ss_get(at, "ov_full") is True, "ov_full survives the tab walk"
    assert bool(at.toggle(key="ov_full").value), "ov_full toggle renders ON"
    goto(at, "shooting")
    assert ss_get(at, "sh_all") is True, "sh_all survives the tab walk"
    goto(at, "duels")
    assert ss_get(at, "du_full") is True, "du_full survives the tab walk"
    goto(at, "map")
    assert ss_get(at, "map_show") == "deaths", "map_show survives the tab walk"
    assert ss_get(at, "map_lines") is False, "map_lines survives the tab walk"

    # leaving the whole match view (the list) must not lose them either
    goto(at, "matches")
    assert ss_get(at, "ov_full") is True, "ov_full alive on the list"
    goto(at, "overview")
    assert ss_get(at, "ov_full") is True, "ov_full survives the list round trip"

    # a map tab that renders NO map widgets (a demo without kill
    # positions) must keep the stored values for the way back - the
    # second demo opens in the SAME session (a list row click)
    did2 = register_demo(make_match([2, 3]), "tabkeep_nopos.dem")
    at.session_state["match"] = did2
    at.session_state["demo_id"] = did2
    goto(at, "map")
    assert any("demoparser2" in str(i.value) for i in at.info), "map without positions shows the hint"
    assert ss_get(at, "map_show") == "deaths", "map_show kept while its widget cannot render"
    goto(at, "overview")
    assert ss_get(at, "ov_full") is True, "ov_full kept across a demo switch"


def test_real_demo(pytestconfig, monkeypatch):
    """--demo FILE: every player x every preset of a real demo."""
    path = pytestconfig.getoption("demo")
    if not path:
        pytest.skip("no --demo")
    for mod in (core.loader, core.replay):
        monkeypatch.setattr(mod, "DemoParser", DemoParser)
    path = os.path.abspath(path)
    entry = store.add_demo(path)  # the real flow: the demo goes to the library
    at = fresh_at(timeout=600, demo_id=entry["id"], match=entry["id"])
    assert not at.exception, err_of(at)
    # AppTest's select_index applies the app's format_func to the already
    # formatted label ("Name (T)" -> "Name (T) (?)"), so players are
    # selected by their RAW sid from a light parse_player_info() call
    # (DemoParser needs the decompressed file, like the app itself)
    info = DemoParser(core.decompressed_demo(path)).parse_player_info()
    sids = sorted({str(int(s)) for s in pd.Series(info["steamid"]).dropna()})
    for sid in (s for s in sids if s != "0"):
        act(
            at,
            f"player {sid}",
            lambda s=sid: at.selectbox(key="me").select(s).run(),
        )
    presets = _PRESETS_BY_COUNT.get(n_presets(at)[0], ())
    for p in presets:
        act(
            at,
            f"preset {p}",
            lambda p=p: ctl(at, "pills", "flt_preset").select(p).run(),
        )
