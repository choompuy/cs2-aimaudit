"""
The Timeline page: one duel replayed tick by tick.

_round_block nests inside the @st.fragment function so it sees the same
locals the fragment unpacks from ctx.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import core
import theme
from context import get_round_replay, get_timeline
from ui.charts import PAL, style_figure
from ui.format import duel_label, fmt_first, fmt_result, num
from ui.roundrep import round_payload, round_replay
from ui.strip import duel_strip

# a fragment - picking another duel re-renders only the timeline


def render(ctx) -> None:
    """Draw the Timeline page."""
    _timeline_fragment(ctx)


@st.fragment
def _timeline_fragment(ctx):
    t = ctx.t
    thr = ctx.thr
    window_s = ctx.window_s
    demo = ctx.demo
    hurts_f = ctx.hurts_f
    me = ctx.me
    mtime = ctx.mtime
    my_duels = ctx.my_duels
    name_of = ctx.name_of
    path = ctx.path
    shots = ctx.shots
    shots_f = ctx.shots_f

    def _round_block(r, tick):
        """Round replay: the WHOLE round on the radar (ui/roundrep.py).

        The map shows everyone of both teams (HP in the circle, the view
        arrow, the weapon under the name), the timeline carries kill marks
        coloured by the killer's side, my duels of the round are cards on
        the right (a click locks the playhead to that duel's segment and
        brings up the duelists' key panels), and the rounds bar below the
        timeline switches the replay between rounds (winner-coloured chips,
        one fragment rerun per switch).
        """
        n_rounds = int(shots["round"].max()) if len(shots) else 0
        round_no = int(r["round"]) if pd.notna(r["round"]) else 0
        if round_no < 1:
            st.info(t("rr_no_round"))
            return
        # the rounds bar can move the replay to any round; selecting another
        # duel in the strip (tl_duel changes) pulls it back to that duel's
        # own round (the reset lives in _timeline_fragment)
        rr_round = st.session_state.get("rr_round")
        follow_duel = rr_round is None or int(rr_round) == round_no
        if rr_round is not None and int(rr_round) != round_no:
            round_no = int(rr_round)

        rd = (
            my_duels[my_duels["round"] == round_no]
            if len(my_duels) and "round" in my_duels.columns
            else pd.DataFrame()
        )
        opps = tuple(dict.fromkeys(rd["opponent"].astype(str))) if len(rd) else ()
        with st.spinner(t("tl_loading")):
            try:
                data = get_round_replay(path, mtime, round_no, me, opps, demo.tickrate)
            except ValueError:  # "no_round_data": honest info, not a crash
                st.info(t("rr_no_round"))
                return

        tr = demo.tickrate
        duels = []
        if len(rd):
            for _, dr in rd.iterrows():
                tk = int(dr["tick"])
                # the duel segment: from the first shot of EITHER duelist
                # (minus a small pre-roll to see the approach) to a bit after
                # the resolution (to see the outcome / the trade)
                first = [
                    x
                    for x in (dr.get("my_first_tick"), dr.get("opp_first_tick"))
                    if pd.notna(x)
                ]
                start = int(min(first)) if first else tk - int(1.0 * tr)
                duels.append(
                    {
                        "a": max(data["t0"], start - int(0.75 * tr)),
                        "b": min(data["t1"], tk + int(1.5 * tr)),
                        "tick": tk,
                        "result": str(dr["result"]),
                        "opp": name_of.get(dr["opponent"], str(dr["opponent"])),
                        "opp_sid": str(dr["opponent"]),
                        "weapon": str(dr["weapon"]),
                        "hs": bool(dr["headshot"]),
                        "dur": dr["duration_s"],
                    }
                )
        duels.sort(key=lambda z: z["tick"])

        # preselect the duel this page was opened from (locks its segment)
        sel = -1
        if follow_duel:
            for pos, tk_r in enumerate(rd["tick"] if len(rd) else []):
                if int(tk_r) == tick:
                    sel = pos
                    break

        winners = core.round_winners(demo)
        payload = round_payload(
            data,
            me=me,
            names=name_of,
            side_of=lambda s: demo.side_at(round_no, s),
            radar=core.load_radar(str(demo.header.get("map_name") or "")),
            duels=duels,
            rounds=[{"n": n, "win": winners.get(n, "")} for n in range(1, n_rounds + 1)],
            at=None if sel >= 0 else data["t0"],
            sel=sel,
            key_players=(
                [(me, name_of.get(me, me))] + [(o, name_of.get(o, o)) for o in opps[:5]]
            ),
            labels={
                "play": t("rr_play"),
                "pause": t("rr_pause"),
                "restart": t("rr_restart"),
                "duel_prev": t("rr_duel_prev"),
                "duel_next": t("rr_duel_next"),
                "speed": t("rr_speed"),
                "time": t("rr_time"),
                "you": t("rr_you"),
                "tm": t("rr_tm"),
                "en": t("rr_en"),
                "dead": t("rr_dead"),
                "shot": t("rr_shot"),
                "hit": t("rr_hit"),
                "lvl0": t("rr_lvl0"),
                "lvl1": t("rr_lvl1"),
                "howto": t("rr_howto"),
                "kill": t("res_kill"),
                "death": t("res_death"),
                "hs": t("col_hs"),
                "players": t("rr_players"),
                "players_hide": t("rr_players_hide"),
                "players_show": t("rr_players_show"),
                "duels": t("rr_duels"),
                "no_duels": t("rr_no_duels"),
                "exit": t("rr_exit_duel"),
                "locked": t("rr_locked"),
                "rounds": t("rr_rounds"),
                "round": t("rr_round"),
                "won_ct": t("rr_won_ct"),
                "won_t": t("rr_won_t"),
                "leg_ct": t("rr_leg_ct"),
                "leg_t": t("rr_leg_t"),
                "view_reset": t("rr_view_reset"),
                **{f"k_{k}": t(f"k_{k}") for k in (data.get("keys") or [])},
            },
            colors={
                "me": PAL["you"],
                "tm": theme.tok("info"),
                "en": theme.tok("tier.poor"),
                "dead": PAL["bad"],
                "ring": theme.tok("text-primary"),
                "hit": theme.tok("chart-5"),
                "flash": theme.tok("text-primary"),
                "good": theme.tok("positive"),
                "bad": PAL["bad"],
                "bg": theme.tok("bg-app"),
                "grid": theme.tok("border-subtle"),
                "text": theme.tok("text-primary"),
                "text2": theme.tok("text-secondary"),
                "hpText": theme.tok("text-primary"),
                "ct": theme.tok("info"),
                "t": theme.tok("warning"),
                "ctb": theme.tok("team-ct"),
                "ttb": theme.tok("team-t"),
                "acc": theme.tok("accent"),
            },
        )
        if not payload["players"]:
            st.info(t("rr_no_data"))
            return

        def _on_round(n: int):
            st.session_state["rr_round"] = int(n)

        round_replay(payload, key="tl_roundrep", on_round=_on_round)

    st.subheader(t("tl_title"))
    if not len(my_duels):
        st.info(t("du_none"))
    else:
        if not isinstance(st.session_state.get("tl_duel"), int) or not (
            0 <= st.session_state["tl_duel"] < len(my_duels)
        ):
            st.session_state["tl_duel"] = 0

        # Fast duel / round navigation (ui/strip.py): every duel is a chip
        # under its round badge, so any duel is <=2 actions away (a chip
        # click, or Shift+Left/Right to the round, then Left/Right).
        # Left/Right walk ALL duels with wrap-around: the whole match from
        # a single key; Home/End jump to the first/last duel. Up/Down keep
        # switching the player, so the steppers never collide.
        def _tl_pick(i: int):
            st.session_state["tl_duel"] = int(i)

        labels = [duel_label(my_duels.iloc[i]) for i in range(len(my_duels))]
        duel_strip(
            [
                {"round": r, "result": w, "tip": lab}
                for r, w, lab in zip(my_duels["round"], my_duels["result"], labels)
            ],
            st.session_state["tl_duel"],
            key="tl_strip",
            on_select=_tl_pick,
            round_label=lambda r, n: t(
                "tl_strip_round", r=(r if r is not None else "?"), n=n
            ),
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
        i = st.session_state["tl_duel"]
        # picking another duel returns the round replay to that duel's
        # own round (drops a round chosen earlier from the rounds bar)
        if st.session_state.get("rr_round_duel") != i:
            st.session_state["rr_round_duel"] = i
            st.session_state.pop("rr_round", None)
        r = my_duels.iloc[i]
        tick = int(r["tick"])
        with st.spinner(t("tl_loading")):
            tdf, missing = get_timeline(path, mtime, me, tick, window_s, demo.tickrate)
        if missing:
            st.warning(t("tl_no_buttons", keys=", ".join(t(f"k_{b}") for b in missing)))

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
                    t(
                        "tl_v_stopped_cs"
                        if bool(fs_row.iloc[0]["counter_strafe"])
                        else "tl_v_stopped_nocs"
                    )
                )
        if r["first"] == "me":
            verdict.append(t("tl_v_first_me"))
        elif r["first"] == "opponent":
            verdict.append(t("tl_v_first_opp"))

        # the verdict card leads - one sentence, the three numbers
        # that explain it (icons + values + units, never icons alone)
        # and the plain-words verdict line
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
            if verdict:
                st.caption(" · ".join(verdict))
        st.session_state.setdefault("tl_view", "charts")
        view = (
            st.segmented_control(
                t("tl_view"),
                ["charts", "replay"],
                format_func=lambda v: t(f"tl_view_{v}"),
                key="tl_view",
            )
            or "charts"
        )
        if view == "replay":
            _round_block(r, tick)
            return
        if tdf is None or tdf.empty:
            st.info(t("tl_empty"))
        else:
            btn_cols = [b for b in core.BUTTONS if b in tdf.columns]
            if pd.notna(r["my_first_tick"]):
                st.markdown(
                    t("tl_first", speed=num(r["my_speed"], 0), err=num(r["my_err"], 1))
                )
                if btn_cols:
                    frow = tdf[tdf["tick"] == int(r["my_first_tick"])]
                    if len(frow):
                        v = frow.iloc[0]
                        held = [b for b in btn_cols if pd.notna(v[b]) and v[b] != 0]
                        if held:
                            st.markdown(
                                t(
                                    "tl_buttons",
                                    keys=", ".join(t(f"k_{b}") for b in held),
                                )
                            )
                        else:
                            st.markdown(t("tl_buttons_none"))

            # Cut the window at the player's death: buttons/"speed" of a
            # dead pawn are parser artifacts, not real input.
            death_x = None
            if "is_alive" in tdf.columns and len(tdf):
                dead_ix = tdf.index[~tdf["is_alive"].astype(bool)]
                if len(dead_ix):
                    d_tick = int(tdf.loc[dead_ix[0], "tick"])
                    if d_tick <= int(tdf["tick"].max()):
                        tdf = tdf[tdf["tick"] <= d_tick]
                        st.caption(t("tl_dead_cut"))
                        death_x = (d_tick - tick) / demo.tickrate
            x = (tdf["tick"] - tick) / demo.tickrate
            xs = x.to_numpy()
            tick_dur = 1.0 / demo.tickrate
            speed = (
                tdf["speed"]
                if "speed" in tdf.columns
                else pd.Series(np.nan, index=tdf.index)
            )
            # Yaw is shown as the turn relative to the aim at the event
            # moment: the absolute angle is an arbitrary map direction (and
            # the unwrapped value drifts past +-180), the turn is what
            # matters. Pitch stays absolute - 0 is the horizon.
            yaw_abs = np.degrees(np.unwrap(np.radians(tdf["yaw"].astype(float))))
            yaw = yaw_abs - yaw_abs[int(np.argmin(np.abs(xs)))]
            pitch = np.degrees(np.unwrap(np.radians(tdf["pitch"].astype(float))))

            # one colour per ROLE, never reused across rows with another
            # meaning: speed = you (accent), yaw / pitch = their own hues,
            # dashed yellow = the moving threshold, keys by key group
            c_speed, c_thr = PAL["you"], theme.tok("warning")
            c_yaw, c_pitch = theme.tok("chart-4"), theme.tok("chart-3")
            key_group = {
                "FORWARD": "move",
                "LEFT": "move",
                "BACK": "move",
                "RIGHT": "move",
                "WALK": "stance",
                "DUCK": "stance",
                "JUMP": "stance",
                "RIGHTCLICK": "weapon",
                "FIRE": "weapon",
                "RELOAD": "weapon",
            }
            group_style = {
                "move": (theme.tok("tier.good"), t("tl_grp_move")),
                "stance": (theme.tok("warning"), t("tl_grp_stance")),
                "weapon": (PAL["you"], t("tl_grp_weapon")),
            }

            rows_n = 4 if btn_cols else 3
            titles = [t("ch_speed"), t("ch_yaw"), t("ch_pitch")] + (
                [t("ch_keys")] if btn_cols else []
            )
            fig = make_subplots(
                rows=rows_n,
                cols=1,
                shared_xaxes=True,
                row_heights=(
                    [0.22, 0.22, 0.22, 0.34] if rows_n == 4 else [0.34, 0.33, 0.33]
                ),
                subplot_titles=titles,
            )
            # the aim at the event moment (yaw) and the horizon (pitch):
            # added BEFORE the traces so the data lines draw on top of them
            fig.add_hline(y=0, line_color=theme.tok("chart-grid"), row=2, col=1)
            fig.add_hline(
                y=0,
                line_color=theme.tok("chart-grid"),
                annotation_text=t("tl_horizon"),
                annotation_position="bottom left",
                row=3,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=speed,
                    mode="lines",
                    name=t("ch_speed"),
                    legendgroup="signals",
                    line=dict(color=c_speed, width=2),
                    hovertemplate="%{y:.0f} units/s<extra>"
                    + t("ch_speed")
                    + "</extra>",
                ),
                row=1,
                col=1,
            )
            # the "moving" threshold: above it a shot counts as a moving shot
            fig.add_trace(
                go.Scatter(
                    x=[float(xs.min()), float(xs.max())],
                    y=[thr, thr],
                    mode="lines",
                    name=t("tl_thr_line", thr=num(thr, 0)),
                    legendgroup="signals",
                    line=dict(color=c_thr, width=1.5, dash="dash"),
                    hoverinfo="skip",
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=yaw,
                    mode="lines",
                    name=t("tl_leg_yaw"),
                    legendgroup="signals",
                    line=dict(color=c_yaw, width=2),
                    hovertemplate="%{y:+.1f}°<extra>" + t("tl_leg_yaw") + "</extra>",
                ),
                row=2,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=pitch,
                    mode="lines",
                    name=t("tl_leg_pitch"),
                    legendgroup="signals",
                    line=dict(color=c_pitch, width=2),
                    hovertemplate="%{y:.1f}°<extra>" + t("tl_leg_pitch") + "</extra>",
                ),
                row=3,
                col=1,
            )
            # shot / hit markers on the speed chart (within the shown window)
            w_lo, w_hi = int(tdf["tick"].min()), int(tdf["tick"].max())
            sp_by_tick = dict(zip(tdf["tick"].astype(int), tdf["speed"]))
            my_fire = shots_f[
                (shots_f["sid"] == me) & shots_f["tick"].between(w_lo, w_hi)
            ]
            fire_pts = [
                ((int(tk) - tick) / demo.tickrate, sp_by_tick.get(int(tk)))
                for tk in my_fire["tick"]
                if pd.notna(sp_by_tick.get(int(tk)))
            ]
            if len(hurts_f) and "user_steamid" in hurts_f.columns:
                my_hits = hurts_f[
                    (hurts_f["attacker_steamid"] == me)
                    & (hurts_f["user_steamid"] == r["opponent"])
                    & hurts_f["tick"].between(w_lo, w_hi)
                ]
                hit_pts = [
                    ((int(tk) - tick) / demo.tickrate, sp_by_tick.get(int(tk)))
                    for tk in my_hits["tick"]
                    if pd.notna(sp_by_tick.get(int(tk)))
                ]
            else:
                hit_pts = []
            if hit_pts:
                fig.add_trace(
                    go.Scatter(
                        x=[p[0] for p in hit_pts],
                        y=[p[1] for p in hit_pts],
                        mode="markers",
                        name=t("tl_mark_hits"),
                        legendgroup="events",
                        marker=dict(symbol="star", size=12, color=PAL["good"]),
                        hovertemplate="%{y:.0f} units/s<extra>"
                        + t("tl_mark_hits")
                        + "</extra>",
                    ),
                    row=1,
                    col=1,
                )
            if fire_pts:
                fig.add_trace(
                    go.Scatter(
                        x=[p[0] for p in fire_pts],
                        y=[p[1] for p in fire_pts],
                        mode="markers",
                        name=t("tl_mark_shots"),
                        legendgroup="events",
                        marker=dict(symbol="triangle-up", size=9, color=PAL["ctx"]),
                        hovertemplate="%{y:.0f} units/s<extra>"
                        + t("tl_mark_shots")
                        + "</extra>",
                    ),
                    row=1,
                    col=1,
                )
            # keys: a bar for every stretch a key was held (a marker per
            # tick looked like noise); colour = key group, named in the legend
            if btn_cols:
                seen_groups: set[str] = set()
                for k, b in enumerate(btn_cols):
                    on = (tdf[b].fillna(0) != 0).to_numpy()
                    bx: list = []
                    by: list = []
                    i0 = None
                    for j, flag in enumerate(on):
                        if flag and i0 is None:
                            i0 = j
                        if i0 is not None and (not flag or j == len(on) - 1):
                            j_end = j if flag else j - 1
                            bx += [float(xs[i0]), float(xs[j_end]) + tick_dur, None]
                            by += [k, k, None]
                            i0 = None
                    if not bx:
                        continue
                    grp = key_group.get(b, "stance")
                    colour, grp_title = group_style[grp]
                    fig.add_trace(
                        go.Scatter(
                            x=bx,
                            y=by,
                            mode="lines",
                            name=t(f"k_{b}"),
                            legendgroup=grp,
                            legendgrouptitle_text=(
                                grp_title if grp not in seen_groups else None
                            ),
                            line=dict(color=colour, width=11),
                            connectgaps=False,
                            hoverinfo="skip",
                        ),
                        row=4,
                        col=1,
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
            # reference lines: the event, the first shot, the death
            first_x = None
            if pd.notna(r["my_first_tick"]):
                fx = (int(r["my_first_tick"]) - tick) / demo.tickrate
                if float(xs.min()) <= fx <= float(xs.max()):
                    first_x = fx
            # x = 0 is the duel event - we know if it was your kill or
            # your death, so name it exactly instead of "kill/death"
            ev_text = fmt_result(r["result"])
            for row_i in range(1, rows_n + 1):
                # annotation kwargs only on the top row: passing
                # annotation_position with no text still creates an
                # (empty) annotation on every other subplot
                top_ann = (
                    {"annotation_text": ev_text, "annotation_position": "top right"}
                    if row_i == 1
                    else {}
                )
                fig.add_vline(
                    x=0,
                    line_dash="dash",
                    line_color=PAL["ctx"],
                    row=row_i,
                    col=1,
                    **top_ann,
                )
                if first_x is not None:
                    fig.add_vline(
                        x=first_x,
                        line_dash="dot",
                        line_color=c_thr,
                        row=row_i,
                        col=1,
                        **(
                            {
                                "annotation_text": t("tl_first_shot"),
                                "annotation_position": "top left",
                            }
                            if row_i == 1
                            else {}
                        ),
                    )
            # the death moment named on the chart (vermillion = bad); when
            # it lands on the event tick the lines would overlap - for a
            # death duel the event line already says "death", for a kill
            # duel (a simultaneous trade) label the shared line with both
            if death_x is not None:
                if abs(death_x) > tick_dur:
                    fig.add_vline(
                        x=death_x,
                        line_color=PAL["bad"],
                        annotation_text=t("tl_death"),
                        annotation_position="bottom right",
                        row=1,
                        col=1,
                    )
                elif r["result"] != "death":
                    fig.add_annotation(
                        text=t("tl_death"),
                        x=0.0,
                        xanchor="left",
                        yanchor="bottom",
                        y=0,
                        yref="y domain",
                        showarrow=False,
                        row=1,
                        col=1,
                    )
            # units on every axis - a bare number was the whole complaint
            fig.update_yaxes(title_text=t("tl_ax_speed"), row=1, col=1)
            fig.update_yaxes(title_text=t("tl_ax_yaw"), row=2, col=1)
            fig.update_yaxes(title_text=t("tl_ax_pitch"), row=3, col=1)
            # half-second ticks: whole seconds were too coarse to line a
            # shot or a key press up with the curves above
            fig.update_xaxes(title_text=t("ch_x"), dtick=0.5, row=rows_n, col=1)
            # shared look (palette, font, margins); the height stays 760 -
            # four stacked rows need it. The legend sits on the right and is
            # always on: it names the lines, markers and key groups.
            style_figure(fig, height=760, margin=dict(t=60, r=10))
            fig.update_layout(
                hovermode="x unified",
                showlegend=True,
                legend=dict(
                    orientation="v",
                    x=1.01,
                    xanchor="left",
                    y=1,
                    yanchor="top",
                    groupclick="toggleitem",
                    font=dict(size=12),
                ),
            )
            st.plotly_chart(fig, width="stretch")
            st.caption(t("tl_howto"))
