"""
Random-walk smoke: N seeds x M random UI actions, then assert the app
never broke.

A complement to smoke_app.py's scripted scenarios: instead of fixed
paths it throws random (but seeded, so reproducible) interactions at the
app - nav buttons, match tabs, language, player picks, list filters,
analysis filters, tab toggles, sliders - and after EVERY action checks:

  - the run raised no exception,
  - no st.error appeared,
  - every widget-bound session_state value is inside its legal option
    set (page, match_tab, mflt_*, flt_*, the tab toggles, slider ranges).

The pick() helper (smoke_app) is what makes this deep enough to matter:
format_func widgets (translated labels, player names) can be driven for
real, which the draft version of this walk could not do.

Usage:
    python tests/walk_app.py               # quick: 3 seeds x 60 actions
    python tests/walk_app.py --full        # deep:  5 seeds x 250 actions
    python tests/walk_app.py --seeds 7 --steps 120

Every failure prints seed/step plus the action that preceded it, so a
report reproduces with: --seeds N (the walk is deterministic per seed).
"""

import argparse
import random
import sys
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import smoke_app  # noqa: E402  (needs the sys.path setup above)

# the legal option sets, mirrored from the app (views/matches.py,
# shell.py): a value outside these after any action is a defect
PERIODS = ("7", "30", "all")
RESULTS = ("all", "win", "loss", "tie")
MATCH_TABS = ("overview", "shooting", "duels", "map", "replay")
NAV = ("dashboard", "matches", "settings", "guide")


def ss(at, key, default=None):
    return smoke_app.ss_get(at, key, default)


def _positions(demo: dict) -> None:
    """Give the synthetic demo kill positions so the Map tab renders."""
    de = demo["events"]["player_death"]
    for who, base in (("attacker", 0.0), ("user", 300.0)):
        for ax, k in (("X", 100.0), ("Y", 50.0), ("Z", 0.0)):
            de[f"{who}_{ax}"] = [base + k * (i + 1) for i in range(len(de))]


def sanity(at, seed: int, step: int, problems: list) -> None:
    """Everything that must hold after ANY action."""
    tag = f"seed{seed}/step{step}"
    if at.exception:
        problems.append(f"{tag}: EXCEPTION {smoke_app.err_of(at)}")
    if at.error:
        problems.append(f"{tag}: st.error {[str(e.value)[:60] for e in at.error]}")
    if ss(at, "page") not in NAV:
        problems.append(f"{tag}: page={ss(at, 'page')!r} not in NAV")
    if ss(at, "match_tab") not in (None, *MATCH_TABS):
        problems.append(f"{tag}: match_tab={ss(at, 'match_tab')!r} not legal")
    for key, legal in (
        ("mflt_period", PERIODS),
        ("mflt_result", RESULTS),
        ("flt_side", ("all", "T", "CT")),
        ("flt_dist", ("all", "close", "mid", "long")),
        ("flt_res", ("both", "kill", "death")),
        ("map_show", ("both", "kills", "deaths")),
        ("ov_full", (True, False)),
        ("du_full", (True, False)),
        ("sh_all", (True, False)),
        ("map_lines", (True, False)),
    ):
        value = ss(at, key)
        if value is not None and value not in legal:
            problems.append(f"{tag}: {key}={value!r} not in {legal}")
    kind = ss(at, "mflt_kind")
    if kind is not None and kind != "all" and kind not in smoke_app.core.KINDS:
        problems.append(f"{tag}: mflt_kind={kind!r} not a kind")
    thr = ss(at, "thr")
    if thr is not None and not (isinstance(thr, int) and 0 <= thr <= 250):
        problems.append(f"{tag}: thr={thr!r} out of range")


def _act_mflt(at, rng, which: str) -> str:
    """Drive one of the four list filters (label-mode pick)."""
    kind = "segmented_control" if which.endswith(("_period", "_result")) else "selectbox"
    w = smoke_app.widget(at, kind, which)
    if not w:
        return f"{which}:absent"
    smoke_app.pick(at, kind, which, rng.choice(list(w.options)))
    return which


_MFLT_KEYS = ("mflt_period", "mflt_result", "mflt_kind", "mflt_map")


def _do_mflt(at, rng, page: str, tab: str) -> str:
    return _act_mflt(at, rng, rng.choice(_MFLT_KEYS))


_FLT_PANEL_KEYS = (
    "flt_preset", "flt_side", "flt_dist", "flt_res",
    "flt_weapons", "flt_classes", "flt_rounds",
)


def _do_flt_panel(at, rng, page: str, tab: str) -> str:
    return _act_flt_panel(at, rng, rng.choice(_FLT_PANEL_KEYS))


_SLIDER_KEYS = ("thr", "window_s", "safe_dist", "strict_dist")


def _act_slider(at, rng, which: str) -> str:
    """Move one of the Settings sliders to a random legal value."""
    w = at.slider(key=which)
    if not w:
        return f"{which}:absent"
    lo, hi = w.min, w.max
    value = rng.choice([lo, hi, (lo + hi) / 2]) if isinstance(lo, float) else rng.randint(lo, hi)
    w.set_value(value).run()
    return which


def _do_sliders(at, rng, page: str, tab: str) -> str:
    return _act_slider(at, rng, rng.choice(_SLIDER_KEYS))


def _act_flt_panel(at, rng, which: str) -> str:
    """Drive one control of the open analysis-filter card."""
    kind = (
        "slider" if which == "flt_rounds"
        else "multiselect" if which == "flt_weapons"
        else "pills" if which == "flt_classes"
        else "segmented_control"
    )
    w = smoke_app.widget(at, kind, which)
    if not w:
        return f"{which}:absent"
    if which == "flt_rounds":
        lo, hi = w.min, w.max
        a = rng.randint(lo, hi)
        w.set_value((a, rng.randint(a, hi))).run()
    elif which in ("flt_weapons", "flt_classes"):
        options = list(w.options)
        w.set_value(rng.sample(options, k=min(len(options), rng.randint(0, 2)))).run()
    else:
        smoke_app.pick(at, kind, which, rng.choice(list(w.options)))
    return which


def _act_toggle(at, rng, tab: str) -> str:
    """Flip one of the tab-scoped toggles that render on this tab."""
    tab_toggles = {"overview": "ov_full", "shooting": "sh_all", "duels": "du_full"}
    candidates = []
    if tab in tab_toggles and smoke_app.widget(at, "toggle", tab_toggles[tab]):
        candidates.append(tab_toggles[tab])
    if tab == "map":
        candidates += [
            k for k in ("map_show", "map_lines")
            if smoke_app.widget(at, "toggle", k)
        ]
    if not candidates:
        return "toggles:absent"
    which = rng.choice(candidates)
    at.toggle(key=which).set_value(rng.choice((True, False))).run()
    return which


def _do_toggles(at, rng, page: str, tab: str) -> str:
    return _act_toggle(at, rng, tab)


def _do_nav(at, rng, page: str, tab: str) -> str:
    """Click a sidebar nav item."""
    target = rng.choice(NAV)
    smoke_app.goto(at, target)
    return f"nav->{target}"


def _do_lang(at, rng, page: str, tab: str) -> str:
    """Switch the language to a random option."""
    w = at.selectbox(key="lang")
    if not w:
        return "lang:absent"
    w.select(rng.choice(list(w.options))).run()
    return "lang"


def _do_tab(at, rng, page: str, tab: str) -> str:
    """Open a random match tab."""
    target = rng.choice(MATCH_TABS)
    smoke_app.goto(at, target)
    return f"tab->{target}"


def _do_player_btn(at, rng, page: str, tab: str) -> str:
    """Step the player with the prev/next buttons."""
    key = rng.choice(("me_prev", "me_next"))
    button = at.button(key=key)
    if button:
        button.click().run()
        return key
    return f"{key}:absent"


def _do_player_pick(at, rng, page: str, tab: str) -> str:
    """Pick a random player in the selectbox (label mode)."""
    w = smoke_app.widget(at, "selectbox", "me")
    if not w:
        return "me:absent"
    smoke_app.pick(at, "selectbox", "me", rng.choice(list(w.options)))
    return "me"


def _do_back(at, rng, page: str, tab: str) -> str:
    """Leave the match back to the list."""
    button = at.button(key="mt_back")
    if button:
        button.click().run()
        return "mt_back"
    smoke_app.goto(at, "matches")
    return "goto list"


def _do_mflt_reset(at, rng, page: str, tab: str) -> str:
    """Press the list filters' Reset."""
    button = at.button(key="mflt_reset")
    if button:
        button.click().run()
        return "mflt_reset"
    return "mflt_reset:absent"


def _do_filters(at, rng, page: str, tab: str) -> str:
    """Reset the analysis filters / dismiss a chip (the Edit popover's
    controls are covered by the flt_panel handler)."""
    for key in ("flt_reset", "flt_chip_0"):
        button = smoke_app.widget(at, "button", key)
        if button:
            button.click().run()
            return key
    return "filters:absent"


def act_random(at, rng: random.Random) -> str:
    """One random interaction on whatever page is currently shown.

    Handlers share the (at, rng, page, tab) signature; the caller weights
    the choice list by where the walk currently is (in-match actions only
    in the match view, list actions only on the list, sliders only on
    Settings)."""
    page = ss(at, "page")
    in_match = page == "matches" and ss(at, "match") is not None
    tab = ss(at, "match_tab")

    table: dict[str, object] = {"nav": _do_nav, "lang": _do_lang}
    if in_match:
        table.update(
            tab=_do_tab, player_btn=_do_player_btn, player_pick=_do_player_pick,
            back=_do_back, filters=_do_filters, toggles=_do_toggles,
        )
        if tab != "replay":
            table["flt_panel"] = _do_flt_panel
    else:
        table["mflt"] = _do_mflt
        if page == "matches":
            table["mflt_reset"] = _do_mflt_reset
        if page == "settings":
            table["sliders"] = _do_sliders
    what = rng.choice(sorted(table))
    return str(table[what](at, rng, page, tab))


def walk(seed: int, steps: int) -> list:
    """One seeded random walk; returns the problem list (empty = clean)."""
    rng = random.Random(seed)
    demo = smoke_app.make_match([1, 2, 3], weapons=("ak47", "awp", "deagle"))
    _positions(demo)
    at = smoke_app.open_demo(demo, f"walk{seed}.dem", me=smoke_app.S1)
    problems: list = []
    sanity(at, seed, 0, problems)
    for step in range(1, steps + 1):
        desc = act_random(at, rng)
        sanity(at, seed, step, problems)
        if problems and problems[-1].startswith(f"seed{seed}/step{step}"):
            problems[-1] += f" [after {desc}]"
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--seeds", type=int, nargs="+", default=None,
                        help="seed list (default: 3 quick / 5 full)")
    parser.add_argument("--steps", type=int, default=None,
                        help="actions per seed (default: 60 quick / 250 full)")
    parser.add_argument("--full", action="store_true",
                        help="the deep preset: 5 seeds x 250 actions")
    args = parser.parse_args()

    seeds = args.seeds or ([11, 22, 33, 44, 55] if args.full else [11, 22, 33])
    steps = args.steps or (250 if args.full else 60)

    # the app must never touch the real data folder from a test
    os.environ["AIMAUDIT_DATA_DIR"] = os.path.realpath(
        tempfile.mkdtemp(prefix="cs2aa_walk_")
    )
    smoke_app.core.loader.DemoParser = smoke_app.FakeParser
    smoke_app.core.replay.DemoParser = smoke_app.FakeParser

    failures = 0
    for seed in seeds:
        problems = walk(seed, steps)
        print(f"seed {seed}: {len(problems)} problems over {steps} actions")
        for problem in problems[:12]:
            print(f"    {problem}")
        failures += len(problems)
    print(f"\n{failures} problems over {len(seeds)} seeds x {steps} actions")
    if failures:
        print("reproduce a failing seed with: python tests/walk_app.py --seeds <N>")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
