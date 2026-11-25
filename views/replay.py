"""
The 2D replay page and the duel-breakdown dialog.

_replay_fragment draws the whole round on the radar for every player
(ui/roundrep.py); open_duel_dialog is the chart window a Duels row opens.
Both read the run's Ctx.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import core
import state
import theme
from context import get_round_outcomes, get_round_replay, get_timeline
from ui.charts import PAL, style_figure
from ui.format import duel_label, fmt_first, fmt_result, num
from ui.roundrep import round_payload, round_replay
from ui.strip import duel_strip


def _cut_at_death(tdf: pd.DataFrame, tick: int, tickrate: int):
    """Cut the window at the player's death: buttons / "speed" of a dead
    pawn are parser artifacts, not real input. -> (frame, death_x | None)."""
    if "is_alive" not in tdf.columns or not len(tdf):
        return tdf, None
    dead = ~tdf["is_alive"].astype(bool).to_numpy()
    if not dead.any():
        return tdf, None
    d_tick = int(tdf["tick"].to_numpy()[int(np.argmax(dead))])
    return tdf[tdf["tick"] <= d_tick], (d_tick - tick) / tickrate


def _key_runs(on: np.ndarray, xs: np.ndarray, row: int, tick_dur: float):
    """Bars of one key: one (start, end) segment per stretch it was held,
    as plotly line data with None gaps (a marker per tick looked like noise)."""
    edges = np.diff(np.concatenate(([0], on.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1
    bx: list = []
    for i0, i1 in zip(starts, ends, strict=True):
        bx += [float(xs[i0]), float(xs[i1]) + tick_dur, None]
    return bx, [row if v is not None else None for v in bx]


def _signal_traces(fig, t, x, xs, speed, yaw, pitch, thr, unit) -> None:
    """Speed / threshold / yaw / pitch lines (rows 1-3 of the figure).
    One colour per ROLE, never reused across rows with another meaning:
    speed = you (accent), yaw / pitch = their own hues, dashed yellow =
    the moving threshold, keys by key group."""
    c_speed, c_thr = PAL["you"], theme.tok("warning")
    c_yaw, c_pitch = theme.tok("chart-4"), theme.tok("chart-3")
    fig.add_trace(
        go.Scatter(
            x=x, y=speed, mode="lines", name=t("ch_speed"), legendgroup="signals",
            line=dict(color=c_speed, width=2), hovertemplate=unit,
        ),
        row=1, col=1,
    )
    # the "moving" threshold: above it a shot counts as a moving shot
    fig.add_trace(
        go.Scatter(
            x=[float(xs.min()), float(xs.max())], y=[thr, thr], mode="lines",
            name=t("tl_thr_line", thr=num(thr, 0)), legendgroup="signals",
            line=dict(color=c_thr, width=1.5, dash="dash"), hoverinfo="skip",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x, y=yaw, mode="lines", name=t("tl_leg_yaw"), legendgroup="signals",
            line=dict(color=c_yaw, width=2),
            hovertemplate="%{y:+.1f}°<extra>" + t("tl_leg_yaw") + "</extra>",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x, y=pitch, mode="lines", name=t("tl_leg_pitch"), legendgroup="signals",
            line=dict(color=c_pitch, width=2),
            hovertemplate="%{y:.1f}°<extra>" + t("tl_leg_pitch") + "</extra>",
        ),
        row=3, col=1,
    )


def _event_marker_traces(ctx, r, tdf, tick, fig, unit) -> None:
    """Shot / hit markers on the speed chart (within the shown window)."""
    t = ctx.t
    me = ctx.me
    tr = ctx.demo.tickrate
    w_lo, w_hi = int(tdf["tick"].min()), int(tdf["tick"].max())
    sp_by_tick = dict(zip(tdf["tick"].astype(int), tdf["speed"], strict=True)) if "speed" in tdf.columns else {}

    def _on_speed(ticks):
        pts = [((int(tk) - tick) / tr, sp_by_tick.get(int(tk))) for tk in ticks]
        return [p for p in pts if p[1] is not None and pd.notna(p[1])]

    my_fire = ctx.shots_f[(ctx.shots_f["sid"] == me) & ctx.shots_f["tick"].between(w_lo, w_hi)]
    fire_pts = _on_speed(my_fire["tick"])
    hit_pts = []
    if len(ctx.hurts_f) and "user_steamid" in ctx.hurts_f.columns:
        my_hits = ctx.hurts_f[
            (ctx.hurts_f["attacker_steamid"] == me)
            & (ctx.hurts_f["user_steamid"] == r["opponent"])
            & ctx.hurts_f["tick"].between(w_lo, w_hi)
        ]
        hit_pts = _on_speed(my_hits["tick"])
    for pts, name_key, marker in (
        (hit_pts, "tl_mark_hits", dict(symbol="star", size=12, color=PAL["good"])),
        (fire_pts, "tl_mark_shots", dict(symbol="triangle-up", size=9, color=PAL["ctx"])),
    ):
        if pts:
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers",
                    name=t(name_key), legendgroup="events", marker=marker,
                    hovertemplate=unit.replace(t("ch_speed"), t(name_key)),
                ),
                row=1, col=1,
            )


_KEY_GROUP = {
    "FORWARD": "move", "LEFT": "move", "BACK": "move", "RIGHT": "move",
    "WALK": "stance", "DUCK": "stance", "JUMP": "stance",
    "RIGHTCLICK": "weapon", "FIRE": "weapon", "RELOAD": "weapon",
}


def _key_traces(fig, tdf, xs, btn_cols, tick_dur, t) -> None:
    """Keys: a bar for every stretch a key was held; colour = key group,
    named in the legend."""
    group_style = {
        "move": (theme.tok("tier.good"), t("tl_grp_move")),
        "stance": (theme.tok("warning"), t("tl_grp_stance")),
        "weapon": (PAL["you"], t("tl_grp_weapon")),
    }
    seen_groups: set[str] = set()
    for k, b in enumerate(btn_cols):
        bx, by = _key_runs((tdf[b].fillna(0) != 0).to_numpy(), xs, k, tick_dur)
        if not bx:
            continue
        grp = _KEY_GROUP.get(b, "stance")
        colour, grp_title = group_style[grp]
        fig.add_trace(
            go.Scatter(
                x=bx, y=by, mode="lines", name=t(f"k_{b}"), legendgroup=grp,
                legendgrouptitle_text=grp_title if grp not in seen_groups else None,
                line=dict(color=colour, width=11), connectgaps=False, hoverinfo="skip",
            ),
            row=4, col=1,
        )
        seen_groups.add(grp)
    fig.update_yaxes(
        tickmode="array",
        tickvals=list(range(len(btn_cols))),
        ticktext=[t(f"k_{b}") for b in btn_cols],
        range=[len(btn_cols) - 0.5, -0.5],  # W on top
        row=4,
        col=1,
    )


def _reference_lines(fig, r, xs, tick, tr, death_x, tick_dur, t, rows_n) -> None:
    """Reference lines: the event, the first shot, the death."""
    c_thr = theme.tok("warning")
    first_x = None
    if pd.notna(r["my_first_tick"]):
        fx = (int(r["my_first_tick"]) - tick) / tr
        if float(xs.min()) <= fx <= float(xs.max()):
            first_x = fx
    # x = 0 is the duel event - label it with the result, not "kill/death"
    for row_i in range(1, rows_n + 1):
        # annotation kwargs only on the top row: passing annotation_position
        # with no text still creates an (empty) annotation on every subplot
        top = row_i == 1
        fig.add_vline(
            x=0, line_dash="dash", line_color=PAL["ctx"], row=row_i, col=1,
            **({"annotation_text": fmt_result(r["result"]), "annotation_position": "top right"} if top else {}),
        )
        if first_x is not None:
            fig.add_vline(
                x=first_x, line_dash="dot", line_color=c_thr, row=row_i, col=1,
                **({"annotation_text": t("tl_first_shot"), "annotation_position": "top left"} if top else {}),
            )
    # the death moment named on the chart (vermillion = bad); when it lands on
    # the event tick the lines would overlap - for a death duel the event line
    # already says "death", for a kill duel (a simultaneous trade) label the
    # shared line with both
    if death_x is not None:
        if abs(death_x) > tick_dur:
            fig.add_vline(
                x=death_x, line_color=PAL["bad"], annotation_text=t("tl_death"),
                annotation_position="bottom right", row=1, col=1,
            )
        elif r["result"] != "death":
            fig.add_annotation(
                text=t("tl_death"), x=0.0, xanchor="left", yanchor="bottom", y=0,
                yref="y domain", showarrow=False, row=1, col=1,
            )


def _duel_figure(ctx, r, tdf: pd.DataFrame, tick: int, death_x):
    """Speed / yaw / pitch / held keys around one duel (plotly)."""
    t = ctx.t
    thr = ctx.thr
    tr = ctx.demo.tickrate
    btn_cols = [b for b in core.BUTTONS if b in tdf.columns]
    x = (tdf["tick"] - tick) / tr
    xs = x.to_numpy()
    tick_dur = 1.0 / tr
    speed = (
        tdf["speed"]
        if "speed" in tdf.columns
        else pd.Series(np.nan, index=tdf.index)
    )
    # Yaw is shown as the turn relative to the aim at the event moment: the
    # absolute angle is an arbitrary map direction (and the unwrapped value
    # drifts past +-180), the turn is what matters. Pitch stays absolute -
    # 0 is the horizon.
    yaw_abs = np.degrees(np.unwrap(np.radians(tdf["yaw"].astype(float))))
    yaw = yaw_abs - yaw_abs[int(np.argmin(np.abs(xs)))]
    pitch = np.degrees(np.unwrap(np.radians(tdf["pitch"].astype(float))))

    rows_n = 4 if btn_cols else 3
    fig = make_subplots(
        rows=rows_n,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.22, 0.22, 0.22, 0.34] if rows_n == 4 else [0.34, 0.33, 0.33],
        subplot_titles=[t("ch_speed"), t("ch_yaw"), t("ch_pitch")]
        + ([t("ch_keys")] if btn_cols else []),
    )
    # the aim at the event moment (yaw) and the horizon (pitch): added BEFORE
    # the traces so the data lines draw on top of them
    fig.add_hline(y=0, line_color=theme.tok("chart-grid"), row=2, col=1)
    fig.add_hline(
        y=0,
        line_color=theme.tok("chart-grid"),
        annotation_text=t("tl_horizon"),
        annotation_position="bottom left",
        row=3,
        col=1,
    )
    unit = "%{y:.0f} units/s<extra>" + t("ch_speed") + "</extra>"
    _signal_traces(fig, t, x, xs, speed, yaw, pitch, thr, unit)
    _event_marker_traces(ctx, r, tdf, tick, fig, unit)
    if btn_cols:
        _key_traces(fig, tdf, xs, btn_cols, tick_dur, t)
    _reference_lines(fig, r, xs, tick, tr, death_x, tick_dur, t, rows_n)
    # units on every axis - a bare number was the whole complaint
    fig.update_yaxes(title_text=t("tl_ax_speed"), row=1, col=1)
    fig.update_yaxes(title_text=t("tl_ax_yaw"), row=2, col=1)
    fig.update_yaxes(title_text=t("tl_ax_pitch"), row=3, col=1)
    # half-second ticks: whole seconds were too coarse to line a shot or a
    # key press up with the curves above
    fig.update_xaxes(title_text=t("ch_x"), dtick=0.5, row=rows_n, col=1)
    style_figure(fig, height=760, margin=dict(t=60, r=10))
    fig.update_layout(
        hovermode="x unified",
        showlegend=True,
        legend=dict(
            orientation="v", x=1.01, xanchor="left", y=1, yanchor="top",
            groupclick="toggleitem", font=dict(size=12),
        ),
    )
    return fig


def _duel_verdict(ctx, r) -> list[str]:
    """The plain-words reasons behind one duel (distance, speed, who shot first)."""
    t = ctx.t
    thr = ctx.thr
    me = ctx.me
    shots_f = ctx.shots_f
    verdict = []
    if pd.notna(r["distance"]):
        verdict.append(t("tl_v_dist", dist=num(r["distance"], 0)))
    if pd.notna(r["my_speed"]):
        if r["my_speed"] > thr:
            verdict.append(t("tl_v_moved", speed=num(r["my_speed"], 0)))
        else:
            verdict.append(t("tl_v_stood"))
    if pd.notna(r["my_first_tick"]):
        fs_row = shots_f[
            (shots_f["sid"] == me) & (shots_f["tick"] == int(r["my_first_tick"]))
        ]
        if len(fs_row) and fs_row.iloc[0]["stop"] == "stopped":
            verdict.append(
                t("tl_v_stopped_cs" if bool(fs_row.iloc[0]["counter_strafe"]) else "tl_v_stopped_nocs")
            )
    if r["first"] == "me":
        verdict.append(t("tl_v_first_me"))
    elif r["first"] == "opponent":
        verdict.append(t("tl_v_first_opp"))
    return verdict


def _open_in_replay(rnd: int, tick: int, victim: str) -> None:
    """Button callback: show this duel in the 2D replay (selected, locked)."""
    st.session_state["rr_round"] = int(rnd)
    st.session_state["rr_sel"] = (int(rnd), int(tick), str(victim))
    state.go_tab("replay")


def _duel_strip(ctx) -> pd.Series:
    """The Left/Right duel selector strip (walks all duels of the table
    without leaving the window); returns the selected duel's row."""
    t = ctx.t
    my_duels = ctx.my_duels

    def _pick(i: int):
        state.TL_DUEL.set(i)

    labels = [duel_label(my_duels.iloc[i]) for i in range(len(my_duels))]
    duel_strip(
        [
            {"round": rd, "result": w, "tip": lab}
            for rd, w, lab in zip(my_duels["round"], my_duels["result"], labels, strict=True)
        ],
        state.TL_DUEL.get(),
        key="tl_strip",
        on_select=_pick,
        round_label=lambda rd, n: t("tl_strip_round", r=(rd if rd is not None else "?"), n=n),
        labels={
            "prev": t("tl_strip_prev"),
            "next": t("tl_strip_next"),
            "rprev": t("tl_strip_rprev"),
            "rnext": t("tl_strip_rnext"),
            "first": t("tl_strip_first"),
            "last": t("tl_strip_last"),
            "duel": t("tl_strip_duels"),
        },
    )
    st.caption(t("tl_strip_hint"))
    return my_duels.iloc[state.TL_DUEL.get()]


def _duel_summary_card(ctx, r) -> None:
    """The verdict card: result line, quick numbers, plain-words reasons."""
    t = ctx.t
    name_of = ctx.name_of
    with st.container(border=True):
        st.markdown(
            t(
                "tl_summary",
                res=fmt_result(r["result"]),
                opp=name_of.get(r["opponent"], r["opponent"]),
                weapon=str(r["weapon"]),
                first=fmt_first(r["first"]),
                shots=int(r["my_shots"]),
                dur=r["duration_s"],
            )
        )
        st.markdown(
            f"🕐 {num(r['duration_s'], 1)} s · ⚡ {num(r['my_speed'], 0)} u/s"
            f" · 🎯 {num(r['my_err'], 1)}°"
        )
        verdict = _duel_verdict(ctx, r)
        if verdict:
            st.caption(" · ".join(verdict))


def _first_shot_keys(ctx, r, tdf, btn_cols) -> None:
    """The first shot's speed / aim error and the keys held at it."""
    t = ctx.t
    if pd.notna(r["my_first_tick"]):
        st.markdown(t("tl_first", speed=num(r["my_speed"], 0), err=num(r["my_err"], 1)))
        frow = tdf[tdf["tick"] == int(r["my_first_tick"])] if btn_cols else tdf.iloc[0:0]
        if len(frow):
            v = frow.iloc[0]
            held = [b for b in btn_cols if pd.notna(v[b]) and v[b] != 0]
            st.markdown(
                t("tl_buttons", keys=", ".join(t(f"k_{b}") for b in held))
                if held
                else t("tl_buttons_none")
            )


def _duel_dialog(ctx) -> None:
    """A duel opened from the Duels table: the verdict and the chart.

    Left/Right (the strip) walk all duels of the table without leaving the
    window; a dialog is a fragment, so every step reruns only this window."""
    t = ctx.t
    demo = ctx.demo
    me = ctx.me
    my_duels = ctx.my_duels
    if not len(my_duels):
        st.info(t("du_none"))
        return
    if state.TL_DUEL.get() is None or not (0 <= state.TL_DUEL.get() < len(my_duels)):
        state.TL_DUEL.set(0)

    r = _duel_strip(ctx)
    tick = int(r["tick"])
    _duel_summary_card(ctx, r)
    # the same window the replay plays decides the round of this duel
    rnd = core.round_of_tick(demo.round_windows, tick) or int(r["round"])
    victim = me if r["result"] == "death" else r["opponent"]
    if rnd in (demo.round_windows or {}):
        if st.button(t("du_open_replay"), key="du_to_replay", on_click=_open_in_replay, args=(rnd, tick, victim)):
            st.rerun()  # the callback picked the page; a full run shows it

    with st.spinner(t("tl_loading")):
        tdf, missing = get_timeline(ctx.path, ctx.mtime, me, tick, ctx.window_s, demo.tickrate)
    if missing:
        st.warning(t("tl_no_buttons", keys=", ".join(t(f"k_{b}") for b in missing)))
    if tdf is None or tdf.empty:
        st.info(t("tl_empty"))
        return
    btn_cols = [b for b in core.BUTTONS if b in tdf.columns]
    _first_shot_keys(ctx, r, tdf, btn_cols)
    tdf, death_x = _cut_at_death(tdf, tick, demo.tickrate)
    if death_x is not None:
        st.caption(t("tl_dead_cut"))
    st.plotly_chart(_duel_figure(ctx, r, tdf, tick, death_x), width="stretch")
    st.caption(t("tl_howto"))


def open_duel_dialog(ctx) -> None:
    """Render the duel-breakdown modal (a Duels row click sets du_open)."""
    st.dialog(ctx.t("du_dialog_title"), width="large")(_duel_dialog)(ctx)


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
    """2D replay: the whole round on the radar (ui/roundrep.py).

    Everyone of both teams, every duel of every player; nobody is selected
    or highlighted. The round bar under the timeline switches rounds (one
    fragment rerun per switch)."""
    t = ctx.t
    demo = ctx.demo
    mtime = ctx.mtime
    path = ctx.path
    st.subheader(t("tab_replay"))
    windows = demo.round_windows or {}
    if not windows:
        st.info(t("rr_no_round"))
        return
    round_no = st.session_state.get("rr_round")
    if round_no not in windows:
        round_no = min(windows)
        st.session_state["rr_round"] = round_no
    with st.spinner(t("rr_loading")):
        try:
            data = get_round_replay(path, mtime, int(round_no))
        except ValueError:  # "no_round_data": an info message, not a crash
            st.info(t("rr_no_round"))
            return

    # the duels of the round by the SAME window the replay plays: a kill can
    # never show up under another round
    duels = core.round_duels(ctx.duels_all, windows[int(round_no)], demo.tickrate)
    sel = _picked_duel(duels, st.session_state.get("rr_sel"), int(round_no))

    # how every round ended: the demo's round_end when it has one, else read
    # from the bomb events and the kills (core.round_outcomes)
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
        st.session_state["rr_round"] = int(n)
        st.session_state.pop("rr_sel", None)

    round_replay(payload, key="rr_player", on_round=_on_round)


def render(ctx) -> None:
    """Draw the 2D replay page."""
    _replay_fragment(ctx)
