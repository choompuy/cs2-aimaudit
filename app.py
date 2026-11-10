"""
CS2-AimAudit — local CS2 demo analysis. Run: streamlit run app.py
Analysis logic lives in core.py, translations in i18n.py. Only UI here.
"""

import base64
import json
import os
import tempfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import core
import i18n

st.set_page_config(page_title="CS2-AimAudit", layout="wide")

# ------------------------------------------------- shareable view in the URL
# The whole view (language, player, filters) is packed into ?f=<b64 json>
# so a filtered tab can be bookmarked, restored or sent to a teammate.
_URL_KEYS = (
    ("lang", "lang"),
    ("me", "me"),
    ("weapons", "flt_weapons"),
    ("classes", "flt_classes"),
    ("rounds", "flt_rounds"),
    ("preset", "flt_preset"),
    ("side", "flt_side"),
    ("res", "flt_res"),
)


def _url_pack(d: dict) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _url_unpack(s: str) -> dict | None:
    try:
        pad = "=" * (-len(s) % 4)
        d = json.loads(base64.urlsafe_b64decode(s + pad).decode())
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001 - a bad link must not break the app
        return None


if "url_state_loaded" not in st.session_state:
    st.session_state["url_state_loaded"] = True
    saved = _url_unpack(st.query_params.get("f", ""))
    if saved:
        for k, sk in _URL_KEYS:
            if saved.get(k) is not None:
                st.session_state[sk] = saved[k]

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

# --------------------------------------------------------------- sidebar
t = i18n.make_t(st.session_state.get("lang", next(iter(i18n.LANGS))))

with st.sidebar:
    lang = st.selectbox(
        t("language"),
        list(i18n.LANGS),
        format_func=lambda k: i18n.LANGS[k],
        key="lang",
    )
    st.header(t("settings"))
    thr = st.slider(t("thr_label"), 0, 250, 40, help=t("thr_help"), key="thr")
    window_s = st.slider(
        t("win_label"), 1.0, 6.0, 3.0, 0.5, help=t("win_help"), key="window_s"
    )
    with st.expander(t("adv")):
        safe_dist = st.slider(
            t("safe_dist_label"),
            100,
            2000,
            500,
            50,
            help=t("safe_dist_help"),
            key="safe_dist",
        )
        strict_dist = st.slider(
            t("strict_dist_label"),
            500,
            4000,
            1500,
            100,
            help=t("strict_dist_help"),
            key="strict_dist",
        )
    strict_dist = max(strict_dist, safe_dist + 100)

st.title("🎯 CS2-AimAudit")
st.caption(t("tagline"))

# ------------------------------------------------------ cached core calls
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
    return core.load_demo(path, progress=_say)


@st.cache_data(show_spinner=False)
def get_timeline(
    path: str, mtime: float, me: str, tick: int, before_s: float, tickrate: int
):
    return core.timeline_data(path, me, tick, before_s=before_s, tickrate=tickrate)


# ------------------------------------------------------------ file input
state = core.load_state()
recent = [
    p for p in state.get("recent_demos", []) if isinstance(p, str) and os.path.exists(p)
]

up = st.file_uploader(
    t("upload_label"), type=["dem", "bz2", "gz", "zst", "zip"], key="demo_file"
)
with st.expander(t("path_expander")):
    if recent:

        def _pick_recent():
            v = st.session_state.get("recent_pick")
            if v:
                st.session_state["demo_path"] = v

        st.selectbox(
            t("recent_label"),
            [""] + recent,
            format_func=lambda v: t("recent_pick_hint") if not v else v,
            key="recent_pick",
            on_change=_pick_recent,
        )
    st.text_input(
        t("path_label"), placeholder=t("path_ph"), key="demo_path", help=t("path_help")
    )

# Explorer's "Copy as path" wraps the path in quotes; also accept ~ and %VARS%
path_in = str(st.session_state.get("demo_path", "")).strip().strip("\"'").strip()
if path_in:
    path_in = os.path.expandvars(os.path.expanduser(path_in))
if path_in:
    if not os.path.isfile(path_in):
        st.error(t("err_path_missing", path=path_in))
        st.stop()
    src = path_in  # read straight from disk, no upload copy
elif up:
    src = os.path.join(tempfile.gettempdir(), up.name)
    if not os.path.exists(src) or os.path.getsize(src) != up.size:
        with open(src, "wb") as f:
            f.write(up.getbuffer())
else:
    st.info(t("no_file"))
    st.stop()
try:
    path = core.decompressed_demo(src)
except ValueError as e:
    st.error(t("load_failed"))
    st.caption(str(e))
    st.stop()
mtime = os.path.getmtime(path)

# ----------------------------------------------------------------- load
status = st.status(t("loading"))
_status["box"] = status
try:
    demo = get_demo(path, mtime)
except ValueError as e:
    _status["box"] = None
    status.update(label=t("load_failed"), state="error")
    if "no_events" in str(e):
        st.error(t("err_no_events"))
    else:
        st.error(t("load_failed"))
        st.caption(str(e))
    st.stop()
except Exception as e:  # noqa: BLE001
    _status["box"] = None
    status.update(label=t("load_failed"), state="error")
    st.error(t("load_failed"))
    st.caption(str(e))
    st.stop()
_status["box"] = None

if demo.players.empty:
    status.update(label=t("load_failed"), state="error")
    st.error(t("err_no_events"))
    st.stop()

# remember what opened fine (recent list stays local, see core.load_state)
rec = state.get("recent_demos") or []
if not rec or rec[0] != src:
    core.remember_demo(src, state)
    core.save_state(state)

# -------------------------------------------------------------- metrics
status.update(label=t("step_metrics"))
shots = core.add_stop_quality(
    core.add_risk(core.shots_table(demo), thr, safe_dist, strict_dist), thr
)
duels_all = core.build_duels(demo, window_s)

info = []
if demo.header.get("map_name"):
    info.append(f"**{t('map')}:** {demo.header['map_name']}")
info.append(f"**{t('players_n')}:** {len(demo.players)}")
info.append(f"**{t('tickrate')}:** {demo.tickrate}")
trp = demo.deaths.get("total_rounds_played") if len(demo.deaths) else None
if trp is not None:
    r_max = pd.to_numeric(trp, errors="coerce").max()
    if pd.notna(r_max):
        info.append(f"**{t('rounds_n')}:** {int(r_max) + 1}")
if info:
    st.caption(" · ".join(info))

name_of = dict(zip(demo.players["sid"], demo.players["name"]))
team_of = dict(zip(demo.players["sid"], demo.players["team"]))
plist = demo.players.assign(
    _n=demo.players["name"].astype(str).str.lower()
).sort_values(["team", "_n"])
me_opts = plist["sid"].tolist()
# preselect the remembered player (and drop a stale pick from another demo)
if st.session_state.get("me") not in me_opts:
    last = state.get("last_player")
    st.session_state["me"] = last if last in me_opts else me_opts[0]
me = st.selectbox(
    t("player"),
    me_opts,
    format_func=lambda s: f"{name_of.get(s, s)} ({team_of.get(s, '?')})",
    key="me",
)
if state.get("last_player") != me:
    state["last_player"] = me
    core.save_state(state)


# -------------------------------------------------------------- helpers
def fmt_result(v: str) -> str:
    return t("res_kill") if v == "kill" else t("res_death")


def fmt_first(v: str) -> str:
    return {
        "me": t("first_me"),
        "opponent": t("first_opponent"),
        "simultaneous": t("first_simultaneous"),
        "none": t("first_none"),
    }.get(v, str(v))


# ------------------------------------------------------ global filter bar
# One bar under the player selector that every tab reads from: all metrics
# below are computed on the FILTERED frames.
with st.container(border=True):
    st.markdown(f"**{t('flt_title')}**")
    demo_weapons = sorted(shots["weapon"].astype(str).unique())
    present_classes = sorted({core.weapon_class(w) for w in demo_weapons})
    n_rounds = int(shots["round"].max()) if len(shots) else 0
    has_sides = bool(demo.sides)
    # drop selections that do not exist in this demo (e.g. from another file
    # or from a shared URL of another match)
    if "flt_weapons" in st.session_state:
        st.session_state["flt_weapons"] = [
            w for w in st.session_state["flt_weapons"] if w in set(demo_weapons)
        ]
    if "flt_classes" in st.session_state:
        st.session_state["flt_classes"] = [
            c for c in st.session_state["flt_classes"] if c in set(present_classes)
        ]
    preset_opts = ["all"] + (["pistols"] if n_rounds >= 2 else [])
    if n_rounds >= 2 * core.ROUNDS_PER_HALF + 1:
        preset_opts = ["all", "first", "second", "ot", "pistols"]
    elif n_rounds >= core.ROUNDS_PER_HALF + 1:
        preset_opts = ["all", "first", "second", "pistols"]
    if st.session_state.get("flt_preset") not in preset_opts:
        st.session_state["flt_preset"] = "all"
    if st.session_state.get("flt_side") not in ("all", "T", "CT"):
        st.session_state["flt_side"] = "all"
    # st.slider raises StreamlitInvalidMinMaxError when min_value == max_value,
    # which kills the whole app on a one-round demo (or any demo where every
    # shot lands in round 1 because total_rounds_played never arrives). A
    # single round needs no range filter, so the slider starts at 2 rounds.
    if n_rounds >= 2:
        # no `value=`: the clamp block above always puts the (normalized)
        # range into session_state, and passing both a default and the
        # session-state value makes Streamlit log a warning.
        rv = st.session_state.get("flt_rounds") or (1, n_rounds)
        try:
            lo_r = max(1, min(int(rv[0]), n_rounds))
            hi_r = max(lo_r, min(int(rv[1]), n_rounds))
            st.session_state["flt_rounds"] = (lo_r, hi_r)
        except (TypeError, ValueError, IndexError):
            st.session_state["flt_rounds"] = (1, n_rounds)

    fc1, fc2, fc3 = st.columns([3, 3, 2])
    fc1.multiselect(
        t("flt_weapons"),
        demo_weapons,
        key="flt_weapons",
        help=t("flt_weapons_help"),
    )
    fc2.multiselect(
        t("flt_classes"),
        present_classes,
        format_func=lambda c: t(f"wc_{c}"),
        key="flt_classes",
        help=t("flt_classes_help"),
    )
    fc3.caption("")  # keeps the n-report below aligned with the widgets

    def _apply_preset():
        p = st.session_state.get("flt_preset", "all")
        if p == "pistols":
            st.session_state["flt_rounds"] = (1, max(n_rounds, 1))
        else:
            st.session_state["flt_rounds"] = core.round_bounds(n_rounds, p)

    def _slider_custom():
        # manual range -> the preset label no longer applies
        st.session_state["flt_preset"] = "all"

    fr1, fr2, fr3, fr4 = st.columns([3, 2, 2, 2])
    if (
        n_rounds >= 2
    ):  # a slider needs min < max (1-round demos have nothing to range over)
        fr1.slider(
            t("flt_rounds"),
            1,
            n_rounds,
            key="flt_rounds",
            help=t("flt_rounds_help"),
            on_change=_slider_custom,
        )
    fr2.selectbox(
        t("flt_preset"),
        preset_opts,
        format_func=lambda p: t(f"fp_{p}"),
        key="flt_preset",
        on_change=_apply_preset,
    )
    if has_sides:
        fr3.selectbox(
            t("flt_side"),
            ["all", "T", "CT"],
            format_func=lambda v: t("all") if v == "all" else v,
            key="flt_side",
            help=t("flt_side_help"),
        )
    fr4.multiselect(
        t("flt_res"),
        ["kill", "death"],
        format_func=fmt_result,
        key="flt_res",
        help=t("flt_res_help"),
    )

    preset = st.session_state.get("flt_preset", "all")
    round_kwargs: dict = {}
    if preset == "pistols":
        round_kwargs["rounds_set"] = core.pistol_rounds(n_rounds)
    elif n_rounds >= 1:
        rv = tuple(st.session_state.get("flt_rounds") or (1, n_rounds))
        if rv != (1, n_rounds):
            round_kwargs["rounds"] = rv
    filters = core.Filters(
        weapons=set(st.session_state.get("flt_weapons") or []) or None,
        classes=set(st.session_state.get("flt_classes") or []) or None,
        side=(
            st.session_state.get("flt_side")
            if has_sides and st.session_state.get("flt_side") != "all"
            else None
        ),
        **round_kwargs,
    )
    shots_f, duels_f, hurts_f = core.apply_filters(
        shots, duels_all, demo.hurts, filters
    )
    res_sel = set(st.session_state.get("flt_res") or [])
    fc3.caption(
        t("flt_n", shots=len(shots_f), duels=len(duels_f))
        + (
            "  \n" + t("flt_n_all", shots=len(shots), duels=len(duels_all))
            if not filters.empty()
            else ""
        )
    )

    # keep the URL in sync so the view can be bookmarked / shared
    url_view = {
        "lang": st.session_state.get("lang"),
        "me": me,
        "weapons": st.session_state.get("flt_weapons") or [],
        "classes": st.session_state.get("flt_classes") or [],
        "rounds": list(st.session_state.get("flt_rounds") or ()),
        "preset": preset,
        "side": st.session_state.get("flt_side", "all"),
        "res": st.session_state.get("flt_res") or [],
    }
    non_default = (
        url_view["weapons"]
        or url_view["classes"]
        or url_view["res"]
        or url_view["side"] != "all"
        or url_view["preset"] != "all"
        or (url_view["rounds"] and tuple(url_view["rounds"]) != (1, n_rounds))
    )
    if non_default:
        st.query_params["f"] = _url_pack(url_view)
        fc3.caption(t("flt_share"))
    elif "f" in st.query_params:
        del st.query_params["f"]

summary = core.player_summary(
    demo, duels_f, shots_f, thr, safe_dist, strict_dist, hurts=hurts_f
)

my_duels = core.duels_for(duels_f, me) if len(duels_f) else pd.DataFrame()
# kill/death filter is a per-player concept: it applies to the selected
# player's duels on every tab (the all-players Overview keeps both)
if res_sel and len(my_duels):
    my_duels = my_duels[my_duels["result"].isin(res_sel)]
status.update(label=t("loaded"), state="complete")


def duel_label(r) -> str:
    return t(
        "tl_label",
        round=int(r["round"]),
        result=fmt_result(r["result"]),
        weapon=str(r["weapon"]),
        opp=name_of.get(r["opponent"], r["opponent"]),
    )


def pct(v) -> str:
    return f"{v:.0f}%" if pd.notna(v) else t("none_val")


def num(v, nd: int = 1) -> str:
    return f"{v:.{nd}f}" if pd.notna(v) else t("none_val")


tab_ov, tab_sh, tab_du, tab_tl, tab_gd = st.tabs(
    [
        t("tab_overview"),
        t("tab_shooting"),
        t("tab_duels"),
        t("tab_timeline"),
        t("tab_guide"),
    ]
)

# -------------------------------------------------------------- overview
with tab_ov:
    st.subheader(t("ov_title"))
    st.caption(t("ov_caption"))
    cols = {
        "player": t("col_player"),
        "sides": t("col_sides"),
        "kills": t("col_kills"),
        "deaths": t("col_deaths"),
        "shots": t("col_shots"),
        "hits": t("col_hits"),
        "accuracy": t("col_accuracy"),
        "hs_hits": t("col_hs_hits"),
        "hs_kills": t("col_hs_kills"),
        "moving": t("col_moving"),
        "risky": t("col_risky"),
        "first_shot": t("col_first_shot"),
        "aim_err": t("col_aim_err"),
        "duration": t("col_duration"),
    }
    team_order = ["T", "CT"] + sorted(set(summary["team"]) - {"T", "CT"})
    for team in team_order:
        g = summary[summary["team"] == team]
        if not len(g):
            continue
        st.markdown(f"**{team}**")
        st.dataframe(
            g.drop(columns=["sid", "team"]).rename(columns=cols),
            hide_index=True,
            width="stretch",
        )

# -------------------------------------------------------------- shooting
with tab_sh:
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
    first_pct = (fs["first"] == "me").mean() * 100 if len(fs) else np.nan
    dur = dd["duration_s"].median() if len(dd) else np.nan
    err = dd["my_err"].median() if len(dd) else np.nan
    spd = dd["my_speed"].median() if len(dd) else np.nan
    dd_err = dd[dd["my_err"].notna()] if len(dd) else dd
    onhead_pct = dd_err["my_would_head"].mean() * 100 if len(dd_err) else np.nan
    stops = sh_me[sh_me["stop"] == "stopped"] if len(sh_me) else sh_me
    cs_pct = stops["counter_strafe"].mean() * 100 if len(stops) else np.nan

    c = st.columns(5)
    c[0].metric(t("m_shots"), len(sh_me), help=t("m_shots_help"))
    c[1].metric(t("m_hits"), hits_n, help=t("m_hits_help"))
    c[2].metric(t("m_kd"), f"{kills} / {dths}", help=t("m_kd_help"))
    c[3].metric(t("m_moving"), pct(moving_pct), help=t("m_moving_help"))
    c[4].metric(t("m_risky"), pct(risky_pct), help=t("m_risky_help"))
    c = st.columns(5)
    c[0].metric(t("m_hs"), pct(hs_hits), help=t("m_hs_help"))
    c[1].metric(t("m_dur"), num(dur, 2), help=t("m_dur_help"))
    c[2].metric(t("m_err"), num(err, 1), help=t("m_err_help"))
    c[3].metric(t("m_speed"), num(spd, 0), help=t("m_speed_help"))
    c[4].metric(t("m_first"), pct(first_pct), help=t("m_first_help"))
    c = st.columns(5)
    c[0].metric(t("m_onhead"), pct(onhead_pct), help=t("m_onhead_help"))
    c[1].metric(t("m_cs"), pct(cs_pct), help=t("m_cs_help"), delta=None)

    # honesty block: n, Wilson 95% CIs and lobby ranks for the rates above
    def _ci(k, n):
        lo, hi = core.wilson_ci(k, n)
        return "—" if pd.isna(lo) else f"{lo:.0f}–{hi:.0f}%"

    st.caption(
        t(
            "ci_line",
            acc=_ci(hits_n, len(sh_me)),
            n_shots=len(sh_me),
            hs=_ci(hg_me.get("head", 0), hits_n),
            n_hits=hits_n,
            first=_ci(int((fs["first"] == "me").sum()), len(fs)) if len(fs) else "—",
            n_duels=len(fs),
            hw=(
                _ci(int(dd_err["my_would_head"].sum()), len(dd_err))
                if len(dd_err)
                else "—"
            ),
            n_hw=len(dd_err),
        )
    )
    if len(sh_me) < core.MIN_N_SHOTS or len(dd) < core.MIN_N_DUELS:
        st.caption(t("small_sample", shots=core.MIN_N_SHOTS, duels=core.MIN_N_DUELS))
    parts = []
    for col, key, best in (
        ("accuracy", "rk_acc", "high"),
        ("first_shot", "rk_first", "high"),
        ("risky", "rk_risky", "low"),
    ):
        rk = core.rank_in_lobby(summary, me, col, best)
        if rk:
            parts.append(t("rank_part", name=t(key), pos=rk[0], n=rk[1]))
    if parts:
        st.caption(t("rank_line", parts=" · ".join(parts)))

    if len(dd):
        k_err = dd.loc[dd["result"] == "kill", "my_err"].median()
        if pd.notna(k_err) and k_err > 25:
            st.warning(t("warn_angles"))

    st.subheader(t("tips"))
    st.caption(t("tips_caption"))
    tips = []
    if len(sh_me) >= 10:
        if pd.notna(risky_pct) and risky_pct > 35:
            tips.append(t("tip_risky", pct=risky_pct))
        elif pd.notna(moving_pct) and moving_pct > 35:
            tips.append(t("tip_moving", pct=moving_pct))
        else:
            tips.append(
                t("tip_moving_ok", pct=moving_pct if pd.notna(moving_pct) else 0)
            )
    if len(dd) >= 5 and pd.notna(err) and err > 5:
        tips.append(t("tip_err", err=err))
    dth_df = dd[dd["result"] == "death"] if len(dd) else dd
    if len(dth_df) >= 3:
        n_lost = int((dth_df["first"] == "opponent").sum())
        if n_lost and n_lost / len(dth_df) >= 0.5:
            tips.append(t("tip_lost_first", n=n_lost, m=len(dth_df)))
    if not tips:
        tips.append(t("tip_few"))
    for tip in tips:
        st.markdown(f"- {tip}")

    left, right = st.columns(2)
    with left:
        st.markdown(f"**{t('hit_where')}**")
        if hg_me.empty:
            st.info(t("hit_none"))
        else:
            idx = [k for k in HG_ORDER if k in hg_me.index]
            idx += [k for k in hg_me.index if k not in HG_ORDER]
            fig = go.Figure(
                go.Bar(x=[t(f"hg_{k}") for k in idx], y=hg_me.reindex(idx).values)
            )
            fig.update_layout(height=280, margin=dict(t=10))
            st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown(f"**{t('speed_hist')}**")
        sv = sh_me["speed"].dropna() if len(sh_me) else pd.Series(dtype=float)
        if len(sv):
            fig = go.Figure(go.Histogram(x=sv, nbinsx=40))
            fig.add_vline(x=thr, line_dash="dot")
            fig.update_layout(
                height=280,
                margin=dict(t=10),
                xaxis_title=t("speed_x"),
                yaxis_title=t("speed_y"),
            )
            st.plotly_chart(fig, width="stretch")

    st.markdown(f"**{t('risk_title')}**")
    known_r = sh_me[sh_me["dist"].notna() & sh_me["speed"].notna()]
    if not len(known_r):
        st.info(t("risk_none"))
    else:
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
                marker=dict(size=5, color="#8899aa"),
            )
        )
        fig.add_trace(
            go.Scattergl(
                x=hit["dist"],
                y=hit["speed"],
                mode="markers",
                name=t("lbl_hit"),
                opacity=0.8,
                marker=dict(size=5, color="#e15759"),
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
                line=dict(color="#4daf4a", dash="dash"),
            )
        )
        fig.update_layout(
            height=380,
            margin=dict(t=10),
            xaxis_title=t("risk_x"),
            yaxis_title=t("risk_y"),
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(fig, width="stretch")

    st.markdown(f"**{t('weapons')}**")
    w = core.weapon_stats(demo, shots_f, me, thr, safe_dist, strict_dist, hurts=hurts_f)
    if len(w):
        w = w.rename(
            columns={
                "weapon": t("col_weapon"),
                "shots": t("col_shots"),
                "median_speed": t("col_median_speed"),
                "median_dist": t("col_median_dist"),
                "moving": t("col_moving_w"),
                "risky": t("col_risky_w"),
                "hits": t("col_hits"),
                "hs": t("col_hs_hits"),
                "accuracy": t("col_accuracy"),
            }
        )
        st.dataframe(w, hide_index=True, width="stretch")

# ---------------------------------------------------------------- duels
with tab_du:
    st.subheader(t("du_title"))
    st.caption(t("du_caption"))
    if not len(my_duels):
        st.info(t("du_none"))
    else:
        ddf = my_duels
        flags = []
        for r in ddf.itertuples():
            fl = []
            if r.low_conf:
                fl.append(t("flag_lowconf"))
            if r.noscope:
                fl.append(t("flag_noscope"))
            if r.thrusmoke:
                fl.append(t("flag_thrusmoke"))
            if r.attackerblind:
                fl.append(t("flag_attackerblind"))
            if r.penetrated:
                fl.append(t("flag_penetrated"))
            flags.append(", ".join(fl))
        disp = pd.DataFrame(
            {
                t("col_round"): ddf["round"].to_numpy(),
                "tick": ddf["tick"].to_numpy(),
                t("col_result"): [fmt_result(v) for v in ddf["result"]],
                t("col_opponent"): [name_of.get(s, s) for s in ddf["opponent"]],
                t("col_side"): ddf["my_side"].to_numpy(),
                t("col_weapon"): ddf["weapon"].to_numpy(),
                t("col_hs"): ["✓" if v else "" for v in ddf["headshot"]],
                t("col_distance"): ddf["distance"].round(0).to_numpy(),
                t("col_first"): [fmt_first(v) for v in ddf["first"]],
                t("col_my_shots"): ddf["my_shots"].to_numpy(),
                t("col_opp_shots"): ddf["opp_shots"].to_numpy(),
                t("col_duration"): ddf["duration_s"].to_numpy(),
                t("col_speed"): ddf["my_speed"].round(0).to_numpy(),
                t("col_err"): ddf["my_err"].round(1).to_numpy(),
                t("col_errhw"): ddf["my_err_hw"].round(2).to_numpy(),
                t("col_flags"): flags,
            }
        )
        du_sel = st.dataframe(
            disp,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="du_table",
        )
        st.caption(t("du_click_hint"))
        sel_rows = list(du_sel.selection.rows or [])
        if sel_rows and 0 <= sel_rows[0] < len(my_duels):
            # preselect this duel on the Timeline tab (row order == my_duels)
            st.session_state["tl_duel"] = int(sel_rows[0])

# ------------------------------------------------------------- timeline
with tab_tl:
    st.subheader(t("tl_title"))
    if not len(my_duels):
        st.info(t("du_none"))
    else:
        if not isinstance(st.session_state.get("tl_duel"), int) or not (
            0 <= st.session_state["tl_duel"] < len(my_duels)
        ):
            st.session_state["tl_duel"] = 0
        labels = [duel_label(my_duels.iloc[i]) for i in range(len(my_duels))]
        i = st.selectbox(
            t("tl_pick"),
            range(len(my_duels)),
            format_func=lambda k: labels[k],
            key="tl_duel",
        )
        r = my_duels.iloc[i]
        tick = int(r["tick"])
        with st.spinner(t("tl_loading")):
            tdf, missing = get_timeline(path, mtime, me, tick, window_s, demo.tickrate)
        if missing:
            st.warning(t("tl_no_buttons", keys=", ".join(t(f"k_{b}") for b in missing)))
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

        # auto verdict in plain words
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
        if verdict:
            st.caption(" · ".join(verdict))
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
            if "is_alive" in tdf.columns and len(tdf):
                dead_ix = tdf.index[~tdf["is_alive"].astype(bool)]
                if len(dead_ix):
                    d_tick = int(tdf.loc[dead_ix[0], "tick"])
                    if d_tick <= int(tdf["tick"].max()):
                        tdf = tdf[tdf["tick"] <= d_tick]
                        st.caption(t("tl_dead_cut"))
            x = (tdf["tick"] - tick) / demo.tickrate
            speed = (
                tdf["speed"]
                if "speed" in tdf.columns
                else pd.Series(np.nan, index=tdf.index)
            )
            yaw = np.degrees(np.unwrap(np.radians(tdf["yaw"].astype(float))))
            pitch = np.degrees(np.unwrap(np.radians(tdf["pitch"].astype(float))))
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
            fig.add_trace(go.Scatter(x=x, y=speed, mode="lines"), row=1, col=1)
            fig.add_trace(go.Scatter(x=x, y=yaw, mode="lines"), row=2, col=1)
            fig.add_trace(go.Scatter(x=x, y=pitch, mode="lines"), row=3, col=1)
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
                        marker=dict(symbol="star", size=11, color="#e15759"),
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
                        marker=dict(symbol="triangle-up", size=9, color="#4daf4a"),
                    ),
                    row=1,
                    col=1,
                )
            if btn_cols:
                for k, b in enumerate(btn_cols):
                    on = (tdf[b].fillna(0) != 0).to_numpy()
                    fig.add_trace(
                        go.Scatter(
                            x=x.to_numpy()[on],
                            y=[k] * int(on.sum()),
                            mode="markers",
                            marker=dict(symbol="square", size=6),
                        ),
                        row=4,
                        col=1,
                    )
                fig.update_yaxes(
                    tickmode="array",
                    tickvals=list(range(len(btn_cols))),
                    ticktext=[t(f"k_{b}") for b in btn_cols],
                    row=4,
                    col=1,
                )
            for row_i in range(1, rows_n + 1):
                if row_i == 1:
                    fig.add_vline(
                        x=0,
                        line_dash="dash",
                        annotation_text=t("ch_event"),
                        row=1,
                        col=1,
                    )
                else:
                    fig.add_vline(x=0, line_dash="dash", row=row_i, col=1)
            fig.update_xaxes(title_text=t("ch_x"), row=rows_n, col=1)
            fig.update_layout(
                height=760,
                showlegend=bool(fire_pts or hit_pts),
                legend=dict(orientation="h", y=1.06),
                margin=dict(t=60),
            )
            st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- guide
with tab_gd:
    st.markdown(t("guide"))

# ---------------------------------------------------------------- debug
with st.expander(f"🐞 {t('debug')}"):
    st.caption(t("dbg_hint"))
    checks = core.data_checks(demo, shots, duels_all)
    icons = {"ok": "✅", "warn": "⚠️", "anomaly": "❗"}
    order = {"anomaly": 0, "warn": 1, "ok": 2}
    if checks and all(c["level"] == "ok" for c in checks):
        st.markdown(f"✅ {t('dbg_ok_all')}")
    for c in sorted(checks, key=lambda c: order[c["level"]]):
        kw = {"n": c.get("n", 0)}
        if "pct" in c:
            kw["pct"] = c["pct"]
        if "med" in c:
            kw["med"] = c["med"] if pd.notna(c["med"]) else 0.0
        kw["vals"] = ", ".join(map(str, c.get("vals") or [])) or "—"
        st.markdown(f"{icons[c['level']]} {t('chk_' + c['id'], **kw)}")
    rep = core.debug_report(
        demo,
        shots,
        duels_all,
        me,
        {
            "thr": thr,
            "window_s": window_s,
            "safe_dist": safe_dist,
            "strict_dist": strict_dist,
        },
    )
    st.download_button(
        t("dbg_download"),
        data=json.dumps(rep, ensure_ascii=False, indent=1, default=str),
        file_name=f"cs2_aimaudit_debug_{me}.json",
        mime="application/json",
        key="dbg_dl",
    )
