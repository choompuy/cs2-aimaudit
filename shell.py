"""The app shell: navigation, demo loading, the context row, the match header.

The functions fill the shared Ctx in a fixed call order (widget order, and so widget
state, depends on it)."""

import os
import re

import streamlit as st

import context
import core
import i18n
import nativedialog
import state
import store
import ui.format
from context import _status, get_demo, get_duels, get_hurts, get_shots

# ------------------------------------------------------------- navigation

# this run's st.Page registry, filled by navigation()
_PAGES: dict[str, "st.Page"] = {}

NAV_ICONS = {
    "dashboard": ":material/dashboard:",
    "matches": ":material/sports_esports:",
    "settings": ":material/settings:",
    "guide": ":material/menu_book:",
}

# the analysis sliders of the Settings page
ANALYSIS_DEFAULTS = {"thr": 40, "window_s": 3.0, "safe_dist": 500, "strict_dist": 1500}


def apply_match_url() -> None:
    """?m=<demo id> is the truth about the open match: a link or a reload reopens it, a
    rail click (no params) lands on the list, a dead id is removed.

    ?tab= applies once per session: afterwards it would clobber tab switches that have
    not reached the URL yet."""
    m = st.query_params.get("m", "")
    if re.fullmatch(r"[0-9a-f]{16}", m or "") and store.get_demo(m) is not None:
        state.MATCH.set(m)
        state.DEMO_ID.set(m)
        if not st.session_state.get("_url_tab_done"):
            st.session_state["_url_tab_done"] = True
            tab = st.query_params.get("tab")
            if tab in state.MATCH_TABS:
                state.MATCH_TAB.set(tab)
    else:
        if m:
            del st.query_params["m"]  # a dead link must not come back
        state.MATCH.pop()


def sync_nav_url() -> None:
    """Keep `?m=&tab` in step with the session (bookmark / share / reload).
    The page is the URL path; other pages keep the URL clean."""
    want: dict[str, str] = {}
    if state.PAGE.get() == "matches" and state.MATCH.get():
        want["m"] = state.MATCH.get()
        if state.MATCH_TAB.get() != "overview":
            want["tab"] = state.MATCH_TAB.get()
    for k in ("m", "tab"):
        if k not in want and k in st.query_params:
            del st.query_params[k]
        elif k in want and st.query_params.get(k) != want[k]:
            st.query_params[k] = want[k]


def go_page(page: str) -> None:
    """Switch the page from code; 'matches' means the LIST, like a rail click."""
    if page == "matches":
        state.MATCH.pop()
    st.switch_page(_PAGES[page])


def go_match(demo_id: str, tab: str = "overview") -> None:
    """The one way to open a match: a list row, a fresh download. Sets the
    registry keys and the shareable ?m=; the caller reruns."""
    state.MATCH.set(demo_id)
    state.DEMO_ID.set(demo_id)
    state.MATCH_TAB.set(tab)
    sync_nav_url()


def navigation(ctx, page_fns: dict) -> "st.navigation":
    """Register the pages (hidden: the rail is our own st.page_link list) and fill the run
    context; the returned object's .run() draws the active page."""
    t = i18n.make_t(state.LANG.get() or next(iter(i18n.LANGS)))
    context.set_t(t)
    pages = []
    for pid in state.NAV_PAGES:
        page = st.Page(
            lambda fn=page_fns[pid]: fn(ctx),
            title=t(f"nav_{pid}"),
            icon=NAV_ICONS[pid],
            url_path=pid,
            default=(pid == "matches"),
        )
        _PAGES[pid] = page
        pages.append(page)
    pg = st.navigation(pages, position="hidden")
    state.PAGE.set(next(pid for pid, page in _PAGES.items() if page is pg))
    with st.sidebar:
        for pid in state.NAV_PAGES:
            st.page_link(_PAGES[pid], label=t(f"nav_{pid}"), icon=NAV_ICONS[pid])
        st.divider()
        st.selectbox(
            t("language"),
            list(i18n.LANGS),
            format_func=lambda k: i18n.LANGS[k],
            key="lang",
        )
    strict_dist = max(
        st.session_state.get("strict_dist", ANALYSIS_DEFAULTS["strict_dist"]),
        st.session_state.get("safe_dist", ANALYSIS_DEFAULTS["safe_dist"]) + 100,
    )

    ctx.t = t
    ctx.thr = st.session_state.get("thr", ANALYSIS_DEFAULTS["thr"])
    ctx.window_s = st.session_state.get("window_s", ANALYSIS_DEFAULTS["window_s"])
    ctx.safe_dist = st.session_state.get("safe_dist", ANALYSIS_DEFAULTS["safe_dist"])
    ctx.strict_dist = strict_dist
    return pg


def match_shell(ctx) -> None:
    """The match view header: back, "map · score", the meta line and the tabs. It renders
    before the demo loads; context_bar() adds the demo info and the player to its rows."""
    t = ctx.t
    ctx.back_row = st.container(
        horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
    )
    with ctx.back_row:
        st.button(
            t("back_to_matches"),
            key="mt_back",
            icon=":material/arrow_back:",
            on_click=state.leave_match,
            width="content",
            type="tertiary",
        )
    ctx.title_slot = st.empty()
    _match_title(ctx)
    ctx.tabs_row = st.container(
        horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
    )
    # not st.tabs: it executes EVERY tab body on every rerun, and code must be able to
    # switch the tab
    state.MATCH_TAB.set(state.MATCH_TAB.get())  # no pick yet means the default tab
    with ctx.tabs_row:
        ctx.tab = st.segmented_control(
            t("nav_pages"),
            state.MATCH_TABS,
            format_func=lambda p: t(f"tab_{p}"),
            label_visibility="collapsed",
            key="match_tab",
            required=True,
            persist_state="session",
        )


def _match_title(ctx) -> None:
    """"map · score", the result and the meta line."""
    t = ctx.t
    entry = store.get_demo(state.MATCH.get())
    # the same merge the Matches table shows
    row = core.build_rows([], [entry], stats_of=core.matchstats.cached_stats)[0]
    title = [ui.format.map_name(row.map_name) or entry.get("name") or t("mt_title")]
    if row.score:
        title.append(f"{row.score[0]}:{row.score[1]}")
    with ctx.title_slot.container(horizontal=True, vertical_alignment="center"):
        st.header(" · ".join(title), anchor=False, width="content")
        # colour never stands alone: the badge has its label
        res_color = {"win": "green", "loss": "red", "tie": "gray"}.get(row.result or "")
        if res_color:
            st.badge(t(f"mt_result_{row.result}"), color=res_color)
        st.caption(_meta_line(t, row))


def _meta_line(t, row: core.MatchRow) -> str:
    """Mode, source and date of the match."""
    parts = [str(row.mode)] if row.mode else []
    parts.append(t(f"kind_{row.kind}"))
    if row.played_at is not None:
        parts.append(row.played_at.astimezone().strftime("%d.%m.%y %H:%M"))
    return " · ".join(parts)


def match_stats(ctx) -> None:
    """Cache the summary for the resolved 'me', or ask who that is."""
    t = ctx.t
    demo = ctx.demo
    entry = ctx.entry
    accounts = store.read_accounts()
    me_sid = core.matchstats.resolve_me(demo, entry, accounts)
    if me_sid is None:
        _ask_who(t, demo, entry)
        return
    known = core.matchstats.read_cached(store.demo_path(entry))
    with st.spinner(t("mt_summarizing")):
        core.matchstats.ensure(demo, entry, me_sid)
    if (known or {}).get("me_sid") != me_sid:
        _match_title(ctx)  # the score is known only now


def _remember_me(me_sid: str) -> None:
    """Save the picked identity globally: the accounts' SteamID64 - every
    match resolves on its own from now on, and the Settings field follows."""
    accounts = store.read_accounts()
    accounts["steam_id64"] = me_sid
    accounts.setdefault("version", 1)
    store.write_accounts(accounts)
    # the Settings field follows
    st.session_state["mt_steamid"] = me_sid


def _ask_who(t, demo, entry: dict) -> None:
    """'Which player are you?' - asked once per demo."""
    names = demo.names
    sids = list(names)
    with st.container(border=True):
        st.caption(t("mt_who_title"))
        st.caption(t("mt_who_hint"))
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            pick = st.selectbox(
                t("player"),
                sids,
                index=0,
                format_func=lambda s: names[s],
                key="mt_who_pick",
                label_visibility="collapsed",
            )
            remember = st.checkbox(
                t("mt_who_remember"),
                value=True,
                key="mt_who_remember",
                help=t("mt_who_remember_hint"),
            )
            st.button(
                t("mt_who_set"),
                key="mt_who_set",
                type="primary",
                icon=":material/person_check:",
                on_click=_set_me,
                args=(entry["id"], pick, remember),
            )


def _set_me(demo_id: str, me_sid: str, remember: bool) -> None:
    """The ask card's answer: save the identity and follow it with the
    player selectbox, so the analysis below opens on the same player."""
    if remember:
        _remember_me(me_sid)
    else:
        store.update_meta(demo_id, me_sid=me_sid)
    state.ME.set(me_sid)


def _change_me(demo_id: str, me_sid: str, remember: bool) -> None:
    """"this is not me": like _set_me, but the summary of the other player is dropped."""
    entry = store.get_demo(demo_id) or {}
    if remember:
        _remember_me(me_sid)
        # a stale per-match pick must not keep winning over the new global
        store.update_meta(demo_id, me_sid=None)
    else:
        store.update_meta(demo_id, me_sid=me_sid)
    core.matchstats.drop_cached(store.demo_path(entry))
    state.ME.set(me_sid)


# ----------------------------------------------------- library actions
def _ask_delete(did: str) -> None:
    st.session_state["del_pending"] = did


def _cancel_delete() -> None:
    st.session_state.pop("del_pending", None)


def confirm_delete(did: str) -> None:
    """Remove a demo from the library; an open match that pointed at it
    closes back to the Matches list."""
    freed = store.delete_demo(did)
    st.session_state.pop("del_pending", None)
    for key in (state.DEMO_ID, state.DEMO_LOADED, state.MATCH):
        if key.get() == did:
            key.pop()
    st.session_state["_freed"] = freed


def _clear_cache() -> None:
    st.session_state["_freed"] = store.clear_cache()


def _clear_icons() -> None:
    """The icons are re-downloadable art, not cache: their own button."""
    st.session_state["_freed"] = store.clear_icons()
    import ui.icons

    ui.icons.reset_cache()


def locate_demo(t, did: str) -> None:
    """A linked demo was moved: ask where it is now and point the entry there."""
    chosen = nativedialog.pick_files(
        t("lib_locate"), t("lib_dialog_demos"), t("lib_dialog_all")
    )
    if chosen is None:
        state.PICK_ERR.set(t("lib_no_dialog"))
    elif chosen:
        try:
            store.relink(did, chosen[0])
        except (ValueError, OSError) as e:
            state.PICK_ERR.set(str(e))
            return
        if state.DEMO_LOADED.get() == did:
            st.session_state.pop(state.DEMO_LOADED)  # load it again from the new place


def _lib_entry_row(t, e: dict) -> None:
    """One library entry: the name/size line and the remove / delete
    buttons its mode allows (a linked demo only drops the list entry)."""
    c1, c2 = st.columns([5, 3], vertical_alignment="center")
    warn = ":material/warning: " if e["status"] == "missing" else ""
    kind = t("lib_badge_link") if e["mode"] == "link" else t("lib_badge_copy")
    c1.markdown(
        f"{warn}**{e['name']}** · {e.get('map') or '—'} · "
        f"{store.human_size(e['size'])} · {kind}"
    )
    if e["mode"] == "link":
        # a linked demo: only the list entry goes, the user's file stays
        c1.caption(e["path"])
        if e["status"] == "missing":
            c2.button(
                t("lib_locate"),
                key=f"loc_{e['id']}",
                on_click=locate_demo,
                args=(t, e["id"]),
            )
        c2.button(
            t("lib_remove"),
            key=f"rm_{e['id']}",
            on_click=confirm_delete,
            args=(e["id"],),
            help=t("lib_remove_help"),
        )
    elif st.session_state.get("del_pending") == e["id"]:
        c2.button(
            t("lib_delete_yes"),
            key=f"delyes_{e['id']}",
            on_click=confirm_delete,
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


def library_panel(t, entries: list[dict]) -> None:
    """Where the files are, how big, delete a demo, clear the caches."""
    rep = store.storage_report()
    st.caption(t("lib_folder"))
    st.code(rep["data"], language=None)
    if rep["fallback"]:
        st.warning(t("lib_fallback_warn", path=rep["data"]))
    st.caption(
        t(
            "lib_sizes",
            demos=store.human_size(rep["demos"]),
            linked_n=rep["linked_count"],
            linked=store.human_size(rep["linked"]),
            parsed=store.human_size(rep["parsed"]),
            unpacked=store.human_size(rep["unpacked"]),
            radars=store.human_size(rep["radars"]),
            icons=store.human_size(rep["icons"]),
        )
    )
    st.caption(t("lib_by_hand"))
    for e in entries:
        _lib_entry_row(t, e)
    st.button(
        t("lib_clear"),
        key="lib_clear",
        on_click=_clear_cache,
        help=t("lib_clear_help"),
    )
    st.button(
        t("lib_clear_icons"),
        key="lib_clear_icons",
        on_click=_clear_icons,
        help=t("lib_clear_icons_help"),
    )
    if "_freed" in st.session_state:
        st.success(
            t(
                "lib_freed",
                size=store.human_size(st.session_state.pop("_freed")),
            )
        )


def _missing_demo_card(t, entry: dict) -> None:
    """A linked demo whose file is gone: a clear card with the three
    ways out (locate it, drop the entry, back to the list), not a crash."""
    with st.container(border=True):
        st.error(t("lib_missing", path=entry["path"]))
        m1, m2, m3 = st.columns(3)
        m1.button(
            t("lib_locate"),
            key="lib_locate",
            on_click=locate_demo,
            args=(t, entry["id"]),
        )
        m2.button(
            t("lib_remove"),
            key="lib_remove_missing",
            on_click=confirm_delete,
            args=(entry["id"],),
            help=t("lib_remove_help"),
        )
        m3.button(
            t("back_to_matches"),
            key="mt_back_missing",
            icon=":material/arrow_back:",
            on_click=state.leave_match,
        )
    st.stop()


def _load_or_stop(t, path: str, mtime: float, fresh_load: bool):
    """get_demo with a loading status (only while this file is not loaded yet); st.stop()s
    on failure."""
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
    return demo, status


def open_demo(ctx) -> None:
    """Load the open match's demo and build the cached base tables."""
    t = ctx.t
    thr = ctx.thr
    window_s = ctx.window_s
    safe_dist = ctx.safe_dist
    strict_dist = ctx.strict_dist

    saved = core.load_state()
    lib = store.list_demos()
    by_id = {e["id"]: e for e in lib}
    _loaded_id = state.DEMO_LOADED.get()
    entry = by_id.get(state.DEMO_ID.get())
    if entry is None:  # the demo left the library since the list was drawn
        state.leave_match()
        st.rerun()
    if entry["status"] == "missing":
        _missing_demo_card(t, entry)
    src = store.demo_path(entry)
    try:
        path = core.decompressed_demo(src, tag=entry["id"][:8])
    except ValueError as e:
        st.error(t("load_failed"))
        st.caption(str(e))
        st.stop()
    mtime = os.path.getmtime(path)

    fresh_load = entry["id"] != _loaded_id
    demo, status = _load_or_stop(t, path, mtime, fresh_load)

    if fresh_load:
        store.touch(entry["id"])
        store.mark_analyzed(entry["id"])
    store.update_meta(
        entry["id"],
        map=demo.header.get("map_name") or None,
        tickrate=int(demo.tickrate),
        players=[str(n) for n in demo.players["name"]],
    )
    # the demo is now loaded: remember it and fold the "Change demo" panel
    state.DEMO_LOADED.set(entry["id"])
    if fresh_load:
        # replace the loading state with the compact header right away
        st.rerun()

    # -------------------------------------------------------------- metrics
    if status is not None:
        status.update(label=t("step_metrics"))
    shots = get_shots(path, mtime, thr, safe_dist, strict_dist)
    hurts = get_hurts(path, mtime)
    duels_all = get_duels(path, mtime, window_s)

    ctx.state = saved
    ctx.status = status
    ctx.entry = entry
    ctx.demo = demo
    ctx.path = path
    ctx.mtime = mtime
    ctx.shots = shots
    ctx.hurts = hurts
    ctx.duels_all = duels_all


def _demo_info(ctx) -> None:
    """The file behind the match, out of the way in a popover."""
    t, demo = ctx.t, ctx.demo
    info = [
        f"**{ctx.entry['name']}**",
        f"{t('map')}: {demo.header.get('map_name') or '—'}",
        f"{t('players_n')}: {len(demo.players)}",
        f"{t('rounds_n')}: {len(demo.round_windows)}",
        f"{t('tickrate')}: {demo.tickrate}",
    ]
    with ctx.back_row, st.popover(t("demo_info"), icon=":material/info:", type="tertiary"):
        for line in info:
            st.caption(line)


def _excluded_line(demo: core.Demo, t) -> None:
    """Pre-match rounds that were dropped (knife round / warmup / restarts)."""
    _excl = getattr(demo, "excluded", {}) or {}
    _excl_parts = []
    if _excl.get("warmup"):
        _excl_parts.append(t("excl_warmup", n=_excl["warmup"]))
    if _excl.get("knife_round"):
        _excl_parts.append(t("excl_knife", n=_excl["knife_round"]))
    if _excl.get("restart"):
        _excl_parts.append(t("excl_restart", n=_excl["restart"]))
    if _excl_parts:
        st.caption(t("excluded_line", parts=", ".join(_excl_parts)), help=t("excl_help"))


def _player_row(ctx, saved: dict, demo: core.Demo, t) -> None:
    """[selectbox] [prev] [next] [identity]; Up/Down, since the duels use Left/Right."""
    name_of = dict(zip(demo.players["sid"], demo.players["name"], strict=True))
    team_of = dict(zip(demo.players["sid"], demo.players["team"], strict=True))
    plist = demo.players.assign(
        _n=demo.players["name"].astype(str).str.lower()
    ).sort_values(["team", "_n"])
    me_opts = plist["sid"].tolist()
    # preselect the remembered player (and drop a stale pick from another demo)
    if state.ME.get() not in me_opts:
        last = saved.get("last_player")
        state.ME.set(last if last in me_opts else me_opts[0])

    def _me_step(delta: int):
        """Buttons/hotkeys cycle the player selectbox (wraps around)."""
        cur = state.ME.get()
        if cur in me_opts:
            i = (me_opts.index(cur) + delta) % len(me_opts)
        else:
            i = 0 if delta > 0 else len(me_opts) - 1
        state.ME.set(me_opts[i])

    with st.container(
        horizontal=True, gap="xsmall", vertical_alignment="center", width="content"
    ):
        me = st.selectbox(
            t("player"),
            me_opts,
            format_func=lambda s: f"{name_of.get(s, s)} ({team_of.get(s, '?')})",
            key="me",
            label_visibility="collapsed",
            icon=":material/person:",
            width=240,
        )
        for delta, key, shortcut in ((-1, "me_prev", "Up"), (1, "me_next", "Down")):
            # no label of its own: Streamlit draws the shortcut's arrow
            st.button(
                "",
                key=key,
                on_click=_me_step,
                args=(delta,),
                help=t("player_prev") if delta < 0 else t("player_next"),
                shortcut=shortcut,
                width="content",
            )
        _who_menu(t, demo, me_opts, name_of)
    if saved.get("last_player") != me:
        saved["last_player"] = me
        core.save_state(saved)

    ctx.me = me
    ctx.name_of = name_of


def _who_menu(t, demo, me_opts: list, name_of: dict) -> None:
    """The identity menu: "this is not me", for every match or only for this one."""
    entry = store.get_demo(state.MATCH.get()) or {}
    accounts = store.read_accounts()
    cur = core.matchstats.resolve_me(demo, entry, accounts)
    cur_name = name_of.get(cur, cur) if cur else None
    with st.popover(
        "",
        icon=":material/manage_accounts:",
        key="mt_who_pop",
        help=t("mt_me_title"),
        width="content",
    ):
        st.caption(t("mt_me_title"))
        if cur_name:
            scope = (
                t("mt_me_from_settings")
                if str(accounts.get("steam_id64") or "").strip() == cur
                and not entry.get("me_sid")
                else t("mt_me_from_match")
            )
            st.caption(t("mt_me_current", name=cur_name, scope=scope))
        else:
            st.caption(t("mt_me_unset"))
        st.caption(t("mt_me_hint"))
        idx = me_opts.index(cur) if cur in me_opts else 0
        pick = st.selectbox(
            t("player"),
            me_opts,
            index=idx,
            format_func=lambda s: name_of.get(s, s),
            key="mt_me_pick",
            label_visibility="collapsed",
        )
        remember = st.checkbox(
            t("mt_who_remember"),
            value=True,
            key="mt_me_remember",
            help=t("mt_who_remember_hint"),
        )
        st.button(
            t("mt_who_set"),
            key="mt_me_set",
            type="primary",
            icon=":material/person_check:",
            on_click=_change_me,
            args=(entry.get("id") or "", pick, remember),
        )


def context_bar(ctx) -> None:
    """The header parts that need the demo: its info, the player, the excluded rounds."""
    demo = ctx.demo
    t = ctx.t
    _demo_info(ctx)
    with ctx.tabs_row:
        _player_row(ctx, ctx.state, demo, t)
    _excluded_line(demo, t)
