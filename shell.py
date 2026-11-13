"""
The app shell: sidebar, demo library, loading, context row, navigation.

The functions fill the shared Ctx as the run progresses, in a fixed call
order (widget order - and so widget state - depends on it).
"""

import html
import os

import pandas as pd
import streamlit as st

import context
import core
import i18n
import store
from context import _status, get_demo, get_duels, get_shots


def sidebar(ctx) -> None:
    """Language + analysis settings rail and the app title bar."""
    t = i18n.make_t(st.session_state.get("lang", next(iter(i18n.LANGS))))
    context.set_t(t)

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

            st.button(
                t("reset_defaults"), on_click=_reset_settings, key="btn_reset_set"
            )
        strict_dist = max(strict_dist, safe_dist + 100)

    st.markdown(
        f'<div class="aa-app-title" role="heading" aria-level="1">'
        f'<span class="aa-app-name">🎯 CS2-AimAudit</span>'
        f'<span class="aa-app-tag">{html.escape(t("tagline"))}</span></div>',
        unsafe_allow_html=True,
    )
    ctx.t = t
    ctx.thr = thr
    ctx.window_s = window_s
    ctx.safe_dist = safe_dist
    ctx.strict_dist = strict_dist


def open_demo(ctx) -> None:
    """Demo library, upload, the load itself and the cached base tables."""
    t = ctx.t
    thr = ctx.thr
    window_s = ctx.window_s
    safe_dist = ctx.safe_dist
    strict_dist = ctx.strict_dist

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
                        t("lib_delete_no"),
                        key=f"delno_{e['id']}",
                        on_click=_cancel_delete,
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
                    t(
                        "lib_freed",
                        size=store.human_size(st.session_state.pop("_freed")),
                    )
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

    ctx.state = state
    ctx.status = status
    ctx.entry = entry
    ctx.change_open = _change_open
    ctx.demo = demo
    ctx.path = path
    ctx.mtime = mtime
    ctx.shots = shots
    ctx.duels_all = duels_all


def context_bar(ctx) -> None:
    """The compact header: demo info, the player row, the excluded rounds."""
    t = ctx.t
    demo = ctx.demo
    entry = ctx.entry
    state = ctx.state
    _change_open = ctx.change_open

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

    ctx.me = me
    ctx.name_of = name_of


def nav(ctx) -> None:
    """The page segmented control inside the sticky header row."""
    t = ctx.t
    _hdr = ctx.hdr

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

    ctx.page = page
