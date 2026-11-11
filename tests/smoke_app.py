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

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
                }
            ),
        },
        "ticks": pd.DataFrame(rows),
        "unsupported": set(),
    }


# ------------------------------------------------------------- app runner
STATE_DIR = tempfile.mkdtemp(prefix="cs2aa_smoke_state_")
DEMO_DIR = tempfile.mkdtemp(prefix="cs2aa_smoke_demos_")
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


def widget(at, kind, key):
    """Element with that key, or None when the app did not render it."""
    try:
        w = getattr(at, kind)(key=key)
        return w if w else None
    except Exception:  # noqa: BLE001 - missing key -> None
        return None


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

    sb = widget(at, "selectbox", "flt_preset")
    return (len(sb.options), sb.value) if sb else (0, None)


# preset selectbox contents by option count (see app.py: preset_opts)
_PRESETS_BY_COUNT = {
    1: ("all",),
    2: ("all", "pistols"),
    5: ("all", "first", "second", "ot", "pistols"),
}


# ---------------------------------------------------------------- scenarios
def scenario_one_round():
    """The regression: a demo with a single round used to crash the app."""
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
    act(at, "thr slider 200", lambda: at.slider(key="thr").set_value(200).run())
    goto(at, "timeline")
    act(
        at, "timeline duel renders", lambda: at.selectbox(key="tl_duel").select(0).run()
    )


def scenario_two_rounds():
    print("== two-round demo (slider, pistols preset, filters) ==")
    at = open_demo(make_match([1, 2], weapons=("ak47", "awp")), "two_rounds.dem")
    check("runs without exception", not at.exception, err_of(at))
    check("round-range slider shown", widget(at, "slider", "flt_rounds") is not None)
    check("presets all+pistols", n_presets(at) == (2, "all"))
    act(
        at,
        "pistols preset",
        lambda: at.selectbox(key="flt_preset").select("pistols").run(),
    )
    act(
        at,
        "manual range (2,2)",
        lambda: at.slider(key="flt_rounds").set_value((2, 2)).run(),
    )
    act(
        at,
        "weapon filter awp",
        lambda: at.multiselect(key="flt_weapons").set_value(["awp"]).run(),
    )
    act(
        at,
        "class filter sniper",
        lambda: at.multiselect(key="flt_classes").set_value(["sniper"]).run(),
    )
    act(
        at,
        "result filter death (no such duels)",
        lambda: at.multiselect(key="flt_res").set_value(["death"]).run(),
    )
    check(
        "side filter shown (sides parsed)",
        widget(at, "selectbox", "flt_side") is not None,
    )
    act(at, "side filter T", lambda: at.selectbox(key="flt_side").select("T").run())


def scenario_long25():
    print("== 25-round demo (halves, overtime, pistol rounds) ==")
    at = open_demo(
        make_match([1, 13, 25], weapons=("ak47", "awp", "deagle")), "long25.dem"
    )
    check("runs without exception", not at.exception, err_of(at))
    check(
        "all five presets offered",
        n_presets(at) == (5, "all"),
    )
    for p in ("first", "second", "ot", "pistols"):
        act(
            at,
            f"preset {p}",
            lambda p=p: at.selectbox(key="flt_preset").select(p).run(),
        )
    act(
        at,
        "side filter CT (round 13 only)",
        lambda: at.selectbox(key="flt_side").select("CT").run(),
    )
    goto(at, "timeline")
    act(at, "timeline duel 1 of 3", lambda: at.selectbox(key="tl_duel").select(1).run())


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
    """FACEIT shape: a knife round before begin_new_match (step 2B)."""
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
    check("ru: banner shown", any("ножевой раунд (1)" in b for b in banners_ru), banners_ru)


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
            presets = _PRESETS_BY_COUNT.get(n_presets(at)[0], ())
            for p in presets:
                act(
                    at,
                    f"preset {p}",
                    lambda p=p: at.selectbox(key="flt_preset").select(p).run(),
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
                    lambda c=cls: at.multiselect(key="flt_classes")
                    .set_value([c])
                    .run(),
                )
            for side in ("T", "CT"):
                if widget(at, "selectbox", "flt_side") is not None:
                    act(
                        at,
                        f"side {side}",
                        lambda s=side: at.selectbox(key="flt_side").select(s).run(),
                    )
            for res in (["kill"], ["death"], ["kill", "death"]):
                act(
                    at,
                    f"result {res}",
                    lambda r=res: at.multiselect(key="flt_res").set_value(r).run(),
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
                if widget(at, "selectbox", "tl_duel") is not None:
                    act(
                        at,
                        f"timeline duel #{i}",
                        lambda i=i: at.selectbox(key="tl_duel").select(i).run(),
                    )


def scenario_tables():
    """
    Basic/Full table switches, and the overview table
    must survive a row-selection state (the browser sends it on click;
    AppTest cannot fire on_select callbacks, but the state must not crash
    the app either).
    """
    print("== tables: Basic/Full switches, row selection state ==")
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
    # the same widget state the browser sends after a row click on the
    # overview table: the app must render (the on_select callback itself
    # only runs in a real browser session)
    at.session_state["ov_table"] = {"selection": {"rows": [2], "columns": []}}
    at.run()
    check("row-selection state does not crash", not at.exception, err_of(at))
    # A duels row click jumps to the Timeline; AppTest cannot fire
    # on_select, but the widget state alone must not crash the app
    goto(at, "duels")
    at.session_state["du_table"] = {"selection": {"rows": [0], "columns": []}}
    at.run()
    check("duels row-click state does not crash", not at.exception, err_of(at))
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
    sb = widget(at, "selectbox", "me")
    # .options holds formatted labels ("Name (T)"); select_index picks raw sids
    for i in range(len(sb.options) if sb else 0):
        act(
            at, f"player #{i}", lambda i=i: at.selectbox(key="me").select_index(i).run()
        )
    presets = _PRESETS_BY_COUNT.get(n_presets(at)[0], ())
    for p in presets:
        act(
            at,
            f"preset {p}",
            lambda p=p: at.selectbox(key="flt_preset").select(p).run(),
        )


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
        scenario_two_rounds()
        scenario_long25()
        scenario_knife_round()
        scenario_quoted_path()
        scenario_bad_input()
        scenario_tables()
        if arg == "--all":
            scenario_matrix()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
