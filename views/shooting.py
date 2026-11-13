"""The Shooting page: accuracy, hit groups, first shots, weapons."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core
from ui import schema
from ui.charts import PAL, style_figure
from ui.format import fmt_result, num, pct

HG_ORDER = [
    "head",
    "neck",
    "chest",
    "stomach",
    "left_arm",
    "right_arm",
    "left_leg",
    "right_leg",
    "other",
]


def render(ctx) -> None:
    """Draw the Shooting page."""
    t = ctx.t
    demo = ctx.demo
    hurts_f = ctx.hurts_f
    impact = ctx.impact
    me = ctx.me
    my_duels = ctx.my_duels
    name_of = ctx.name_of
    safe_dist = ctx.safe_dist
    shots_f = ctx.shots_f
    strict_dist = ctx.strict_dist
    thr = ctx.thr
    sh_me = shots_f[shots_f["sid"] == me]
    hg_me = core.hitgroup_counts(demo, me, hurts_f)
    dd = my_duels
    kills = int((dd["result"] == "kill").sum()) if len(dd) else 0
    dths = int((dd["result"] == "death").sum()) if len(dd) else 0
    hits_n = int(hg_me.sum()) if len(hg_me) else 0
    hs_hits = hg_me.get("head", 0) / hits_n * 100 if hits_n else np.nan
    moving_pct = (sh_me["speed"] > thr).mean() * 100 if len(sh_me) else np.nan
    known = (
        sh_me["dist"].notna() & sh_me["speed"].notna()
        if len(sh_me)
        else pd.Series(dtype=bool)
    )
    risky_pct = sh_me.loc[known, "risky"].mean() * 100 if known.any() else np.nan
    fs = dd[dd["first"].isin(["me", "opponent"])] if len(dd) else dd
    # round-impact row of the selected player (follows Side / Rounds)
    imp_me = impact[impact["sid"] == me]
    imp_me = imp_me.iloc[0] if len(imp_me) else None
    first_pct = (fs["first"] == "me").mean() * 100 if len(fs) else np.nan
    dur = dd["duration_s"].median() if len(dd) else np.nan
    err = dd["my_err"].median() if len(dd) else np.nan
    spd = dd["my_speed"].median() if len(dd) else np.nan
    dd_err = dd[dd["my_err"].notna()] if len(dd) else dd
    onhead_pct = dd_err["my_would_head"].mean() * 100 if len(dd_err) else np.nan
    stops = sh_me[sh_me["stop"] == "stopped"] if len(sh_me) else sh_me
    cs_pct = stops["counter_strafe"].mean() * 100 if len(stops) else np.nan

    # shot discipline: firing while flashed / while damage-slowed.
    # NA (prop unknown on old demos) never counts as False - a verdict is
    # only issued when the data exists.
    fl_me = sh_me["flashed"]
    sl_me = sh_me["slowed"]
    fl_known, sl_known = int(fl_me.notna().sum()), int(sl_me.notna().sum())
    fl_n = int(fl_me.fillna(False).sum())
    sl_n = int(sl_me.fillna(False).sum())

    # first-shot discipline: accuracy on the first shot of an engagement
    # (crosshair placement) and the mouse movement that preceded it
    # (usercmd deltas; NA on demos without them - no verdict then).
    fs_me = sh_me[sh_me["first_shot"]] if len(sh_me) else sh_me
    fu_me = sh_me[~sh_me["first_shot"]] if len(sh_me) else sh_me
    first_n = len(fs_me)
    first_acc = fs_me["hit"].mean() * 100 if first_n else np.nan
    follow_acc = fu_me["hit"].mean() * 100 if len(fu_me) else np.nan
    fm = fs_me["flick_move"] if first_n else pd.Series(dtype=float)
    fm_known = int(fm.notna().sum())
    swing_med = fm.median() if fm_known else np.nan
    # mid-swing first shots: pre-fire movement >= 2x the player's own
    # median - the bar self-calibrates for sensitivity and playstyle
    swing_n = (
        int((fm >= 2.0 * float(swing_med)).sum())
        if fm_known and pd.notna(swing_med)
        else 0
    )

    # "Top 3 things to work on" opens the tab: a severity chip
    # (HIGH / MED / INFO), the sentence with its number, and the evidence
    # behind a "show me" popover (the duels behind the number) or a
    # pointer to the chart below. The engine emits at most three items:
    # one movement verdict, the aim-error one, the first-shot one.
    tips = []
    if len(sh_me) >= 10:
        if pd.notna(risky_pct) and risky_pct > 35:
            tips.append((2, t("tip_risky", pct=risky_pct), "risk"))
        elif pd.notna(moving_pct) and moving_pct > 35:
            tips.append((1, t("tip_moving", pct=moving_pct), "speed"))
        else:
            tips.append(
                (
                    0,
                    t("tip_moving_ok", pct=moving_pct if pd.notna(moving_pct) else 0),
                    None,
                )
            )
    if len(dd) >= 5 and pd.notna(err) and err > 5:
        tips.append((1, t("tip_err", err=err), "err"))
    dth_df = dd[dd["result"] == "death"] if len(dd) else dd
    if len(dth_df) >= 3:
        n_lost = int((dth_df["first"] == "opponent").sum())
        if n_lost and n_lost / len(dth_df) >= 0.5:
            tips.append((1, t("tip_lost_first", n=n_lost, m=len(dth_df)), "lost"))
    # opening-duel verdict: only when the player takes openings
    # often enough (>= 5) and keeps losing them (<= 30% won)
    if imp_me is not None and imp_me["open_attempts"] >= 5:
        _o_rate = imp_me["open_rate"]
        if pd.notna(_o_rate) and _o_rate <= 30.0:
            tips.append(
                (
                    1,
                    t(
                        "tip_openings",
                        won=int(imp_me["open_wins"]),
                        n=int(imp_me["open_attempts"]),
                    ),
                    None,
                )
            )
    # discipline tips - appended AFTER the established verdicts, so
    # at equal severity the aim / first-shot coaching keeps its top-3 place
    if fl_known >= 20 and fl_n >= 3 and fl_n / fl_known >= 0.08:
        tips.append((1, t("tip_flashed", n=fl_n, pct=fl_n / fl_known * 100), None))
    if sl_known >= 20 and sl_n >= 5 and sl_n / sl_known >= 0.10:
        tips.append((1, t("tip_slowed", n=sl_n, pct=sl_n / sl_known * 100), None))
    if fm_known >= 30 and swing_n >= 3 and swing_n / fm_known >= 0.35:
        tips.append((1, t("tip_swing", n=swing_n, pct=swing_n / fm_known * 100), None))
    if not tips:
        tips.append((0, t("tip_few"), None))
    tips.sort(key=lambda item: -item[0])

    # severity chips: colour through the .aa-chip-* classes in
    # theme/overrides.css (token-driven) + a text label, so meaning
    # never rides on colour alone
    sev_style = {
        2: ("sev_high", "negative"),  # red    — bad / risky
        1: ("sev_med", "accent"),  # accent — warning
        0: ("sev_info", "info"),  # blue   — info / good
    }
    with st.container(border=True):
        st.markdown(f"**{t('top3_title')}**")
        st.caption(t("tips_caption"))
        for sev, tip_text, ev in tips[:3]:
            sev_key, sev_token = sev_style[sev]
            chip, text_col, ev_col = st.columns([1, 6, 2])
            chip.markdown(
                f"<span class='aa-chip aa-chip-{sev_token}'>● {t(sev_key)}</span>",
                unsafe_allow_html=True,
            )
            text_col.markdown(tip_text)
            with ev_col:
                if ev == "err":
                    with st.popover(t("top3_show")):
                        st.caption(t("top3_ev_err"))
                        ev_df = dd[dd["my_err"].notna()].nlargest(5, "my_err")
                        if len(ev_df):
                            st.dataframe(
                                pd.DataFrame(
                                    {
                                        t("col_round"): ev_df["round"].to_numpy(),
                                        t("col_opponent"): [
                                            name_of.get(s, s) for s in ev_df["opponent"]
                                        ],
                                        t("col_weapon"): ev_df["weapon"].to_numpy(),
                                        t("col_err"): ev_df["my_err"].to_numpy(),
                                        t("col_result"): [
                                            fmt_result(v) for v in ev_df["result"]
                                        ],
                                    }
                                ),
                                column_config={
                                    t("col_round"): st.column_config.NumberColumn(
                                        t("col_round"), format="%d"
                                    ),
                                    t("col_err"): st.column_config.NumberColumn(
                                        t("col_err"), format="%.1f°"
                                    ),
                                },
                                hide_index=True,
                                width="stretch",
                            )
                elif ev == "lost":
                    with st.popover(t("top3_show")):
                        st.caption(t("top3_ev_lost"))
                        ev_df = dth_df[dth_df["first"] == "opponent"]
                        if len(ev_df):
                            st.dataframe(
                                pd.DataFrame(
                                    {
                                        t("col_round"): ev_df["round"].to_numpy(),
                                        t("col_opponent"): [
                                            name_of.get(s, s) for s in ev_df["opponent"]
                                        ],
                                        t("col_weapon"): ev_df["weapon"].to_numpy(),
                                        t("col_err"): ev_df["my_err"].to_numpy(),
                                        t("col_duration"): ev_df[
                                            "duration_s"
                                        ].to_numpy(),
                                    }
                                ),
                                column_config={
                                    t("col_round"): st.column_config.NumberColumn(
                                        t("col_round"), format="%d"
                                    ),
                                    t("col_err"): st.column_config.NumberColumn(
                                        t("col_err"), format="%.1f°"
                                    ),
                                    t("col_duration"): st.column_config.NumberColumn(
                                        t("col_duration"), format="%.2f s"
                                    ),
                                },
                                hide_index=True,
                                width="stretch",
                            )
                elif ev is not None:  # "risk" / "speed": chart is below
                    st.caption(
                        t(
                            "top3_see_chart",
                            chart=(
                                t("risk_title") if ev == "risk" else t("speed_hist")
                            ),
                        )
                    )

    # one headline tile per theme — Accuracy, Movement (risky, the
    # distance-aware one), Crosshair, Duels; the rest are grouped under
    # section headers behind "All metrics"
    # (mirrors the tables' Basic/Full switch).
    acc_pct = hits_n / len(sh_me) * 100 if len(sh_me) else np.nan
    c = st.columns(4)
    c[0].metric(t("m_acc"), pct(acc_pct), help=t("m_acc_help"))
    c[1].metric(t("m_risky"), pct(risky_pct), help=t("m_risky_help"))
    c[2].metric(t("m_err"), num(err, 1), help=t("m_err_help"))
    c[3].metric(t("m_first"), pct(first_pct), help=t("m_first_help"))
    if st.toggle(t("all_metrics"), key="sh_all"):
        st.markdown(f"**{t('sec_accuracy')}**")
        c = st.columns(4)
        c[0].metric(t("m_shots"), len(sh_me), help=t("m_shots_help"))
        c[1].metric(t("m_hits"), hits_n, help=t("m_hits_help"))
        c[2].metric(t("m_hs"), pct(hs_hits), help=t("m_hs_help"))
        st.markdown(f"**{t('sec_movement')}**")
        c = st.columns(4)
        c[0].metric(t("m_moving"), pct(moving_pct), help=t("m_moving_help"))
        c[1].metric(t("m_speed"), num(spd, 0), help=t("m_speed_help"))
        c[2].metric(t("m_cs"), pct(cs_pct), help=t("m_cs_help"), delta=None)
        st.markdown(f"**{t('sec_crosshair')}**")
        c = st.columns(4)
        c[0].metric(t("m_onhead"), pct(onhead_pct), help=t("m_onhead_help"))
        st.markdown(f"**{t('sec_first')}**")
        c = st.columns(4)
        c[0].metric(
            t("m_first_acc"),
            pct(first_acc),
            help=t("m_first_acc_help"),
            delta=(
                f"{first_acc - follow_acc:+.0f} pp"
                if pd.notna(first_acc) and pd.notna(follow_acc)
                else None
            ),
        )
        c[1].metric(t("m_first_n"), first_n, help=t("m_first_n_help"))
        c[2].metric(t("m_swing_med"), num(swing_med, 0), help=t("m_swing_med_help"))
        st.markdown(f"**{t('sec_duels')}**")
        c = st.columns(4)
        c[0].metric(t("m_kd"), f"{kills} / {dths}", help=t("m_kd_help"))
        c[1].metric(t("m_dur"), num(dur, 2), help=t("m_dur_help"))
        st.markdown(f"**{t('sec_impact')}**")
        c = st.columns(4)
        c[0].metric(
            t("m_open"),
            (
                f"{int(imp_me['open_wins'])} / "
                f"{int(imp_me['open_attempts'] - imp_me['open_wins'])}"
                if imp_me is not None
                else t("none_val")
            ),
            help=t("m_open_help"),
        )
        c[1].metric(
            t("m_open_rate"),
            pct(imp_me["open_rate"]) if imp_me is not None else t("none_val"),
            help=t("m_open_rate_help"),
        )
        c[2].metric(
            t("m_trade_kills"),
            num(imp_me["trade_kills"], 0) if imp_me is not None else t("none_val"),
            help=t("m_trade_kills_help"),
        )
        c[3].metric(
            t("m_traded"),
            pct(imp_me["traded_pct"]) if imp_me is not None else t("none_val"),
            help=t("m_traded_help"),
        )
        c = st.columns(4)
        for _i, _mk in enumerate(("m_k2", "m_k3", "m_k4", "m_k5")):
            c[_i].metric(
                t(_mk),
                num(imp_me[_mk[2:]], 0) if imp_me is not None else t("none_val"),
                help=t("m_k2_help"),
            )
        c = st.columns(2)
        c[0].metric(
            t("m_assists"),
            num(imp_me["assists"], 0) if imp_me is not None else t("none_val"),
            help=t("m_assists_help"),
        )
        c[1].metric(
            t("m_flash"),
            num(imp_me["flash_assists"], 0) if imp_me is not None else t("none_val"),
            help=t("m_flash_help"),
        )

    if len(dd):
        k_err = dd.loc[dd["result"] == "kill", "my_err"].median()
        if pd.notna(k_err) and k_err > 25:
            st.warning(t("warn_angles"))

    st.divider()
    left, right = st.columns(2)
    with left:
        st.markdown(f"**{t('hit_where')}**")
        if hg_me.empty:
            st.info(t("hit_none"))
        else:
            idx = [k for k in HG_ORDER if k in hg_me.index]
            idx += [k for k in hg_me.index if k not in HG_ORDER]
            # your hits in the accent colour; style_figure keeps both
            # small charts at the same height, axis style and hover format
            fig = go.Figure(
                go.Bar(
                    x=[t(f"hg_{k}") for k in idx],
                    y=hg_me.reindex(idx).values,
                    marker_color=PAL["you"],
                )
            )
            style_figure(
                fig,
                height=260,
                y_title=t("col_hits"),
                hover="%{x}: %{y}<extra></extra>",
            )
            st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown(f"**{t('speed_hist')}**")
        sv = sh_me["speed"].dropna() if len(sh_me) else pd.Series(dtype=float)
        if len(sv):
            # your speeds in the accent colour; the moving threshold
            # is a grey context line — the danger boundary is the
            # vermillion curve on the risk chart below, not this line
            fig = go.Figure(go.Histogram(x=sv, nbinsx=40, marker_color=PAL["you"]))
            fig.add_vline(x=thr, line_dash="dot", line_color=PAL["ctx"])
            style_figure(fig, height=260, x_title=t("speed_x"), y_title=t("speed_y"))
            st.plotly_chart(fig, width="stretch")

    st.markdown(f"**{t('risk_title')}**")
    known_r = sh_me[sh_me["dist"].notna() & sh_me["speed"].notna()]
    if not len(known_r):
        st.info(t("risk_none"))
    else:
        # grey = misses (context), blue = hits (good), vermillion = the
        # "too fast for this distance" boundary; a plain red/green pair
        # would be the classic colour-blind trap, and every series keeps
        # its own label
        fig = go.Figure()
        miss = known_r[~known_r["hit"].astype(bool)]
        hit = known_r[known_r["hit"].astype(bool)]
        fig.add_trace(
            go.Scattergl(
                x=miss["dist"],
                y=miss["speed"],
                mode="markers",
                name=t("lbl_miss"),
                opacity=0.45,
                marker=dict(size=5, color=PAL["ctx"]),
            )
        )
        fig.add_trace(
            go.Scattergl(
                x=hit["dist"],
                y=hit["speed"],
                mode="markers",
                name=t("lbl_hit"),
                opacity=0.8,
                marker=dict(size=5, color=PAL["good"]),
            )
        )
        dmax = float(known_r["dist"].max())
        xs = np.linspace(0, max(strict_dist * 1.3, dmax), 240)
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=core.allowed_speed(xs, thr, safe_dist, strict_dist),
                mode="lines",
                name=t("risk_curve"),
                line=dict(color=PAL["bad"], dash="dash"),
            )
        )
        style_figure(
            fig,
            height=380,
            x_title=t("risk_x"),
            y_title=t("risk_y"),
            hover="%{x:.0f} u<br>%{y:.0f} u/s<extra>%{fullData.name}</extra>",
        )
        st.plotly_chart(fig, width="stretch")

    st.divider()
    st.markdown(f"**{t('weapons')}**")
    w = core.weapon_stats(demo, shots_f, me, thr, safe_dist, strict_dist, hurts=hurts_f)
    if len(w):
        # same table conventions as the other tables - units in the
        # numbers, progress bars for percentages, weapon name pinned.
        schema.table(
            "weapons",
            {c: w[c].to_numpy() for c in schema.columns("weapons")},
            key="w_grid",
        )
