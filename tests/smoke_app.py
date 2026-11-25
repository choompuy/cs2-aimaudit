"""
UI smoke test: runs the REAL app.py through streamlit AppTest on synthetic
demos - no real .dem file needed.

test_core.py never executes app.py, so UI-level crashes slip through:
a one-round demo can kill the whole app (st.slider with
min_value == max_value raises StreamlitInvalidMinMaxError). This script
runs the actual UI end to end against fake demos and fails on any uncaught
exception, missing widget or unfriendly error.

Usage, from the repo root:
    python tests/smoke_app.py             # synthetic demos, fast subset (CI)
    python tests/smoke_app.py --all       # + the full interaction matrix
    python tests/smoke_app.py match.dem   # a REAL demo (.dem/.bz2/.gz/.zip/.zst)

The fake parser mirrors tests/test_core.py::FakeParser (duplicated so
both scripts stay runnable on their own). The local state file (recent
demos, last player) is redirected to a temp dir - the real
~/.cs2-aimaudit/state.json is never touched.
"""

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The Russian scenarios print Cyrillic, which a cp1252 console (stock
# Windows) cannot encode: force UTF-8. CI sets PYTHONUTF8=1 as well -
# this covers local runs.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402
from streamlit.util import calc_hash  # noqa: E402

# every file the app stores goes to a throw-away data folder (realpath: on
# Windows CI the temp dir has an 8.3 short name that realpath expands)
os.environ["AIMAUDIT_DATA_DIR"] = os.path.realpath(tempfile.mkdtemp(prefix="cs2aa_smoke_data_"))
import core  # noqa: E402
import store  # noqa: E402
from state import MATCH_TABS  # noqa: E402
from ui import schema  # noqa: E402

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


# ------------------------------------------------------------------- fakes
S1 = "76561111111111111"  # Alpha, T in the first half
S2 = "76561111111111112"  # Bravo, T
S3 = "76561111111111113"  # Charlie, CT
S4 = "76561111111111114"  # Delta, CT
IDS = [S1, S2, S3, S4]
NAMES = {S1: "Alpha", S2: "Bravo", S3: "Charlie", S4: "Delta"}


def f64_sid(s):
    """Simulate the polars->pandas float64 conversion quirk (rounds > 2^53)."""
    return float(int(s))


class FakeParser:
    """Slim copy of tests/test_core.py::FakeParser - see the module docstring."""

    demos: dict = {}

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
        # usercmd mouse deltas: config {"mouse": {(tick, sid): (dx, dy)}};
        # without the config the fake returns no usercmd columns (an old
        # parser / demo) and the app must degrade to "-", never crash
        if "usercmd_mouse_dx" in wanted_props or "usercmd_mouse_dy" in wanted_props:
            mo = self.d.get("mouse")
            rows = []
            for t in ticks or []:
                for (mt, sid), (dx, dy) in (mo or {}).items():
                    if mt == int(t):
                        rows.append(
                            {
                                "tick": int(t),
                                "steamid": f64_sid(sid),
                                "name": NAMES[sid],
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
        # scoreboard aggregates (game kills_total / deaths_total), see
        # tests/test_core.py::FakeParser; without a "totals" config the
        # fake returns an empty frame and the app skips the check
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
        return out.reset_index(drop=True)


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
                    "distance": [500] * len(death_t),
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
    """First exception message, for the check() output."""
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


def record_grids():
    """AppTest cannot look inside a custom component: keep its rows here."""
    draw = schema.data_table

    def rec(rows, cols, *, key, **kw):
        GRIDS[key] = pd.DataFrame({c.label: list(rows[c.key]) for c in cols})
        draw(rows, cols, key=key, **kw)

    schema.data_table = rec


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
            mid = core.load_state().get("last_demo")
            if mid is not None and store.get_demo(mid) is None:
                # the last demo left the library: any saved one
                lib = store.list_demos()
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
    check(f"page {page} renders", not at.exception, err_of(at))


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
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - the interaction itself failed
        check(name, False, f"interaction error: {e!r}")
        return
    check(name, not at.exception, err_of(at))


def n_presets(at):
    """
    How many round presets the app offers (labels are localized,
    so only the count and the selected raw value are language-neutral).
    """

    sb = widget(at, "segmented_control", "flt_preset")
    return (len(sb.options), sb.value) if sb else (0, None)


# preset segmented control contents by option count (see app.py:
# preset_opts; Custom needs n_rounds >= 2 and is always last). 5 comes
# from sides-derived halves without overtime (e.g. a 13-x FACEIT game).
_PRESETS_BY_COUNT = {
    1: ("all",),
    3: ("all", "pistols", "custom"),
    5: ("all", "first", "second", "pistols", "custom"),
    6: ("all", "first", "second", "ot", "pistols", "custom"),
}


def _shots_shown(at):
    """Shot count from the filters summary line ('... N shots / M duels')."""
    for c in at.caption:
        m = re.search(r"(\d+) shots", c.value)
        if m:
            return int(m.group(1))
    return None


# the filter controls live inside the Edit popover, but Streamlit mounts
# popover bodies on every run: the widgets are always reachable in
# AppTest (no open step needed - the old open_filters collapsed-card
# dance is gone with the card)


# ---------------------------------------------------------------- scenarios
def scenario_one_round():
    """A demo with a single round must not crash the app."""
    print("== one-round demo (slider crash regression) ==")
    at = open_demo(make_match([1]), "one_round.dem")
    check("runs without exception", not at.exception, err_of(at))
    check("no st.error on load", not at.error)
    check(
        "no round-range slider on a single round",
        widget(at, "slider", "flt_rounds") is None,
    )
    check("only the 'all' preset offered", n_presets(at) == (1, "all"))
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
    check(
        "duel window: the speed/aim/keys chart now lives in Duels (one plotly chart)",
        len(at.get("plotly_chart")) == 1,
        len(at.get("plotly_chart") or []),
    )
    check(
        "duel window: 'show in 2D replay' button",
        widget(at, "button", "du_to_replay") is not None,
    )
    check("the row click flag is consumed", not ss_get(at, "du_open"), ss_get(at, "du_open"))
    goto(at, "replay")
    check(
        "2D replay: the canvas component draws it, no plotly chart",
        not at.get("plotly_chart"),
        len(at.get("plotly_chart") or []),
    )
    check(
        "2D replay: the filter panel is hidden (the replay is the whole round)",
        widget(at, "segmented_control", "flt_preset") is None,
    )
    # the payload behind the canvas: the whole round, every player, with
    # HP / weapon / keys - checked straight against the fake demo so the
    # data contract is asserted, not just "no exception"
    _demo_path = os.path.realpath(
        store.demo_path(store.get_demo(at.session_state["demo_id"]))
    )
    _m = core.load_demo(_demo_path)
    check(
        "replay: the demo has a window for round 1",
        1 in _m.round_windows,
        _m.round_windows,
    )
    _rr = core.round_replay_data(_m, 1)
    check(
        "replay data: every player of the round + hp/weapon/keys parsed",
        set(_rr["frames"]) == set(IDS)
        and "health" in _rr["frames"][S1].columns
        and "active_weapon_name" in _rr["frames"][S1].columns
        and "FORWARD" in _rr["keys"],
        (sorted(_rr["frames"]), _rr["keys"]),
    )
    check(
        "replay data: round 1 events (3 shots, 1 kill, 1 hit)",
        len(_rr["shots"]) == 3 and len(_rr["kills"]) == 1 and len(_rr["hits"]) == 1,
        (_rr["shots"], _rr["kills"], _rr["hits"]),
    )
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
    check(
        "replay payload: CT group then T group, sorted by nickname inside a team; hp, weapon change, held keys",
        all(v == sorted(v, key=str.casefold) for v in _by_side.values())
        and _sides_seen == sorted(_sides_seen, key=lambda x: {"CT": 0, "T": 1}.get(x, 2))
        and _p0["hp"][0] == 100
        and _p0["w"][0][1] == "ak47"
        and all("kb" in p for p in _pl["players"] if p["sid"] == S1),
        (_names_sorted, _pl["keys"]),
    )
    check(
        "replay payload: every duel of the round as a card + locked segment + rounds bar",
        len(_pl["duels"]) == len(_all_duels) >= 1
        and _pl["duels"][0]["kn"] == NAMES[S1]
        and _pl["duels"][0]["vn"] == NAMES[S3]
        and _pl["duels"][0]["ki"] >= 0
        and _pl["rounds"] == [[1, "T", "bomb"]]
        and _pl["sel"] == 0
        and _pl["at"] == _pl["duels"][0]["a"]
        and "tel" in _pl["duels"][0],
        (_pl["duels"], _pl["rounds"], _pl["sel"], _pl["at"]),
    )


def scenario_round_replay():
    """The 2D replay: rounds bar, the hand-over from the duel window."""
    print("== 2D replay (rounds bar, duel hand-over) ==")
    at = open_demo(make_match([1, 2]), "rr_switch.dem")
    check("runs without exception", not at.exception, err_of(at))
    goto(at, "replay")
    check("starts at the first round", ss_get(at, "rr_round") == 1, ss_get(at, "rr_round"))
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
    check("fell back to round 1", ss_get(at, "rr_round") == 1, ss_get(at, "rr_round"))
    # the duel window's button hands the duel over: page, round, selection
    open_duel(at, 1, "open the round-2 duel")
    act(at, "show it in the 2D replay", lambda: at.button(key="du_to_replay").click().run())
    check("switched to the replay tab", ss_get(at, "match_tab") == "replay", ss_get(at, "match_tab"))
    check("on the duel's own round", ss_get(at, "rr_round") == 2, ss_get(at, "rr_round"))
    sel = ss_get(at, "rr_sel")
    check(
        "the duel is handed over (round, tick, victim)",
        sel is not None and sel[0] == 2 and sel[2] == S3,
        sel,
    )
    act(
        at,
        "rounds bar switch drops the hand-over",
        lambda: at.session_state.__setitem__("rr_round", 1)
        or at.session_state.__delitem__("rr_sel")
        or at.run(),
    )


def scenario_nav():
    """The sidebar rail (st.page_link over st.navigation): navigation,
    the ?m&tab URL state."""
    print("== sidebar navigation ==")
    at = open_demo(make_match([1, 2]), "nav.dem")
    did = at.session_state["match"]
    # the rail: one st.page_link per section (labels are localized; the
    # four ids in order are enough to pin the rail itself)
    _labels = [str(link.value).strip() for link in at.sidebar.page_link]
    check(
        "nav: four sidebar page_links render",
        len(_labels) == 4 and all(_labels),
        _labels,
    )
    check(
        "nav: the match view syncs ?m",
        at.query_params.get("m") == did,
        dict(at.query_params),
    )
    # a rail click is a page_link navigation: the page switches and the
    # query params clear, so the sidebar Matches item opens the LIST (an
    # open match is left) - goto() applies exactly that
    goto(at, "matches")
    check(
        "nav: the list is shown, the match is closed",
        ss_get(at, "page") == "matches" and ss_get(at, "match") is None,
    )
    goto(at, "settings")
    check(
        "nav: the settings page renders",
        ss_get(at, "page") == "settings" and not at.exception,
        err_of(at),
    )
    goto(at, "guide")
    check("nav: the guide page renders without a demo", ss_get(at, "page") == "guide" and not at.exception, err_of(at))
    goto(at, "dashboard")
    check(
        "nav: the dashboard stub renders with a way to Matches",
        ss_get(at, "page") == "dashboard" and widget(at, "button", "dash_go_matches") is not None,
    )
    act(at, "nav: dashboard -> Matches", lambda: at.button(key="dash_go_matches").click().run())
    check("nav: back on the list", ss_get(at, "page") == "matches" and ss_get(at, "match") is None)
    # ?m&tab: a copied link reopens the exact screen; garbage is ignored
    at3 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at3.query_params["m"] = did
    at3.query_params["tab"] = "duels"
    at3.run()
    check(
        "nav: ?m=<id>&tab=duels reopens the duels tab",
        ss_get(at3, "page") == "matches"
        and ss_get(at3, "match") == did
        and ss_get(at3, "match_tab") == "duels"
        and not at3.exception,
        err_of(at3),
    )
    at4 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at4.query_params["m"] = "zzzz-not-an-id"
    at4.query_params["tab"] = "nonsense"
    at4.run()
    check(
        "nav: broken ?m&tab values fall back safely",
        ss_get(at4, "page") == "matches" and ss_get(at4, "match") is None and not at4.exception,
    )


def scenario_settings():
    """The Settings page: sections, and the sliders surviving page switches."""
    print("== settings page ==")
    at = open_demo(make_match([1, 2]), "settings.dem")
    goto(at, "settings")
    check(
        "settings: the analysis sliders render (moved from the sidebar)",
        widget(at, "slider", "thr") is not None
        and widget(at, "slider", "window_s") is not None
        and widget(at, "slider", "safe_dist") is not None
        and widget(at, "slider", "strict_dist") is not None
        and widget(at, "button", "btn_reset_set") is not None,
    )
    check(
        "settings: no analysis slider in the sidebar anymore",
        all(widget(at.sidebar, "slider", k) is None for k in ("thr", "window_s", "safe_dist", "strict_dist"))
        and widget(at.sidebar, "selectbox", "lang") is not None,
    )
    act(at, "settings: thr to 200", lambda: at.slider(key="thr").set_value(200).run())
    act(at, "settings: safe_dist to 1000", lambda: at.slider(key="safe_dist").set_value(1000).run())
    # the values survive Settings -> Matches -> a match
    goto(at, "matches")
    check("settings: on the list", ss_get(at, "page") == "matches" and not at.exception, err_of(at))
    check("settings: thr survived the trip to the list", ss_get(at, "thr") == 200, ss_get(at, "thr"))
    goto(at, "overview")
    check(
        "settings: the match reopened with the slider values intact",
        ss_get(at, "match") is not None
        and ss_get(at, "thr") == 200
        and ss_get(at, "safe_dist") == 1000
        and not at.exception,
        (ss_get(at, "thr"), ss_get(at, "safe_dist"), err_of(at)),
    )
    # the reset restores the defaults (plain keys, the sliders follow)
    goto(at, "settings")
    act(at, "settings: reset to defaults", lambda: at.button(key="btn_reset_set").click().run())
    check(
        "settings: reset restores the defaults",
        ss_get(at, "thr") == 40 and ss_get(at, "window_s") == 3.0
        and ss_get(at, "safe_dist") == 500 and ss_get(at, "strict_dist") == 1500,
        (ss_get(at, "thr"), ss_get(at, "window_s"), ss_get(at, "safe_dist"), ss_get(at, "strict_dist")),
    )
    # accounts: the fields persist to accounts.json
    act(
        at,
        "settings: steam id typed",
        lambda: at.text_input(key="mt_steamid").set_value("76561111111111111").run(),
    )
    check(
        "settings: steam id persisted to accounts.json",
        store.read_accounts().get("steam_id64") == "76561111111111111",
        store.read_accounts(),
    )
    # the Steam helper card lives here now
    if not __import__("sources").steam.helper_installed():
        check(
            "settings: the Steam helper card offers the install",
            widget(at, "button", "mt_helper_install") is not None,
        )
    # the library and storage section (moved from the change-demo block)
    check(
        "settings: the library panel renders (clear cache, per-demo delete)",
        widget(at, "button", "lib_clear") is not None
        and widget(at, "button", f"del_{at.session_state['match']}") is not None,
    )
    # privacy names the offline gate
    check(
        "settings: the privacy section names AIMAUDIT_OFFLINE",
        any("AIMAUDIT_OFFLINE" in str(c.value) for c in at.caption),
    )
    # clear cache (the Library and storage section) keeps the demos
    act(at, "settings: clear cache", lambda: at.button(key="lib_clear").click().run())
    check(
        "settings: clear cache empties the caches, demos stay",
        store.storage_report()["parsed"] == 0 and store.get_demo(at.session_state["match"]) is not None,
        (store.storage_report()["parsed"],),
    )
    # deleting the open match from the library section falls back to the list
    did = at.session_state["match"]
    act(at, "settings: delete: ask", lambda: at.button(key=f"del_{did}").click().run())
    act(at, "settings: delete: confirm", lambda: at.button(key=f"delyes_{did}").click().run())
    check(
        "settings: deleting the open match drops it (back to the list from a match view)",
        ss_get(at, "page") == "settings" and ss_get(at, "match") is None and store.get_demo(did) is None,
        (ss_get(at, "page"), ss_get(at, "match")),
    )


def scenario_player_stepper():
    """Compact header: the demo row and the ▲/▼ player stepper."""
    print("== player stepper (compact header) ==")
    at = open_demo(make_match([1, 2]), "stepper.dem")
    check("runs without exception", not at.exception, err_of(at))
    check(
        "prev/next player buttons rendered",
        widget(at, "button", "me_prev") is not None
        and widget(at, "button", "me_next") is not None,
    )
    check(
        "the match header offers the way back to the list",
        widget(at, "button", "mt_back") is not None
        and widget(at, "button", "demo_change_btn") is None,
    )
    # the compact row replaces the old add block after the first load
    act(at, "next player (Bravo)", lambda: at.button(key="me_next").click().run())
    check("next moved the selection", ss_get(at, "me") == S2, ss_get(at, "me"))
    caps = [c.value for c in at.caption]
    check(
        "compact demo row names the file",
        any("**stepper**" in str(c) for c in caps),
        [str(c)[:40] for c in caps][:4],
    )
    act(
        at,
        "prev player (back to Alpha)",
        lambda: at.button(key="me_prev").click().run(),
    )
    check("prev returned to the first player", ss_get(at, "me") == S1, ss_get(at, "me"))
    act(
        at,
        "prev wraps around to the last player",
        lambda: at.button(key="me_prev").click().run(),
    )
    check("prev wrapped to Delta", ss_get(at, "me") == S4, ss_get(at, "me"))
    act(
        at,
        "next wraps to the first player",
        lambda: at.button(key="me_next").click().run(),
    )
    check("next wrapped to Alpha", ss_get(at, "me") == S1, ss_get(at, "me"))
    # '← Матчи' leaves the match without losing it: a fresh row click on
    # the list brings it straight back
    act(at, "back to the matches list", lambda: at.button(key="mt_back").click().run())
    check(
        "back keeps the demo loaded, the match closed",
        ss_get(at, "page") == "matches"
        and ss_get(at, "match") is None
        and ss_get(at, "demo_loaded") == _DEMO_IDS["stepper.dem"],
        (ss_get(at, "page"), ss_get(at, "match"), ss_get(at, "demo_loaded")),
    )
    open_match(at, _DEMO_IDS["stepper.dem"], "reopen the same match")
    check(
        "reopen lands on the same match",
        ss_get(at, "match") == _DEMO_IDS["stepper.dem"]
        and ss_get(at, "demo_loaded") == _DEMO_IDS["stepper.dem"]
        and not at.exception,
        err_of(at),
    )


def scenario_duel_strip():
    """Fast duel/round navigation: the strip (in the duel window) owns the selection."""
    print("== duel window strip (fast duel/round navigation) ==")
    at = open_demo(make_match([1, 2, 13]), "strip.dem")
    check("runs without exception", not at.exception, err_of(at))
    open_duel(at, 0, "open the duel window")
    # the old long "pick a duel" selectbox and the round selectbox are
    # gone: the strip chips, its hotkeys and the Duels row click move the
    # selection now, and they all write the plain tl_duel session key
    check("no tl_duel selectbox anymore", widget(at, "selectbox", "tl_duel") is None)
    check("no tl_round selectbox anymore", widget(at, "selectbox", "tl_round") is None)
    check(
        "hotkey hint rendered under the strip",
        any("Shift+←" in str(c.value) for c in at.caption),
        [str(c.value)[:60] for c in at.caption][:6],
    )
    act(
        at,
        "open duel 1 (a chip click writes the same key)",
        lambda: at.session_state.__setitem__("tl_duel", 1) or at.run(),
    )
    check("moved to duel 1", ss_get(at, "tl_duel") == 1, ss_get(at, "tl_duel"))
    open_duel(at, 99, "stale pick beyond the duel list clamps")
    check("clamped back to duel 0", ss_get(at, "tl_duel") == 0, ss_get(at, "tl_duel"))
    act(at, "language ru", lambda: at.selectbox(key="lang").select("ru").run())
    open_duel(at, 0, "ru: reopen the duel window")
    check(
        "ru: hotkey hint rendered",
        any("Shift+←" in str(c.value) for c in at.caption),
        [str(c.value)[:60] for c in at.caption][:6],
    )


def scenario_roundtrip():
    """The path Matches -> row -> tabs -> back,
    and nothing is lost on the way."""
    print("== match view round trip ==")
    at = open_demo(make_match([1, 2, 13]), "roundtrip.dem")
    did = at.session_state["match"]
    act(at, "roundtrip: first-half preset", lambda: ctl(at, "segmented_control", "flt_preset").select("first").run())
    check("roundtrip: preset set", ss_get(at, "flt_preset") == "first", ss_get(at, "flt_preset"))
    act(at, "roundtrip: to the duels tab", lambda: ctl(at, "segmented_control", "match_tab").select("duels").run())
    check("roundtrip: on duels", ss_get(at, "match_tab") == "duels" and not at.exception, err_of(at))
    act(at, "roundtrip: back to the list", lambda: at.button(key="mt_back").click().run())
    check(
        "roundtrip: the list is back, the demo stays loaded",
        ss_get(at, "page") == "matches" and ss_get(at, "match") is None
        and ss_get(at, "demo_loaded") == did and not at.exception,
        (ss_get(at, "page"), ss_get(at, "match"), err_of(at)),
    )
    # the list's own filters live next to the analysis ones
    act(at, "roundtrip: a list filter", lambda: ctl(at, "segmented_control", "mflt_result").select("tie").run())
    check("roundtrip: the list filter set", ss_get(at, "mflt_result") == "tie", ss_get(at, "mflt_result"))
    # back into the match: the analysis filter survived the trip through the
    # list (a fresh row click opens it on the overview tab)
    open_match(at, did, "roundtrip: reopen the match")
    check(
        "roundtrip: the analysis filter survived the list trip",
        ss_get(at, "flt_preset") == "first" and ss_get(at, "match") == did and not at.exception,
        (ss_get(at, "flt_preset"), ss_get(at, "match"), err_of(at)),
    )
    check(
        "roundtrip: ?m and the list filters ride in the URL",
        at.query_params.get("m") == did and at.query_params.get("fres") == "tie",
        dict(at.query_params),
    )
    # a copied link reopens the exact screen: the match on its tab
    at2 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at2.query_params["m"] = did
    at2.query_params["tab"] = "duels"
    at2.run()
    check(
        "roundtrip: ?m=<id>&tab=duels reopens the duels tab",
        ss_get(at2, "page") == "matches" and ss_get(at2, "match") == did
        and ss_get(at2, "match_tab") == "duels" and not at2.exception,
        err_of(at2),
    )


def scenario_two_rounds():
    print("== two-round demo (presets, custom slider, filters) ==")
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "two_rounds.dem")
    check("runs without exception", not at.exception, err_of(at))
    check(
        "no slider while preset=All (slider lives in Custom)",
        widget(at, "slider", "flt_rounds") is None,
    )
    check("presets all+pistols+custom", n_presets(at) == (3, "all"))
    act(
        at,
        "pistols preset",
        lambda: ctl(at, "segmented_control", "flt_preset").select("pistols").run(),
    )
    check("preset switched to pistols", ss_get(at, "flt_preset") == "pistols")
    act(
        at,
        "custom preset",
        lambda: ctl(at, "segmented_control", "flt_preset").select("custom").run(),
    )
    check(
        "custom shows the range slider",
        widget(at, "slider", "flt_rounds") is not None,
    )
    act(
        at,
        "manual range (2,2)",
        lambda: at.slider(key="flt_rounds").set_value((2, 2)).run(),
    )
    check("slider keeps Custom selected", ss_get(at, "flt_preset") == "custom")
    act(
        at,
        "weapon filter awp (More popover)",
        lambda: at.multiselect(key="flt_weapons").set_value(["awp"]).run(),
    )
    act(
        at,
        "class chips sniper",
        lambda: ctl(at, "pills", "flt_classes").select("sniper").run(),
    )
    check(
        "side control is a segmented control",
        widget(at, "segmented_control", "flt_side") is not None,
    )
    act(
        at,
        "side filter T",
        lambda: ctl(at, "segmented_control", "flt_side").select("T").run(),
    )
    check(
        "result control not on the overview page",
        widget(at, "segmented_control", "flt_res") is None,
    )
    goto(at, "duels")
    check(
        "result control on the duels page",
        widget(at, "segmented_control", "flt_res") is not None,
    )
    act(
        at,
        "result filter death (no such duels)",
        lambda: ctl(at, "segmented_control", "flt_res").select("death").run(),
    )
    check("death filter leaves the page clean", not at.exception, err_of(at))


def scenario_long25():
    print("== 25-round demo (halves, overtime, pistol rounds) ==")
    at = open_demo(
        make_match([1, 13, 25], weapons=("ak47", "awp", "deagle")), "long25.dem"
    )
    check("runs without exception", not at.exception, err_of(at))
    check(
        "all six presets offered",
        n_presets(at) == (6, "all"),
    )
    for p in ("first", "second", "ot", "pistols"):
        act(
            at,
            f"preset {p}",
            lambda p=p: ctl(at, "segmented_control", "flt_preset").select(p).run(),
        )
    act(
        at,
        "side filter CT (round 13 only)",
        lambda: ctl(at, "segmented_control", "flt_side").select("CT").run(),
    )
    open_duel(at, 1, "duel window 1 of 3")


def scenario_filter_panel():
    """Popover panel: distance bands, reset, page scope, links."""
    print("== filter panel (popover, distance, reset, links) ==")
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "panel.dem")
    check("runs without exception", not at.exception, err_of(at))
    # the controls live behind the Edit popover, and Streamlit mounts a
    # popover body on every run: they are reachable without opening it
    base = _shots_shown(at)
    check("summary line shows the shot count", bool(base), str(base))
    for k in ("flt_side", "flt_preset", "flt_dist"):
        check(
            f"{k} segmented control visible (behind the popover)",
            widget(at, "segmented_control", k) is not None,
        )
    check("class pills visible (behind the popover)", widget(at, "pills", "flt_classes") is not None)
    check(
        "no Reset without active filters",
        widget(at, "button", "flt_reset") is None,
    )
    act(
        at,
        "weapon filter awp",
        lambda: at.multiselect(key="flt_weapons").set_value(["awp"]).run(),
    )
    filtered = _shots_shown(at)
    check(
        "filtered count reported and smaller",
        filtered is not None and 0 <= filtered < base,
        f"{filtered} of {base}",
    )
    for band in ("close", "mid", "long"):
        act(
            at,
            f"distance band {band}",
            lambda b=band: ctl(at, "segmented_control", "flt_dist").select(b).run(),
        )
        check(f"band {band} leaves the app clean", not at.exception, err_of(at))
    check("reset button offered", widget(at, "button", "flt_reset") is not None)
    act(at, "reset filters", lambda: at.button(key="flt_reset").click().run())
    check(
        "reset restores the full view", _shots_shown(at) == base, str(_shots_shown(at))
    )
    # a closed popover keeps every selection (its widgets stay mounted)
    act(
        at,
        "weapon filter awp again",
        lambda: at.multiselect(key="flt_weapons").set_value(["awp"]).run(),
    )
    act(
        at,
        "plain rerun with the popover closed",
        lambda: at.button(key="mt_back").click().run(),
    )
    open_match(at, at.session_state["demo_id"], "back into the match")
    check(
        "closed popover kept the weapon filter",
        ss_get(at, "flt_weapons") == ["awp"],
        ss_get(at, "flt_weapons"),
    )
    check(
        "summary names the filter",
        any("awp" in str(c.value) for c in at.caption),
    )
    check(
        "Reset offered while filters are active",
        widget(at, "button", "flt_reset") is not None,
    )
    act(
        at,
        "reset from the summary row",
        lambda: at.button(key="flt_reset").click().run(),
    )
    check(
        "summary-row reset cleared the weapon filter", ss_get(at, "flt_weapons") == []
    )
    check("reset cleared the distance band", ss_get(at, "flt_dist") == "all")
    check("reset cleared the weapon filter", ss_get(at, "flt_weapons") == [])
    # Result is a duels-page control; its value must survive page hops
    check(
        "no result control on the overview page",
        widget(at, "segmented_control", "flt_res") is None,
    )
    goto(at, "duels")
    act(
        at,
        "result kills only",
        lambda: ctl(at, "segmented_control", "flt_res").select("kill").run(),
    )
    goto(at, "overview")
    check(
        "result value survives other pages",
        ss_get(at, "flt_res") == "kill",
    )
    check(
        "result control still hidden on the overview",
        widget(at, "segmented_control", "flt_res") is None,
    )
    # the guide page hides the whole panel but keeps every selection
    act(
        at,
        "side filter T before the guide",
        lambda: ctl(at, "segmented_control", "flt_side").select("T").run(),
    )
    goto(at, "guide")
    check(
        "guide hides the panel",
        widget(at, "segmented_control", "flt_preset") is None,
    )
    goto(at, "overview")
    check(
        "side filter survives the guide page",
        ss_get(at, "flt_side") == "T",
    )
    # link round-trip: filters -> URL -> fresh app
    at3 = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "panel3.dem")
    act(
        at3,
        "distance band mid (for the link)",
        lambda: ctl(at3, "segmented_control", "flt_dist").select("mid").run(),
    )
    act(at3, "weapons for the link", lambda: at3.multiselect(key="flt_weapons").set_value(["ak47"]).run())
    act(at3, "custom rounds for the link", lambda: ctl(at3, "segmented_control", "flt_preset").select("custom").run())
    act(at3, "round 2 for the link", lambda: at3.slider(key="flt_rounds").set_value((2, 2)).run())
    link = dict(at3.query_params)
    check("the filtered view is in the URL", link.get("dist") == ["mid"] or link.get("dist") == "mid", link)
    at4 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    for k, v in link.items():
        at4.query_params[k] = v
    at4.query_params["res"] = "nonsense"
    at4.query_params["lang"] = "xx"
    at4.run()
    check("link restores without exception", not at4.exception, err_of(at4))
    check(
        "link restores the filters, unknown values fall back",
        (ss_get(at4, "flt_dist"), ss_get(at4, "flt_weapons"), ss_get(at4, "flt_preset"),
         tuple(ss_get(at4, "flt_rounds")), ss_get(at4, "flt_res"), ss_get(at4, "lang"))
        == ("mid", ["ak47"], "custom", (2, 2), "both", "en"),
        {k: ss_get(at4, k) for k in ("flt_dist", "flt_weapons", "flt_preset", "flt_rounds", "flt_res", "lang")},
    )
    # a chip click clears exactly its filter
    goto(at4, "duels")
    act(at4, "deaths only", lambda: ctl(at4, "segmented_control", "flt_res").select("death").run())
    chips = [b.key for b in at4.button if (b.key or "").startswith("flt_chip_")]
    act(at4, "the result chip (the last one) clears it", lambda: at4.button(key=chips[-1]).click().run())
    check("the result filter is cleared by its chip", ss_get(at4, "flt_res") == "both", ss_get(at4, "flt_res"))


def scenario_knife_round():
    """FACEIT shape: a knife round before begin_new_match ."""
    print("== knife-round demo (live match start, banner) ==")

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
                        "distance": [500],
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
    check("runs without exception", not at.exception, err_of(at))
    banners = [c.value for c in at.caption if "span title=" in str(c.value)]
    check("excluded banner shown", len(banners) == 1, banners)
    check(
        "banner says knife round (1)",
        any("knife round (1)" in b for b in banners),
        banners,
    )
    # the knife kill must not leak into the live data: one duel per round
    goto(at, "duels")
    check("no knife duel leaked", not at.exception, err_of(at))
    # the same banner in russian
    at = open_demo(make_knife_match([1, 2]), "knife_round.dem", lang="ru")
    check("ru: runs without exception", not at.exception, err_of(at))
    banners_ru = [c.value for c in at.caption if "span title=" in str(c.value)]
    check(
        "ru: banner shown",
        any("ножевой раунд (1)" in b for b in banners_ru),
        banners_ru,
    )


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


def scenario_links():
    """Choose file LINK demos (no copy); missing file; locate; remove."""
    print("== linked demos ==")
    import nativedialog

    real_files = nativedialog.pick_files
    try:
        at = open_demo(make_match([1, 2]), "links_base.dem")
        check("links: base demo opens", not at.exception, err_of(at))
        # the Add match button on the Matches page opens the file dialog
        goto(at, "matches")
        check(
            "links: the Add match button is offered",
            widget(at, "button", "mt_add") is not None,
        )

        # --- choosing a file links it by its path; nothing is copied
        p = _plain_demo("link_a", "my_link.dem")
        copies_before = sorted(os.listdir(store.demos_dir()))
        nativedialog.pick_files = lambda *a, **k: [p]
        act(at, "links: choose a demo file", lambda: at.button(key="mt_add").click().run())
        lid = store.link_id(p)
        e = store.get_demo(lid) or {}
        check("links: choosing a file links it by its path", e.get("mode") == "link" and e.get("path") == os.path.abspath(p), e)
        check("links: ... nothing was copied into demos/", sorted(os.listdir(store.demos_dir())) == copies_before)
        check(
            "links: ... and the app opened it as the match",
            at.session_state["demo_id"] == lid and at.session_state["match"] == lid
            and at.session_state["demo_loaded"] == lid and not at.exception,
            err_of(at),
        )
        check("links: ... the map was remembered in the library", (store.get_demo(lid) or {}).get("map") == "de_smoke")

        # --- the file disappears: a clear message that names the path, not a crash
        moved = os.path.join(os.path.dirname(p), "moved_here.dem")
        os.rename(p, moved)
        at = fresh_at(demo_id=lid, match=lid)
        check("links: a missing file does not crash", not at.exception, err_of(at))
        check(
            "links: ... the error names the file",
            any(os.path.basename(p) in str(x.value) for x in at.error),
            [str(x.value)[:80] for x in at.error],
        )
        check(
            "links: ... the no-file card offers Locate, Remove and the way back",
            widget(at, "button", "lib_locate") is not None
            and widget(at, "button", "lib_remove_missing") is not None
            and widget(at, "button", "mt_back_missing") is not None,
        )
        check("links: ... the library lists it as missing", store.get_demo(lid)["status"] == "missing")
        FakeParser.demos[os.path.realpath(moved)] = make_match([1, 2])
        nativedialog.pick_files = lambda *a, **k: [moved]
        act(at, "links: locate the file", lambda: at.button(key="lib_locate").click().run())
        e = store.get_demo(lid) or {}
        check(
            "links: locate points the entry at the new file and it opens",
            e.get("path") == os.path.abspath(moved) and e.get("status") == "ok" and not at.exception and not at.error,
            (e, err_of(at)),
        )

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
        check(
            "links: the list row of a moved demo offers the locate action",
            _ml_row.missing and _mv.action_of(_t, _ml_row) == _t("act_locate"),
            _mv.action_of(_t, _ml_row),
        )
        _i = _ml_rows.index(_ml_row)
        nativedialog.pick_files = lambda *a, **k: [moved2]
        act(
            at2,
            "links: a row click locates the file",
            lambda: at2.session_state.__setitem__("mt_action_row", _i) or at2.run(),
        )
        e = store.get_demo(lid) or {}
        check(
            "links: the row click pointed the entry at the new file",
            e.get("path") == os.path.abspath(moved2) and e.get("status") == "ok" and not at2.exception,
            (e, err_of(at2)),
        )

        # --- removing a linked demo never deletes the user's file (the
        # Library and storage section of Settings owns the button)
        at = at2
        goto(at, "settings")
        act(at, "links: remove from the list", lambda: at.button(key=f"rm_{lid}").click().run())
        check(
            "links: remove drops the entry, the user's file STAYS",
            os.path.isfile(moved2) and store.get_demo(lid) is None and not at.exception,
            err_of(at),
        )

        # --- no dialog available (no tkinter): a hint, never a crash
        goto(at, "matches")
        nativedialog.pick_files = lambda *a, **k: None
        act(at, "links: choose a file without a dialog", lambda: at.button(key="mt_add").click().run())
        check(
            "links: no dialog -> a hint, not a crash",
            not at.exception and any("tkinter" in str(w.value) for w in at.warning),
            [str(w.value)[:60] for w in at.warning],
        )
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


def scenario_bad_input():
    print("== bad input ==")
    did = register_demo(make_match([1, 2]), "vanishing.dem")
    os.remove(store.demo_path(store.get_demo(did)))  # deleted from demos/ by hand
    # opened as a match (a Matches row): the vanished copy leaves the list
    # (a copy is adopted from the folder, so its entry goes with the file) -
    # the app must land on the list, not crash
    at = fresh_at(demo_id=did, match=did)
    check("vanished demo file: no exception", not at.exception, err_of(at))
    check(
        "vanished copy drops the stale match, back to the list",
        ss_get(at, "page") == "matches" and ss_get(at, "match") is None and not at.error,
        (ss_get(at, "page"), ss_get(at, "match")),
    )
    with _empty_library():
        at = fresh_at()
        check("empty library: no exception", not at.exception, err_of(at))
        check(
            "empty library: the matches list says what to do",
            len(at.subheader) >= 1 and not at.error,
        )


def scenario_library():
    """One input: saved demos in a list, delete, clear cache, meta, switching."""
    print("== demo library ==")
    idb = register_demo(make_match([1]), "lib_b.dem")
    at = open_demo(make_match([1, 2]), "lib_a.dem")
    ida = at.session_state["match"]
    check("library demo opens", not at.exception, err_of(at))
    check(
        "the match header is the compact way around (no picker, no change button)",
        widget(at, "selectbox", "lib_pick") is None and widget(at, "button", "demo_change_btn") is None,
    )
    check("there is no path field any more", not any(getattr(w, "key", "") == "demo_path" for w in at.text_input))
    meta = store.get_demo(ida)
    check(
        "a loaded demo remembers its map and players in the library",
        meta["map"] == "de_smoke" and len(meta["players"]) >= 2 and meta["tickrate"] == 64,
        meta,
    )
    check("opening a demo marks it as the latest", store.list_demos()[0]["id"] == ida)
    check("the parsed demo was cached to the data folder", store.storage_report()["parsed"] > 0)
    # switch to the other saved demo: its row in the Matches list
    goto(at, "matches")
    rows = core.build_rows([], store.list_demos())
    _ib = next(i for i, r in enumerate(rows) if r.demo_id == idb)
    act(
        at,
        "a list row opens the other saved demo",
        lambda: at.session_state.__setitem__("mt_action_row", _ib) or at.run(),
    )
    check(
        "the row click switches the match",
        at.session_state["demo_id"] == idb and at.session_state["match"] == idb and not at.exception,
        err_of(at),
    )
    check("the switched demo is now the latest", store.list_demos()[0]["id"] == idb)
    # a new browser session starts on the matches list; the last demo is
    # one row click away
    at = fresh_at()
    check(
        "a new session lands on the matches list",
        ss_get(at, "page") == "matches" and ss_get(at, "match") is None and not at.exception,
        err_of(at),
    )
    goto(at, "overview")
    check("the last opened demo is one click away", ss_get(at, "match") == idb, ss_get(at, "match"))
    # the clear-cache / delete buttons live on Settings now (scenario_settings
    # locks them); here only the library state itself is checked
    check(
        "both demos are still in the library",
        store.get_demo(ida) is not None and store.get_demo(idb) is not None,
    )


def scenario_matrix():
    """--all: every widget combination that a user can realistically click."""
    demos = {
        "two_rounds.dem": make_match([1, 2], weapons=("ak47", "awp")),
        "long25.dem": make_match([1, 13, 25], weapons=("ak47", "awp", "deagle")),
    }
    for lang in ("en", "ru"):
        for fname, demo in demos.items():
            print(f"== matrix: {fname}, {lang} ==")
            at = open_demo(demo, fname, lang=lang)
            check(f"open {lang}", not at.exception, err_of(at))
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
                    lambda p=p: ctl(at, "segmented_control", "flt_preset")
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


def scenario_tables():
    """
    Basic/Full table switches on the custom grids (the "All columns"
    toggles still pick which columns the component receives).
    """
    print("== tables: Basic/Full switches ==")
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "tables.dem")
    check("runs without exception", not at.exception, err_of(at))
    check("overview toggle exists", widget(at, "toggle", "ov_full") is not None)
    act(
        at,
        "overview: all columns",
        lambda: at.toggle(key="ov_full").set_value(True).run(),
    )
    goto(at, "duels")
    check("duels toggle exists", widget(at, "toggle", "du_full") is not None)
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


def scenario_context():
    """
    Round seconds in the duels table, the by-zone table, and the
    flashed/slowed discipline tips. A 25-round fake match: Alpha's t0+2
    shot is flashed every round (50% of his shots), Charlie's t0+5 answer
    is damage-slowed every round (100% of his).
    """
    print("== shot/duel context (round s, zones, flashed/slowed) ==")
    at = open_demo(make_match(list(range(1, 26))), "context.dem")
    check("runs without exception", not at.exception, err_of(at))

    # tips live on the shooting page: Alpha fired 25 of 50 shots flashed
    # (50% >= 8%) -> the blind-fire verdict; 0 slowed shots -> no verdict
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    check("flashed tip shown for Alpha", "while flashed" in tips_txt)
    check(
        "no slowed tip for Alpha (0 slowed shots)",
        "slowed by recent damage" not in tips_txt,
    )

    goto(at, "duels")

    # the by-zone table: Alpha = 25 kills from BombsiteA, 0 deaths
    ztabs = [GRIDS.pop("du_zones")] if "du_zones" in GRIDS else []
    check("zone table rendered", len(ztabs) == 1, [list(v.columns) for v in ztabs])
    if ztabs:
        z = ztabs[0]
        check(
            "zone table: BombsiteA 25/0 +25",
            len(z) == 1
            and z.iloc[0]["Zone"] == "BombsiteA"
            and z.iloc[0]["Kills"] == 25
            and z.iloc[0]["Deaths"] == 0
            and z.iloc[0]["K-D"] == 25,
            z.to_dict(),
        )

    # Charlie: his zone table flips to the victim zones (deaths in Middle),
    # and every answer shot is damage-slowed -> the slowed verdict
    act(
        at,
        "switch player (Charlie)",
        lambda: at.selectbox(key="me").select(S3).run(),
    )
    ztabs = [GRIDS.pop("du_zones")] if "du_zones" in GRIDS else []
    if ztabs:
        z = ztabs[0]
        check(
            "zone table (Charlie): Middle 0/25 -25",
            len(z) == 1
            and z.iloc[0]["Zone"] == "Middle"
            and z.iloc[0]["Kills"] == 0
            and z.iloc[0]["Deaths"] == 25
            and z.iloc[0]["K-D"] == -25,
            z.to_dict(),
        )
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    check("slowed tip shown for Charlie", "slowed by recent damage" in tips_txt)
    check(
        "no flashed tip for Charlie (0 flashed shots)", "while flashed" not in tips_txt
    )

    # russian labels: the new tables/columns must hold the longer strings
    goto(at, "duels")
    act(
        at,
        "context tables in russian",
        lambda: at.selectbox(key="lang").select("ru").run(),
    )
    check("ru: zone table renders", not at.exception, err_of(at))
    check(
        "ru: zone table localized",
        list(GRIDS["du_zones"].columns) == ["Зона", "Килы", "Смерти", "К-С"],
        list(GRIDS["du_zones"].columns),
    )


def scenario_first_shots():
    """
    First-shot discipline: first_shot/first_acc in the tables and the
    mid-swing tip from usercmd mouse deltas. 30 rounds: Alpha's t0 shot
    is always the first of an engagement (the t0+2 hit is a follow-up),
    12 of those 30 first shots come after a big mouse swing (>= 2x his
    own median) -> the verdict; Charlie has no mouse rows at all -> the
    tile degrades to "-" and no verdict.
    """
    print("== first shots (accuracy, mouse swing) ==")
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
    check("runs without exception", not at.exception, err_of(at))

    # the state file remembers the previous scenario's player - pin Alpha
    act(
        at,
        "switch player (Alpha)",
        lambda: at.selectbox(key="me").select(S1).run(),
    )
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    check("swing tip shown for Alpha", "still swinging fast" in tips_txt)

    act(at, "all metrics", lambda: at.toggle(key="sh_all").set_value(True).run())
    labels = {m.label: m for m in at.metric}
    check("first-acc tile present", "1st-shot accuracy" in labels, list(labels))
    m = labels.get("1st-shot accuracy")
    if m:
        check("first-acc value 0% (t0 always misses)", m.value == "0%", m.value)
        check("first-acc delta vs follow-ups", m.delta == "-100 pp", m.delta)
    m = labels.get("First shots")
    check(
        "first-shot count 30", m is not None and m.value == "30", m.value if m else ""
    )
    m = labels.get("Mouse move before 1st shot")
    check(
        "median swing 2 counts (calm majority)",
        m is not None and m.value == "2",
        m.value if m else "",
    )

    # Charlie: no mouse rows -> flick_move NA -> tile "-" and no verdict
    act(
        at,
        "switch player (Charlie)",
        lambda: at.selectbox(key="me").select(S3).run(),
    )
    tips_txt = "\n".join(m.value for m in at.markdown)
    check("no swing tip without mouse data", "still swinging fast" not in tips_txt)
    labels = {m.label: m for m in at.metric}
    m = labels.get("Mouse move before 1st shot")
    check(
        "swing tile degrades to dash",
        m is not None and m.value == "—",
        m.value if m else "",
    )

    # a demo WITHOUT usercmd data: no crash, no verdict, tile "-"
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "no_mouse.dem")
    check("no-mouse demo runs", not at.exception, err_of(at))
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
    check("no swing tip on old demo", "still swinging fast" not in tips_txt)
    labels = {m.label: m for m in at.metric}
    m = labels.get("Mouse move before 1st shot")
    check(
        "no-mouse swing tile dash",
        m is not None and m.value == "—",
        m.value if m else "",
    )

    # russian labels render (the longer strings must not break columns)
    act(
        at,
        "first shots in russian",
        lambda: at.selectbox(key="lang").select("ru").run(),
    )
    labels = {m.label: m for m in at.metric}
    check(
        "ru first-acc tile",
        "Точность 1-го выстрела" in labels and "Движение мыши перед 1-м" in labels,
        list(labels)[:8],
    )


def scenario_impact():
    """
    Round-impact layer: opening duels, trades, multi-kills, assists.
    30 rounds, Alpha kills Charlie every round: Alpha opens 30/30 (100%,
    no verdict), Charlie opens 0/30 (the tip_openings verdict), nobody
    trades, assists degrade to '-' (the fake match has no assister
    columns). The openings table on the Duels page lists every round,
    marks the selected player with a star and follows the round filter.
    """
    print("== impact (openings, trades, multi-kills) ==")
    at = open_demo(make_match(list(range(1, 31))), "impact.dem")
    check("runs without exception", not at.exception, err_of(at))

    # Charlie: 30 opening deaths, 0 won -> the tip; Alpha: 30/30 -> none
    act(
        at,
        "switch player (Charlie)",
        lambda: at.selectbox(key="me").select(S3).run(),
    )
    goto(at, "shooting")
    tips_txt = "\n".join(m.value for m in at.markdown)
    check("openings tip shown for Charlie", "opening duel and keep losing" in tips_txt)
    act(
        at,
        "all metrics",
        lambda: at.toggle(key="sh_all").set_value(True).run(),
    )
    labels = {m.label: m for m in at.metric}
    m = labels.get("Opening duels (W / L)")
    check(
        "opening tile 0 / 30",
        m is not None and m.value == "0 / 30",
        m.value if m else "",
    )
    m = labels.get("Opening winrate")
    check("opening winrate 0%", m is not None and m.value == "0%", m.value if m else "")
    m = labels.get("Trade kills")
    check("trade kills 0", m is not None and m.value == "0", m.value if m else "")
    m = labels.get("Traded deaths")
    check("traded deaths 0%", m is not None and m.value == "0%", m.value if m else "")
    m = labels.get("Assists")
    check(
        "assists dash without column",
        m is not None and m.value == "—",
        m.value if m else "",
    )

    act(
        at,
        "switch player (Alpha)",
        lambda: at.selectbox(key="me").select(S1).run(),
    )
    tips_txt = "\n".join(m.value for m in at.markdown)
    check(
        "no openings tip for Alpha (100%)",
        "opening duel and keep losing" not in tips_txt,
    )
    labels = {m.label: m for m in at.metric}
    m = labels.get("Opening duels (W / L)")
    check(
        "opening tile 30 / 0",
        m is not None and m.value == "30 / 0",
        m.value if m else "",
    )
    m = labels.get("Opening winrate")
    check(
        "opening winrate 100%",
        m is not None and m.value == "100%",
        m.value if m else "",
    )

    # Duels page: the openings table (30 rows, Alpha starred as killer)
    goto(at, "duels")
    otabs = [GRIDS.pop("du_openings")] if "du_openings" in GRIDS else []
    check("openings table rendered", len(otabs) == 1, [list(v.columns) for v in otabs])
    if otabs:
        o = otabs[0]
        check(
            "openings: 30 rows, Alpha starred, Charlie victim",
            len(o) == 30
            and (o["Killer"] == "★ Alpha").all()
            and (o["Victim"] == "Charlie").all(),
            o.head(2).to_dict(),
        )

    # round filter: first half preset -> 12 opening rows
    act(
        at,
        "round preset first half",
        lambda: ctl(at, "segmented_control", "flt_preset").select("first").run(),
    )
    otabs = [GRIDS.pop("du_openings")] if "du_openings" in GRIDS else []
    check(
        "openings follow the round filter",
        len(otabs) == 1 and len(otabs[0]) == 12,
        [len(v) for v in otabs],
    )
    act(
        at,
        "round preset back to all",
        lambda: ctl(at, "segmented_control", "flt_preset").select("all").run(),
    )

    # russian labels render
    act(
        at,
        "impact tables in russian",
        lambda: at.selectbox(key="lang").select("ru").run(),
    )
    check(
        "ru: openings table localized",
        "Убийца" in GRIDS["du_openings"].columns,
        list(GRIDS["du_openings"].columns),
    )
    goto(at, "shooting")
    act(
        at,
        "all metrics (ru)",
        lambda: at.toggle(key="sh_all").set_value(True).run(),
    )
    labels = {m.label: m for m in at.metric}
    check(
        "ru impact tiles",
        "Первые дуэли (W / L)" in labels and "Процент входов" in labels,
        list(labels)[:8],
    )


def scenario_scoreboard():
    """
    The debug expander gets the scoreboard cross-check: the game's own
    kills_total / deaths_total aggregates vs our tables (2-round match,
    Alpha kills Charlie in both rounds).
    """
    print("== scoreboard cross-check (debug expander) ==")
    d = make_match([1, 2])
    d["totals"] = {
        S1: ("Alpha", 2, 0),
        S2: ("Bravo", 0, 0),
        S3: ("Charlie", 0, 2),
        S4: ("Delta", 0, 0),
    }
    at = open_demo(d, "score_ok.dem")
    check("ok totals: no exception", not at.exception, err_of(at))
    dbg = "\n".join(m.value for m in at.markdown)
    check("scoreboard line rendered", "scoreboard counters" in dbg)
    check("scoreboard reports 0 mismatches", "0 players with a K/D mismatch" in dbg)

    # the game claims a kill we never found -> the check flags the player
    d2 = make_match([1, 2])
    d2["totals"] = {
        S1: ("Alpha", 3, 0),
        S2: ("Bravo", 0, 0),
        S3: ("Charlie", 0, 2),
        S4: ("Delta", 0, 0),
    }
    at = open_demo(d2, "score_bad.dem")
    check("bad totals: no exception", not at.exception, err_of(at))
    dbg = "\n".join(m.value for m in at.markdown)
    check("scoreboard flags the mismatch", "1 players with a K/D mismatch" in dbg)
    check("scoreboard names the player", "Alpha: K 2 vs 3" in dbg)


def run_real(path):
    """Run the real app on a real demo: every player x every preset."""
    print(f"== real demo: {path} ==")
    path = os.path.abspath(path)
    t0 = time.time()
    entry = store.add_demo(path)  # the real flow: the demo goes to the library
    at = fresh_at(timeout=600, demo_id=entry["id"])
    check("runs without exception", not at.exception, err_of(at))
    print(f"  (loaded and rendered in {time.time() - t0:.1f}s)")
    if at.exception:
        return
    # AppTest's select_index applies the app's format_func to the already
    # formatted label ("Name (T)" -> "Name (T) (?)"), so players are
    # selected by their RAW sid from a light parse_player_info() call
    # (DemoParser needs the decompressed file, like the app itself)
    info = core.loader.DemoParser(core.decompressed_demo(path)).parse_player_info()
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
            lambda p=p: ctl(at, "segmented_control", "flt_preset").select(p).run(),
        )


def scenario_map():
    """The Map page: friendly message without positions, a chart with them."""
    print("== map page ==")
    import struct
    import zlib

    at = open_demo(make_match([1, 2]), "map_nopos.dem")
    goto(at, "map")
    check(
        "map: kill events without positions -> a friendly info",
        any("demoparser2" in str(i.value) for i in at.info),
        [str(i.value)[:50] for i in at.info],
    )

    d = make_match([1, 2])
    de = d["events"]["player_death"]
    for who, base in (("attacker", 0.0), ("user", 300.0)):
        for ax, k in (("X", 100.0), ("Y", 50.0), ("Z", 0.0)):
            de[f"{who}_{ax}"] = [base + k * (i + 1) for i in range(len(de))]
    at = open_demo(d, "map_pos.dem")
    goto(at, "map")
    check(
        "map: with positions and no radar -> chart + radar hint",
        len(at.get("plotly_chart")) == 1
        and any("fetch_radars" in str(i.value) for i in at.info),
        ([str(i.value)[:50] for i in at.info], len(at.get("plotly_chart"))),
    )
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
        check(
            "map: with a radar -> chart and no radar hint",
            len(at.get("plotly_chart")) == 1
            and not any("fetch_radars" in str(i.value) for i in at.info),
            [str(i.value)[:50] for i in at.info],
        )
    finally:
        if old is None:
            os.environ.pop("AIMAUDIT_RADAR_DIR", None)
        else:
            os.environ["AIMAUDIT_RADAR_DIR"] = old


def scenario_matchstats():
    """The per-match summary: the ask flow, compute-at-open, and the resets."""
    print("== match summary (matchstats) ==")
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
    check(
        "ms: the ask card renders without a known 'me'",
        widget(at, "selectbox", "mt_who_pick") is not None
        and widget(at, "checkbox", "mt_who_remember") is not None
        and widget(at, "button", "mt_who_set") is not None,
    )
    check(
        "ms: nothing cached before the answer",
        core.matchstats.read_cached(store.demo_path(store.get_demo(did))) is None,
    )
    # remember OFF: the pick lands in the library entry only
    act(
        at,
        "ms: untick 'remember in Settings'",
        lambda: at.checkbox(key="mt_who_remember").uncheck().run(),
    )
    act(at, "ms: pick yourself (Charlie)", lambda: at.selectbox(key="mt_who_pick").select(S3).run())
    act(at, "ms: press 'That's me'", lambda: at.button(key="mt_who_set").click().run())
    entry = store.get_demo(did)
    check("ms: the pick is remembered in the library", entry.get("me_sid") == S3, entry.get("me_sid"))
    check(
        "ms: without the checkbox the accounts stay untouched",
        store.read_accounts().get("steam_id64") == "",
        store.read_accounts().get("steam_id64"),
    )
    stats = core.matchstats.read_cached(store.demo_path(entry))
    check("ms: the summary was computed right away", stats is not None and stats["me_sid"] == S3, stats)
    check(
        "ms: the numbers are the picked player's (Alpha killed Charlie twice)",
        stats is not None and stats["kills"] == 0 and stats["deaths"] == 2,
        (stats or {}).get("kills"),
    )

    # 1b) the identity menu ("this is not me"): pick another player; with
    # remember ON the pick goes to the accounts and the summary recomputes
    check(
        "ms: the identity menu offers the picker",
        widget(at, "selectbox", "mt_me_pick") is not None
        and widget(at, "checkbox", "mt_me_remember") is not None
        and widget(at, "button", "mt_me_set") is not None,
    )
    act(at, "ms: the menu picks Alpha", lambda: at.selectbox(key="mt_me_pick").select(S1).run())
    act(at, "ms: the menu presses 'That's me'", lambda: at.button(key="mt_me_set").click().run())
    entry = store.get_demo(did)
    check(
        "ms: the new pick went to the accounts (remember ON)",
        store.read_accounts().get("steam_id64") == S1,
        store.read_accounts().get("steam_id64"),
    )
    check("ms: the stale per-match pick was cleared", not entry.get("me_sid"), entry.get("me_sid"))
    stats = core.matchstats.read_cached(store.demo_path(entry))
    check(
        "ms: the summary recomputed for the new player",
        stats is not None and stats["me_sid"] == S1 and stats["kills"] == 2,
        ((stats or {}).get("me_sid"), (stats or {}).get("kills")),
    )
    check("ms: the player row followed the new identity", ss_get(at, "me") == S1, ss_get(at, "me"))

    # 2) the SteamID64 route: a match of the account's player never asks
    store.write_accounts({**store.read_accounts(), "version": 1, "steam_id64": S1})
    at = open_demo(make_match([1, 2, 13]), "ms_auto.dem")
    did2 = at.session_state["match"]
    check("ms: no ask card when the SteamID64 played", widget(at, "selectbox", "mt_who_pick") is None)
    stats2 = core.matchstats.read_cached(store.demo_path(store.get_demo(did2)))
    check(
        "ms: the summary computed at open for the SteamID64 player",
        stats2 is not None and stats2["me_sid"] == S1 and stats2["kills"] == 3,
        (stats2 or {}).get("kills"),
    )

    # 3) the table rows carry the numbers; an un-analyzed row offers Analyze
    did3 = register_demo(make_match([1]), "ms_third.dem")
    goto(at, "matches")
    at.run()
    rows = _rows()
    check(
        "ms: the analyzed rows carry their summary",
        next(r for r in rows if r.demo_id == did2).stats is not None
        and next(r for r in rows if r.demo_id == did).stats is not None,
    )
    row3 = next(r for r in rows if r.demo_id == did3)
    check(
        "ms: an un-analyzed row offers Analyze, an analyzed one Open",
        _mv.action_of(_t, row3) == _t("act_analyze")
        and _mv.action_of(_t, next(r for r in rows if r.demo_id == did2)) == _t("mt_open"),
        (_mv.action_of(_t, row3), _mv.action_of(_t, next(r for r in rows if r.demo_id == did2))),
    )
    i3 = rows.index(row3)
    act(
        at,
        "ms: a row click opens (and analyzes) the match",
        lambda: at.session_state.__setitem__("mt_action_row", i3) or at.run(),
    )
    check(
        "ms: the opened match got its summary on the way in",
        core.matchstats.read_cached(store.demo_path(store.get_demo(did3))) is not None
        and ss_get(at, "match") == did3
        and not at.exception,
        (ss_get(at, "match"), err_of(at)),
    )

    # 4) the resets: clear cache and delete take the summaries with them
    did4 = register_demo(make_match([1, 2]), "ms_fourth.dem")
    goto(at, "settings")
    act(at, "ms: clear the caches", lambda: at.button(key="lib_clear").click().run())
    check(
        "ms: clear_cache resets the summaries",
        core.matchstats.read_cached(store.demo_path(store.get_demo(did4))) is None
        and os.listdir(store.matchstats_dir()) == [],
        sorted(os.listdir(store.matchstats_dir())),
    )


def scenario_matches():
    """The Matches page: the unified table, refresh, download -> open."""
    print("== matches page (the unified table) ==")
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
        check("matches: renders on an empty library", not at.exception, err_of(at))
        check("matches: refresh button offered", widget(at, "button", "mt_refresh") is not None)
        check(
            "matches: the add-match button is offered",
            widget(at, "button", "mt_add") is not None,
        )
        check(
            "matches: the helper status points at Settings",
            widget(at, "button", "mt_configure") is not None
            and widget(at, "button", "mt_helper_install") is None,
        )
        check(
            "matches: the empty state says what to do",
            len(at.subheader) >= 1 and not at.error,
        )

        # 2) a failing refresh keeps the page usable
        def _boom(provider, cfg, n, **kw):
            raise sm.NetworkError("down")

        _orig_list, _orig_fetch = sources.list_matches, sources.fetch_demo
        sources.list_matches = _boom
        try:
            act(at, "refresh fails", lambda: at.button(key="mt_refresh").click().run())
            check("matches: network error is shown", len(at.error) >= 1, [str(e.value)[:60] for e in at.error])
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
            check("matches: cached to matches.json", len(sources.read_cache("steam")) == 30)
            rows = _rows()
            check(
                "matches: one row per game, results win/loss/tie in the list",
                len(rows) == 30 and {"win", "loss", "tie"} <= {r.result for r in rows},
                [(r.key, r.result) for r in rows[:3]],
            )
            check(
                "matches: the counter shows the first page (25 of 30)",
                _shown_of(at) == (25, 30),
                _shown_of(at),
            )
            check("matches: the Show more button is offered", widget(at, "button", "mt_more") is not None)
            act(at, "show more", lambda: at.button(key="mt_more").click().run())
            check("matches: the second page completes the table", _shown_of(at) == (30, 30), _shown_of(at))

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
            check("matches: download bound the demo in the library", entry is not None and "steam_m1" in entry["file"], entry)
            rows = _rows()
            m1row = next(r for r in rows if r.match_id == "m1")
            check(
                "matches: the row is in-library now, one row still (no dupes)",
                m1row.demo_id == entry["id"] and m1row.demo_state == sm.DemoState.DOWNLOADED
                and sum(1 for r in rows if r.match_id == "m1") == 1,
                (m1row.demo_id, m1row.demo_state),
            )

            # multi-download: the checkboxes (the component reports the row
            # KEYS into mt_checked), the button, the loop with per-demo
            # analysis, and the last download opening as the match
            goto(at, "matches")  # the single download opened m1 as a match
            store.write_accounts({"version": 1, "match_count": 5, "steam_id64": S1})
            rows = _rows()
            sel_keys = {r.key for r in rows if r.match_id in ("m2", "m3")}
            check(
                "matches: the download bar stays disabled without a selection",
                widget(at, "button", "mt_dl_selected") is not None
                and at.button(key="mt_dl_selected").disabled,
            )
            act(
                at,
                "check two rows",
                lambda: at.session_state.__setitem__("mt_checked", set(sel_keys)) or at.run(),
            )
            check(
                "matches: the download bar wakes up with a selection",
                not at.button(key="mt_dl_selected").disabled,
            )
            act(
                at,
                "press Download selected",
                lambda: at.session_state.__setitem__("mt_dl_multi", True) or at.run(),
            )
            e2 = store.find_by_match("steam", "m2")
            e3 = store.find_by_match("steam", "m3")
            check(
                "matches: both selected demos were downloaded",
                e2 is not None and e3 is not None,
                ((e2 or {}).get("file"), (e3 or {}).get("file")),
            )
            analyzed = []
            for _e in (e2, e3):
                _s = core.matchstats.read_cached(store.demo_path(_e)) if _e else None
                analyzed.append(_s is not None and _s["me_sid"] == S1 and _s["kills"] == 2)
            check(
                "matches: multi downloads were analyzed on the spot",
                analyzed == [True, True],
                analyzed,
            )
            check(
                "matches: the last download opened as the match",
                ss_get(at, "match") is not None
                and ss_get(at, "match") == (e3 or {}).get("id"),
                ss_get(at, "match"),
            )
            check(
                "matches: the selection was consumed",
                not ss_get(at, "mt_checked"),
                ss_get(at, "mt_checked"),
            )
            goto(at, "matches")

            # a row click opens the match
            FakeParser.demos[os.path.realpath(store.demo_path(entry))] = make_match([1, 2])
            act(
                at,
                "row click opens the match",
                lambda: at.session_state.__setitem__("mt_action_row", i_m1) or at.run(),
            )
            check(
                "matches: open lands in the match view (overview tab)",
                ss_get(at, "page") == "matches"
                and ss_get(at, "match") == entry["id"]
                and ss_get(at, "match_tab") == "overview"
                and not at.exception,
                (ss_get(at, "page"), ss_get(at, "match_tab"), err_of(at)),
            )
            check("matches: the demo is the loaded one", ss_get(at, "demo_id") == entry["id"], ss_get(at, "demo_id"))
            check("matches: back to the matches list still works", goto(at, "matches") or True)
            open_match(at, entry["id"], "continue: reopen the last match")
            check(
                "matches: reopen lands back in the match view",
                ss_get(at, "match") == entry["id"] and not at.exception,
                (ss_get(at, "match"), err_of(at)),
            )
            goto(at, "matches")

            # an own demo joins the table as its own row (source: File)
            own_id = register_demo(make_match([1, 2]), "own_demo.dem")
            at.run()
            rows = _rows()
            check(
                "matches: an own demo is its own row with the File source",
                any(r.demo_id == own_id and r.source == "file" and r.provider is None for r in rows),
                [r.key for r in rows[:4]],
            )
            check("matches: the counter counts it (31 rows)", _shown_of(at) == (31, 31), _shown_of(at))
            check(
                "matches: rows classify into pools (competitive / manual)",
                {r.kind for r in rows} == {"competitive", "manual"},
                sorted({r.kind for r in rows}),
            )

            # filters: result / pool + the counter + the URL (the tie
            # filter keeps one row, safely under the Show-more cut)
            act(at, "filter: ties only", lambda: ctl(at, "segmented_control", "mflt_result").select("tie").run())
            check("matches: the result filter narrows the table", _shown_of(at) == (1, 31), _shown_of(at))
            check("matches: the filters ride in the URL", at.query_params.get("fres") == "tie", dict(at.query_params))
            act(at, "filter: reset", lambda: at.button(key="mflt_reset").click().run())
            check("matches: reset restores the table (a fresh 25-row page)", _shown_of(at) == (25, 31), _shown_of(at))
            check("matches: reset clears the URL", "fres" not in at.query_params, dict(at.query_params))
            act(
                at,
                "filter: manual uploads only",
                lambda: ctl(at, "selectbox", "mflt_kind").select("manual").run(),
            )
            check("matches: the pool filter keeps only your files", _shown_of(at) == (1, 31), _shown_of(at))
            check("matches: the pool filter rides in the URL", at.query_params.get("fkind") == "manual", dict(at.query_params))
            act(at, "filter: reset again", lambda: at.button(key="mflt_reset").click().run())

            # a copied link restores the filter in a fresh session
            at5 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
            at5.query_params["fres"] = "tie"
            at5.run()
            check(
                "matches: ?fres=tie restores the filter",
                ss_get(at5, "mflt_result") == "tie" and not at5.exception,
                (ss_get(at5, "mflt_result"), err_of(at5)),
            )

            # delete the whole library at once (the provider rows stay)
            act(at, "ask clear", lambda: at.button(key="mt_clear").click().run())
            check("matches: clear asks to confirm", widget(at, "button", "mt_clear_yes") is not None)
            act(at, "confirm clear", lambda: at.button(key="mt_clear_yes").click().run())
            check("matches: library empty after clear", store.list_demos() == [])
            check("matches: the table survives clearing (download rows)", not at.exception, err_of(at))

            # 4) refresh error after a successful load: the cached list stays
            def _boom2(provider, cfg, n, **kw):
                raise sm.RateLimitError(retry_after=42)

            sources.list_matches = _boom2
            act(at, "refresh hits the rate limit", lambda: at.button(key="mt_refresh").click().run())
            check(
                "matches: rate limit message shown",
                any("42" in str(e.value) for e in at.error),
                [str(e.value)[:70] for e in at.error],
            )
            check("matches: the cached rows survive the error", len(sources.read_cache("steam")) >= 2)
        finally:
            sources.list_matches = _orig_list
            sources.fetch_demo = _orig_fetch
            sources.inbox.replays_records = _orig_replays


def scenario_pick():
    """pick(): format_func widgets accept the raw option AND the label.

    Regression for the harness helper: AppTest's select_index() is
    unusable on format_func widgets (double formatting), which used to
    make whole widget groups unreachable for tests (mflt_kind,
    mflt_period, lang). pick() must select by raw value and by label,
    in EN and in RU, on selectbox and segmented_control alike.
    """
    print("== pick helper (format_func selects) ==")
    at = open_demo(make_match([1, 2]), "pick.dem", me=S1)
    goto(at, "matches")

    # selectbox with translated labels (mflt_kind: all/matchmaking/premier/...)
    act(at, "pick mflt_kind by raw value", lambda: pick(at, "selectbox", "mflt_kind", "manual"))
    check("pick: selectbox raw value landed", ss_get(at, "mflt_kind") == "manual", ss_get(at, "mflt_kind"))
    act(at, "pick mflt_kind by label", lambda: pick(at, "selectbox", "mflt_kind", "Manual upload"))
    check("pick: selectbox label landed", ss_get(at, "mflt_kind") == "manual", ss_get(at, "mflt_kind"))

    # segmented_control with translated labels (mflt_period: 7/30/all)
    act(at, "pick mflt_period by raw value", lambda: pick(at, "segmented_control", "mflt_period", "30"))
    check("pick: segmented raw value landed", ss_get(at, "mflt_period") == "30", ss_get(at, "mflt_period"))
    act(at, "pick mflt_period by label", lambda: pick(at, "segmented_control", "mflt_period", "7 days"))
    check("pick: segmented label landed", ss_get(at, "mflt_period") == "7", ss_get(at, "mflt_period"))

    # the language selectbox formats its options through i18n.LANGS
    act(at, "pick lang by label", lambda: pick(at, "selectbox", "lang", "Русский"))
    check("pick: lang label landed", ss_get(at, "lang") == "ru", ss_get(at, "lang"))

    # ... and after the switch the SAME widgets show Russian labels
    act(at, "pick mflt_kind by RU label", lambda: pick(at, "selectbox", "mflt_kind", "Ручная загрузка"))
    check("pick: RU selectbox label landed", ss_get(at, "mflt_kind") == "manual", ss_get(at, "mflt_kind"))
    act(at, "pick mflt_period by RU label", lambda: pick(at, "segmented_control", "mflt_period", "7 дней"))
    check("pick: RU segmented label landed", ss_get(at, "mflt_period") == "7", ss_get(at, "mflt_period"))

    # a value that is neither an option nor a label must raise, not click
    # something random
    try:
        pick(at, "selectbox", "mflt_kind", "no such choice")
        check("pick: unknown value raises", False, "no exception")
    except ValueError:
        check("pick: unknown value raises", True)

    # back to English for the scenarios that follow
    act(at, "lang back to EN", lambda: pick(at, "selectbox", "lang", "en"))


def scenario_list_filter_keep():
    """The Matches-list filters must survive leaving the list.

    Regression for the state-loss bug: mflt_* are keyed widgets that
    render only on the list, and Streamlit drops the widget state of
    anything a run does not render - so a trip to Settings (or into a
    match and back) used to reset every filter to "all" and silently
    strip ?fkind/?fp from the shareable URL. persist_state="session"
    keeps the values alive.
    """
    print("== list filters survive leaving the list ==")
    at = open_demo(make_match([1, 2]), "mfltkeep.dem", me=S1)
    goto(at, "matches")

    act(at, "filter the list to manual uploads", lambda: pick(at, "selectbox", "mflt_kind", "manual"))
    act(at, "filter the list to 30 days", lambda: pick(at, "segmented_control", "mflt_period", "30"))
    check("mflt set on the list", ss_get(at, "mflt_kind") == "manual" and ss_get(at, "mflt_period") == "30",
          (ss_get(at, "mflt_kind"), ss_get(at, "mflt_period")))
    check("filter is shareable (?fkind in URL)", at.query_params.get("fkind") == "manual",
          dict(at.query_params))

    # leave to Settings and come back: the widgets do not render there
    goto(at, "settings")
    check("mflt_kind alive on Settings", ss_get(at, "mflt_kind") == "manual", ss_get(at, "mflt_kind"))
    goto(at, "matches")
    check("mflt_kind survives Settings round trip", ss_get(at, "mflt_kind") == "manual", ss_get(at, "mflt_kind"))
    check("mflt_period survives Settings round trip", ss_get(at, "mflt_period") == "30", ss_get(at, "mflt_period"))
    check("URL keeps ?fkind after the round trip", at.query_params.get("fkind") == "manual",
          dict(at.query_params))

    # into the match and back: the list does not render in the match view
    goto(at, "overview")
    check("mflt_kind alive inside the match", ss_get(at, "mflt_kind") == "manual", ss_get(at, "mflt_kind"))
    goto(at, "matches")
    check("mflt_kind survives match round trip", ss_get(at, "mflt_kind") == "manual", ss_get(at, "mflt_kind"))
    check("URL keeps ?fkind after the match round trip", at.query_params.get("fkind") == "manual",
          dict(at.query_params))

    # the Reset button still clears everything (also through persist)
    act(at, "reset the list filters", lambda: at.button(key="mflt_reset").click().run())
    check("mflt_reset clears the kind", ss_get(at, "mflt_kind") == "all", ss_get(at, "mflt_kind"))
    check("mflt_reset clears the period", ss_get(at, "mflt_period") == "all", ss_get(at, "mflt_period"))
    check("mflt_reset drops ?fkind from the URL", "fkind" not in dict(at.query_params),
          dict(at.query_params))


def scenario_tab_state_keep():
    """Tab-scoped toggles must survive switching match tabs.

    Regression for the state-loss bug: the "all columns / all metrics"
    toggles and the Map controls render only on their own tab, so every
    tab switch used to reset them (ov_full, sh_all, du_full, map_show,
    map_lines). persist_state="session" keeps the values alive.
    """
    print("== tab widgets survive tab switches ==")
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
    check("ov_full survives the tab walk", ss_get(at, "ov_full") is True, ss_get(at, "ov_full"))
    check("ov_full toggle renders ON", bool(at.toggle(key="ov_full").value), None)
    goto(at, "shooting")
    check("sh_all survives the tab walk", ss_get(at, "sh_all") is True, ss_get(at, "sh_all"))
    goto(at, "duels")
    check("du_full survives the tab walk", ss_get(at, "du_full") is True, ss_get(at, "du_full"))
    goto(at, "map")
    check("map_show survives the tab walk", ss_get(at, "map_show") == "deaths", ss_get(at, "map_show"))
    check("map_lines survives the tab walk", ss_get(at, "map_lines") is False, ss_get(at, "map_lines"))

    # leaving the whole match view (the list) must not lose them either
    goto(at, "matches")
    check("ov_full alive on the list", ss_get(at, "ov_full") is True, ss_get(at, "ov_full"))
    goto(at, "overview")
    check("ov_full survives the list round trip", ss_get(at, "ov_full") is True, ss_get(at, "ov_full"))

    # a map tab that renders NO map widgets (a demo without kill
    # positions) must keep the stored values for the way back - the
    # second demo opens in the SAME session (a list row click)
    did2 = register_demo(make_match([2, 3]), "tabkeep_nopos.dem")
    at.session_state["match"] = did2
    at.session_state["demo_id"] = did2
    goto(at, "map")
    check("map without positions shows the hint", any("demoparser2" in str(i.value) for i in at.info),
          [str(i.value)[:40] for i in at.info])
    check("map_show kept while its widget cannot render", ss_get(at, "map_show") == "deaths",
          ss_get(at, "map_show"))
    goto(at, "overview")
    check("ov_full kept across a demo switch", ss_get(at, "ov_full") is True, ss_get(at, "ov_full"))


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg and arg not in ("--all", "--fast"):
        if not os.path.exists(arg):
            print(f"no such file: {arg}")
            sys.exit(2)
        run_real(arg)  # real demoparser2, no FakeParser patch
    else:
        # patch BEFORE any app run: DemoParser is used by core.loader
        # (load_demo) and core.replay (timeline/replay data); the facade
        # name is patched too for direct core.DemoParser calls.
        core.loader.DemoParser = core.replay.DemoParser = FakeParser
        record_grids()
        scenario_one_round()
        scenario_nav()
        scenario_settings()
        scenario_round_replay()
        scenario_roundtrip()
        scenario_player_stepper()
        scenario_duel_strip()
        scenario_two_rounds()
        scenario_long25()
        scenario_filter_panel()
        scenario_knife_round()
        scenario_bad_input()
        scenario_library()
        scenario_links()
        scenario_tables()
        scenario_context()
        scenario_first_shots()
        scenario_impact()
        scenario_scoreboard()
        scenario_map()
        scenario_matchstats()
        scenario_matches()
        scenario_pick()
        scenario_list_filter_keep()
        scenario_tab_state_keep()
        if arg == "--all":
            scenario_matrix()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
