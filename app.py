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

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
from plotly.subplots import make_subplots

import core
import i18n
import mapview
import store
import theme
from ui import schema
from ui.roundrep import round_payload, round_replay
from ui.strip import duel_strip

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
        # a link that restores filters opens with the card expanded, so
        # the filters are visible instead of hiding behind the summary
        if (
            saved.get("weapons")
            or saved.get("classes")
            or saved.get("rounds")
            or saved.get("preset") not in (None, "all")
            or saved.get("side") not in (None, "all")
            or saved.get("res") not in (None, "both")
            or saved.get("dist") not in (None, "all")
        ):
            st.session_state["flt_open"] = True

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

st.markdown(
    f'<div class="aa-app-title" role="heading" aria-level="1">'
    f'<span class="aa-app-name">🎯 CS2-AimAudit</span>'
    f'<span class="aa-app-tag">{html.escape(t("tagline"))}</span></div>',
    unsafe_allow_html=True,
)

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
    # through the disk cache of the data folder: a demo parsed once opens
    # again instantly, also after the app was restarted
    demo, _from_cache = core.load_demo_cached(path, progress=_say)
    return demo


@st.cache_data(show_spinner=False)
def get_timeline(
    path: str, mtime: float, me: str, tick: int, before_s: float, tickrate: int
):
    return core.timeline_data(path, me, tick, before_s=before_s, tickrate=tickrate)


@st.cache_data(show_spinner=False)
def get_round_replay(
    path: str,
    mtime: float,
    round_no: int,
    me: str,
    opps: tuple[str, ...],
    tickrate: int,
):
    """Tick data of the whole round for the round replay (ui/roundrep.py).

    ``opps`` are ALL duel opponents of the round: their held keys get a
    panel while their duel segment is selected."""
    demo = get_demo(path, mtime)
    return core.round_replay_data(
        path,
        round_no,
        me,
        key_sids=[s for s in (me, *opps) if s],
        tickrate=tickrate,
        fires=demo.fires,
        deaths=demo.deaths,
        hurts=demo.hurts,
    )


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


# ------------------------------------------------------------ demo library
# ONE way to open a demo: add a file (it is saved to the library in the data
# folder next to the launcher) or pick one of the saved demos. There is no
# path field any more: a stale path used to silently win over a new upload.
# The choice lives in plain session keys, not widget states: once a demo is
# loaded the picker is folded into the compact header row, and Streamlit drops
# the state of widgets that did not render.
#   demo_id      the library demo that should be open
#   demo_loaded  the library demo that is loaded (they differ = a fresh load)
#   demo_change  the "Change demo" panel is open
state = core.load_state()
lib = store.list_demos()
by_id = {e["id"]: e for e in lib}
st.session_state.setdefault("up_n", 0)
_loaded_id = st.session_state.get("demo_loaded")
_change_open = bool(st.session_state.get("demo_change"))


def _lib_label(e: dict) -> str:
    return "  ·  ".join(
        [
            e["name"],
            e.get("map") or "—",
            store.human_size(e["size"]),
            str(e.get("added", ""))[:10],
        ]
    )


def _pick_from_library() -> None:
    st.session_state["demo_id"] = st.session_state["lib_pick"]


def _ask_delete(did: str) -> None:
    st.session_state["del_pending"] = did


def _cancel_delete() -> None:
    st.session_state.pop("del_pending", None)


def _confirm_delete(did: str) -> None:
    freed = store.delete_demo(did)
    st.session_state.pop("del_pending", None)
    for k in ("demo_id", "demo_loaded"):
        if st.session_state.get(k) == did:
            st.session_state.pop(k, None)
    st.session_state["_freed"] = freed


def _clear_cache() -> None:
    st.session_state["_freed"] = store.clear_cache()


def _library_panel(entries: list[dict]) -> None:
    """Where the files are, how big, delete a demo, clear the cache."""
    rep = store.storage_report()
    with st.expander(t("lib_manage")):
        st.caption(t("lib_folder"))
        st.code(rep["data"], language=None)
        st.caption(
            t(
                "lib_sizes",
                demos=store.human_size(rep["demos"]),
                parsed=store.human_size(rep["parsed"]),
                unpacked=store.human_size(rep["unpacked"]),
                radars=store.human_size(rep["radars"]),
            )
        )
        st.caption(t("lib_by_hand"))
        for e in entries:
            c1, c2 = st.columns([5, 3], vertical_alignment="center")
            c1.markdown(
                f"**{e['name']}** · {e.get('map') or '—'} · {store.human_size(e['size'])}"
            )
            if st.session_state.get("del_pending") == e["id"]:
                c2.button(
                    t("lib_delete_yes"),
                    key=f"delyes_{e['id']}",
                    on_click=_confirm_delete,
                    args=(e["id"],),
                    type="primary",
                )
                c2.button(
                    t("lib_delete_no"), key=f"delno_{e['id']}", on_click=_cancel_delete
                )
            else:
                c2.button(
                    t("lib_delete"),
                    key=f"del_{e['id']}",
                    on_click=_ask_delete,
                    args=(e["id"],),
                    help=t("lib_delete_help"),
                )
        st.button(
            t("lib_clear"),
            key="lib_clear",
            on_click=_clear_cache,
            help=t("lib_clear_help"),
        )
        if "_freed" in st.session_state:
            st.success(
                t("lib_freed", size=store.human_size(st.session_state.pop("_freed")))
            )


if lib and st.session_state.get("demo_id") not in by_id:
    # first run of a session (or the open demo was deleted): the last used one
    _last = state.get("last_demo")
    st.session_state["demo_id"] = _last if _last in by_id else lib[0]["id"]

# the picker is shown while nothing is loaded yet (also after a failed load,
# so another demo can be tried) and behind the "Change demo" button
if _loaded_id is None or _change_open or not lib:
    if lib:
        _ids = [e["id"] for e in lib]
        st.selectbox(
            t("lib_label"),
            _ids,
            index=_ids.index(st.session_state["demo_id"]),
            format_func=lambda i: _lib_label(by_id[i]),
            key="lib_pick",
            on_change=_pick_from_library,
            help=t("lib_help"),
        )
    up = st.file_uploader(
        t("upload_label"),
        type=["dem", "bz2", "gz", "zst", "zip"],
        key=f"demo_upload_{st.session_state['up_n']}",
    )
    if up is not None:
        # an upload always wins over whatever was selected before: it is saved
        # to the library (a demo already there is recognised by content)
        try:
            with st.spinner(t("lib_saving")):
                added = store.add_demo(up, up.name)
        except (OSError, ValueError) as e:
            st.error(t("lib_save_failed", err=str(e)))
            st.stop()
        st.session_state["demo_id"] = added["id"]
        st.session_state["up_n"] += 1  # a fresh, empty uploader
        st.session_state["_added"] = added["name"]
        st.rerun()
    if lib:
        _library_panel(lib)
if "_added" in st.session_state:
    st.toast(t("lib_added", name=st.session_state.pop("_added")))

if not lib:
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
entry = by_id[st.session_state["demo_id"]]
src = store.demo_path(entry)
try:
    path = core.decompressed_demo(src)
except ValueError as e:
    st.error(t("load_failed"))
    st.caption(str(e))
    st.stop()
mtime = os.path.getmtime(path)

# ----------------------------------------------------------------- load
# The progress plate is created only while this exact file is not loaded
# yet; every later rerun skips it, so the compact header row sits at the
# top instead of the "Demo loaded" box.
fresh_load = entry["id"] != _loaded_id
status = st.status(t("loading")) if fresh_load else None
_status["box"] = status
try:
    demo = get_demo(path, mtime)
except ValueError as e:
    _status["box"] = None
    if status is not None:
        status.update(label=t("load_failed"), state="error")
    if "no_events" in str(e):
        st.error(t("err_no_events"))
    else:
        st.error(t("load_failed"))
        st.caption(str(e))
    st.stop()
except Exception as e:  # noqa: BLE001
    _status["box"] = None
    if status is not None:
        status.update(label=t("load_failed"), state="error")
    st.error(t("load_failed"))
    st.caption(str(e))
    st.stop()
_status["box"] = None

if demo.players.empty:
    if status is not None:
        status.update(label=t("load_failed"), state="error")
    st.error(t("err_no_events"))
    st.stop()

# remember what this demo is (map, tick rate, players) in the library, mark
# it as the latest opened and make it the one that opens next time
if fresh_load:
    store.touch(entry["id"])
store.update_meta(
    entry["id"],
    map=demo.header.get("map_name") or None,
    tickrate=int(demo.tickrate),
    players=[str(n) for n in demo.players["name"]],
)
if state.get("last_demo") != entry["id"]:
    state["last_demo"] = entry["id"]
    core.save_state(state)
# the demo is now loaded: remember it and fold the "Change demo" panel
st.session_state["demo_loaded"] = entry["id"]
if fresh_load:
    st.session_state["demo_change"] = False
    # fold the upload block / progress plate into the compact header
    # immediately: without this the loading UI stays on screen until the
    # next interaction
    st.rerun()

# -------------------------------------------------------------- metrics
if status is not None:
    status.update(label=t("step_metrics"))
shots = get_shots(path, mtime, thr, safe_dist, strict_dist)
duels_all = get_duels(path, mtime, window_s)


def _open_change():
    st.session_state["demo_change"] = True


def _close_change():
    st.session_state["demo_change"] = False


# the compact demo row replaces the upload block, the "Demo loaded"
# plate and the map caption: one line with what matters about the file
info = [f"**{entry['name']}**"]
if demo.header.get("map_name"):
    info.append(f"**{t('map')}:** {demo.header['map_name']}")
info.append(f"**{t('players_n')}:** {len(demo.players)}")
info.append(f"**{t('tickrate')}:** {demo.tickrate}")
trp = demo.deaths.get("total_rounds_played") if len(demo.deaths) else None
if trp is not None:
    r_max = pd.to_numeric(trp, errors="coerce").max()
    if pd.notna(r_max):
        info.append(f"**{t('rounds_n')}:** {int(r_max) + 1}")
with st.container(horizontal=True, gap="small", vertical_alignment="center"):
    st.caption(" · ".join(info))
    if _change_open:
        st.button(
            t("change_cancel"),
            key="demo_cancel",
            on_click=_close_change,
            type="tertiary",
            width="content",
        )
    else:
        st.button(
            t("change_demo"),
            key="demo_change_btn",
            on_click=_open_change,
            type="secondary",
            width="content",
        )

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


def _me_step(delta: int):
    """Buttons/hotkeys cycle the player selectbox (wraps around)."""
    cur = st.session_state.get("me")
    if cur in me_opts:
        i = (me_opts.index(cur) + delta) % len(me_opts)
    else:
        i = 0 if delta > 0 else len(me_opts) - 1
    st.session_state["me"] = me_opts[i]


# One compact row: [Player label] [prev] [selectbox] [next]. Up/Down are
# the player keys on purpose: step 2 takes Left/Right for duels and
# Shift+Left/Right for rounds, so the three steppers never collide.
with st.container(horizontal=True, gap="small", vertical_alignment="center"):
    st.caption(t("player"))
    st.button(
        "▲",
        key="me_prev",
        on_click=_me_step,
        args=(-1,),
        help=t("player_prev"),
        shortcut="Up",
        width="content",
        type="tertiary",
    )
    me = st.selectbox(
        t("player"),
        me_opts,
        format_func=lambda s: f"{name_of.get(s, s)} ({team_of.get(s, '?')})",
        key="me",
        label_visibility="collapsed",
    )
    st.button(
        "▼",
        key="me_next",
        on_click=_me_step,
        args=(1,),
        help=t("player_next"),
        shortcut="Down",
        width="content",
        type="tertiary",
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
# re-assign the keys of the controls this page does not show - and of
# the filter card while it is collapsed - so neither a page switch nor
# folding the card loses a selection
_flt_open = bool(st.session_state.get("flt_open"))
if not show_result:
    st.session_state["flt_res"] = st.session_state.get("flt_res", "both")
if not show_panel or not _flt_open:
    for _k, _d in (
        ("flt_weapons", []),
        ("flt_classes", []),
        ("flt_side", "all"),
        ("flt_preset", "all"),
        ("flt_dist", "all"),
        ("flt_res", "both"),
    ):
        st.session_state[_k] = st.session_state.get(_k, _d)
    if n_rounds >= 2:
        st.session_state["flt_rounds"] = st.session_state.get("flt_rounds") or (
            1,
            n_rounds,
        )

# The filters are resolved BEFORE the panel renders: the collapsed
# summary line needs the filtered counts as well.
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


def _flt_show(is_open: bool):
    st.session_state["flt_open"] = is_open


# how much data is in view: the one caption shared by the collapsed
# summary row and the open card (the per-page "Showing..." bar is gone)
_counts = t("flt_sum_counts", shots=len(shots_f), duels=len(duels_f))
if not filters.empty():
    _counts += " " + t("flt_sum_of", shots=len(shots), duels=len(duels_all))

# The sticky bar: the filter summary row and the page tabs stay
# visible while the page scrolls (position: sticky in
# theme/overrides.css, scoped to this container's key; on Streamlit
# builds without .st-key-* classes the rule simply does not match and
# the bar scrolls away - no fallback hacks).
_hdr = st.container(key="hdr_sticky", gap="xsmall")

if show_panel:
    if _flt_open:
        with st.container(border=True, key="flt_card"):
            with st.container(
                horizontal=True, gap="small", vertical_alignment="center"
            ):
                st.caption(t("flt_title"))
                st.button(
                    t("flt_done"),
                    on_click=_flt_show,
                    args=(False,),
                    key="flt_done",
                    type="tertiary",
                    width="content",
                )
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
            # popover with the single guns, combined with OR (core.Filters
            # unions weapons and classes)
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
            # the range slider exists only in Custom mode (and only when a
            # range is possible at all); moving it keeps Custom selected
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
            # live feedback while editing: the same counts the collapsed
            # summary line shows
            st.caption(_counts)
    else:
        # one line: the active filters, how much is in view, and the
        # tools that used to be separate rows (Reset)
        _sum = (
            t("flt_sum", parts=" · ".join(_label for _label, _ in _chips))
            if _chips
            else t("flt_sum_none")
        )
        with _hdr:
            with st.container(
                horizontal=True, gap="small", vertical_alignment="center"
            ):
                st.caption(f"{_sum} · {_counts}")
                st.button(
                    t("flt_edit"),
                    on_click=_flt_show,
                    args=(True,),
                    key="flt_edit",
                    type="secondary",
                    width="content",
                )
                if _chips:
                    st.button(
                        t("flt_reset"),
                        on_click=_reset_filters,
                        key="flt_reset_sum",
                        type="tertiary",
                        width="content",
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
# round-impact layer: opening duels, trades, multi-kills,
# assists. Round structure, so like the K/D in the summary it follows the
# Side and Rounds filters (a player's rounds on that side), not the
# weapon / distance ones. The openings table on the Duels page is the
# match context of both teams and stays unfiltered.
openings = core.openings_table(demo)
impact = core.impact_stats(demo, f=filters)
if status is not None:
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
with _hdr:
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

    def _pick_player_row(i):
        """Grid row click -> select that player (callback, runs before widgets)."""
        if 0 <= i < len(ov_sids):
            st.session_state["me"] = ov_sids[i]

    schema.table(
        "overview",
        {**ov_cells, "player_raw": ov["player"].astype(str).to_numpy()},
        key="ov_grid",
        selected=sel_row,
        on_click=_pick_player_row,
        all_columns=ov_full,
    )

# -------------------------------------------------------------- shooting
elif page == "shooting":
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
        schema.table(
            "weapons",
            {c: w[c].to_numpy() for c in schema.columns("weapons")},
            key="w_grid",
        )

# ---------------------------------------------------------------- duels
# ~8 columns by default, "All columns" reveals the detail metrics and
# the technical tick column; numbers carry their units; the opponent name
# stays pinned when the full table scrolls horizontally.
elif page == "duels":
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

        def _duel_click_row(i):
            """Grid row click -> open that duel in the Timeline."""
            if 0 <= i < len(my_duels):
                st.session_state["tl_duel"] = int(i)
                st.session_state["page"] = "timeline"

        schema.table(
            "duels",
            {
                **du_cells,
                "result_raw": ddf["result"].astype(str).to_numpy(),
                "first_raw": ddf["first"].astype(str).to_numpy(),
                "hs_raw": ddf["headshot"].astype(int).to_numpy(),
            },
            key="du_grid",
            on_click=_duel_click_row,
            all_columns=du_full,
        )
        st.caption(t("du_click_hint"))

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


@st.fragment
def _timeline_fragment():
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


# ------------------------------------------------------------------- map
@st.fragment
def _map_fragment():
    """Where the selected player killed and died, on the radar (Map page)."""
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
