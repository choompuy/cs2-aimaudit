"""
CS2-AimAudit — local CS2 demo analysis. Run: streamlit run app.py
Analysis logic lives in core.py, translations in i18n.py. Only UI here.
"""

import base64
import functools
import html
import json
import os
import subprocess
import tempfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
from plotly.subplots import make_subplots

import core
import i18n
import mapview
import theme
from ui.table import Col, data_table

st.set_page_config(
    page_title="CS2-AimAudit",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# The design system: CSS variables + overrides (theme/overrides.css) are
# injected here once per run; charts and inline HTML read tokens through
# theme.tok(); native widgets take their colours from .streamlit/config.toml,
# which tools/sync_theme.py regenerates from the same theme/tokens.toml.
theme.inject_theme()


@functools.lru_cache(maxsize=1)
def _app_version() -> str:
    """Short commit of the running checkout for the footer ('dev' outside a repo)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return out.stdout.strip() or "dev"
    except Exception:  # noqa: BLE001 - no git / no repo: footer still renders
        return "dev"


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
    ("dist", "flt_dist"),
    ("page", "page"),
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
    # thresholds are advanced tuning - one closed "Analysis settings"
    # expander with a reset, instead of jargon sliders above the content;
    # each slider's effect is one tooltip line (help=)
    with st.expander(t("settings_box")):
        thr = st.slider(t("thr_label"), 0, 250, 40, help=t("thr_help"), key="thr")
        window_s = st.slider(
            t("win_label"), 1.0, 6.0, 3.0, 0.5, help=t("win_help"), key="window_s"
        )
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

        def _reset_settings():
            st.session_state["thr"] = 40
            st.session_state["window_s"] = 3.0
            st.session_state["safe_dist"] = 500
            st.session_state["strict_dist"] = 1500

        st.button(t("reset_defaults"), on_click=_reset_settings, key="btn_reset_set")
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

# Windows "Copy as path" adds quotes, terminals leave ~ / %VAR% / single
# quotes behind - core.normalize_input_path cleans all that up before the
# os.path.isfile check, otherwise the app says "File not found".
path_in = core.normalize_input_path(st.session_state.get("demo_path", ""))
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
    # the empty state is a 3-step card with the demo instructions
    # inline (the same text the Guide shows), so a first-time player
    # knows what they will get before any file is loaded
    st.info(t("no_file"))
    with st.container(border=True):
        st.subheader(t("start_title"))
        s1, s2, s3 = st.columns(3)
        s1.markdown(f"**1 · {t('start_1')}**")
        s1.caption(t("start_1_t"))
        s2.markdown(f"**2 · {t('start_2')}**")
        s2.caption(t("start_2_t"))
        s3.markdown(f"**3 · {t('start_3')}**")
        s3.caption(t("start_3_t"))
        with st.expander(t("demo_how_title")):
            st.markdown(t("demo_how"))
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
shots = get_shots(path, mtime, thr, safe_dist, strict_dist)
duels_all = get_duels(path, mtime, window_s)

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

# pre-match rounds that were dropped (knife round / warmup / restarts) -
# one honest line instead of silently "lost" rounds
_excl = getattr(demo, "excluded", {}) or {}
_excl_parts = []
if _excl.get("warmup"):
    _excl_parts.append(t("excl_warmup", n=_excl["warmup"]))
if _excl.get("knife_round"):
    _excl_parts.append(t("excl_knife", n=_excl["knife_round"]))
if _excl.get("restart"):
    _excl_parts.append(t("excl_restart", n=_excl["restart"]))
if _excl_parts:
    st.caption(
        f'<span title="{html.escape(t("excl_help"))}">'
        f"{html.escape(t('excluded_line', parts=', '.join(_excl_parts)))}</span>",
        unsafe_allow_html=True,
    )

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


def _cell_style(styler, css_fn, subset):
    """Apply a per-cell CSS function to a Styler subset."""
    return styler.map(css_fn, subset=subset)


def _pct_col(label, help=None):
    """A 0-100 percentage column with a progress bar (units live in the cell)."""
    return st.column_config.ProgressColumn(
        label, min_value=0, max_value=100, format="%.1f%%", help=help
    )


# One semantic palette everywhere, colour-blind-safe (Okabe–Ito based),
# tuned for the dark theme. Roles, not decoration: accent = you (your
# lines and bars), grey = context (misses, baselines, thresholds),
# red = bad/risky, blue = good. Meaning never rides on colour
# alone: series keep their label, marker shape or dash too.
# Values come from theme/tokens.toml — the single source of truth.
PAL = {
    "you": theme.tok("accent"),
    "ctx": theme.tok("context"),
    "bad": theme.tok("negative"),
    "good": theme.tok("info"),
}
# Order for traces without a semantic role (the timeline keys row); the
# same list lives in .streamlit/config.toml (theme.chartCategoricalColors,
# kept in sync by tools/sync_theme.py) so theme and code agree.
CHART_COLORS = theme.chart_colors()

# The shared Plotly template built from the theme tokens, registered once
# so the name "aimaudit" resolves anywhere; style_figure() additionally
# applies the object and its key attributes explicitly — see the note
# inside it for why that matters.
pio.templates["aimaudit"] = theme.plotly_template()
PLOTLY_TEMPLATE = pio.templates["aimaudit"]


def style_figure(
    fig, height=380, margin=None, x_title=None, y_title=None, hover=None, legend_y=1.12
):
    """
    The one place every chart gets its look: height, margins,
    font, the categorical palette, a horizontal legend above the plot,
    axis titles, and — where the caller passes one — a shared hovertemplate
    so hover always shows units. Semantic per-trace colours come from PAL
    at trace setup.

    The token look is applied explicitly (template + layout attributes),
    not only through the template: the browser merges Streamlit's own
    plotly theme into layout.template, but explicit layout.* values always
    win, so charts render identically on every supported Streamlit version.
    """
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        height=height,
        margin=margin or dict(t=10, r=10),
        paper_bgcolor=theme.TRANSPARENT,
        plot_bgcolor=theme.TRANSPARENT,
        font=dict(
            size=13,
            family=theme.tok("font.family-stack"),
            color=theme.tok("text-primary"),
        ),
        colorway=CHART_COLORS,
        hoverlabel=dict(
            bgcolor=theme.tok("surface-3"),
            bordercolor=theme.tok("border-strong"),
            font=dict(color=theme.tok("text-primary")),
        ),
        legend=dict(orientation="h", y=legend_y),
    )
    for update_axis in (fig.update_xaxes, fig.update_yaxes):
        update_axis(
            gridcolor=theme.tok("chart-grid"),
            zerolinecolor=theme.tok("chart-grid"),
            tickfont=dict(color=theme.tok("chart-axis")),
        )
    if x_title is not None:
        fig.update_xaxes(title_text=x_title)
    if y_title is not None:
        fig.update_yaxes(title_text=y_title)
    if hover is not None:
        fig.update_traces(hovertemplate=hover)
    return fig


# ------------------------------------------------------ global filter bar
# A persistent panel (no popover): Side / Rounds / Distance segmented
# controls plus weapon class pills; the Result control exists only on
# the duels pages, and the guide page shows no panel at all. Active
# filters repeat as dismissible chips with a Reset. All metrics below
# are computed on the FILTERED frames.
demo_weapons = sorted(shots["weapon"].astype(str).unique())
present_classes = sorted({core.weapon_class(w) for w in demo_weapons})
n_rounds = int(shots["round"].max()) if len(shots) else 0
has_sides = bool(demo.sides)
# data-driven halves (MR15 / overtime lengths) when sides are known;
# [] falls back to the MR12 assumption everywhere below
halves = core.halves_from_sides(demo.sides, n_rounds)
# which controls a page needs: the panel renders above the nav, but the
# page is already known from session_state (the nav widget only
# re-binds it on the next run)
page_now = st.session_state.get("page") or "overview"
show_result = page_now in ("duels", "timeline")
show_panel = page_now != "guide"
# distance bands reuse the stop-quality thresholds: close is inside
# SAFE_DIST (run-and-gun territory), long is beyond STRICT_DIST
DIST_KEYS = ("all", "close", "mid", "long")
DIST_RANGES = {
    "close": (0.0, core.SAFE_DIST),
    "mid": (core.SAFE_DIST, core.STRICT_DIST),
    "long": (core.STRICT_DIST, float("inf")),
}
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
# a preset is offered only when the match actually has that part: with
# sides, halves decide; without them the MR12 heuristics on round count
# (Custom needs the range slider, i.e. at least two rounds)
preset_opts = ["all"] + (["pistols"] if n_rounds >= 2 else [])
if len(halves) >= 2:
    preset_opts = ["all", "first", "second"] + preset_opts[1:]
    if len(halves) >= 3:
        preset_opts.insert(3, "ot")
elif n_rounds >= 2 * core.ROUNDS_PER_HALF + 1:
    preset_opts = ["all", "first", "second", "ot", "pistols"]
elif n_rounds >= core.ROUNDS_PER_HALF + 1:
    preset_opts = ["all", "first", "second", "pistols"]
if n_rounds >= 2:
    preset_opts.append("custom")
# ---- state migration: old links / old session keys keep working
# res used to be a multiselect list ["kill","death"] -> both/kill/death
_res = st.session_state.get("flt_res")
if isinstance(_res, (list, tuple, set)):
    st.session_state["flt_res"] = (
        "kill"
        if "kill" in _res and "death" not in _res
        else "death" if "death" in _res and "kill" not in _res else "both"
    )
elif _res not in ("both", "kill", "death"):
    st.session_state["flt_res"] = "both"
# dist is new: anything unknown resets to "all"
if st.session_state.get("flt_dist") not in DIST_KEYS:
    st.session_state["flt_dist"] = "all"
# an old link could carry preset=all with a slider-moved range: that
# state is called Custom now (only when the link actually carried a
# range - a fresh session has no flt_rounds yet)
if (
    st.session_state.get("flt_preset", "all") == "all"
    and st.session_state.get("flt_rounds") is not None
    and n_rounds >= 2
    and tuple(st.session_state["flt_rounds"]) != (1, n_rounds)
):
    st.session_state["flt_preset"] = "custom"
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


def _preset_label(p: str) -> str:
    """Preset option label with the actual round numbers of this match."""
    h = halves or []
    if p == "first":
        lo, hi = h[0] if h else (1, core.ROUNDS_PER_HALF)
        return t("fp_first", lo=lo, hi=hi)
    if p == "second":
        lo, hi = (
            h[1] if len(h) > 1 else (core.ROUNDS_PER_HALF + 1, 2 * core.ROUNDS_PER_HALF)
        )
        return t("fp_second", lo=lo, hi=hi)
    if p == "ot":
        lo = h[2][0] if len(h) > 2 else 2 * core.ROUNDS_PER_HALF + 1
        return t("fp_ot", lo=lo)
    if p == "pistols":
        rounds = sorted(core.pistol_rounds(n_rounds, halves or None))
        shown = ", ".join(map(str, rounds[:4])) + ("…" if len(rounds) > 4 else "")
        return t("fp_pistols", rounds=shown)
    if p == "custom":
        return t("flt_custom")
    return t("fp_all")


def _apply_preset():
    p = st.session_state.get("flt_preset", "all")
    if p == "custom":
        return  # the slider owns the range; keep whatever it holds
    if p == "pistols":
        st.session_state["flt_rounds"] = (1, max(n_rounds, 1))
    else:
        st.session_state["flt_rounds"] = core.round_bounds(n_rounds, p, halves or None)


def _slider_custom():
    # a manual range IS the Custom preset now (it used to fall back to
    # "all", which hid that a range was active)
    st.session_state["flt_preset"] = "custom"


def _clr(key, value):
    """A chip click clears one filter; on_click runs before widgets."""

    def _fn():
        st.session_state[key] = value

    return _fn


def _clr_rounds():
    st.session_state["flt_rounds"] = (1, n_rounds)
    st.session_state["flt_preset"] = "all"


def _reset_filters():
    st.session_state["flt_weapons"] = []
    st.session_state["flt_classes"] = []
    st.session_state["flt_res"] = "both"
    st.session_state["flt_side"] = "all"
    st.session_state["flt_dist"] = "all"
    _clr_rounds()


def _dist_label(v: str) -> str:
    return t("all") if v == "all" else t(f"flt_dist_{v}")


# one dismissible chip per active filter: (label, clear callback)
_chips = []
if st.session_state.get("flt_weapons"):
    _chips.append((", ".join(st.session_state["flt_weapons"]), _clr("flt_weapons", [])))
if st.session_state.get("flt_classes"):
    _chips.append(
        (
            ", ".join(t(f"wc_{c}") for c in st.session_state["flt_classes"]),
            _clr("flt_classes", []),
        )
    )
if has_sides and st.session_state.get("flt_side") != "all":
    _chips.append((st.session_state["flt_side"], _clr("flt_side", "all")))
if st.session_state.get("flt_preset", "all") == "custom":
    _rv = tuple(st.session_state.get("flt_rounds") or ())
    _chips.append(
        (
            t("flt_custom") + (f" {_rv[0]}–{_rv[1]}" if len(_rv) == 2 else ""),
            _clr_rounds,
        )
    )
elif st.session_state.get("flt_preset", "all") != "all":
    _chips.append((_preset_label(st.session_state["flt_preset"]), _clr_rounds))
if st.session_state.get("flt_dist", "all") != "all":
    _chips.append((_dist_label(st.session_state["flt_dist"]), _clr("flt_dist", "all")))
if show_result and st.session_state.get("flt_res", "both") != "both":
    _chips.append((fmt_result(st.session_state["flt_res"]), _clr("flt_res", "both")))

# Streamlit deletes widget state when the widget is absent from a run:
# re-assign the keys of the controls this page does not show, so a
# page switch never loses a selection
if not show_result:
    st.session_state["flt_res"] = st.session_state.get("flt_res", "both")
if not show_panel:
    for _k, _d in (
        ("flt_weapons", []),
        ("flt_classes", []),
        ("flt_side", "all"),
        ("flt_preset", "all"),
        ("flt_dist", "all"),
        ("flt_res", "both"),
    ):
        st.session_state[_k] = st.session_state.get(_k, _d)

if show_panel:
    with st.container(border=True, key="flt_card"):
        st.caption(t("flt_title"))
        # row 1: the round range gets the widest slot so its options never clip
        _r1 = st.columns(
            [3.9] + ([1.0] if has_sides else []) + ([1.4] if show_result else [])
        )
        _c = 0
        with _r1[_c]:
            st.segmented_control(
                t("flt_rounds"),
                preset_opts,
                format_func=_preset_label,
                key="flt_preset",
                on_change=_apply_preset,
                help=t("flt_rounds_help"),
            )
        _c += 1
        if has_sides:
            with _r1[_c]:
                st.segmented_control(
                    t("flt_side"),
                    ["all", "T", "CT"],
                    format_func=lambda v: t("all") if v == "all" else v,
                    key="flt_side",
                    help=t("flt_side_help"),
                )
            _c += 1
        if show_result:
            with _r1[_c]:
                st.segmented_control(
                    t("flt_res"),
                    ["both", "kill", "death"],
                    format_func=lambda v: (
                        t("res_both") if v == "both" else fmt_result(v)
                    ),
                    key="flt_res",
                    help=t("flt_res_help"),
                )
        # row 2: distance, then ONE weapons control: class chips + a More
        # popover with the single guns, combined with OR (core.Filters unions
        # weapons and classes)
        _r2 = st.columns([3.1, 2.2, 0.8], vertical_alignment="bottom")
        with _r2[0]:
            st.segmented_control(
                t("flt_dist"),
                list(DIST_KEYS),
                format_func=_dist_label,
                key="flt_dist",
                help=t("flt_dist_help"),
            )
        with _r2[1]:
            st.pills(
                t("flt_classes"),
                present_classes,
                selection_mode="multi",
                format_func=lambda c: t(f"wc_{c}"),
                key="flt_classes",
                help=t("flt_classes_help"),
            )
        with _r2[2]:
            with st.popover(t("flt_more"), width="stretch"):
                st.multiselect(
                    t("flt_weapons"),
                    demo_weapons,
                    key="flt_weapons",
                    help=t("flt_weapons_help"),
                )
        # the range slider exists only in Custom mode (and only when a range
        # is possible at all); moving it keeps Custom selected
        if st.session_state.get("flt_preset") == "custom" and n_rounds >= 2:
            st.slider(
                t("flt_rounds"),
                1,
                n_rounds,
                key="flt_rounds",
                help=t("flt_rounds_help"),
                on_change=_slider_custom,
            )
        # active filters: compact dismissible chips + a text-style Reset
        if _chips:
            with st.container(
                horizontal=True, gap="small", vertical_alignment="center"
            ):
                for _i, (_label, _fn) in enumerate(_chips):
                    st.button(
                        f"{_label} ✕",
                        on_click=_fn,
                        key=f"flt_chip_{_i}",
                        width="content",
                    )
                st.button(
                    t("flt_reset"),
                    on_click=_reset_filters,
                    key="flt_reset",
                    type="tertiary",
                    width="content",
                )

preset = st.session_state.get("flt_preset", "all")
round_kwargs: dict = {}
if preset == "pistols":
    round_kwargs["rounds_set"] = core.pistol_rounds(n_rounds, halves or None)
elif preset == "custom" and n_rounds >= 2:
    rv = tuple(st.session_state.get("flt_rounds") or (1, n_rounds))
    if rv != (1, n_rounds):
        round_kwargs["rounds"] = rv
elif preset in ("first", "second", "ot"):
    # bounds come from the preset itself, so a URL-restored preset works
    # even though its on_change callback never fired
    rv = core.round_bounds(n_rounds, preset, halves or None)
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
    dist=DIST_RANGES.get(st.session_state.get("flt_dist", "all")),
    **round_kwargs,
)
shots_f, duels_f, hurts_f = core.apply_filters(shots, duels_all, demo.hurts, filters)
# the kill/death filter follows its control: it lives on the duels
# pages, so it applies there (other pages show all duels)
res_sel = set()
if show_result and st.session_state.get("flt_res", "both") != "both":
    res_sel = {st.session_state["flt_res"]}


def _ctx_bar():
    """
    One line of context at the top of every page - how much data is in
    view. The active filters themselves are always visible as chips in
    the panel above the navigation.
    """
    line = t("flt_n", shots=len(shots_f), duels=len(duels_f))
    if not filters.empty():
        line += " " + t("flt_n_all", shots=len(shots), duels=len(duels_all))
    st.caption(line)


# keep the URL in sync so the view can be bookmarked / shared
url_view = {
    "lang": st.session_state.get("lang"),
    "me": me,
    "weapons": st.session_state.get("flt_weapons") or [],
    "classes": st.session_state.get("flt_classes") or [],
    "rounds": list(st.session_state.get("flt_rounds") or ()),
    "preset": preset,
    "side": st.session_state.get("flt_side", "all"),
    "res": st.session_state.get("flt_res", "both"),
    "dist": st.session_state.get("flt_dist", "all"),
    "page": st.session_state.get("page", "overview"),
}
non_default = (
    url_view["weapons"]
    or url_view["classes"]
    or url_view["res"] != "both"
    or url_view["dist"] != "all"
    or url_view["side"] != "all"
    or url_view["preset"] != "all"
    or url_view["page"] != "overview"
    or (url_view["rounds"] and tuple(url_view["rounds"]) != (1, n_rounds))
)
if non_default:
    st.query_params["f"] = _url_pack(url_view)
    st.caption(t("flt_share"))
elif "f" in st.query_params:
    del st.query_params["f"]

summary = core.player_summary(
    demo, duels_f, shots_f, thr, safe_dist, strict_dist, hurts=hurts_f, f=filters
)

my_duels = core.duels_for(duels_f, me) if len(duels_f) else pd.DataFrame()
# kill/death filter is a per-player concept: it follows the Result
# control (Duels / Timeline); the all-players Overview keeps both
if res_sel and len(my_duels):
    my_duels = my_duels[my_duels["result"].isin(res_sel)]
# round-impact layer (notes s.16): opening duels, trades, multi-kills,
# assists. Round structure, so like the K/D in the summary it follows the
# Side and Rounds filters (a player's rounds on that side), not the
# weapon / distance ones. The openings table on the Duels page is the
# match context of both teams and stays unfiltered.
openings = core.openings_table(demo)
impact = core.impact_stats(demo, f=filters)
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


# st.tabs executes EVERY tab body on every rerun and cannot be
# switched from code. A segmented control bound to session_state["page"]
# renders only the active page: widget changes on one page stop
# recomputing the others, and code can switch pages (a Duels row click
# opens that duel in the Timeline).
PAGES = ("overview", "shooting", "duels", "map", "timeline", "guide")
if st.session_state.get("page") not in PAGES:
    st.session_state["page"] = "overview"
page = (
    st.segmented_control(
        t("nav_pages"),
        PAGES,
        format_func=lambda p: t(f"tab_{p}"),
        label_visibility="collapsed",
        key="page",
    )
    or "overview"
)

# -------------------------------------------------------------- overview
# One table for both teams (Team column + subtle team tint); ~8 columns
# by default, "All columns" adds the detail metrics; the selected
# player's row is highlighted (★ + tint) and clicking any row selects
# that player - same as the select box above.
if page == "overview":
    _ctx_bar()
    st.subheader(t("ov_title"))
    st.caption(t("ov_caption"))
    if filters.side or filters.rounds or filters.rounds_set:
        st.caption(t("ov_scope_note"))
    _tcol, _ = st.columns([1, 2])
    with _tcol:
        ov_full = st.toggle(t("all_columns"), key="ov_full")
    st.caption(t("ov_click_hint"))

    team_order = ["T", "CT"] + sorted(set(summary["team"]) - {"T", "CT"})
    ov = (
        summary.assign(
            _n=summary["player"].astype(str).str.lower(),
            _t=summary["team"].map(
                lambda x: team_order.index(x) if x in team_order else len(team_order)
            ),
        )
        .sort_values(["_t", "_n"])
        .reset_index(drop=True)
    )
    ov_sids = ov["sid"].tolist()
    sel_row = ov_sids.index(me) if me in ov_sids else None

    ov_labels = {
        "player": t("col_player"),
        "team": t("col_team"),
        "kdr": t("col_kdr"),
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
        "first_acc": t("col_first_acc"),
        "open_rate": t("col_open"),
        "sides": t("col_sides"),
        "aim_err": t("col_aim_err"),
        "duration": t("col_duration"),
    }
    ov_cells = {
        "player": [
            ("★ " if s == me else "") + str(p) for s, p in zip(ov["sid"], ov["player"])
        ],
        "team": ov["team"].to_numpy(),
        "kdr": ov["kd_ratio"].to_numpy(),
        "kills": ov["kills"].to_numpy(),
        "deaths": ov["deaths"].to_numpy(),
        "shots": ov["shots"].to_numpy(),
        "hits": ov["hits"].to_numpy(),
        "accuracy": ov["accuracy"].to_numpy(),
        "hs_hits": ov["hs_hits"].to_numpy(),
        "hs_kills": ov["hs_kills"].to_numpy(),
        "moving": ov["moving"].to_numpy(),
        "risky": ov["risky"].to_numpy(),
        "first_shot": ov["first_shot"].to_numpy(),
        "first_acc": ov["first_acc"].to_numpy(),
        "open_rate": ov["open_rate"].to_numpy(),
        "sides": ov["sides"].to_numpy(),
        "aim_err": ov["aim_err"].to_numpy(),
        "duration": ov["duration"].to_numpy(),
    }
    ov_cfg_all = {
        "player": st.column_config.TextColumn(
            ov_labels["player"], pinned=True, width="medium"
        ),
        "team": st.column_config.TextColumn(ov_labels["team"], width="small"),
        "kdr": st.column_config.NumberColumn(
            ov_labels["kdr"], format="%.2f", help=t("col_kdr_help")
        ),
        "kills": st.column_config.NumberColumn(ov_labels["kills"], format="%d"),
        "deaths": st.column_config.NumberColumn(ov_labels["deaths"], format="%d"),
        "shots": st.column_config.NumberColumn(ov_labels["shots"], format="%d"),
        "hits": st.column_config.NumberColumn(ov_labels["hits"], format="%d"),
        "accuracy": _pct_col(ov_labels["accuracy"]),
        "hs_hits": _pct_col(ov_labels["hs_hits"]),
        "hs_kills": _pct_col(ov_labels["hs_kills"]),
        "moving": _pct_col(ov_labels["moving"]),
        "risky": _pct_col(ov_labels["risky"]),
        "first_shot": _pct_col(ov_labels["first_shot"]),
        "first_acc": _pct_col(ov_labels["first_acc"]),
        "open_rate": _pct_col(ov_labels["open_rate"], help=t("col_open_help")),
        "sides": st.column_config.TextColumn(ov_labels["sides"]),
        "aim_err": st.column_config.NumberColumn(ov_labels["aim_err"], format="%.1f°"),
        "duration": st.column_config.NumberColumn(
            ov_labels["duration"], format="%.2f s"
        ),
    }
    _ov_basic = [
        "player",
        "team",
        "kdr",
        "kills",
        "deaths",
        "accuracy",
        "hs_hits",
        "risky",
        "first_shot",
    ]
    _ov_all = [
        "player",
        "team",
        "kdr",
        "kills",
        "deaths",
        "shots",
        "hits",
        "accuracy",
        "hs_hits",
        "hs_kills",
        "moving",
        "risky",
        "first_shot",
        "first_acc",
        "open_rate",
        "sides",
        "aim_err",
        "duration",
    ]
    _ov_shown = _ov_all if ov_full else _ov_basic
    ov_disp = pd.DataFrame({ov_labels[k]: ov_cells[k] for k in _ov_shown})

    def _pick_player():
        """
        Row click -> select that player (runs before widgets, so setting
        session_state['me'] here is allowed; doing it in the tab body would
        raise StreamlitWidgetAlreadyInstantiatedError).
        """
        try:
            sel = st.session_state["ov_table"]
        except KeyError:
            return
        rows = list(sel.selection.rows or [])
        if rows and 0 <= rows[0] < len(ov_sids):
            st.session_state["me"] = ov_sids[rows[0]]

    def _pick_player_row(i):
        """Grid row click -> select that player (callback, runs before widgets)."""
        if 0 <= i < len(ov_sids):
            st.session_state["me"] = ov_sids[i]

    _pct_bad = {"tone": "context", "best": "low"}
    _ov_grid = {
        "player": Col(
            "player", ov_labels["player"], kind="text", sort="player_raw", sticky=True
        ),
        "team": Col(
            "team",
            ov_labels["team"],
            kind="badge",
            tones={"T": "warning", "CT": "info"},
        ),
        "kdr": Col(
            "kdr",
            ov_labels["kdr"],
            fmt="{:.2f}",
            best="high",
            grade=(0.7, 1.0, 1.4),
            help=t("col_kdr_help"),
        ),
        "kills": Col(
            "kills",
            ov_labels["kills"],
            fmt="{:.0f}",
            bar="rel",
            tone="context",
            best="high",
        ),
        "deaths": Col(
            "deaths",
            ov_labels["deaths"],
            fmt="{:.0f}",
            bar="rel",
            tone="context",
            best="low",
        ),
        "shots": Col(
            "shots", ov_labels["shots"], fmt="{:.0f}", bar="rel", tone="context"
        ),
        "hits": Col("hits", ov_labels["hits"], fmt="{:.0f}", bar="rel", tone="context"),
        "accuracy": Col(
            "accuracy",
            ov_labels["accuracy"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="high",
        ),
        "hs_hits": Col(
            "hs_hits",
            ov_labels["hs_hits"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="high",
        ),
        "hs_kills": Col(
            "hs_kills",
            ov_labels["hs_kills"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="high",
        ),
        "moving": Col(
            "moving",
            ov_labels["moving"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="low",
        ),
        "risky": Col(
            "risky",
            ov_labels["risky"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="low",
        ),
        "first_shot": Col(
            "first_shot",
            ov_labels["first_shot"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="high",
        ),
        "first_acc": Col(
            "first_acc",
            ov_labels["first_acc"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="high",
        ),
        "open_rate": Col(
            "open_rate",
            ov_labels["open_rate"],
            fmt="{:.1f}%",
            bar="pct",
            tone="context",
            best="high",
            help=t("col_open_help"),
        ),
        "sides": Col("sides", ov_labels["sides"], kind="text"),
        "aim_err": Col(
            "aim_err",
            ov_labels["aim_err"],
            fmt="{:.1f}°",
            best="low",
        ),
        "duration": Col(
            "duration",
            ov_labels["duration"],
            fmt="{:.2f} s",
            best="low",
        ),
    }
    data_table(
        {**ov_cells, "player_raw": ov["player"].astype(str).to_numpy()},
        [_ov_grid[k] for k in _ov_shown],
        key="ov_grid",
        selected=sel_row,
        stripe=[{"T": "warning", "CT": "info"}.get(x) for x in ov["team"]],
        on_click=_pick_player_row,
    )
    _ov_raw = st.expander(t("raw_table"))
    _ov_styler = ov_disp.style
    # team stripe tints and the selected-row highlight come from the tokens
    _team_css = {
        "T": f"background-color: {theme.tok('team-t')}",
        "CT": f"background-color: {theme.tok('team-ct')}",
    }
    _ov_styler = _cell_style(
        _ov_styler, lambda v: _team_css.get(v, ""), subset=[ov_labels["team"]]
    )
    if sel_row is not None:
        _ov_styler = _cell_style(
            _ov_styler,
            lambda v: f"background-color: {theme.tok('surface-selected')}",
            subset=([sel_row], ov_disp.columns.tolist()),
        )
    with _ov_raw:
        st.dataframe(
            _ov_styler,
            column_config={ov_labels[k]: ov_cfg_all[k] for k in _ov_shown},
            hide_index=True,
            width="stretch",
            on_select=_pick_player,
            selection_mode="single-row",
            key="ov_table",
        )

# -------------------------------------------------------------- shooting
elif page == "shooting":
    _ctx_bar()
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
    # round-impact row of the selected player (follows Side / Rounds, notes s.16)
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
    # only issued when the data actually exists.
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
    # opening-duel verdict: only when the player actually takes openings
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
    # distance-aware one), Crosshair, Duels — instead of anonymous rows;
    # the rest are grouped under section headers behind "All metrics"
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
        w_labels = {
            "weapon": t("col_weapon"),
            "shots": t("col_shots"),
            "median_speed": t("col_median_speed"),
            "median_dist": t("col_median_dist"),
            "moving": t("col_moving_w"),
            "risky": t("col_risky_w"),
            "hits": t("col_hits"),
            "hs": t("col_hs_hits"),
            "accuracy": t("col_accuracy"),
            "first_acc": t("col_first_acc"),
        }
        w = w.rename(columns=w_labels)
        w_cfg = {
            w_labels["weapon"]: st.column_config.TextColumn(
                w_labels["weapon"], pinned=True, width="medium"
            ),
            w_labels["shots"]: st.column_config.NumberColumn(
                w_labels["shots"], format="%d"
            ),
            w_labels["median_speed"]: st.column_config.NumberColumn(
                w_labels["median_speed"], format="%d u/s"
            ),
            w_labels["median_dist"]: st.column_config.NumberColumn(
                w_labels["median_dist"], format="%d u"
            ),
            w_labels["moving"]: _pct_col(w_labels["moving"]),
            w_labels["risky"]: _pct_col(w_labels["risky"]),
            w_labels["hits"]: st.column_config.NumberColumn(
                w_labels["hits"], format="%d"
            ),
            w_labels["hs"]: _pct_col(w_labels["hs"]),
            w_labels["accuracy"]: _pct_col(w_labels["accuracy"]),
            w_labels["first_acc"]: _pct_col(w_labels["first_acc"]),
        }
        _w_pct = {"bar": "pct", "tone": "context", "fmt": "{:.1f}%"}
        data_table(
            {k: w[lbl].to_numpy() for k, lbl in w_labels.items()},
            [
                Col("weapon", w_labels["weapon"], kind="text", sticky=True),
                Col(
                    "shots", w_labels["shots"], fmt="{:.0f}", bar="rel", tone="context"
                ),
                Col(
                    "median_speed",
                    w_labels["median_speed"],
                    fmt="{:.0f} u/s",
                    best="low",
                ),
                Col("median_dist", w_labels["median_dist"], fmt="{:.0f} u"),
                Col("moving", w_labels["moving"], best="low", **_w_pct),
                Col("risky", w_labels["risky"], best="low", **_w_pct),
                Col("hits", w_labels["hits"], fmt="{:.0f}", bar="rel", tone="context"),
                Col("hs", w_labels["hs"], best="high", **_w_pct),
                Col("accuracy", w_labels["accuracy"], best="high", **_w_pct),
                Col("first_acc", w_labels["first_acc"], best="high", **_w_pct),
            ],
            key="w_grid",
        )
        with st.expander(t("raw_table")):
            st.dataframe(w, column_config=w_cfg, hide_index=True, width="stretch")

# ---------------------------------------------------------------- duels
# ~8 columns by default, "All columns" reveals the detail metrics and
# the technical tick column; numbers carry their units; the opponent name
# stays pinned when the full table scrolls horizontally.
elif page == "duels":
    _ctx_bar()
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
        _tcol, _ = st.columns([1, 2])
        with _tcol:
            du_full = st.toggle(t("all_columns"), key="du_full")
        du_labels = {
            "round": t("col_round"),
            "round_sec": t("col_round_sec"),
            "result": t("col_result"),
            "opponent": t("col_opponent"),
            "weapon": t("col_weapon"),
            "hs": t("col_hs"),
            "distance": t("col_distance"),
            "zone": t("col_zone"),
            "first": t("col_first"),
            "aim_err": t("col_err"),
            "my_shots": t("col_my_shots"),
            "opp_shots": t("col_opp_shots"),
            "duration": t("col_duration"),
            "speed": t("col_speed"),
            "errhw": t("col_errhw"),
            "side": t("col_side"),
            "flags": t("col_flags"),
            "tick": t("col_tick"),
        }
        du_cells = {
            "round": ddf["round"].to_numpy(),
            "round_sec": ddf["round_sec"].to_numpy(),
            "result": [fmt_result(v) for v in ddf["result"]],
            "opponent": [name_of.get(s, s) for s in ddf["opponent"]],
            "weapon": ddf["weapon"].to_numpy(),
            "hs": ["✓" if v else "" for v in ddf["headshot"]],
            "distance": ddf["distance"].to_numpy(),
            "zone": [z if z else "—" for z in ddf["my_zone"]],
            "first": [fmt_first(v) for v in ddf["first"]],
            "aim_err": ddf["my_err"].to_numpy(),
            "my_shots": ddf["my_shots"].to_numpy(),
            "opp_shots": ddf["opp_shots"].to_numpy(),
            "duration": ddf["duration_s"].to_numpy(),
            "speed": ddf["my_speed"].to_numpy(),
            "errhw": ddf["my_err_hw"].to_numpy(),
            "side": ddf["my_side"].to_numpy(),
            "flags": flags,
            "tick": ddf["tick"].to_numpy(),
        }
        du_cfg_all = {
            "round": st.column_config.NumberColumn(du_labels["round"], format="%d"),
            "round_sec": st.column_config.NumberColumn(
                du_labels["round_sec"], format="%.1f s", help=t("col_round_sec_help")
            ),
            "result": st.column_config.TextColumn(du_labels["result"], width="small"),
            "opponent": st.column_config.TextColumn(
                du_labels["opponent"], pinned=True, width="medium"
            ),
            "weapon": st.column_config.TextColumn(du_labels["weapon"], width="small"),
            "hs": st.column_config.Column(du_labels["hs"], width="small"),
            "distance": st.column_config.NumberColumn(
                du_labels["distance"], format="%d u"
            ),
            "zone": st.column_config.TextColumn(
                du_labels["zone"], width="medium", help=t("col_zone_help")
            ),
            "first": st.column_config.TextColumn(du_labels["first"], width="small"),
            "aim_err": st.column_config.NumberColumn(
                du_labels["aim_err"], format="%.1f°"
            ),
            "my_shots": st.column_config.NumberColumn(
                du_labels["my_shots"], format="%d"
            ),
            "opp_shots": st.column_config.NumberColumn(
                du_labels["opp_shots"], format="%d"
            ),
            "duration": st.column_config.NumberColumn(
                du_labels["duration"], format="%.2f s"
            ),
            "speed": st.column_config.NumberColumn(du_labels["speed"], format="%d u/s"),
            "errhw": st.column_config.NumberColumn(du_labels["errhw"], format="%.2f"),
            "side": st.column_config.TextColumn(du_labels["side"], width="small"),
            "flags": st.column_config.TextColumn(du_labels["flags"], width="medium"),
            "tick": st.column_config.NumberColumn(
                du_labels["tick"], format="%d", width="small"
            ),
        }
        _du_basic = [
            "round",
            "round_sec",
            "result",
            "opponent",
            "weapon",
            "hs",
            "distance",
            "first",
            "aim_err",
        ]
        _du_shown = list(du_labels) if du_full else _du_basic
        disp = pd.DataFrame({du_labels[k]: du_cells[k] for k in _du_shown})

        def _duel_row_click():
            """
            A row click opens that duel in the Timeline right away
            (row order == my_duels).
            """
            try:
                sel = st.session_state["du_table"]
            except KeyError:
                return
            rows = list(sel.selection.rows or [])
            if rows and 0 <= rows[0] < len(my_duels):
                st.session_state["tl_duel"] = int(rows[0])
                st.session_state["page"] = "timeline"

        def _duel_click_row(i):
            """Grid row click -> open that duel in the Timeline."""
            if 0 <= i < len(my_duels):
                st.session_state["tl_duel"] = int(i)
                st.session_state["page"] = "timeline"

        _du_grid = {
            "round": Col("round", du_labels["round"], fmt="{:.0f}", best="low"),
            "round_sec": Col(
                "round_sec",
                du_labels["round_sec"],
                fmt="{:.1f} s",
                best="low",
                help=t("col_round_sec_help"),
            ),
            "result": Col(
                "result",
                du_labels["result"],
                kind="badge",
                sort="result_raw",
                tones={"kill": "positive", "death": "negative"},
            ),
            "opponent": Col(
                "opponent", du_labels["opponent"], kind="text", sticky=True
            ),
            "weapon": Col("weapon", du_labels["weapon"], kind="text"),
            "hs": Col("hs", du_labels["hs"], kind="text", sort="hs_raw"),
            "distance": Col("distance", du_labels["distance"], fmt="{:.0f} u"),
            "zone": Col(
                "zone", du_labels["zone"], kind="text", help=t("col_zone_help")
            ),
            "first": Col(
                "first",
                du_labels["first"],
                kind="badge",
                sort="first_raw",
                tones={
                    "me": "positive",
                    "opponent": "negative",
                    "simultaneous": "warning",
                },
            ),
            "aim_err": Col(
                "aim_err",
                du_labels["aim_err"],
                fmt="{:.1f}°",
                best="low",
            ),
            "my_shots": Col("my_shots", du_labels["my_shots"], fmt="{:.0f}"),
            "opp_shots": Col("opp_shots", du_labels["opp_shots"], fmt="{:.0f}"),
            "duration": Col(
                "duration",
                du_labels["duration"],
                fmt="{:.2f} s",
                best="low",
            ),
            "speed": Col(
                "speed",
                du_labels["speed"],
                fmt="{:.0f} u/s",
                best="low",
            ),
            "errhw": Col(
                "errhw",
                du_labels["errhw"],
                fmt="{:.2f}",
                best="low",
            ),
            "side": Col("side", du_labels["side"], kind="text"),
            "flags": Col("flags", du_labels["flags"], kind="text"),
            "tick": Col("tick", du_labels["tick"], fmt="{:.0f}", best="low"),
        }
        data_table(
            {
                **du_cells,
                "result_raw": ddf["result"].astype(str).to_numpy(),
                "first_raw": ddf["first"].astype(str).to_numpy(),
                "hs_raw": ddf["headshot"].astype(int).to_numpy(),
            },
            [_du_grid[k] for k in _du_shown],
            key="du_grid",
            stripe=["positive" if v == "kill" else "negative" for v in ddf["result"]],
            on_click=_duel_click_row,
            max_height=720,
        )
        st.caption(t("du_click_hint"))
        with st.expander(t("raw_table")):
            st.dataframe(
                disp,
                column_config={du_labels[k]: du_cfg_all[k] for k in _du_shown},
                hide_index=True,
                width="stretch",
                on_select=_duel_row_click,
                selection_mode="single-row",
                key="du_table",
            )

        # duels by named map zone - "where do my fights actually
        # happen". The zone comes from the death event (last_place_name at
        # the kill tick); duels without a known zone are skipped, not
        # lumped into a fake "(unknown)" row.
        _zd = (
            my_duels[my_duels["my_zone"].astype(str).str.len() > 0]
            if len(my_duels)
            else my_duels
        )
        if len(_zd):
            zt = (
                _zd.groupby("my_zone")
                .agg(
                    kills=("result", lambda s: int((s == "kill").sum())),
                    deaths=("result", lambda s: int((s == "death").sum())),
                )
                .reset_index()
            )
            zt["kd"] = zt["kills"] - zt["deaths"]
            zt = zt.sort_values(["deaths", "kills"], ascending=[False, False])
            st.divider()
            st.markdown(f"**{t('zone_title')}**")
            st.caption(t("zone_caption"))
            st.dataframe(
                pd.DataFrame(
                    {
                        t("col_zone"): zt["my_zone"].to_numpy(),
                        t("col_kills"): zt["kills"].to_numpy(),
                        t("col_deaths"): zt["deaths"].to_numpy(),
                        t("zone_kd"): zt["kd"].to_numpy(),
                    }
                ),
                column_config={
                    t("col_zone"): st.column_config.TextColumn(
                        t("col_zone"),
                        pinned=True,
                        width="medium",
                        help=t("col_zone_help"),
                    ),
                    t("col_kills"): st.column_config.NumberColumn(
                        t("col_kills"), format="%d"
                    ),
                    t("col_deaths"): st.column_config.NumberColumn(
                        t("col_deaths"), format="%d"
                    ),
                    t("zone_kd"): st.column_config.NumberColumn(
                        t("zone_kd"), format="%+d"
                    ),
                },
                hide_index=True,
                width="stretch",
            )

    # opening duels - how every round started, for the whole match (both
    # teams; ★ marks the selected player). Independent of the player's
    # own duels above: shown even when they have none. Only the round
    # filter applies - weapon/side filters have no meaning for a
    # kill-by-kill view of the round structure.
    op = openings
    if len(op):
        if filters.rounds is not None:
            _lo, _hi = filters.rounds
            op = op[(op["round"] >= _lo) & (op["round"] <= _hi)]
        if filters.rounds_set:
            op = op[op["round"].isin(filters.rounds_set)]
    if len(op):
        st.divider()
        st.markdown(f"**{t('open_title')}**")
        st.caption(t("open_caption"))
        st.dataframe(
            pd.DataFrame(
                {
                    t("col_round"): op["round"].to_numpy(),
                    t("col_round_sec"): op["round_sec"].to_numpy(),
                    t("col_killer"): [
                        ("★ " if s == me else "") + name_of.get(s, s)
                        for s in op["killer"]
                    ],
                    t("col_victim"): [
                        ("★ " if s == me else "") + name_of.get(s, s)
                        for s in op["victim"]
                    ],
                    t("col_weapon"): ["—" if w == core.NA else w for w in op["weapon"]],
                    t("col_hs"): ["✓" if v else "" for v in op["headshot"]],
                    t("col_zone_k"): [z if z else "—" for z in op["killer_zone"]],
                    t("col_zone_v"): [z if z else "—" for z in op["victim_zone"]],
                }
            ),
            column_config={
                t("col_round"): st.column_config.NumberColumn(
                    t("col_round"), format="%d"
                ),
                t("col_round_sec"): st.column_config.NumberColumn(
                    t("col_round_sec"), format="%.1f s", help=t("col_round_sec_help")
                ),
                t("col_killer"): st.column_config.TextColumn(
                    t("col_killer"), width="medium"
                ),
                t("col_victim"): st.column_config.TextColumn(
                    t("col_victim"), width="medium"
                ),
                t("col_weapon"): st.column_config.TextColumn(
                    t("col_weapon"), width="small"
                ),
                t("col_hs"): st.column_config.Column(t("col_hs"), width="small"),
                t("col_zone_k"): st.column_config.TextColumn(t("col_zone_k")),
                t("col_zone_v"): st.column_config.TextColumn(t("col_zone_v")),
            },
            hide_index=True,
            width="stretch",
        )


# ------------------------------------------------------------- timeline
# a fragment - picking another duel re-renders only the timeline
def _replay_block(r, tick):
    """2D replay of the selected duel: only the two duellists (Timeline page)."""
    opp = r["opponent"]
    with st.spinner(t("tl_loading")):
        rp, _missing = get_replay(path, mtime, me, opp, tick, window_s, demo.tickrate)
    f_me, f_opp = rp.get(me), rp.get(opp)
    if (
        f_me is None
        or f_opp is None
        or f_me.empty
        or f_opp.empty
        or not {"X", "Y", "yaw"} <= set(f_me.columns) & set(f_opp.columns)
    ):
        st.info(t("rp_no_data"))
        return
    lo = min(int(f_me["tick"].min()), int(f_opp["tick"].min()))
    hi = max(int(f_me["tick"].max()), int(f_opp["tick"].max()))
    in_win = shots["tick"].between(lo, hi)
    shot_ticks = {
        "me": [int(x) for x in shots.loc[in_win & (shots["sid"] == me), "tick"]],
        "opp": [int(x) for x in shots.loc[in_win & (shots["sid"] == opp), "tick"]],
    }
    hits = []
    if len(demo.hurts) and {"attacker_steamid", "user_steamid", "tick"} <= set(
        demo.hurts.columns
    ):
        for h in demo.hurts[demo.hurts["tick"].between(lo, hi)].itertuples():
            if h.attacker_steamid == me and h.user_steamid == opp:
                hits.append((int(h.tick), "me"))
            elif h.attacker_steamid == opp and h.user_steamid == me:
                hits.append((int(h.tick), "opp"))
    keys = [b for b in core.BUTTONS if b in f_me.columns and b in f_opp.columns]
    fig = mapview.build_replay_figure(
        {"me": f_me, "opp": f_opp},
        core.load_radar(str(demo.header.get("map_name") or "")),
        tick,
        demo.tickrate,
        shots=shot_ticks,
        hits=hits,
        keys=keys,
        labels={
            "me": name_of.get(me, me),
            "opp": name_of.get(opp, opp),
            "time": t("rp_time"),
            "pause": t("rp_pause"),
            "keys": {k: t(f"k_{k}") for k in keys},
        },
        colors={
            "me": PAL["you"],
            "opp": theme.tok("opponent"),
            "dead": PAL["bad"],
            "outline": theme.tok("text-primary"),
            "ui_bg": theme.tok("surface-2"),
            "ui_fg": theme.tok("text-primary"),
            "ui_border": theme.tok("border-subtle"),
        },
    )
    if fig is None:
        st.info(t("rp_no_data"))
        return
    style_figure(fig, height=600, margin=dict(t=56, r=10, l=10, b=80))
    st.plotly_chart(fig, width="stretch")
    st.caption(t("rp_howto"))


@st.fragment
def _timeline_fragment():
    _ctx_bar()
    st.subheader(t("tl_title"))
    if not len(my_duels):
        st.info(t("du_none"))
    else:
        if not isinstance(st.session_state.get("tl_duel"), int) or not (
            0 <= st.session_state["tl_duel"] < len(my_duels)
        ):
            st.session_state["tl_duel"] = 0

        # prev/next buttons and a round selector instead of one long
        # select box; the exact duel picker stays below for precision
        def _tl_shift(d):
            j = st.session_state.get("tl_duel", 0) + d
            st.session_state["tl_duel"] = max(0, min(len(my_duels) - 1, j))

        def _tl_round_pick():
            rv = st.session_state.get("tl_round")
            hits = np.nonzero(my_duels["round"].to_numpy() == rv)[0]
            if len(hits):
                st.session_state["tl_duel"] = int(hits[0])

        labels = [duel_label(my_duels.iloc[i]) for i in range(len(my_duels))]
        rounds_avail = sorted(int(x) for x in pd.unique(my_duels["round"].dropna()))
        _cur_round = my_duels.iloc[st.session_state["tl_duel"]]["round"]
        if pd.notna(_cur_round) and int(_cur_round) in rounds_avail:
            st.session_state["tl_round"] = int(_cur_round)
        nav1, nav2, nav3 = st.columns([1, 2, 1], vertical_alignment="bottom")
        nav1.button(
            t("tl_prev"),
            key="tl_prev",
            on_click=_tl_shift,
            args=(-1,),
            disabled=(st.session_state["tl_duel"] == 0),
            use_container_width=True,
        )
        if rounds_avail:
            nav2.selectbox(
                t("col_round"), rounds_avail, key="tl_round", on_change=_tl_round_pick
            )
        else:
            nav2.caption(duel_label(my_duels.iloc[st.session_state["tl_duel"]]))
        nav3.button(
            t("tl_next"),
            key="tl_next",
            on_click=_tl_shift,
            args=(1,),
            disabled=(st.session_state["tl_duel"] == len(my_duels) - 1),
            use_container_width=True,
        )
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
            _replay_block(r, tick)
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


# ------------------------------------------------------------------- map
@st.fragment
def _map_fragment():
    """Where the selected player killed and died, on the radar (Map page)."""
    _ctx_bar()
    st.subheader(t("map_title"))
    has_pos = {"attacker_X", "attacker_Y", "user_X", "user_Y"} <= set(
        demo.deaths.columns
    )
    if not has_pos:
        st.info(t("map_no_pos"))
        return
    pts = core.kill_points(demo, me, filters)
    if not len(pts):
        st.info(t("map_none"))
        return
    map_name = str(demo.header.get("map_name") or "")
    radar = core.load_radar(map_name)

    st.session_state.setdefault("map_show", "both")
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    show = (
        c1.segmented_control(
            t("map_show"),
            ["both", "kills", "deaths"],
            format_func=lambda v: t(f"map_show_{v}"),
            key="map_show",
        )
        or "both"
    )
    lines = c2.toggle(t("map_lines"), value=True, key="map_lines")
    level = "upper"
    if radar and radar.get("lower"):
        st.session_state.setdefault("map_level", "upper")
        level = (
            st.segmented_control(
                t("map_level"),
                ["upper", "lower"],
                format_func=lambda v: t(f"map_level_{v}"),
                key="map_level",
            )
            or "upper"
        )

    def _hover(r) -> str:
        return t(
            "map_hover_kill" if r["result"] == "kill" else "map_hover_death",
            rd=int(r["round"]),
            opp=name_of.get(r["opponent"], r["opponent"]),
            weapon=str(r["weapon"]),
            dist=num(r["distance"], 0),
            hs=t("map_hover_hs") if r["headshot"] else "",
        )

    pts = pts.assign(hover=[_hover(r) for _, r in pts.iterrows()])
    fig = mapview.build_figure(
        pts,
        radar,
        show=show,
        lines=lines,
        level=level,
        labels={
            "my_kill": t("map_leg_my_kill"),
            "victim": t("map_leg_victim"),
            "my_death": t("map_leg_my_death"),
            "killer": t("map_leg_killer"),
        },
        colors={
            "kill": PAL["good"],
            "death": PAL["bad"],
            "outline": theme.tok("bg-app"),
        },
    )
    style_figure(fig, height=640, margin=dict(t=48, r=10, l=10, b=10), legend_y=1.08)
    st.plotly_chart(fig, width="stretch")
    st.caption(
        t(
            "map_count",
            k=int((pts["result"] == "kill").sum()),
            d=int((pts["result"] == "death").sum()),
        )
    )
    if radar is None:
        st.info(t("map_no_radar", map=map_name or "?"))
    st.caption(t("map_howto"))


if page == "map":
    _map_fragment()

if page == "timeline":
    _timeline_fragment()

# ---------------------------------------------------------------- guide
if page == "guide":
    st.markdown(t("guide"))
    # every metric tooltip also reachable without hover (touch
    # devices have none) - one list, reused from the i18n help texts
    with st.expander(t("glossary_title")):
        for _mk, _hk in (
            ("m_acc", "m_acc_help"),
            ("m_shots", "m_shots_help"),
            ("m_hits", "m_hits_help"),
            ("m_hs", "m_hs_help"),
            ("m_risky", "m_risky_help"),
            ("m_moving", "m_moving_help"),
            ("m_speed", "m_speed_help"),
            ("m_cs", "m_cs_help"),
            ("m_err", "m_err_help"),
            ("m_onhead", "m_onhead_help"),
            ("m_first", "m_first_help"),
            ("m_kd", "m_kd_help"),
            ("m_dur", "m_dur_help"),
        ):
            st.markdown(f"**{t(_mk)}** — {t(_hk)}")

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

st.divider()
st.caption(
    f"CS2-AimAudit · {_app_version()} · {t('footer_privacy')} · "
    f"[{t('footer_issues')}](https://github.com/choompuy/cs2-aimaudit/issues)"
)
