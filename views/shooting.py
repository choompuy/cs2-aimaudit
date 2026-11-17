"""The Shooting page: accuracy, hit groups, first shots, weapons."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dataclasses import dataclass

import core
from ui import icons
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




@dataclass
class _Stats:
    """Every number the Shooting page shows, computed once from ctx."""

    sh_me: object
    hg_me: object
    dd: object
    dth_df: object
    dd_err: object
    stops: object
    fs: object
    fs_me: object
    fu_me: object
    fm: object
    kills: int
    dths: int
    hits_n: int
    hs_hits: float
    moving_pct: float
    risky_pct: float
    first_pct: float
    dur: float
    err: float
    spd: float
    onhead_pct: float
    cs_pct: float
    acc_pct: float
    fl_known: int
    fl_n: int
    sl_known: int
    sl_n: int
    first_n: int
    first_acc: float
    follow_acc: float
    swing_med: float
    swing_n: int
    fm_known: int
    imp_me: object
    thr: float
    safe_dist: int
    strict_dist: int


def _duel_stats(dd, impact, me):
    """Duel / round-impact numbers: K/D, duration, aim error, on-head
    share, counter-strafe quality, first-shot share, the impact row."""
    kills = int((dd["result"] == "kill").sum()) if len(dd) else 0
    dths = int((dd["result"] == "death").sum()) if len(dd) else 0
    fs = dd[dd["first"].isin(["me", "opponent"])] if len(dd) else dd
    imp_me = impact[impact["sid"] == me]
    imp_me = imp_me.iloc[0] if len(imp_me) else None
    first_pct = (fs["first"] == "me").mean() * 100 if len(fs) else np.nan
    dur = dd["duration_s"].median() if len(dd) else np.nan
    err = dd["my_err"].median() if len(dd) else np.nan
    spd = dd["my_speed"].median() if len(dd) else np.nan
    dd_err = dd[dd["my_err"].notna()] if len(dd) else dd
    onhead_pct = dd_err["my_would_head"].mean() * 100 if len(dd_err) else np.nan
    return kills, dths, fs, imp_me, first_pct, dur, err, spd, dd_err, onhead_pct


def _discipline_stats(sh_me, thr):
    """Shot discipline (firing while flashed / while damage-slowed) and
    first-shot discipline (accuracy on the first shot of an engagement,
    the pre-fire mouse movement). NA (a prop unknown on old demos) never
    counts as False - a verdict is only issued when the data exists."""
    moving_pct = (sh_me["speed"] > thr).mean() * 100 if len(sh_me) else np.nan
    known = (
        sh_me["dist"].notna() & sh_me["speed"].notna()
        if len(sh_me)
        else pd.Series(dtype=bool)
    )
    risky_pct = sh_me.loc[known, "risky"].mean() * 100 if known.any() else np.nan
    stops = sh_me[sh_me["stop"] == "stopped"] if len(sh_me) else sh_me
    cs_pct = stops["counter_strafe"].mean() * 100 if len(stops) else np.nan

    fl_me = sh_me["flashed"]
    sl_me = sh_me["slowed"]
    fl_known, sl_known = int(fl_me.notna().sum()), int(sl_me.notna().sum())
    fl_n = int(fl_me.fillna(False).sum())
    sl_n = int(sl_me.fillna(False).sum())

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
    return (
        moving_pct, risky_pct, stops, cs_pct, fl_known, fl_n, sl_known, sl_n,
        fs_me, fu_me, first_n, first_acc, follow_acc, fm, fm_known, swing_med,
        swing_n,
    )


def _stats(ctx) -> _Stats:
    """Compute the page's numbers once from ctx (hit stats, duel stats,
    shot discipline, first-shot discipline, the round-impact row)."""
    demo = ctx.demo
    me = ctx.me
    thr = ctx.thr
    shots_f = ctx.shots_f

    sh_me = shots_f[shots_f["sid"] == me]
    hg_me = core.hitgroup_counts(demo, me, ctx.hurts_f)
    dd = ctx.my_duels
    hits_n = int(hg_me.sum()) if len(hg_me) else 0
    hs_hits = hg_me.get("head", 0) / hits_n * 100 if hits_n else np.nan
    (kills, dths, fs, imp_me, first_pct, dur, err, spd, dd_err,
     onhead_pct) = _duel_stats(dd, ctx.impact, me)
    (moving_pct, risky_pct, stops, cs_pct, fl_known, fl_n, sl_known, sl_n,
     fs_me, fu_me, first_n, first_acc, follow_acc, fm, fm_known, swing_med,
     swing_n) = _discipline_stats(sh_me, thr)
    acc_pct = hits_n / len(sh_me) * 100 if len(sh_me) else np.nan
    dth_df = dd[dd["result"] == "death"] if len(dd) else dd

    return _Stats(
        sh_me=sh_me, hg_me=hg_me, dd=dd, dth_df=dth_df, dd_err=dd_err,
        stops=stops, fs=fs, fs_me=fs_me, fu_me=fu_me, fm=fm, kills=kills,
        dths=dths, hits_n=hits_n, hs_hits=hs_hits, moving_pct=moving_pct,
        risky_pct=risky_pct, first_pct=first_pct, dur=dur, err=err, spd=spd,
        onhead_pct=onhead_pct, cs_pct=cs_pct, acc_pct=acc_pct,
        fl_known=fl_known, fl_n=fl_n, sl_known=sl_known, sl_n=sl_n,
        first_n=first_n, first_acc=first_acc, follow_acc=follow_acc,
        swing_med=swing_med, swing_n=swing_n, fm_known=fm_known,
        imp_me=imp_me, thr=thr, safe_dist=ctx.safe_dist,
        strict_dist=ctx.strict_dist,
    )


def _movement_tips(t, s) -> list:
    """The movement verdict (risky shots / moving shots) and the aim-error
    one; the engine emits at most one movement item."""
    tips = []
    if len(s.sh_me) >= 10:
        if pd.notna(s.risky_pct) and s.risky_pct > 35:
            tips.append((2, t("tip_risky", pct=s.risky_pct), "risk"))
        elif pd.notna(s.moving_pct) and s.moving_pct > 35:
            tips.append((1, t("tip_moving", pct=s.moving_pct), "speed"))
        else:
            tips.append(
                (
                    0,
                    t("tip_moving_ok", pct=s.moving_pct if pd.notna(s.moving_pct) else 0),
                    None,
                )
            )
    if len(s.dd) >= 5 and pd.notna(s.err) and s.err > 5:
        tips.append((1, t("tip_err", err=s.err), "err"))
    return tips


def _discipline_tips(t, s) -> list:
    """The discipline verdicts (lost first shots, opening duels, firing
    flashed / slowed, mid-swing first shots) - appended AFTER the
    established verdicts, so at equal severity the aim / first-shot
    coaching keeps its top-3 place."""
    tips = []
    if len(s.dth_df) >= 3:
        n_lost = int((s.dth_df["first"] == "opponent").sum())
        if n_lost and n_lost / len(s.dth_df) >= 0.5:
            tips.append((1, t("tip_lost_first", n=n_lost, m=len(s.dth_df)), "lost"))
    # opening-duel verdict: only when the player takes openings
    # often enough (>= 5) and keeps losing them (<= 30% won)
    if s.imp_me is not None and s.imp_me["open_attempts"] >= 5:
        _o_rate = s.imp_me["open_rate"]
        if pd.notna(_o_rate) and _o_rate <= 30.0:
            tips.append(
                (
                    1,
                    t(
                        "tip_openings",
                        won=int(s.imp_me["open_wins"]),
                        n=int(s.imp_me["open_attempts"]),
                    ),
                    None,
                )
            )
    if s.fl_known >= 20 and s.fl_n >= 3 and s.fl_n / s.fl_known >= 0.08:
        tips.append((1, t("tip_flashed", n=s.fl_n, pct=s.fl_n / s.fl_known * 100), None))
    if s.sl_known >= 20 and s.sl_n >= 5 and s.sl_n / s.sl_known >= 0.10:
        tips.append((1, t("tip_slowed", n=s.sl_n, pct=s.sl_n / s.sl_known * 100), None))
    if s.fm_known >= 30 and s.swing_n >= 3 and s.swing_n / s.fm_known >= 0.35:
        tips.append((1, t("tip_swing", n=s.swing_n, pct=s.swing_n / s.fm_known * 100), None))
    return tips


def _build_tips(t, s) -> list:
    """The coaching engine: at most three items - one movement verdict,
    the aim-error one, the first-shot one - each (severity, sentence with
    its number, evidence) where evidence is a popover key, a chart key or
    None; severity 2 HIGH / 1 MED / 0 INFO."""
    tips = _movement_tips(t, s) + _discipline_tips(t, s)
    if not tips:
        tips.append((0, t("tip_few"), None))
    tips.sort(key=lambda item: -item[0])
    return tips


def _top3_card(t, s, tips, name_of) -> None:
    """The "Top 3 things to work on" card: a severity chip (colour
    through the .aa-chip-* classes in theme/overrides.css, token-driven)
    + a text label, the sentence with its number, and the evidence behind
    a "show me" popover or a pointer to the chart below.

    72 lines, under the 80 cap: the per-tip chip/text/evidence row is one
    visual unit - splitting the popover arms from the chip row would
    separate the layout from the data it renders."""
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
                    ev_df = s.dd[s.dd["my_err"].notna()].nlargest(5, "my_err")
                    _events_popover(
                        t,
                        t("top3_ev_err"),
                        ev_df,
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
                        },
                        {t("col_round"): "%d", t("col_err"): "%.1f°"},
                    )
                elif ev == "lost":
                    ev_df = s.dth_df[s.dth_df["first"] == "opponent"]
                    _events_popover(
                        t,
                        t("top3_ev_lost"),
                        ev_df,
                        {
                            t("col_round"): ev_df["round"].to_numpy(),
                            t("col_opponent"): [
                                name_of.get(s, s) for s in ev_df["opponent"]
                            ],
                            t("col_weapon"): ev_df["weapon"].to_numpy(),
                            t("col_err"): ev_df["my_err"].to_numpy(),
                            t("col_duration"): ev_df["duration_s"].to_numpy(),
                        },
                        {
                            t("col_round"): "%d",
                            t("col_err"): "%.1f°",
                            t("col_duration"): "%.2f s",
                        },
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


def _metric_tiles(t, s) -> None:
    """One headline tile per theme - Accuracy, Movement (risky, the
    distance-aware one), Crosshair, Duels; the rest are grouped under
    section headers behind "All metrics" (mirrors the tables' Basic/Full
    switch)."""

    c = st.columns(4)
    c[0].metric(t("m_acc"), pct(s.acc_pct), help=t("m_acc_help"))
    c[1].metric(t("m_risky"), pct(s.risky_pct), help=t("m_risky_help"))
    c[2].metric(t("m_err"), num(s.err, 1), help=t("m_err_help"))
    c[3].metric(t("m_first"), pct(s.first_pct), help=t("m_first_help"))


    if st.toggle(t("all_metrics"), key="sh_all", persist_state="session"):
        _all_metrics_tiles(t, s)


def _all_metrics_tiles(t, s) -> None:
    """The full tile list behind "All metrics", grouped by section header
    (kept whole under the 80-line cap: a flat table of metric cells)."""
    st.markdown(f"**{t('sec_accuracy')}**")
    c = st.columns(4)
    c[0].metric(t("m_shots"), len(s.sh_me), help=t("m_shots_help"))
    c[1].metric(t("m_hits"), s.hits_n, help=t("m_hits_help"))
    c[2].metric(t("m_hs"), pct(s.hs_hits), help=t("m_hs_help"))
    st.markdown(f"**{t('sec_movement')}**")
    c = st.columns(4)
    c[0].metric(t("m_moving"), pct(s.moving_pct), help=t("m_moving_help"))
    c[1].metric(t("m_speed"), num(s.spd, 0), help=t("m_speed_help"))
    c[2].metric(t("m_cs"), pct(s.cs_pct), help=t("m_cs_help"), delta=None)
    st.markdown(f"**{t('sec_crosshair')}**")
    c = st.columns(4)
    c[0].metric(t("m_onhead"), pct(s.onhead_pct), help=t("m_onhead_help"))
    st.markdown(f"**{t('sec_first')}**")
    c = st.columns(4)
    c[0].metric(
        t("m_first_acc"),
        pct(s.first_acc),
        help=t("m_first_acc_help"),
        delta=(
            f"{s.first_acc - s.follow_acc:+.0f} pp"
            if pd.notna(s.first_acc) and pd.notna(s.follow_acc)
            else None
        ),
    )
    c[1].metric(t("m_first_n"), s.first_n, help=t("m_first_n_help"))
    c[2].metric(t("m_swing_med"), num(s.swing_med, 0), help=t("m_swing_med_help"))
    st.markdown(f"**{t('sec_duels')}**")
    c = st.columns(4)
    c[0].metric(t("m_kd"), f"{s.kills} / {s.dths}", help=t("m_kd_help"))
    c[1].metric(t("m_dur"), num(s.dur, 2), help=t("m_dur_help"))
    st.markdown(f"**{t('sec_impact')}**")
    c = st.columns(4)
    c[0].metric(
        t("m_open"),
        (
            f"{int(s.imp_me['open_wins'])} / "
            f"{int(s.imp_me['open_attempts'] - s.imp_me['open_wins'])}"
            if s.imp_me is not None
            else t("none_val")
        ),
        help=t("m_open_help"),
    )
    c[1].metric(
        t("m_open_rate"),
        pct(s.imp_me["open_rate"]) if s.imp_me is not None else t("none_val"),
        help=t("m_open_rate_help"),
    )
    c[2].metric(
        t("m_trade_kills"),
        num(s.imp_me["trade_kills"], 0) if s.imp_me is not None else t("none_val"),
        help=t("m_trade_kills_help"),
    )
    c[3].metric(
        t("m_traded"),
        pct(s.imp_me["traded_pct"]) if s.imp_me is not None else t("none_val"),
        help=t("m_traded_help"),
    )
    c = st.columns(4)
    for _i, _mk in enumerate(("m_k2", "m_k3", "m_k4", "m_k5")):
        c[_i].metric(
            t(_mk),
            num(s.imp_me[_mk[2:]], 0) if s.imp_me is not None else t("none_val"),
            help=t("m_k2_help"),
        )
    c = st.columns(2)
    c[0].metric(
        t("m_assists"),
        num(s.imp_me["assists"], 0) if s.imp_me is not None else t("none_val"),
        help=t("m_assists_help"),
    )
    c[1].metric(
        t("m_flash"),
        num(s.imp_me["flash_assists"], 0) if s.imp_me is not None else t("none_val"),
        help=t("m_flash_help"),
    )



def _charts(t, s) -> None:
    """The angle warning, hit placement + speed histogram (one row,
    style_figure keeps both small charts at the same height), then the
    risk chart."""

    if len(s.dd):
        k_err = s.dd.loc[s.dd["result"] == "kill", "my_err"].median()
        if pd.notna(k_err) and k_err > 25:
            st.warning(t("warn_angles"))


    st.divider()
    left, right = st.columns(2)
    with left:
        st.markdown(f"**{t('hit_where')}**")
        if s.hg_me.empty:
            st.info(t("hit_none"))
        else:
            idx = [k for k in HG_ORDER if k in s.hg_me.index]
            idx += [k for k in s.hg_me.index if k not in HG_ORDER]
            # your hits in the accent colour; style_figure keeps both
            # small charts at the same height, axis style and hover format
            fig = go.Figure(
                go.Bar(
                    x=[t(f"hg_{k}") for k in idx],
                    y=s.hg_me.reindex(idx).values,
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
        sv = s.sh_me["speed"].dropna() if len(s.sh_me) else pd.Series(dtype=float)
        if len(sv):
            # your speeds in the accent colour; the moving threshold
            # is a grey context line — the danger boundary is the
            # vermillion curve on the risk chart below, not this line
            fig = go.Figure(go.Histogram(x=sv, nbinsx=40, marker_color=PAL["you"]))
            fig.add_vline(x=s.thr, line_dash="dot", line_color=PAL["ctx"])
            style_figure(fig, height=260, x_title=t("speed_x"), y_title=t("speed_y"))
            st.plotly_chart(fig, width="stretch")

    _risk_chart(t, s)


def _risk_chart(t, s) -> None:
    """The "too fast for this distance" scatter: grey = misses (context),
    blue = hits (good), vermillion = the boundary; a plain red/green pair
    would be the classic colour-blind trap, and every series keeps its
    own label."""

    st.markdown(f"**{t('risk_title')}**")
    known_r = s.sh_me[s.sh_me["dist"].notna() & s.sh_me["speed"].notna()]
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
        xs = np.linspace(0, max(s.strict_dist * 1.3, dmax), 240)
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=core.allowed_speed(xs, s.thr, s.safe_dist, s.strict_dist),
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


def _weapons_grid(ctx) -> None:
    """The per-weapon table: same conventions as the other tables - units
    in the numbers, progress bars for percentages, the weapon name pinned;
    the name itself is the silhouette (tooltip carries the display name)."""
    t = ctx.t

    st.divider()
    st.markdown(f"**{t('weapons')}**")
    w = core.weapon_stats(
        ctx.demo, ctx.shots_f, ctx.me, ctx.thr, ctx.safe_dist, ctx.strict_dist,
        hurts=ctx.hurts_f,
    )
    if len(w):
        # same table conventions as the other tables - units in the
        # numbers, progress bars for percentages, weapon name pinned; the
        # name itself is the silhouette (tooltip carries the display name)
        w_vals, w_labels, w_icons = icons.weapon_cells(w["weapon"])
        cells = {c: w[c].to_numpy() for c in schema.columns("weapons")}
        schema.table(
            "weapons",
            {**cells, "weapon": w_vals, "weapon_disp": w_labels},
            key="w_grid",
            icons=w_icons,
        )


def render(ctx) -> None:
    """Draw the Shooting page: stats, the coaching card, the metric
    tiles, the charts, the weapons grid."""
    t = ctx.t
    s = _stats(ctx)
    tips = _build_tips(t, s)
    _top3_card(t, s, tips, ctx.name_of)
    _metric_tiles(t, s)
    _charts(t, s)
    _weapons_grid(ctx)



def _events_popover(t, caption: str, ev_df, cols: dict, numfmt: dict) -> None:
    """The Top-3 evidence popover: a small localized events table behind
    the "Show" button (started as the err/lost near-duplicate). Empty
    evidence draws only the caption."""
    with st.popover(t("top3_show")):
        st.caption(caption)
        if len(ev_df):
            st.dataframe(
                pd.DataFrame(cols),
                column_config={
                    k: st.column_config.NumberColumn(k, format=v)
                    for k, v in numfmt.items()
                },
                hide_index=True,
                width="stretch",
            )
