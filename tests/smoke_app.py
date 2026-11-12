"""
UI smoke test: runs the REAL app.py through streamlit AppTest on synthetic
demos - no real .dem file needed.

test_core.py never executes app.py, so UI-level crashes slipped through:
a one-round demo used to kill the whole app (st.slider with
min_value == max_value raises StreamlitInvalidMinMaxError). This script
runs the actual UI end to end against fake demos and fails on any uncaught
exception, missing widget or unfriendly error.

Usage, from the repo root:
    python tests/smoke_app.py             # synthetic demos, fast subset (CI)
    python tests/smoke_app.py --all       # + the full interaction matrix
    python tests/smoke_app.py match.dem   # a REAL demo (.dem/.bz2/.gz/.zip/.zst)

The fake parser mirrors tests/test_core.py::FakeParser (duplicated on purpose
so both scripts stay runnable on their own). The local state file (recent
demos, last player) is redirected to a temp dir - the real
~/.cs2-aimaudit/state.json is never touched.
"""

import base64
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
    t0+16 so the timeline tab has real data to plot.
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
                        "is_alive": True,
                        "aim_punch_angle": "0 0 0",
                        "team_num": s1_team if sid in (S1, S2) else 5 - s1_team,
                        # shooter state: Alpha's t0+2 shot (the headshot
                        # hit) comes while flashed (1.5s left); Charlie's t0+5
                        # answer comes still slowed (0.4) from the damage.
                        "flash_duration": 1.5 if (sid == S1 and t == t0 + 2) else 0.0,
                        "velo_modifier": 0.4 if (sid == S3 and t == t0 + 5) else 1.0,
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
# so FakeParser.demos keys and demo_path must already be resolved.
STATE_DIR = os.path.realpath(tempfile.mkdtemp(prefix="cs2aa_smoke_state_"))
DEMO_DIR = os.path.realpath(tempfile.mkdtemp(prefix="cs2aa_smoke_demos_"))
core.STATE_PATH = os.path.join(STATE_DIR, "state.json")  # keep the home clean


def fresh_at(timeout=300, **session):
    """A run of the real app.py with session_state pre-set."""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=timeout)
    for k, v in session.items():
        at.session_state[k] = v
    at.run()
    return at


def open_demo(demo, fname, **session):
    """Register a fake demo under a temp path and run the app on it."""
    path = os.path.join(DEMO_DIR, fname)
    with open(path, "wb") as f:
        f.write(b"smoke fake demo - content is irrelevant for FakeParser")
    FakeParser.demos[path] = demo
    return fresh_at(demo_path=path, **session)


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
        # st.dataframe learned `key` in AppTest only on streamlit 1.57:
        # on older versions every dataframe reports key=None, so a keyed
        # lookup always fails. Fall back to the first dataframe on the
        # page when no dataframe carries a key.
        if kind == "dataframe":
            try:
                dfs = list(at.dataframe)
                if dfs and all(d.key is None for d in dfs):
                    return dfs[0]
            except Exception:  # noqa: BLE001
                pass
        return None


def ctl(at, kind, key):
    """A rendered segmented_control/pills widget for an interaction."""
    w = widget(at, kind, key)
    if w is None:
        raise KeyError(f"{kind} widget {key!r} not rendered")
    return w


def ss_get(at, key, default=None):
    """
    session_state.get(): dict methods on AppTest.session_state are
    only available on newer streamlit; [] access works everywhere.
    """
    try:
        return at.session_state[key]
    except KeyError:
        return default


def goto(at, page):
    """
    Only the active page renders, so switch the page before
    interacting with widgets that live on it.
    """

    at.session_state["page"] = page
    at.run()
    check(f"page {page} renders", not at.exception, err_of(at))


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


def _pack_view(d: dict) -> str:
    """The app's ?f= packing, replicated for old/new link tests."""
    return (
        base64.urlsafe_b64encode(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _shots_shown(at):
    """Shot count from the filters summary line ('... N shots / M duels')."""
    for c in at.caption:
        m = re.search(r"(\d+) shots", c.value)
        if m:
            return int(m.group(1))
    return None


def open_filters(at):
    """Expand the filter card (collapsed summary row is the default)."""
    if widget(at, "button", "flt_edit") is not None:
        act(at, "open the filter card", lambda: at.button(key="flt_edit").click().run())


# ---------------------------------------------------------------- scenarios
def scenario_one_round():
    """The regression: a demo with a single round used to crash the app."""
    print("== one-round demo (slider crash regression) ==")
    at = open_demo(make_match([1]), "one_round.dem")
    check("runs without exception", not at.exception, err_of(at))
    check("no st.error on load", not at.error)
    open_filters(at)
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
    act(at, "thr slider 200", lambda: at.slider(key="thr").set_value(200).run())
    goto(at, "timeline")
    act(
        at,
        "timeline duel renders",
        lambda: at.session_state.__setitem__("tl_duel", 0) or at.run(),
    )
    act(
        at,
        "timeline: 2D replay view",
        lambda: at.session_state.__setitem__("tl_view", "replay") or at.run(),
    )
    charts = at.get("plotly_chart")
    spec = ""
    try:
        spec = charts[0].proto.spec
    except Exception:  # noqa: BLE001 - AppTest proto layout differs between versions
        pass
    check(
        "replay: an animated chart (frames + slider) for the two duellists",
        len(charts) == 1 and '"frames"' in spec and '"sliders"' in spec,
        (len(charts), bool(spec)),
    )
    act(
        at,
        "timeline: back to charts",
        lambda: at.session_state.__setitem__("tl_view", "charts") or at.run(),
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
        "change-demo button rendered",
        widget(at, "button", "demo_change_btn") is not None,
    )
    # the compact row replaces the upload block after the first load
    act(at, "next player (Bravo)", lambda: at.button(key="me_next").click().run())
    check("next moved the selection", ss_get(at, "me") == S2, ss_get(at, "me"))
    caps = [c.value for c in at.caption]
    check(
        "compact demo row names the file",
        any("stepper.dem" in str(c) for c in caps),
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
    # 'Change demo' re-opens the upload block without losing the demo
    act(
        at,
        "open the change-demo panel",
        lambda: at.button(key="demo_change_btn").click().run(),
    )
    check(
        "change panel shows the uploader",
        widget(at, "file_uploader", "demo_file") is not None,
    )
    act(
        at,
        "cancel the change-demo panel",
        lambda: at.button(key="demo_cancel").click().run(),
    )
    check(
        "cancel keeps the demo loaded",
        ss_get(at, "demo_src", "").endswith("stepper.dem"),
    )
    check("cancel closes the panel", ss_get(at, "demo_change") is False)


def scenario_duel_strip():
    """Fast duel/round navigation: the strip owns the timeline selection."""
    print("== duel strip (fast duel/round navigation) ==")
    at = open_demo(make_match([1, 2, 13]), "strip.dem")
    check("runs without exception", not at.exception, err_of(at))
    goto(at, "timeline")
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
    act(
        at,
        "stale pick beyond the duel list clamps",
        lambda: at.session_state.__setitem__("tl_duel", 99) or at.run(),
    )
    check("clamped back to duel 0", ss_get(at, "tl_duel") == 0, ss_get(at, "tl_duel"))
    act(at, "language ru", lambda: at.selectbox(key="lang").select("ru").run())
    check(
        "ru: hotkey hint rendered",
        any("Shift+←" in str(c.value) for c in at.caption),
        [str(c.value)[:60] for c in at.caption][:6],
    )


def scenario_two_rounds():
    print("== two-round demo (presets, custom slider, filters) ==")
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "two_rounds.dem")
    check("runs without exception", not at.exception, err_of(at))
    open_filters(at)
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
    open_filters(at)
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
    goto(at, "timeline")
    act(
        at,
        "timeline duel 1 of 3",
        lambda: at.session_state.__setitem__("tl_duel", 1) or at.run(),
    )


def scenario_filter_panel():
    """Persistent panel: distance bands, reset, page scope, links."""
    print("== filter panel (persistent, distance, reset, links) ==")
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "panel.dem")
    check("runs without exception", not at.exception, err_of(at))
    # collapsed by default: the controls sit behind the one-line summary
    check(
        "collapsed: no preset control rendered",
        widget(at, "segmented_control", "flt_preset") is None,
    )
    base = _shots_shown(at)
    check("summary line shows the shot count", bool(base), str(base))
    check(
        "collapsed: Edit button offered",
        widget(at, "button", "flt_edit") is not None,
    )
    check(
        "collapsed: no Reset without active filters",
        widget(at, "button", "flt_reset_sum") is None,
    )
    open_filters(at)
    for k in ("flt_side", "flt_preset", "flt_dist"):
        check(
            f"{k} segmented control visible",
            widget(at, "segmented_control", k) is not None,
        )
    check("class pills visible", widget(at, "pills", "flt_classes") is not None)
    check("done button offered", widget(at, "button", "flt_done") is not None)
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
    # collapsing the card keeps every selection (widget states survive
    # only because the app re-assigns the keys while it is hidden)
    act(
        at,
        "weapon filter awp again",
        lambda: at.multiselect(key="flt_weapons").set_value(["awp"]).run(),
    )
    act(at, "collapse the filter card", lambda: at.button(key="flt_done").click().run())
    check(
        "collapsed summary names the filter",
        any("awp" in str(c.value) for c in at.caption),
    )
    check(
        "collapsed: Reset offered while filters are active",
        widget(at, "button", "flt_reset_sum") is not None,
    )
    act(
        at,
        "reset from the summary row",
        lambda: at.button(key="flt_reset_sum").click().run(),
    )
    check(
        "summary-row reset cleared the weapon filter", ss_get(at, "flt_weapons") == []
    )
    open_filters(at)
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
    # old link format (res as a multiselect list, preset=all with a
    # slider-moved range) must open as the equivalent new state
    at2 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at2.session_state["demo_path"] = os.path.join(DEMO_DIR, "panel.dem")
    at2.query_params["f"] = _pack_view(
        {
            "lang": "en",
            "me": None,
            "weapons": [],
            "classes": [],
            "rounds": [2, 2],
            "preset": "all",
            "side": "T",
            "res": ["death"],
            "page": "overview",
        }
    )
    at2.run()
    check("old link opens without exception", not at2.exception, err_of(at2))
    check(
        "old preset=all + range becomes Custom",
        ss_get(at2, "flt_preset") == "custom",
    )
    check(
        "old res list ['death'] becomes 'death'",
        ss_get(at2, "flt_res") == "death",
    )
    check("old link keeps the side filter", ss_get(at2, "flt_side") == "T")
    check(
        "migrated link shows the custom slider",
        widget(at2, "slider", "flt_rounds") is not None,
    )
    # new link round-trip: filter -> URL -> fresh app
    at3 = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "panel3.dem")
    open_filters(at3)
    act(
        at3,
        "distance band mid (for the link)",
        lambda: ctl(at3, "segmented_control", "flt_dist").select("mid").run(),
    )
    link = at3.query_params.get("f")
    check("filtered view is packed into the URL", bool(link))
    at4 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at4.session_state["demo_path"] = os.path.join(DEMO_DIR, "panel3.dem")
    at4.query_params["f"] = link
    at4.run()
    check("link restores without exception", not at4.exception, err_of(at4))
    check("link restores the distance band", ss_get(at4, "flt_dist") == "mid")


def scenario_quoted_path():
    """
    Fix regression: a path pasted via Windows 'Copy as path' (quoted)
    used to end in 'File not found'.
    """

    print("== quoted path (Windows 'Copy as path') ==")
    path = os.path.join(DEMO_DIR, "quoted.dem")
    with open(path, "wb") as f:
        f.write(b"smoke fake demo")
    FakeParser.demos[path] = make_match([1, 2])
    at = fresh_at(demo_path=f'"{path}"')  # double quotes, as Explorer gives
    check("double-quoted path opens the demo", not at.exception, err_of(at))
    check("double-quoted path: no error element", not at.error)
    at = fresh_at(demo_path=f"'{path}'")  # single quotes, as shells give
    check("single-quoted path opens the demo", not at.exception, err_of(at))
    check("single-quoted path: no error element", not at.error)


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


def scenario_bad_input():
    print("== bad input ==")
    at = fresh_at(demo_path="/no/such/demo.dem")
    check("missing path: no exception", not at.exception, err_of(at))
    check("missing path: friendly error shown", len(at.error) == 1)
    at = fresh_at()
    check("no input at all: no exception", not at.exception, err_of(at))


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
                    lambda v=thr: at.slider(key="thr").set_value(v).run(),
                )
            for w in (1.0, 6.0):
                act(
                    at,
                    f"window={w}",
                    lambda v=w: at.slider(key="window_s").set_value(v).run(),
                )
            for sd in (100, 2000):
                act(
                    at,
                    f"safe_dist={sd}",
                    lambda v=sd: at.slider(key="safe_dist").set_value(v).run(),
                )
            for sdist in (500, 4000):
                act(
                    at,
                    f"strict_dist={sdist}",
                    lambda v=sdist: at.slider(key="strict_dist").set_value(v).run(),
                )
            open_filters(at)
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
            goto(at, "timeline")
            for i in range(3):
                act(
                    at,
                    f"timeline duel #{i}",
                    lambda i=i: at.session_state.__setitem__("tl_duel", i)
                    or at.run(),
                )


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
    ztabs = [d.value for d in at.dataframe if "K-D" in list(d.value.columns)]
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
    ztabs = [d.value for d in at.dataframe if "K-D" in list(d.value.columns)]
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
    ztabs = [
        d.value
        for d in at.dataframe
        if list(d.value.columns) == ["Зона", "Килы", "Смерти", "К-С"]
    ]
    check("ru: zone table localized", len(ztabs) == 1, [list(v.columns) for v in ztabs])


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
    otabs = [d.value for d in at.dataframe if "Killer" in list(d.value.columns)]
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
    open_filters(at)
    act(
        at,
        "round preset first half",
        lambda: ctl(at, "segmented_control", "flt_preset").select("first").run(),
    )
    otabs = [d.value for d in at.dataframe if "Killer" in list(d.value.columns)]
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
    otabs = [d.value for d in at.dataframe if "Убийца" in list(d.value.columns)]
    check(
        "ru: openings table localized",
        len(otabs) == 1,
        [list(v.columns) for v in otabs],
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
    at = fresh_at(timeout=600, demo_path=path)
    check("runs without exception", not at.exception, err_of(at))
    print(f"  (loaded and rendered in {time.time() - t0:.1f}s)")
    if at.exception:
        return
    # AppTest's select_index applies the app's format_func to the already
    # formatted label ("Name (T)" -> "Name (T) (?)"), so players are
    # selected by their RAW sid from a light parse_player_info() call
    # (DemoParser needs the decompressed file, like the app itself)
    info = core.DemoParser(core.decompressed_demo(path)).parse_player_info()
    sids = sorted({str(int(s)) for s in pd.Series(info["steamid"]).dropna()})
    for sid in (s for s in sids if s != "0"):
        act(
            at,
            f"player {sid}",
            lambda s=sid: at.selectbox(key="me").select(s).run(),
        )
    open_filters(at)
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


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg and arg not in ("--all", "--fast"):
        if not os.path.exists(arg):
            print(f"no such file: {arg}")
            sys.exit(2)
        run_real(arg)  # real demoparser2, no FakeParser patch
    else:
        core.DemoParser = FakeParser  # patch BEFORE any app run
        scenario_one_round()
        scenario_player_stepper()
        scenario_duel_strip()
        scenario_two_rounds()
        scenario_long25()
        scenario_filter_panel()
        scenario_knife_round()
        scenario_quoted_path()
        scenario_bad_input()
        scenario_tables()
        scenario_context()
        scenario_first_shots()
        scenario_impact()
        scenario_scoreboard()
        scenario_map()
        if arg == "--all":
            scenario_matrix()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
