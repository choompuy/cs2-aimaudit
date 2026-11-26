"""Drives the real app.py under AppTest (tests/test_app.py, tests/test_walk.py)."""

import os
import re
import tempfile
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

import core
import store
from fakes import FakeParser, IDS, S1, S2, S3, S4, f64_sid
from state import MATCH_TABS
from ui import schema


ROOT = Path(__file__).resolve().parents[1]


NAMES = {S1: "Alpha", S2: "Bravo", S3: "Charlie", S4: "Delta"}

# POS of each player: S1 and S3 duel at 500 u, S2/S4 far away from everything.
_POS = {
    S1: (0.0, 0.0, 0.0),  # x, y, yaw - looks +X, straight at S3
    S2: (0.0, 1500.0, 0.0),
    S3: (500.0, 0.0, 180.0),  # looks -X, straight at S1
    S4: (500.0, 1500.0, 180.0),
}


def make_match(rounds, weapons=("ak47",)):
    """
    Synthetic demo with one duel per requested 1-based round: Alpha (S1)
    kills Charlie (S3). Round r lives around tick 1000*r (far apart, so
    duels never bleed into each other): S1 fires at t0 and t0+2 (the t0+2
    shot hits the head), S3 answers at t0+5, S3 dies at t0+10 at 500 u.

    team_num in the tick rows follows the MR12 halves (S1/S2: T in rounds
    1-12 and 25+, CT in 13-24), so demo.sides is populated and the side
    filter shows up in the UI. Tick rows are contiguous from t0-64 to
    t0+16 so the duel chart has real data to plot.
    """
    fire_t, hurt_t, death_t = [], [], []
    for i, r in enumerate(rounds):
        t0 = 1000 * r
        w = f"weapon_{weapons[i % len(weapons)]}"
        fire_t += [(t0, S1, w, r), (t0 + 2, S1, w, r), (t0 + 5, S3, w, r)]
        hurt_t += [(t0 + 2, S1, S3, w, r)]
        death_t += [(t0 + 10, S1, S3, w, r)]
    rows = []
    for i, r in enumerate(rounds):
        t0 = 1000 * r
        s1_team = 2 if (r <= 12 or r >= 25) else 3  # T / CT / T again in OT
        for t in range(t0 - 64, t0 + 17):
            for sid in IDS:
                x, y, yaw = _POS[sid]
                # round replay props: HP drops when the duel lands (S3
                # dies at t0+10), Alpha holds W the whole approach and
                # FIRE only around its own shots, weapon switches once
                dead = sid == S3 and t >= t0 + 10
                rows.append(
                    {
                        "tick": t,
                        "steamid": f64_sid(sid),
                        "name": NAMES[sid],
                        "X": x,
                        "Y": y,
                        "Z": 0.0,
                        "pitch": 0.0,
                        "yaw": yaw,
                        "is_alive": not dead,
                        "aim_punch_angle": "0 0 0",
                        "team_num": s1_team if sid in (S1, S2) else 5 - s1_team,
                        # shooter state: Alpha's t0+2 shot (the headshot
                        # hit) comes while flashed (1.5s left); Charlie's t0+5
                        # answer comes still slowed (0.4) from the damage.
                        "flash_duration": 1.5 if (sid == S1 and t == t0 + 2) else 0.0,
                        "velo_modifier": 0.4 if (sid == S3 and t == t0 + 5) else 1.0,
                        "health": 0 if dead else 100,
                        "active_weapon_name": (
                            ("weapon_awp" if t >= t0 + 8 else "weapon_ak47")
                            if sid == S1
                            else "weapon_ak47"
                        ),
                        "FORWARD": sid == S1 and not dead,
                        "FIRE": sid == S1 and t in (t0, t0 + 2),
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
        "header": {"map_name": "de_smoke", "server_name": "smoke"},
        "events": {
            "weapon_fire": pd.DataFrame(
                {
                    "tick": [t for t, _, _, _ in fire_t],
                    "user_steamid": pd.array(
                        [int(s) for _, s, _, _ in fire_t], dtype="uint64"
                    ),
                    "user_name": [NAMES[s] for _, s, _, _ in fire_t],
                    "weapon": [w for _, _, w, _ in fire_t],
                    "total_rounds_played": [r - 1 for _, _, _, r in fire_t],
                    "is_warmup_period": [False] * len(fire_t),
                    "user_last_place_name": [
                        "BombsiteA" if s == S1 else "Middle" for _, s, _, _ in fire_t
                    ],
                    "round_start_time": [10.0 * (r - 1) for _, _, _, r in fire_t],
                    "game_time": [
                        10.0 * (r - 1) + 25.0 + (t - 1000 * r) / 64.0
                        for t, _, _, r in fire_t
                    ],
                }
            ),
            # the flash behind Alpha's flash_duration at t0+2
            "player_blind": pd.DataFrame(
                {
                    "tick": [1000 * r + 2 for r in rounds],
                    "user_steamid": [S1] * len(rounds),
                    "user_name": [NAMES[S1]] * len(rounds),
                    "blind_duration": [1.5] * len(rounds),
                }
            ),
            "player_hurt": pd.DataFrame(
                {
                    "tick": [t for t, _, _, _, _ in hurt_t],
                    "attacker_steamid": pd.array(
                        [int(a) for _, a, _, _, _ in hurt_t], dtype="float64"
                    ),
                    "attacker_name": [NAMES[a] for _, a, _, _, _ in hurt_t],
                    "user_steamid": pd.array(
                        [int(u) for _, _, u, _, _ in hurt_t], dtype="float64"
                    ),
                    "user_name": [NAMES[u] for _, _, u, _, _ in hurt_t],
                    "weapon": [w for _, _, _, w, _ in hurt_t],
                    "hitgroup": [1] * len(hurt_t),
                    "total_rounds_played": [r - 1 for _, _, _, _, r in hurt_t],
                    "is_warmup_period": [False] * len(hurt_t),
                    "attacker_last_place_name": ["BombsiteA"] * len(hurt_t),
                    "user_last_place_name": ["Middle"] * len(hurt_t),
                    "round_start_time": [10.0 * (r - 1) for _, _, _, _, r in hurt_t],
                    "game_time": [
                        10.0 * (r - 1) + 25.0 + (t - 1000 * r) / 64.0
                        for t, _, _, _, r in hurt_t
                    ],
                }
            ),
            "player_death": pd.DataFrame(
                {
                    "tick": [t for t, _, _, _, _ in death_t],
                    "attacker_steamid": pd.array(
                        [int(a) for _, a, _, _, _ in death_t], dtype="float64"
                    ),
                    "attacker_name": [NAMES[a] for _, a, _, _, _ in death_t],
                    "user_steamid": pd.array(
                        [int(u) for _, _, u, _, _ in death_t], dtype="float64"
                    ),
                    "user_name": [NAMES[u] for _, _, u, _, _ in death_t],
                    "weapon": [w for _, _, _, w, _ in death_t],
                    "headshot": [True] * len(death_t),
                    "distance": [12.7] * len(death_t),
                    "total_rounds_played": [r - 1 for _, _, _, _, r in death_t],
                    "is_warmup_period": [False] * len(death_t),
                    "attacker_last_place_name": ["BombsiteA"] * len(death_t),
                    "user_last_place_name": ["Middle"] * len(death_t),
                    "round_start_time": [10.0 * (r - 1) for _, _, _, _, r in death_t],
                    "game_time": [
                        10.0 * (r - 1) + 25.0 + (t - 1000 * r) / 64.0
                        for t, _, _, _, r in death_t
                    ],
                }
            ),
        },
        "ticks": pd.DataFrame(rows),
        "unsupported": set(),
    }


# ------------------------------------------------------------- app runner
# realpath: on Windows CI the temp dir contains an 8.3 short name
# (RUNNER~1) that os.path.realpath expands inside decompressed_demo(),
# so FakeParser.demos keys must already be resolved.
DEMO_DIR = os.path.realpath(tempfile.mkdtemp(prefix="cs2aa_smoke_demos_"))
_DEMO_IDS: dict[str, str] = {}  # file name -> library id of the latest registration
_demo_n = [0]


def fresh_at(timeout=300, **session):
    """A run of the real app.py with session_state pre-set; a `match` /
    `match_tab` pair also lands in ?m= / ?tab= (the URL is the truth about
    the open match, and the Matches page re-reads it every run)."""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=timeout)
    for k, v in session.items():
        at.session_state[k] = v
    if session.get("match"):
        at.query_params["m"] = session["match"]
        tab = session.get("match_tab") or "overview"
        if tab != "overview":
            at.query_params["tab"] = tab
    at.run()
    return at


def register_demo(demo, fname):
    """
    Save a fake demo into the library exactly like adding one does, and
    return its library id. Unique bytes per call -> a unique id, so two
    scenarios that reuse a file name never share a cached parse.
    """
    _demo_n[0] += 1
    src = os.path.join(DEMO_DIR, fname)
    with open(src, "wb") as f:
        f.write(f"smoke fake demo {fname} #{_demo_n[0]}".encode())
    entry = store.add_demo(src)
    FakeParser.demos[os.path.realpath(store.demo_path(entry))] = demo
    _DEMO_IDS[fname] = entry["id"]
    return entry["id"]


def open_demo(demo, fname, **session):
    """Register a fake demo in the library and run the app with it open as a
    match (as if its Matches-list row had been clicked)."""
    did = register_demo(demo, fname)
    return fresh_at(demo_id=did, match=did, match_tab="overview", **session)


def err_of(at):
    """First exception message, for the assert output."""
    return repr(at.exception[0].value)[:300] if at.exception else ""


# segmented_control/pills got their own AppTest accessors only in
# streamlit 1.57; before that both are plain button_group widgets
_ALIASES = {"segmented_control": "button_group", "pills": "button_group"}


def widget(at, kind, key):
    """Element with that key, or None when the app did not render it."""
    if kind in _ALIASES and not hasattr(type(at), kind):
        kind = _ALIASES[kind]
    try:
        w = getattr(at, kind)(key=key)
        return w if w else None
    except Exception:  # noqa: BLE001 - missing key -> None
        return None


def ctl(at, kind, key):
    """A rendered segmented_control/pills widget for an interaction."""
    w = widget(at, kind, key)
    if w is None:
        raise KeyError(f"{kind} widget {key!r} not rendered")
    return w


GRIDS: dict[str, pd.DataFrame] = {}  # grid key -> what the app last drew


_draw = schema.data_table


def _record_grid(rows, cols, *, key, **kw):
    """AppTest cannot look inside a custom component: keep its rows here."""
    GRIDS[key] = pd.DataFrame({c.label: list(rows[c.key]) for c in cols})
    _draw(rows, cols, key=key, **kw)


schema.data_table = _record_grid


def ss_get(at, key, default=None):
    """
    session_state.get(): dict methods on AppTest.session_state are
    only available on newer streamlit; [] access works everywhere.
    """
    try:
        return at.session_state[key]
    except KeyError:
        return default


def pick(at, kind, key, value):
    """
    Select `value` - the RAW option or its displayed label - in a
    selectbox / segmented_control that may format its options through
    format_func (translated labels, player names, language titles).
    Single-select widgets only.

    AppTest's .select() takes the raw option value: format_func(raw) must
    be one of the displayed options, because the wire carries the label
    and the app maps it back to the option. .select_index() cannot be
    used with format_func widgets at all: it treats the already-formatted
    label as a value and formats it a second time ('kind_Manual upload'
    is not in list).

    Label mode swaps the widget's format_func for str() through the
    testing session state, so the label itself goes on the wire
    unchanged; the app's own deserializer then maps it back to the raw
    option. The following at.run() re-registers the real format_func.
    """
    w = ctl(at, kind, key)
    try:
        raw_ok = w.format_func(value) in w.options
    except Exception:  # noqa: BLE001 - the value does not format: label mode
        raw_ok = False
    if raw_ok:
        return w.select(value).run()
    if value in w.options:
        from streamlit.runtime.state.common import TESTING_KEY

        at.session_state[TESTING_KEY][w.id] = str
        return w.select(value).run()
    raise ValueError(
        f"pick {key!r}: {value!r} is neither a raw option nor a displayed"
        f" label of the widget (options: {w.options})"
    )


def run_page(at, page):
    """A run on another page, like a rail click or an opened link: AppTest's
    own switch_page only knows file-based pages."""
    at._page_hash = calc_hash("" if page == "matches" else page)
    at.run()
    at._page_hash = ""  # later runs stay where the app itself navigates


def goto(at, page):
    """
    Switch the page: a match tab (overview / shooting / duels / map /
    replay) opens the current - or last - demo as a match (a list row click:
    ?m= plus the tab); a top-level id navigates like a rail click
    (st.page_link clears the query params, so Matches means the LIST). Only
    the active page renders, so switch before interacting with widgets that
    live on it.
    """
    if page in MATCH_TABS:
        mid = ss_get(at, "match") or ss_get(at, "demo_id")
        if mid is None:
            lib = store.list_demos()  # the latest opened one is first
            mid = lib[0]["id"] if lib else None
        if mid is None:
            # nothing to open: stay on the matches list
            at.query_params.pop("m", None)
            at.query_params.pop("tab", None)
        else:
            at.session_state["match"] = mid
            at.session_state["demo_id"] = mid
            at.session_state["match_tab"] = page
            at.query_params["m"] = mid
            at.query_params["tab"] = page
        run_page(at, "matches")
    else:
        at.query_params.clear()
        run_page(at, page)
    assert not at.exception, err_of(at)


def open_match(at, did, name="open the match again"):
    """Reopen a library demo as the match (what a list row click does)."""
    act(
        at,
        name,
        lambda: (
            at.session_state.__setitem__("match", did),
            at.session_state.__setitem__("demo_id", did),
            at.session_state.__setitem__("demo_change", False),
            at.query_params.__setitem__("m", did),
            run_page(at, "matches"),
        ),
    )


def open_duel(at, i, name=None):
    """A Duels row click: the duel window (st.dialog) opens over the page."""
    goto(at, "duels")
    act(
        at,
        name or f"duel window #{i}",
        lambda: at.session_state.__setitem__("tl_duel", i)
        or at.session_state.__setitem__("du_open", True)
        or at.run(),
    )


def act(at, name, fn):
    """Apply one UI interaction, re-run, and demand a clean run."""
    fn()
    assert not at.exception, f"{name}: {err_of(at)}"


def n_presets(at):
    """
    How many round presets the app offers (labels are localized,
    so only the count and the selected raw value are language-neutral).
    """

    sb = widget(at, "selectbox", "flt_preset")
    return (len(sb.options), sb.value) if sb else (0, None)


# preset selectbox contents by option count (see app.py:
# preset_opts; Custom needs n_rounds >= 2 and is always last). 5 comes
# from sides-derived halves without overtime (e.g. a 13-x FACEIT game).
_PRESETS_BY_COUNT = {
    1: ("all",),
    3: ("all", "pistols", "custom"),
    5: ("all", "first", "second", "pistols", "custom"),
    6: ("all", "first", "second", "ot", "pistols", "custom"),
}


def _shots_shown(at):
    """Shot count from the filter bar ('... N shots / M duels')."""
    for c in at.caption:
        m = re.search(r"(\d+) shots", c.value)
        if m:
            return int(m.group(1))
    return None


# the weapon filters live inside a popover, but Streamlit mounts popover
# bodies on every run: the widgets are always reachable in AppTest
