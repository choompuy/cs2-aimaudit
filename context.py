"""
Run context for the app: cached core calls and the shared Ctx.

The cached wrappers (get_demo / get_shots / get_duels / get_timeline /
get_replay) are st.cache_* functions keyed on the demo file. Ctx is the
bundle of page-shared state that the shell fills as the run progresses
and every view reads (see views/*.render(ctx)).
"""

from dataclasses import dataclass

import pandas as pd
import streamlit as st

import core

# The translator used by _say() progress labels, injected once per run
# (after the language is known) via set_t().
t = None


def set_t(translator):
    """Remember the run's translator (called right after make_t)."""
    global t
    t = translator


_status = {"box": None}


def _say(step, **kw):
    """Progress updates from core.load_demo -> st.status."""
    box = _status["box"]
    if box is None:
        return
    if step == "players":
        box.update(label=t("step_players"))
    elif step == "events":
        box.update(label=t("step_events"))
    elif step == "ticks":
        box.update(label=t("step_ticks", n=kw.get("n", 0)))


@st.cache_resource(show_spinner=False)
def get_demo(path: str, mtime: float) -> core.Demo:
    # through the disk cache of the data folder: a demo parsed once opens
    # again instantly, also after the app was restarted
    demo, _from_cache = core.load_demo_cached(path, progress=_say)
    return demo


@st.cache_data(show_spinner=False)
def get_timeline(
    path: str, mtime: float, me: str, tick: int, before_s: float, tickrate: int
):
    return core.timeline_data(path, me, tick, before_s=before_s, tickrate=tickrate)


@st.cache_data(show_spinner=False)
def get_replay(
    path: str,
    mtime: float,
    me: str,
    opp: str,
    tick: int,
    before_s: float,
    tickrate: int,
):
    return core.replay_data(path, [me, opp], tick, before_s=before_s, tickrate=tickrate)


@st.cache_data(show_spinner=False)
def get_round_replay(
    path: str,
    mtime: float,
    round_no: int,
    me: str,
    opps: tuple[str, ...],
    tickrate: int,
):
    """Tick data of the whole round for the round replay (ui/roundrep.py).

    ``opps`` are ALL duel opponents of the round: their held keys get a
    panel while their duel segment is selected."""
    demo = get_demo(path, mtime)
    return core.round_replay_data(
        path,
        round_no,
        me,
        key_sids=[s for s in (me, *opps) if s],
        tickrate=tickrate,
        fires=demo.fires,
        deaths=demo.deaths,
        hurts=demo.hurts,
    )


# The heavy frames behind st.cache_data, keyed on the demo file and
# the analysis thresholds, so a rerun (or a slider tweak that misses the
# cache only once) does not recompute every page from scratch.
@st.cache_data(show_spinner=False)
def get_shots(path: str, mtime: float, thr: int, safe_dist: int, strict_dist: int):
    demo = get_demo(path, mtime)
    return core.add_stop_quality(
        core.add_risk(core.shots_table(demo), thr, safe_dist, strict_dist), thr
    )


@st.cache_data(show_spinner=False)
def get_duels(path: str, mtime: float, window_s: float):
    demo = get_demo(path, mtime)
    return core.build_duels(demo, window_s)


# ------------------------------------------------------------------- Ctx
@dataclass
class Ctx:
    """
    The run-shared state the shell fills and every view reads.

    Flat on purpose: each view's render(ctx) unpacks the few fields it
    needs into locals. Fields are grouped by the shell function that
    produces them.
    """

    # sidebar(ctx)
    t = None  # the run's translator
    thr = None  # stop-quality threshold
    window_s = None  # duel window, seconds
    safe_dist = None  # close-range band
    strict_dist = None  # long-range band
    # open_demo(ctx)
    state = None  # persistent state.json dict
    status = None  # the st.status plate while loading
    entry = None  # the library entry of the open demo
    change_open = None  # the "Change demo" panel is open
    demo = None  # core.Demo
    path = None  # unpacked demo path (cache key part)
    mtime = None  # its mtime (cache key part)
    shots = None  # unfiltered shots table
    duels_all = None  # unfiltered duels table
    # context_bar(ctx)
    me = None  # the selected player sid
    name_of = None  # sid -> name
    # filters_ui.render(ctx)
    filters = None  # core.Filters built from the panel
    shots_f = None  # filtered frames
    duels_f = None
    hurts_f = None
    res_sel = None  # kill/death filter (duels pages)
    hdr = None  # the sticky header container
    # page_metrics(ctx)
    summary = None
    my_duels = None
    openings = None
    impact = None
    # nav(ctx)
    page = None  # the selected page id


def page_metrics(ctx) -> None:
    """The per-run tables every page reads."""
    t = ctx.t
    demo = ctx.demo
    duels_f = ctx.duels_f
    shots_f = ctx.shots_f
    thr = ctx.thr
    safe_dist = ctx.safe_dist
    strict_dist = ctx.strict_dist
    hurts_f = ctx.hurts_f
    filters = ctx.filters
    me = ctx.me
    res_sel = ctx.res_sel
    status = ctx.status

    summary = core.player_summary(
        demo, duels_f, shots_f, thr, safe_dist, strict_dist, hurts=hurts_f, f=filters
    )

    my_duels = core.duels_for(duels_f, me) if len(duels_f) else pd.DataFrame()
    # kill/death filter is a per-player concept: it follows the Result
    # control (Duels / Timeline); the all-players Overview keeps both
    if res_sel and len(my_duels):
        my_duels = my_duels[my_duels["result"].isin(res_sel)]
    # round-impact layer: opening duels, trades, multi-kills,
    # assists. Round structure, so like the K/D in the summary it follows the
    # Side and Rounds filters (a player's rounds on that side), not the
    # weapon / distance ones. The openings table on the Duels page is the
    # match context of both teams and stays unfiltered.
    openings = core.openings_table(demo)
    impact = core.impact_stats(demo, f=filters)
    if status is not None:
        status.update(label=t("loaded"), state="complete")

    ctx.summary = summary
    ctx.my_duels = my_duels
    ctx.openings = openings
    ctx.impact = impact
