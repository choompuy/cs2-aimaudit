"""The Shooting page: the coaching card, graded metric tiles, hit placement, speed at
every shot, the speed-vs-distance risk chart, weapons."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dataclasses import dataclass

import core
from ui import icons, meters
from ui import schema
from ui.charts import PAL, style_figure
from ui.format import num, pct

HG_ORDER = [
    "head", "neck", "chest", "stomach", "left_arm", "right_arm", "left_leg",
    "right_leg", "other",
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
    """Firing while flashed / slowed and the first shots of engagements. NA never counts as
    False."""
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
    # pre-fire movement >= 2x the player's own median (self-calibrates for sensitivity)
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


def _only(df, weapon):
    """The rows of one weapon (all of them without a focus)."""
    if weapon is None or df is None or not len(df) or "weapon" not in df.columns:
        return df
    return df[df["weapon"] == weapon]


def _stats(ctx, weapon=None) -> _Stats:
    """Compute the page's numbers once from ctx (hit stats, duel stats,
    shot discipline, first-shot discipline, the round-impact row); ``weapon``
    narrows the shots, hits and duels to that gun (round impact has no weapon:
    it is left out then)."""
    demo = ctx.demo
    me = ctx.me
    thr = ctx.thr
    shots_f = _only(ctx.shots_f, weapon)

    sh_me = shots_f[shots_f["sid"] == me]
    hurts = _only(ctx.hurts_f if ctx.hurts_f is not None else demo.hurts, weapon)
    hg_me = core.hitgroup_counts(demo, me, hurts)
    dd = _only(ctx.my_duels, weapon)
    hits_n = int(sh_me["hit"].sum()) if len(sh_me) else 0
    hs_hits = hg_me.get("head", 0) / hg_me.sum() * 100 if len(hg_me) else np.nan
    impact = ctx.impact if weapon is None else ctx.impact.iloc[0:0]
    (kills, dths, fs, imp_me, first_pct, dur, err, spd, dd_err,
     onhead_pct) = _duel_stats(dd, impact, me)
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
    """The discipline verdicts, after the established ones at equal severity."""
    tips = []
    if len(s.dth_df) >= 3:
        n_lost = int((s.dth_df["first"] == "opponent").sum())
        if n_lost and n_lost / len(s.dth_df) >= 0.5:
            tips.append((1, t("tip_lost_first", n=n_lost, m=len(s.dth_df)), "lost"))
    # only when the player takes openings (>= 5) and loses them (<= 30% won)
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
    """[(severity 2 HIGH / 1 MED / 0 INFO, sentence, evidence key or None)]."""
    tips = _movement_tips(t, s) + _discipline_tips(t, s)
    if not tips:
        tips.append((0, t("tip_few"), None))
    tips.sort(key=lambda item: -item[0])
    return tips


def _top3_card(t, tips) -> None:
    """The "Top 3 things to work on" card: severity badge + the sentence, one line each
    (the duels behind a tip are on the Duels page)."""
    sev_color = {
        2: ("sev_high", "red"),  # red    — bad / risky
        1: ("sev_med", "orange"),  # orange — warning
        0: ("sev_info", "blue"),  # blue   — info / good
    }
    with st.container(border=True, gap="small"):
        st.markdown(f"**{t('top3_title')}**", help=t("tips_caption"))
        for sev, tip_text, _ev in tips[:3]:
            sev_key, sev_badge = sev_color[sev]
            st.markdown(f":{sev_badge}-badge[{t(sev_key)}] {tip_text}")


# How each tile is judged: grade (bad, ok, good) and the bar's scale. Rough club-level
# norms for matchmaking / FACEIT, not pro benchmarks: the colour says "worth a look",
# the tick on the bar is the "ok" line.
_SPEC = {
    "acc": meters.Spec("high", (10, 18, 28), (0, 50)),
    "risky": meters.Spec("low", (30, 15, 5), (0, 60)),
    "err": meters.Spec("low", (6, 3, 1.5), (0, 10), "°"),
    "first": meters.Spec("high", (35, 50, 65)),
    "hs": meters.Spec("high", (15, 25, 40), (0, 80)),
    "moving": meters.Spec("low", (50, 30, 15)),
    "speed": meters.Spec("low", (100, 40, 15), (0, 250), " u/s"),
    "cs": meters.Spec("high", (30, 50, 70)),
    "onhead": meters.Spec("high", (15, 30, 45)),
    "first_acc": meters.Spec("high", (15, 25, 40), (0, 80)),
    "kd": meters.Spec("high", (0.7, 1.0, 1.4), (0, 2.5), ""),
    "open_rate": meters.Spec("high", (35, 50, 65)),
    "traded": meters.Spec("high", (15, 30, 50)),
}


def _tile(t, label_key, help_key, text, value=None, spec=None, sub="") -> dict:
    norm = ""
    if spec is not None and spec.grade is not None:
        norm = t("m_norm_low" if spec.best == "low" else "m_norm_high")
    helps = help_key if isinstance(help_key, tuple) else (help_key,)
    return meters.tile(
        t(label_key), text, value, spec, help="\n\n".join(t(h) for h in helps), sub=sub,
        norm_label=norm,
    )


def _metric_tiles(t, s) -> None:
    """One headline tile per theme; the rest behind "All metrics"."""
    meters.meters(
        [
            _tile(t, "m_acc", "m_acc_help", pct(s.acc_pct), s.acc_pct, _SPEC["acc"]),
            _tile(t, "m_risky", "m_risky_help", pct(s.risky_pct), s.risky_pct, _SPEC["risky"]),
            _tile(t, "m_err", "m_err_help", num(s.err, 1), s.err, _SPEC["err"]),
            _tile(t, "m_first", "m_first_help", pct(s.first_pct), s.first_pct, _SPEC["first"]),
        ],
        key="sh_m_head",
    )
    if st.toggle(t("all_metrics"), key="sh_all", persist_state="session"):
        _all_metrics_tiles(t, s)


def _imp(t, s, field, fmt=num):
    """An impact-row value formatted, "—" without the row."""
    if s.imp_me is None:
        return t("none_val"), None
    v = s.imp_me[field]
    return (fmt(v) if fmt is pct else fmt(v, 0)), v


def _group(t, label_key, help_keys, parts, span=2) -> dict:
    """One tile for several related counts: a row of small value / label cells."""
    return meters.group(
        t(label_key),
        [(t(k), v) for k, v in parts],
        help="\n\n".join(t(h) for h in help_keys),
        span=span,
    )


def _accuracy_movement_rows(t, s) -> dict:
    return {
        "sec_accuracy": [
            _tile(t, "m_hits", "m_hits_help", str(s.hits_n),
                  sub=t("m_of_shots", n=len(s.sh_me))),
            _tile(t, "m_hs", "m_hs_help", pct(s.hs_hits), s.hs_hits, _SPEC["hs"]),
            _tile(t, "m_onhead", "m_onhead_help", pct(s.onhead_pct), s.onhead_pct,
                  _SPEC["onhead"]),
        ],
        "sec_movement": [
            _tile(t, "m_moving", "m_moving_help", pct(s.moving_pct), s.moving_pct,
                  _SPEC["moving"]),
            _tile(t, "m_speed", "m_speed_help", num(s.spd, 0), s.spd, _SPEC["speed"]),
            _tile(t, "m_cs", "m_cs_help", pct(s.cs_pct), s.cs_pct, _SPEC["cs"]),
        ],
        "sec_first": [
            _tile(t, "m_first_acc", "m_first_acc_help", pct(s.first_acc), s.first_acc,
                  _SPEC["first_acc"],
                  sub=(f"{s.first_acc - s.follow_acc:+.0f} pp"
                       if pd.notna(s.first_acc) and pd.notna(s.follow_acc) else "")),
            _tile(t, "m_first_n", "m_first_n_help", str(s.first_n)),
            _tile(t, "m_swing_med", "m_swing_med_help", num(s.swing_med, 0)),
        ],
    }


def _duel_impact_rows(t, s) -> dict:
    kd = s.kills / s.dths if s.dths else (float(s.kills) if s.kills else None)
    open_wl = (
        f"{int(s.imp_me['open_wins'])} / "
        f"{int(s.imp_me['open_attempts'] - s.imp_me['open_wins'])}"
        if s.imp_me is not None
        else t("none_val")
    )
    rate_txt, rate = _imp(t, s, "open_rate", pct)
    duels = [
        _tile(t, "m_kd", "m_kd_help", f"{s.kills} / {s.dths}", kd, _SPEC["kd"]),
        # the W / L count, the bar and the colour read the winrate
        _tile(t, "m_open", ("m_open_help", "m_open_rate_help"), open_wl, rate,
              _SPEC["open_rate"],
              sub=f"{t('m_open_rate')}: {rate_txt}"),
        _tile(t, "m_dur", "m_dur_help", num(s.dur, 2)),
    ]
    if s.imp_me is None:
        # one weapon in focus: openings, multi-kills, trades are per round, not per gun
        return {"sec_duels": [duels[0], duels[2]]}
    return {
        "sec_duels": duels,
        "sec_impact": [
            _group(t, "m_multi", ["m_k2_help"], [
                ("m_k2", _imp(t, s, "k2")[0]),
                ("m_k3", _imp(t, s, "k3")[0]),
                ("m_k4", _imp(t, s, "k4")[0]),
                ("m_k5", _imp(t, s, "k5")[0]),
            ]),
            _group(t, "m_trades", ["m_trade_kills_help", "m_traded_help"], [
                ("m_trade_kills", _imp(t, s, "trade_kills")[0]),
                ("m_traded", _imp(t, s, "traded_pct", pct)[0]),
            ], span=1),
            _group(t, "m_assists", ["m_assists_help", "m_flash_help"], [
                ("m_assists", _imp(t, s, "assists")[0]),
                ("m_flash", _imp(t, s, "flash_assists")[0]),
            ], span=1),
        ],
    }


def _all_metrics_tiles(t, s) -> None:
    """The full tile list behind "All metrics", one titled row per theme; related counts
    (multi-kills, trades, assists) share one tile."""
    rows = {**_accuracy_movement_rows(t, s), **_duel_impact_rows(t, s)}
    for sec, tiles in rows.items():
        meters.meters(tiles, key=f"sh_m_{sec}", title=t(sec))


def _angles_warning(t, s) -> None:
    """Kills land with a wide aim error: most of them are close-range sprays."""
    if len(s.dd):
        k_err = s.dd.loc[s.dd["result"] == "kill", "my_err"].median()
        if pd.notna(k_err) and k_err > 25:
            st.warning(t("warn_angles"))


def _hits_chart(t, s) -> None:
    """Where the hits land: head in the "good" colour, the share on every bar."""
    st.markdown(f"**{t('hit_where')}**")
    if s.hg_me.empty:
        st.info(t("hit_none"))
        return
    idx = [k for k in HG_ORDER if k in s.hg_me.index]
    idx += [k for k in s.hg_me.index if k not in HG_ORDER]
    vals = s.hg_me.reindex(idx).fillna(0).to_numpy()
    total = float(vals.sum()) or 1.0
    fig = go.Figure(
        go.Bar(
            x=[t(f"hg_{k}") for k in idx],
            y=vals,
            text=[f"{v / total * 100:.0f}%" for v in vals],
            textposition="outside",
            cliponaxis=False,
            marker_color=[PAL["good"] if k == "head" else PAL["ctx"] for k in idx],
        )
    )
    style_figure(
        fig, height=260, y_title=t("col_hits"), hover="%{x}: %{y}<extra></extra>",
        margin=dict(t=24, r=10),
    )
    st.plotly_chart(fig, width="stretch")


def _speed_chart(t, s) -> None:
    """Speed at every shot, split at the movement threshold: stopped vs on the move,
    each share in the legend."""
    st.markdown(f"**{t('speed_hist')}**")
    sv = s.sh_me["speed"].dropna() if len(s.sh_me) else pd.Series(dtype=float)
    if not len(sv):
        st.info(t("risk_none"))
        return
    still, moving = sv[sv <= s.thr], sv[sv > s.thr]
    bins = dict(start=0, end=float(max(sv.max(), s.thr)) + 10, size=10)
    fig = go.Figure()
    for part, name, color in (
        (still, t("speed_still", thr=s.thr, pct=len(still) / len(sv) * 100), PAL["good"]),
        (moving, t("speed_moving", pct=len(moving) / len(sv) * 100), PAL["bad"]),
    ):
        fig.add_trace(go.Histogram(x=part, xbins=bins, name=name, marker_color=color))
    fig.add_vline(x=s.thr, line_dash="dot", line_color=PAL["ctx"])
    fig.update_layout(barmode="stack", bargap=0.05)
    style_figure(fig, height=260, x_title=t("speed_x"), y_title=t("speed_y"))
    st.plotly_chart(fig, width="stretch")


def _risk_chart(t, s) -> None:
    """The "too fast for this distance" scatter: grey misses, blue hits, vermillion
    boundary (colour-blind-safe)."""
    st.markdown(f"**{t('risk_title')}**")
    known_r = s.sh_me[s.sh_me["dist"].notna() & s.sh_me["speed"].notna()]
    if not len(known_r):
        st.info(t("risk_none"))
        return
    fig = go.Figure()
    hit_mask = known_r["hit"].astype(bool)
    for part, name, opacity, color in (
        (known_r[~hit_mask], t("lbl_miss"), 0.45, PAL["ctx"]),
        (known_r[hit_mask], t("lbl_hit"), 0.8, PAL["good"]),
    ):
        fig.add_trace(
            go.Scattergl(
                x=part["dist"], y=part["speed"], mode="markers", name=name,
                opacity=opacity, marker=dict(size=5, color=color),
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
        height=340,
        x_title=t("risk_x"),
        y_title=t("risk_y"),
        hover="%{x:.0f} u<br>%{y:.0f} u/s<extra>%{fullData.name}</extra>",
    )
    st.plotly_chart(fig, width="stretch")


def _charts(t, s) -> None:
    """Hit placement and the speed split side by side, the risk scatter under them."""
    left, right = st.columns(2)
    with left:
        _hits_chart(t, s)
    with right:
        _speed_chart(t, s)
    _risk_chart(t, s)


_FOCUS = "sh_weapon"  # the weapon the page is narrowed to (this page only)


def _weapon_table(ctx):
    return core.weapon_stats(
        ctx.demo, ctx.shots_f, ctx.me, ctx.thr, ctx.safe_dist, ctx.strict_dist,
        hurts=ctx.hurts_f,
    )


def _weapons_grid(ctx, w, focus) -> None:
    """The per-weapon table: a row click narrows the page to that weapon, a second
    click (or "All weapons") widens it back."""
    t = ctx.t
    names = w["weapon"].astype(str).tolist()

    def _pick(i):
        if 0 <= i < len(names):
            st.session_state[_FOCUS] = None if focus == names[i] else names[i]

    w_vals, w_labels, w_icons = icons.weapon_cells(w["weapon"])
    cells = {c: w[c].to_numpy() for c in schema.columns("weapons")}
    schema.table(
        "weapons",
        {**cells, "weapon": w_vals, "weapon_disp": w_labels},
        key="w_grid",
        icons=w_icons,
        selected=names.index(focus) if focus in names else None,
        on_click=_pick,
    )


def _focus_bar(t, focus) -> None:
    """What the numbers below describe: every weapon or the picked one."""
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        if focus is None:
            st.markdown(f"**{t('sh_scope_all')}**")
            st.caption(t("sh_scope_hint"))
        else:
            st.markdown(f"**{t('sh_scope_one', w=icons.weapon_label(focus))}**")
            st.button(
                t("sh_scope_clear"), key="sh_weapon_clear", icon=":material/close:",
                type="tertiary", on_click=lambda: st.session_state.update({_FOCUS: None}),
            )


def render(ctx) -> None:
    """Draw the Shooting page: the coaching card, the weapons (a row narrows the
    page to that gun), the graded metric tiles and the shooting charts."""
    t = ctx.t
    _top3_card(t, _build_tips(t, _stats(ctx)))
    w = _weapon_table(ctx)
    focus = st.session_state.get(_FOCUS)
    if focus not in set(w["weapon"].astype(str)):
        focus = st.session_state[_FOCUS] = None
    if len(w):
        st.markdown(f"**{t('weapons')}**", help=t("sh_weapons_help"))
        _weapons_grid(ctx, w, focus)
    _focus_bar(t, focus)
    s = _stats(ctx, focus)
    _metric_tiles(t, s)
    _angles_warning(t, s)
    _charts(t, s)
