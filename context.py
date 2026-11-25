"""Cached core calls and Ctx, the run state the shell fills and every view reads."""

from dataclasses import dataclass

import pandas as pd
import streamlit as st

import core

# the translator of the _say() progress labels, set once per run
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
    demo, _from_cache = core.load_demo_cached(path, progress=_say)
    return demo


@st.cache_data(show_spinner=False)
def get_timeline(
    path: str, mtime: float, me: str, tick: int, before_s: float, tickrate: int
):
    return core.timeline_data(path, me, tick, before_s=before_s, tickrate=tickrate)


@st.cache_data(show_spinner=False)
def get_round_replay(path: str, mtime: float, round_no: int):
    """Tick data of the WHOLE round for the 2D replay (ui/roundrep.py)."""
    return core.round_replay_data(get_demo(path, mtime), round_no)


@st.cache_data(show_spinner=False)
def get_round_outcomes(path: str, mtime: float) -> dict:
    """How every round ended (winner + reason; the demo's round_end, else
    read from the bomb events and the kills - core.round_outcomes)."""
    return core.round_outcomes(get_demo(path, mtime))


# keyed on the demo file and the thresholds
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
    """The run-shared state, grouped by the shell function that fills it."""

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
    # match_shell(ctx)
    tab = None  # the active match tab (overview / shooting / ...)
    # page_metrics(ctx)
    summary = None
    my_duels = None
    openings = None
    impact = None


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
    # the kill/death filter is per player: the Overview keeps both
    if res_sel and len(my_duels):
        my_duels = my_duels[my_duels["result"].isin(res_sel)]
    # follows the Side and Rounds filters like the K/D, not weapon / distance; the
    # openings table shows both teams and stays unfiltered
    openings = core.openings_table(demo)
    impact = core.impact_stats(demo, f=filters)
    if status is not None:
        status.update(label=t("loaded"), state="complete")

    ctx.summary = summary
    ctx.my_duels = my_duels
    ctx.openings = openings
    ctx.impact = impact
