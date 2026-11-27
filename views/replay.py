"""The 2D replay page (a Duels row opens its duel here)."""

import streamlit as st

import core
import state
import theme
from context import get_round_outcomes, get_round_replay
from ui.charts import PAL
from ui.roundrep import round_payload, round_replay


_RR_LABEL_KEYS = (
    "play", "pause", "restart", "duel_prev", "duel_next", "speed",
    "time", "dead", "shot", "hit", "lvl0", "lvl1", "howto", "players",
    "players_hide", "players_show", "duels", "no_duels",
    "locked", "rounds", "round", "won_ct", "won_t", "leg_ct", "leg_t",
    "view_reset", "round_prev", "round_next", "eye_hide", "eye_show",
    "eye_all", "eye_none", "u_s", "out_unknown", "duels_hide", "duels_show",
    "out_inferred", "why_bomb", "why_defuse", "why_elim", "why_time",
    "why_surrender", "why_hostage", "why_draw", "why_other",
    "b_plant", "b_defuse", "b_boom", "duel_panel", "first_killer",
    "first_victim", "first_sim", "role_killer", "role_victim",
    "tel_speed", "tel_turn", "tel_err", "tel_dist", "tel_first",
    "tel_shots", "tel_leg_speed", "tel_leg_err", "tel_leg_shot",
    # economy table + movement cues (8e00c08)
    "leg_move", "tel_moving", "tel_standing", "tel_leg_thr",
    "pt_player", "pt_weapon", "pt_armor", "pt_hp", "pt_money", "pt_helmet",
    "pt_money_t", "pt_kit", "pt_bomb", "pt_defusing", "pt_blind",
    "pt_kills", "pt_deaths",
    "duel", "full", "full_exit", "labels", "labels_hide", "labels_show", "howto_title",
)


def _replay_labels(t, data: dict) -> dict:
    """The replay front end's label bundle (every UI string it shows)."""
    return {
        **{k: t(f"rr_{k}") for k in _RR_LABEL_KEYS},
        "exit": t("rr_exit_duel"),
        "hs": t("col_hs"),
        # the kill-modifier tooltips on the duel cards
        "wallbang": t("icon_wallbang"),
        "noscope": t("icon_noscope"),
        "smoke_kill": t("icon_smoke_kill"),
        "flashed_kill": t("icon_flashed_kill"),
        **{f"k_{k}": t(f"k_{k}") for k in (data.get("keys") or [])},
    }


def _replay_colors() -> dict:
    """The replay front end's colour bundle (theme tokens + palette)."""
    return {
        "ct": theme.tok("side-ct"),
        "t": theme.tok("side-t"),
        "dead": PAL["bad"],
        "hit": theme.tok("text-primary"),
        "flash": theme.tok("text-primary"),
        "good": theme.tok("positive"),
        "bad": PAL["bad"],
        "warn": theme.tok("warning"),
        "bg": theme.tok("bg-app"),
        "grid": theme.tok("border-subtle"),
        "text": theme.tok("text-primary"),
        "text2": theme.tok("text-secondary"),
        "acc": theme.tok("accent"),
    }


def _picked_duel(duels: list, pick, round_no: int) -> int:
    """The index of the duel a Duels window handed over (-1: none)."""
    if not pick or int(pick[0]) != int(round_no):
        return -1
    for pos, z in enumerate(duels):
        if z["tick"] == int(pick[1]) and z["victim"] == str(pick[2]):
            return pos
    return -1


@st.fragment
def _replay_fragment(ctx) -> None:
    """The whole round on the radar; the round bar switches rounds."""
    t = ctx.t
    demo = ctx.demo
    mtime = ctx.mtime
    path = ctx.path
    windows = demo.round_windows or {}
    if not windows:
        st.info(t("rr_no_round"))
        return
    round_no = state.RR_ROUND.get()
    if round_no not in windows:
        round_no = min(windows)
        state.RR_ROUND.set(round_no)
    with st.spinner(t("rr_loading")):
        try:
            data = get_round_replay(path, mtime, int(round_no))
        except ValueError:  # "no_round_data": an info message, not a crash
            st.info(t("rr_no_round"))
            return

    # by the SAME window the replay plays
    duels = core.round_duels(ctx.duels_all, windows[int(round_no)], demo.tickrate)
    sel = _picked_duel(duels, state.RR_SEL.get(), int(round_no))

    outcomes = get_round_outcomes(path, mtime)
    data["info"] = {**(data.get("info") or {}), **outcomes.get(int(round_no), {})}
    payload = round_payload(
        data,
        names=ctx.name_of,
        side_of=lambda s: demo.side_at(int(round_no), s),
        radar=core.load_radar(str(demo.header.get("map_name") or "")),
        duels=duels,
        rounds=[
            {"n": n, "win": o["winner"], "why": o["reason"]}
            for n, o in sorted(outcomes.items())
        ],
        thr=ctx.thr,
        sel=sel,
        labels=_replay_labels(t, data),
        colors=_replay_colors(),
    )
    if not payload["players"]:
        st.info(t("rr_no_data"))
        return

    def _on_round(n: int):
        state.RR_ROUND.set(int(n))
        state.RR_SEL.pop()

    round_replay(payload, key="rr_player", on_round=_on_round)


def render(ctx) -> None:
    """Draw the 2D replay page."""
    _replay_fragment(ctx)
