"""
CS2-AimAudit — local CS2 demo analysis. Run: streamlit run app.py
Analysis logic lives in core.py, translations in i18n.py. Only UI here.
"""

import base64
import functools
import json
import os
import subprocess
import tempfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import core
import i18n

st.set_page_config(
    page_title="CS2-AimAudit",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)


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
    """Apply a per-cell CSS function to a Styler subset.

    Styler.map exists from pandas 2.1; pandas 2.0 (our floor) spells it
    applymap, which is deprecated (and removed in pandas 3) later on.
    """
    method = getattr(styler, "map", None)
    if method:
        return method(css_fn, subset=subset)
    return styler.applymap(css_fn, subset=subset)


def _pct_col(label):
    """A 0-100 percentage column with a progress bar (units live in the cell)."""
    return st.column_config.ProgressColumn(
        label, min_value=0, max_value=100, format="%.1f%%"
    )


# One semantic palette everywhere, colour-blind-safe (Okabe–Ito based),
# tuned for the dark theme. Roles, not decoration: accent = you (your
# lines and bars), grey = context (misses, baselines, thresholds),
# vermillion = bad/risky, blue = good. Meaning never rides on colour
# alone: series keep their label, marker shape or dash too.
PAL = {
    "you": "#F5A623",
    "ctx": "#8899AA",
    "bad": "#D55E00",
    "good": "#56B4E9",
}
# Order for traces without a semantic role (the timeline keys row); the
# same list lives in .streamlit/config.toml (theme.chartCategoricalColors,
# Streamlit >=1.46 and the repo floor is 1.49) so theme and code agree.
CHART_COLORS = ["#F5A623", "#56B4E9", "#D55E00", "#8899AA", "#009E73", "#CC79A7"]


def style_figure(
    fig, height=380, margin=None, x_title=None, y_title=None, hover=None, legend_y=1.12
):
    """The one place every chart gets its look: height, margins,
    font size, the categorical palette, a horizontal legend above the plot,
    axis titles, and — where the caller passes one — a shared hovertemplate
    so hover always shows units. Semantic per-trace colours come from PAL
    at trace setup.
    """
    fig.update_layout(
        height=height,
        margin=margin or dict(t=10, r=10),
        font=dict(size=13),
        colorway=CHART_COLORS,
        legend=dict(orientation="h", y=legend_y),
    )
    if x_title is not None:
        fig.update_xaxes(title_text=x_title)
    if y_title is not None:
        fig.update_yaxes(title_text=y_title)
    if hover is not None:
        fig.update_traces(hovertemplate=hover)
    return fig


# ------------------------------------------------------ global filter bar
# The filters sit in one popover; active filters show as dismissible
# chips beside it with a Reset, and every page repeats the same summary
# as a one-line context bar. All metrics below are computed on the
# FILTERED frames.
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


def _apply_preset():
    p = st.session_state.get("flt_preset", "all")
    if p == "pistols":
        st.session_state["flt_rounds"] = (1, max(n_rounds, 1))
    else:
        st.session_state["flt_rounds"] = core.round_bounds(n_rounds, p)


def _slider_custom():
    # manual range -> the preset label no longer applies
    st.session_state["flt_preset"] = "all"


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
    st.session_state["flt_res"] = []
    st.session_state["flt_side"] = "all"
    _clr_rounds()


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
if st.session_state.get("flt_preset", "all") != "all":
    _chips.append((t(f"fp_{st.session_state['flt_preset']}"), _clr_rounds))
elif n_rounds >= 2 and tuple(st.session_state.get("flt_rounds") or ()) != (
    1,
    n_rounds,
):
    _rv = tuple(st.session_state.get("flt_rounds") or ())
    _chips.append((f"{t('flt_rounds')} {_rv[0]}–{_rv[1]}", _clr_rounds))
if st.session_state.get("flt_res"):
    _chips.append(
        (
            ", ".join(fmt_result(v) for v in st.session_state["flt_res"]),
            _clr("flt_res", []),
        )
    )

_fcols = st.columns([1.1] + [1.6] * len(_chips) + ([0.9] if _chips else []))
with _fcols[0].popover(t("flt_title")):
    fc1, fc2 = st.columns(2)
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
for _i, (_label, _fn) in enumerate(_chips):
    _fcols[_i + 1].button(
        f"{_label} ✕",
        on_click=_fn,
        key=f"flt_chip_{_i}",
        use_container_width=True,
    )
if _chips:
    _fcols[-1].button(
        t("flt_reset"),
        on_click=_reset_filters,
        key="flt_reset",
        use_container_width=True,
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


def _ctx_bar():
    """One line of context at the top of every page - which filters
    are active and how much data is in view."""
    parts = [lbl for lbl, _fn in _chips]
    line = t("flt_n", shots=len(shots_f), duels=len(duels_f))
    if parts:
        line = " · ".join(parts) + " — " + line
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
    "res": st.session_state.get("flt_res") or [],
    "page": st.session_state.get("page", "overview"),
}
non_default = (
    url_view["weapons"]
    or url_view["classes"]
    or url_view["res"]
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


# st.tabs executes EVERY tab body on every rerun and cannot be
# switched from code. A segmented control bound to session_state["page"]
# renders only the active page: widget changes on one page stop
# recomputing the others, and code can switch pages (a Duels row click
# opens that duel in the Timeline).
PAGES = ("overview", "shooting", "duels", "timeline", "guide")
if st.session_state.get("page") not in PAGES:
    st.session_state["page"] = "overview"
page = (
    st.segmented_control(
        None, PAGES, format_func=lambda p: t(f"tab_{p}"), key="page"
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
        "kd": t("col_kd"),
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
        "sides": t("col_sides"),
        "aim_err": t("col_aim_err"),
        "duration": t("col_duration"),
    }
    ov_cells = {
        "player": [
            ("★ " if s == me else "") + str(p) for s, p in zip(ov["sid"], ov["player"])
        ],
        "team": ov["team"].to_numpy(),
        "kd": [f"{k} / {d}" for k, d in zip(ov["kills"], ov["deaths"])],
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
        "sides": ov["sides"].to_numpy(),
        "aim_err": ov["aim_err"].to_numpy(),
        "duration": ov["duration"].to_numpy(),
    }
    ov_cfg_all = {
        "player": st.column_config.TextColumn(
            ov_labels["player"], pinned=True, width="medium"
        ),
        "team": st.column_config.TextColumn(ov_labels["team"], width="small"),
        "kd": st.column_config.TextColumn(ov_labels["kd"], width="small"),
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
        "sides": st.column_config.TextColumn(ov_labels["sides"]),
        "aim_err": st.column_config.NumberColumn(ov_labels["aim_err"], format="%.1f°"),
        "duration": st.column_config.NumberColumn(
            ov_labels["duration"], format="%.2f s"
        ),
    }
    _ov_basic = [
        "player",
        "team",
        "kd",
        "shots",
        "accuracy",
        "hs_hits",
        "risky",
        "first_shot",
    ]
    _ov_all = [
        "player",
        "team",
        "kd",
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
        "sides",
        "aim_err",
        "duration",
    ]
    _ov_shown = _ov_all if ov_full else _ov_basic
    ov_disp = pd.DataFrame({ov_labels[k]: ov_cells[k] for k in _ov_shown})

    def _pick_player():
        """Row click -> select that player (runs before widgets, so setting
        session_state['me'] here is allowed; doing it in the tab body would
        raise StreamlitWidgetAlreadyInstantiatedError)."""
        try:
            sel = st.session_state["ov_table"]
        except KeyError:
            return
        rows = list(sel.selection.rows or [])
        if rows and 0 <= rows[0] < len(ov_sids):
            st.session_state["me"] = ov_sids[rows[0]]

    _ov_styler = ov_disp.style
    _team_css = {"T": "background-color: #2A2118", "CT": "background-color: #182432"}
    _ov_styler = _cell_style(
        _ov_styler, lambda v: _team_css.get(v, ""), subset=[ov_labels["team"]]
    )
    if sel_row is not None:
        _ov_styler = _cell_style(
            _ov_styler,
            lambda v: "background-color: #3B3218",
            subset=([sel_row], ov_disp.columns.tolist()),
        )
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
    first_pct = (fs["first"] == "me").mean() * 100 if len(fs) else np.nan
    dur = dd["duration_s"].median() if len(dd) else np.nan
    err = dd["my_err"].median() if len(dd) else np.nan
    spd = dd["my_speed"].median() if len(dd) else np.nan
    dd_err = dd[dd["my_err"].notna()] if len(dd) else dd
    onhead_pct = dd_err["my_would_head"].mean() * 100 if len(dd_err) else np.nan
    stops = sh_me[sh_me["stop"] == "stopped"] if len(sh_me) else sh_me
    cs_pct = stops["counter_strafe"].mean() * 100 if len(stops) else np.nan

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
    if not tips:
        tips.append((0, t("tip_few"), None))
    tips.sort(key=lambda item: -item[0])

    # severity chips: colour from the semantic palette + a text
    # label, so meaning never rides on colour alone
    sev_style = {
        2: ("sev_high", PAL["bad"]),  # vermillion — bad / risky
        1: ("sev_med", PAL["you"]),  # accent      — warning
        0: ("sev_info", PAL["good"]),  # blue       — info / good
    }
    with st.container(border=True):
        st.markdown(f"**{t('top3_title')}**")
        st.caption(t("tips_caption"))
        for sev, tip_text, ev in tips[:3]:
            sev_key, sev_color = sev_style[sev]
            chip, text_col, ev_col = st.columns([1, 6, 2])
            chip.markdown(
                f"<span style='color:{sev_color}; font-weight:600'>"
                f"● {t(sev_key)}</span>",
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
        st.markdown(f"**{t('sec_duels')}**")
        c = st.columns(4)
        c[0].metric(t("m_kd"), f"{kills} / {dths}", help=t("m_kd_help"))
        c[1].metric(t("m_dur"), num(dur, 2), help=t("m_dur_help"))

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
        }
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
            "result": t("col_result"),
            "opponent": t("col_opponent"),
            "weapon": t("col_weapon"),
            "hs": t("col_hs"),
            "distance": t("col_distance"),
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
            "result": [fmt_result(v) for v in ddf["result"]],
            "opponent": [name_of.get(s, s) for s in ddf["opponent"]],
            "weapon": ddf["weapon"].to_numpy(),
            "hs": ["✓" if v else "" for v in ddf["headshot"]],
            "distance": ddf["distance"].to_numpy(),
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
            "result": st.column_config.TextColumn(du_labels["result"], width="small"),
            "opponent": st.column_config.TextColumn(
                du_labels["opponent"], pinned=True, width="medium"
            ),
            "weapon": st.column_config.TextColumn(du_labels["weapon"], width="small"),
            "hs": st.column_config.Column(du_labels["hs"], width="small"),
            "distance": st.column_config.NumberColumn(
                du_labels["distance"], format="%d u"
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
            """A row click opens that duel in the Timeline right away
            (row order == my_duels)."""
            try:
                sel = st.session_state["du_table"]
            except KeyError:
                return
            rows = list(sel.selection.rows or [])
            if rows and 0 <= rows[0] < len(my_duels):
                st.session_state["tl_duel"] = int(rows[0])
                st.session_state["page"] = "timeline"

        st.dataframe(
            disp,
            column_config={du_labels[k]: du_cfg_all[k] for k in _du_shown},
            hide_index=True,
            width="stretch",
            on_select=_duel_row_click,
            selection_mode="single-row",
            key="du_table",
        )
        st.caption(t("du_click_hint"))

# ------------------------------------------------------------- timeline
# a fragment - picking another duel re-renders only the timeline
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
            # your signals in the accent colour ("you"), one per row
            fig.add_trace(
                go.Scatter(
                    x=x, y=speed, mode="lines", line=dict(color=PAL["you"], width=2)
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=x, y=yaw, mode="lines", line=dict(color=PAL["you"], width=2)
                ),
                row=2,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=x, y=pitch, mode="lines", line=dict(color=PAL["you"], width=2)
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
                        marker=dict(symbol="star", size=11, color=PAL["good"]),
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
                        marker=dict(symbol="triangle-up", size=9, color=PAL["ctx"]),
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
                        line_color=PAL["ctx"],
                        annotation_text=t("ch_event"),
                        row=1,
                        col=1,
                    )
                else:
                    fig.add_vline(
                        x=0,
                        line_dash="dash",
                        line_color=PAL["ctx"],
                        row=row_i,
                        col=1,
                    )
            fig.update_xaxes(title_text=t("ch_x"), row=rows_n, col=1)
            # shared look (palette, font, legend, margins); the height
            # stays 760 — four stacked rows need it
            style_figure(fig, height=760, margin=dict(t=60, r=10), legend_y=1.06)
            fig.update_layout(showlegend=bool(fire_pts or hit_pts))
            st.plotly_chart(fig, width="stretch")

if page == "timeline":
    _timeline_fragment()

# ---------------------------------------------------------------- guide
if page == "guide":
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

st.divider()
st.caption(
    f"CS2-AimAudit · {_app_version()} · {t('footer_privacy')} · "
    f"[{t('footer_issues')}](https://github.com/choompuy/cs2-aimaudit/issues)"
)
