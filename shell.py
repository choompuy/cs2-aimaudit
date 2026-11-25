"""
The app shell: the sidebar (navigation + language), demo loading, the
context row, the match header.

Navigation is native (see docs/ARCHITECTURE.md): st.navigation registers
the four pages (hidden - the rail is the app's own st.page_link list, so
the language switcher can sit underneath it) and the page itself lives in
the URL path. A match is opened from the Matches list through go_match(),
which sets `match` / `match_tab` and the shareable ?m=; the Matches page
re-applies ?m / ?tab from the URL on every run.

The functions fill the shared Ctx as the run progresses, in a fixed call
order (widget order - and so widget state - depends on it).
"""

import html
import os
import re

import pandas as pd
import streamlit as st

import context
import core
import i18n
import nativedialog
import state
import store
import ui.format
from context import _status, get_demo, get_duels, get_shots

# ------------------------------------------------------------- navigation
# The two-level model lives in state.py (the registry): `page` mirrors the
# st.navigation page that is running; `match` (a library demo id) and
# `match_tab` live inside the Matches page - a match is always opened FROM
# the list, never from the sidebar. Re-exported here: tests and the
# sidebar read shell.NAV_*.
NAV_PAGES = state.NAV_PAGES
MATCH_TABS = state.MATCH_TABS
go_tab = state.go_tab
leave_match = state.leave_match

# The st.Page registry of the running app, filled by navigation() before
# anything can switch pages (st.switch_page and the rail's st.page_link
# resolve against it). Refilled on every run, so the closures it hands out
# are always this run's.
_PAGES: dict[str, "st.Page"] = {}

NAV_ICONS = {
    "dashboard": ":material/dashboard:",
    "matches": ":material/sports_esports:",
    "settings": ":material/settings:",
    "guide": ":material/menu_book:",
}

# The analysis sliders (Settings page) defaults; the widgets own the keys
# wherever they render and persist_state="session" carries the values
# across every page switch.
ANALYSIS_DEFAULTS = {"thr": 40, "window_s": 3.0, "safe_dist": 500, "strict_dist": 1500}


def apply_match_url() -> None:
    """?m=<demo id> is the truth about the open match (the Matches page,
    every run): a shared link or a reload reopens it; a rail click - which
    clears the query params - lands on the list. A dead id (the demo left
    the library) cleans itself off the URL.

    ?tab= applies once per session (a copied link says which tab it means);
    afterwards the segmented control owns the tab and shell.sync_nav_url
    mirrors it into the URL - re-applying it every run would clobber the
    tab switches that have not reached the URL yet."""
    m = st.query_params.get("m", "")
    if re.fullmatch(r"[0-9a-f]{16}", m or "") and store.get_demo(m) is not None:
        state.set_match(m)
        state.set_demo_id(m)
        if not st.session_state.get("_url_tab_done"):
            st.session_state["_url_tab_done"] = True
            tab = st.query_params.get("tab")
            if tab in MATCH_TABS:
                state.set_match_tab(tab)
    else:
        if m:
            del st.query_params["m"]  # a dead link must not come back
        state.clear_match()


def sync_nav_url() -> None:
    """Keep `?m=&tab` in step with the session (bookmark / share / reload).
    The page is the URL path; other pages keep the URL clean."""
    want: dict[str, str] = {}
    if state.get_page() == "matches" and state.get_match():
        want["m"] = state.get_match()
        if state.get_match_tab() != "overview":
            want["tab"] = state.get_match_tab()
    for k in ("m", "tab"):
        if k not in want and k in st.query_params:
            del st.query_params[k]
        elif k in want and st.query_params.get(k) != want[k]:
            st.query_params[k] = want[k]


def go_page(page: str) -> None:
    """The one programmatic switch for the top level (the dashboard's and
    the Matches page's buttons; the rail itself is st.page_link).
    'matches' means the LIST: an open match is left behind, exactly like a
    rail click - the back button of the match view is the other way out."""
    if page == "matches":
        state.clear_match()
    st.switch_page(_PAGES[page])


def go_match(demo_id: str, tab: str = "overview") -> None:
    """The one way to open a match: a list row, a fresh download. Sets the
    registry keys and the shareable ?m=; the caller reruns."""
    state.set_match(demo_id)
    state.set_demo_id(demo_id)
    state.set_match_tab(tab)
    sync_nav_url()


def navigation(ctx, page_fns: dict) -> "st.navigation":
    """Register the pages (st.navigation, position="hidden" - the rail is
    our own st.page_link list, so the language switcher sits underneath)
    and fill the run context. Returns the nav object whose .run() draws
    the active page (app.py owns the page bodies; shell never imports
    views, keeping the import graph one-way)."""
    t = i18n.make_t(state.get_lang() or next(iter(i18n.LANGS)))
    context.set_t(t)
    pages = []
    for pid in NAV_PAGES:
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
    state.set_page(next(pid for pid, page in _PAGES.items() if page is pg))
    with st.sidebar:
        for pid in NAV_PAGES:
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
    """The match view header: back to the list, the title, the match
    badges and the match tabs - plain flow, nothing pinned (the filter
    summary row of filters_ui.render follows right below)."""
    t = ctx.t
    entry = store.get_demo(state.get_match()) or {}
    m = entry.get("match") or {}
    score = m.get("score") if isinstance(m.get("score"), (list, tuple)) else None
    result = m.get("result")
    # the title: the score when it is known (provider-bound matches; the
    # summary cache fills own demos in later), else the map / file name -
    # the result itself is a badge, not a word in the title
    if score and len(score) == 2:
        title = f"{score[0]}:{score[1]}"
    else:
        title = (
            ui.format.map_name(entry.get("map")) or entry.get("name") or t("mt_title")
        )
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.button(
            t("back_to_matches"),
            key="mt_back",
            icon=":material/arrow_back:",
            on_click=leave_match,
            width="content",
        )
        st.markdown(f"**{html.escape(str(title))}**")
        _chips(t, entry, m, result)
    # the segmented control of the OLD nav: here it only switches the
    # match tab. st.tabs executes EVERY tab body on every rerun; a
    # segmented control bound to the registry key `match_tab` renders
    # only the active one, and code can switch it (the duel window's
    # "show in 2D replay" button does). persist_state="session" keeps
    # the tab alive across the pages that do not render the match view.
    state.set_match_tab(state.get_match_tab())  # a deselect means the default tab
    ctx.tab = (
        st.segmented_control(
            t("nav_pages"),
            MATCH_TABS,
            format_func=lambda p: t(f"tab_{p}"),
            label_visibility="collapsed",
            key="match_tab",
            persist_state="session",
        )
        or "overview"
    )


def _chips(t, entry: dict, m: dict, result) -> None:
    """The match badges (st.badge, the semantic palette): the result
    green / red / gray, the kind violet when the match came from a
    provider, everything else gray - colour never stands alone, every
    badge carries its label."""
    from datetime import datetime

    res_color = {"win": "green", "loss": "red", "tie": "gray"}.get(result or "")
    if res_color:
        st.badge(t(f"mt_result_{result}"), color=res_color)
    mp = ui.format.map_name(entry.get("map"))
    if mp:
        st.badge(mp, color="gray")
    if m.get("mode"):
        st.badge(str(m["mode"]), color="gray")
    provider = str(m.get("provider") or "")
    kind = core.kind_of(provider or None, m.get("mode"))
    st.badge(t(f"kind_{kind}"), color="violet" if provider else "gray")
    when = None
    for iso in (m.get("played_at"), entry.get("opened"), entry.get("added")):
        try:
            when = datetime.fromisoformat(str(iso))
            break
        except (TypeError, ValueError):
            continue
    if when is not None:
        st.badge(when.astimezone().strftime("%d.%m.%y %H:%M"), color="gray")


def match_stats(ctx) -> None:
    """The per-match summary at open (the demo is parsed already, so the
    compute is cheap): fill it for the resolved 'me', or ask who 'me' is -
    once per demo, the answer is remembered (globally or for this match,
    see _ask_who)."""
    t = ctx.t
    demo = ctx.demo
    entry = ctx.entry
    accounts = store.read_accounts()
    me_sid = core.matchstats.resolve_me(demo, entry, accounts)
    if me_sid is None:
        _ask_who(t, demo, entry)
        return
    with st.spinner(t("mt_summarizing")):
        core.matchstats.ensure(demo, entry, me_sid)


def _remember_me(me_sid: str) -> None:
    """Save the picked identity globally: the accounts' SteamID64 - every
    match resolves on its own from now on, and the Settings field follows."""
    accounts = store.read_accounts()
    accounts["steam_id64"] = me_sid
    accounts.setdefault("version", 1)
    store.write_accounts(accounts)
    # the Settings field follows, so the saved id is visible (and editable)
    # there; the widget reads its state on the next render
    st.session_state["mt_steamid"] = me_sid


def _ask_who(t, demo, entry: dict) -> None:
    """'Which player are you?' - asked once per demo; the pick is remembered
    globally (the Settings SteamID64) or for this demo only, and the summary
    follows on the next run."""
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
    state.set_me(me_sid)


def _change_me(demo_id: str, me_sid: str, remember: bool) -> None:
    """The identity menu's answer ("this is not me"): like _set_me, but the
    demo already carries a summary for the other player - drop it so the
    next run recomputes every number for the new one."""
    entry = store.get_demo(demo_id) or {}
    if remember:
        _remember_me(me_sid)
        # a stale per-match pick must not keep winning over the new global
        store.update_meta(demo_id, me_sid=None)
    else:
        store.update_meta(demo_id, me_sid=me_sid)
    core.matchstats.drop_cached(store.demo_path(entry))
    state.set_me(me_sid)


# ----------------------------------------------------- library actions
# Shared by the demo-change block (match view) and the Settings page's
# Library and storage section: one definition, no duplicated keys.
def _ask_delete(did: str) -> None:
    st.session_state["del_pending"] = did


def _cancel_delete() -> None:
    st.session_state.pop("del_pending", None)


def confirm_delete(did: str) -> None:
    """Remove a demo from the library; an open match that pointed at it
    closes back to the Matches list."""
    freed = store.delete_demo(did)
    st.session_state.pop("del_pending", None)
    for k in ("demo_id", "demo_loaded"):
        if st.session_state.get(k) == did:
            st.session_state.pop(k, None)
    if state.get_match() == did:
        state.clear_match()
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
        state.set_pick_err(t("lib_no_dialog"))
    elif chosen:
        try:
            store.relink(did, chosen[0])
        except (ValueError, OSError) as e:
            state.set_pick_err(str(e))
            return
        if state.get_demo_loaded() == did:
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
    """Where the files are, how big, remove / delete a demo, clear the cache.
    Rendered inside the Settings page's bordered section (border=False here
    on purpose: the section already draws the frame)."""
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


def _resolve_demo_id(saved: dict, lib: list) -> None:
    """Defensive: a demo_id this library does not have (deleted in
    Settings mid-session) falls back to the last opened one."""
    by_id = {e["id"]: e for e in lib}
    if lib and (state.get_demo_id() or "") not in by_id:
        _last = saved.get("last_demo")
        state.set_demo_id(_last if _last in by_id else lib[0]["id"])


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
            on_click=leave_match,
        )
    st.stop()


def _load_or_stop(t, path: str, mtime: float, fresh_load: bool):
    """get_demo with the loading-status plate; st.stop()s on failure.

    The progress plate is created only while this exact file is not loaded
    yet; every later rerun skips it, so the compact header row sits at the
    top instead of the "Demo loaded" box."""
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
    """
    Load the open match's demo and build the cached base tables.

    Adding demos lives on the Matches page, the library on Settings; a
    match is always opened through go_match (which sets demo_id), so this
    only resolves the entry, says a clear word about a missing file, and
    loads.
    """
    t = ctx.t
    thr = ctx.thr
    window_s = ctx.window_s
    safe_dist = ctx.safe_dist
    strict_dist = ctx.strict_dist

    saved = core.load_state()
    lib = store.list_demos()
    by_id = {e["id"]: e for e in lib}
    _loaded_id = state.get_demo_loaded()
    _resolve_demo_id(saved, lib)

    entry = by_id[state.get_demo_id() or ""]
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

    # remember what this demo is (map, tick rate, players) in the library, mark
    # it as the latest opened and make it the one that opens next time
    if fresh_load:
        store.touch(entry["id"])
        store.mark_analyzed(entry["id"])
    store.update_meta(
        entry["id"],
        map=demo.header.get("map_name") or None,
        tickrate=int(demo.tickrate),
        players=[str(n) for n in demo.players["name"]],
    )
    if saved.get("last_demo") != entry["id"]:
        saved["last_demo"] = entry["id"]
        core.save_state(saved)
    # the demo is now loaded: remember it and fold the "Change demo" panel
    state.set_demo_loaded(entry["id"])
    if fresh_load:
        # fold the loading UI into the compact header immediately: without
        # this the loading state stays on screen until the next interaction
        st.rerun()

    # -------------------------------------------------------------- metrics
    if status is not None:
        status.update(label=t("step_metrics"))
    shots = get_shots(path, mtime, thr, safe_dist, strict_dist)
    duels_all = get_duels(path, mtime, window_s)

    ctx.state = saved
    ctx.status = status
    ctx.entry = entry
    ctx.demo = demo
    ctx.path = path
    ctx.mtime = mtime
    ctx.shots = shots
    ctx.duels_all = duels_all


def _demo_info_line(entry: dict, demo: core.Demo, t) -> None:
    """The compact demo row: one line with what matters about the file (the
    way out is "← Матчи" in the match header, not a change-demo button)."""
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
        st.caption(
            f'<span title="{html.escape(t("excl_help"))}">'
            f"{html.escape(t('excluded_line', parts=', '.join(_excl_parts)))}</span>",
            unsafe_allow_html=True,
        )


def _player_row(ctx, saved: dict, demo: core.Demo, t) -> None:
    """The compact player row: [label] [prev] [selectbox] [next]. Up/Down
    are the player keys: the Duels page uses Left/Right and
    Shift+Left/Right, so the three steppers never collide."""
    name_of = dict(zip(demo.players["sid"], demo.players["name"], strict=True))
    team_of = dict(zip(demo.players["sid"], demo.players["team"], strict=True))
    plist = demo.players.assign(
        _n=demo.players["name"].astype(str).str.lower()
    ).sort_values(["team", "_n"])
    me_opts = plist["sid"].tolist()
    # preselect the remembered player (and drop a stale pick from another demo)
    if state.get_me() not in me_opts:
        last = saved.get("last_player")
        state.set_me(last if last in me_opts else me_opts[0])

    def _me_step(delta: int):
        """Buttons/hotkeys cycle the player selectbox (wraps around)."""
        cur = state.get_me()
        if cur in me_opts:
            i = (me_opts.index(cur) + delta) % len(me_opts)
        else:
            i = 0 if delta > 0 else len(me_opts) - 1
        state.set_me(me_opts[i])

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
        _who_menu(t, demo, me_opts, name_of)
    if saved.get("last_player") != me:
        saved["last_player"] = me
        core.save_state(saved)

    ctx.me = me
    ctx.name_of = name_of


def _who_menu(t, demo, me_opts: list, name_of: dict) -> None:
    """The "⋮" identity menu of the player row: "this is not me" - pick
    another player and the whole match (the summary of the Matches table
    included) is recomputed for the new one. The pick lands in Settings
    (every match) or only in this match, exactly like the ask card."""
    entry = store.get_demo(state.get_match()) or {}
    accounts = store.read_accounts()
    cur = core.matchstats.resolve_me(demo, entry, accounts)
    cur_name = name_of.get(cur, cur) if cur else None
    with st.popover(
        "",
        icon=":material/more_vert:",
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
    """The compact header: demo info, the player row, the excluded rounds."""
    demo = ctx.demo
    t = ctx.t
    _demo_info_line(ctx.entry, demo, t)
    _excluded_line(demo, t)
    _player_row(ctx, ctx.state, demo, t)
