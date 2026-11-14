"""
The Matches page: recent Valve Matchmaking games, imported on click.

No network happens just by opening the page - only the local cache is read;
every request (Refresh / Download / Install helper) is an explicit button
press. The settings expander is a standalone function so it can move to a
future Settings tab without touching the page body.
"""

from __future__ import annotations

import html
import os
from datetime import datetime, timezone

import streamlit as st
import store
import sources
import sources.http
import sources.inbox
import sources.steam
from sources import model as sm

_RESULT_TONE = {"win": "positive", "loss": "negative", "tie": "info"}
_RESULT_KEY = {"win": "mt_result_win", "loss": "mt_result_loss", "tie": "mt_result_tie"}
_DEMO_TONE = {
    sm.DemoState.READY: "info",
    sm.DemoState.DOWNLOADED: "positive",
    sm.DemoState.MANUAL: "warning",
    sm.DemoState.EXPIRED: "negative",
    sm.DemoState.UNKNOWN: "context",
}
_FILTERS = ("all", "down", "todo")


# ------------------------------------------------------------- shared bits
def _steam_cfg(accounts: dict) -> sm.SteamConfig:
    return sm.SteamConfig(
        steam_id64=accounts.get("steam_id64") or "",
        replays_dir=accounts.get("replays_dir") or "",
    )


def _provider_cache(provider: str) -> list[sm.MatchRecord]:
    cache = store.read_matches_cache()
    pdata = (cache.get("providers") or {}).get(provider) or {}
    out = []
    for d in pdata.get("records") or []:
        try:
            out.append(sm.MatchRecord.from_dict(d))
        except (TypeError, ValueError):
            continue
    return out


def _save_provider_cache(provider: str, records: list[sm.MatchRecord]) -> None:
    cache = store.read_matches_cache()
    cache.setdefault("version", 1)
    cache.setdefault("providers", {})
    cache["providers"][provider] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": [r.to_dict() for r in records],
    }
    store.write_matches_cache(cache)


def _cache_time(provider: str) -> str:
    cache = store.read_matches_cache()
    iso = ((cache.get("providers") or {}).get(provider) or {}).get("updated_at")
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _err_of(provider: str):
    errs = st.session_state.get("mt_errs") or {}
    return errs.get(provider)


def _show_error(t, e) -> None:
    if e is None:
        return
    key, kw = e
    try:
        st.error(t(key, **kw))
    except (KeyError, ValueError, IndexError):
        st.error(t(key))


def _sorted(records: list[sm.MatchRecord]) -> list[sm.MatchRecord]:
    """By date, newest first; undated records keep to the end."""
    return sorted(
        records,
        key=lambda r: r.played_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


# ------------------------------------------------------------ demo deletion
def _ask_delete(demo_id: str) -> None:
    st.session_state["mt_del_pending"] = demo_id


def _cancel_delete() -> None:
    st.session_state.pop("mt_del_pending", None)


def _confirm_delete(demo_id: str) -> None:
    freed = store.delete_demo(demo_id)
    st.session_state.pop("mt_del_pending", None)
    for k in ("demo_id", "demo_loaded"):
        if st.session_state.get(k) == demo_id:
            st.session_state.pop(k, None)
    st.session_state["mt_freed"] = freed


def _delete_control(t, entry: dict) -> None:
    did = entry["id"]
    if st.session_state.get("mt_del_pending") == did:
        st.button(
            t("lib_delete_yes"),
            key=f"mt_delyes_{did}",
            type="primary",
            on_click=_confirm_delete,
            args=(did,),
        )
        st.button(t("lib_delete_no"), key=f"mt_delno_{did}", on_click=_cancel_delete)
    else:
        st.button(
            t("lib_delete"),
            key=f"mt_del_{did}",
            on_click=_ask_delete,
            args=(did,),
            help=t("lib_delete_help"),
        )


def _ask_clear() -> None:
    st.session_state["mt_clear_pending"] = True


def _cancel_clear() -> None:
    st.session_state.pop("mt_clear_pending", None)


def _confirm_clear() -> None:
    freed = store.delete_all_demos()
    st.session_state.pop("mt_clear_pending", None)
    for k in ("demo_id", "demo_loaded"):
        st.session_state.pop(k, None)
    st.session_state["mt_freed"] = freed


def _clear_control(t) -> None:
    """Delete-all with a confirm step; None when the library is empty."""
    lib = store.list_demos()
    if st.session_state.get("mt_clear_pending"):
        with st.container(border=True):
            st.warning(t("mt_clear_q", n=len(lib)))
            c1, c2 = st.columns(2)
            c1.button(
                t("mt_clear_yes"),
                key="mt_clear_yes",
                type="primary",
                on_click=_confirm_clear,
            )
            c2.button(t("lib_delete_no"), key="mt_clear_no", on_click=_cancel_clear)
    elif lib:
        st.button(
            t("mt_clear"),
            key="mt_clear",
            on_click=_ask_clear,
            help=t("lib_delete_help"),
        )


# ------------------------------------------------------------------ header
def _header(t, accounts: dict) -> None:
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        count = accounts.get("match_count") or sm.DEFAULT_MATCH_COUNT
        if count not in sm.MATCH_COUNT_CHOICES:
            count = sm.DEFAULT_MATCH_COUNT
        st.selectbox(
            t("mt_count"),
            list(sm.MATCH_COUNT_CHOICES),
            index=list(sm.MATCH_COUNT_CHOICES).index(count),
            key="mt_count",
        )
        st.button(t("mt_refresh"), key="mt_refresh", type="primary")


# ------------------------------------------------------- settings (movable)
def _settings(t, accounts: dict) -> None:
    """The settings expander - a standalone block so a future Settings tab
    can host it unchanged (see docs/ARCHITECTURE.md)."""
    with st.expander(t("mt_settings")):
        st.text_input(
            t("mt_steamid"),
            value=accounts.get("steam_id64") or "",
            key="mt_steamid",
            help=t("mt_steamid_hint"),
        )
        st.text_input(
            t("mt_replays"),
            value=accounts.get("replays_dir") or "",
            key="mt_replays",
            help=t("mt_replays_hint"),
        )
        st.text_input(
            t("mt_share_code"), value="", key="mt_share_code", help=t("mt_share_hint")
        )
        if st.button(t("mt_share_add"), key="mt_share_add"):
            _add_by_share_code(t)


def _add_by_share_code(t) -> None:
    accounts = _accounts()
    code = (st.session_state.get("mt_share_code") or "").strip()
    if not code:
        return
    cfg = _steam_cfg(accounts)
    try:
        with st.spinner(t("mt_share_add")):
            rec = sources.steam.SteamProvider(
                cfg, transport=sources.http.HttpTransport()
            ).match_by_share_code(code)
    except sm.ProviderError as e:
        _show_error(t, (e.key, e.kw))
        return
    records = _provider_cache("steam")
    records = [r for r in records if r.match_id != rec.match_id]
    records.insert(0, rec)
    _save_provider_cache("steam", _sorted(records))


def _accounts() -> dict:
    """Persist what the settings widgets hold, then return the live values."""
    accounts = store.read_accounts()
    updates = {}
    for field, key in (
        ("steam_id64", "mt_steamid"),
        ("replays_dir", "mt_replays"),
    ):
        v = st.session_state.get(key)
        if v is not None and v != accounts.get(field):
            updates[field] = v
    if updates:
        accounts.update(updates)
        accounts.setdefault("version", 1)
        store.write_accounts(accounts)
    count = st.session_state.get("mt_count")
    if count and count != accounts.get("match_count"):
        accounts["match_count"] = count
        store.write_accounts(accounts)
    return accounts


# ------------------------------------------------------------------ refresh
def _refresh(t, n: int) -> None:
    accounts = _accounts()
    errs = {}
    cfg = _steam_cfg(accounts)
    try:
        records = sources.list_matches("steam", cfg, n)
        known = {r.match_id for r in records}
        for rec in sources.inbox.replays_records(cfg.replays_dir):
            if rec.match_id not in known:
                records.append(rec)
                known.add(rec.match_id)
        _save_provider_cache("steam", _sorted(records))
    except sm.ProviderError as e:
        errs["steam"] = (e.key, e.kw)
    st.session_state["mt_errs"] = errs


# -------------------------------------------------------------------- cards
def _badge(label: str, tone: str) -> str:
    return f'<span class="aa-badge aa-badge-{tone}">{html.escape(str(label))}</span>'


def _match_card(t, rec: sm.MatchRecord) -> None:
    entry = store.find_by_match(rec.provider, rec.match_id)
    demo = rec.demo
    if entry is not None:
        demo = sm.DemoState.DOWNLOADED
    with st.container(border=True):
        info, actions = st.columns([5.4, 1.8], vertical_alignment="center")
        with info:
            when = (
                rec.played_at.astimezone().strftime("%d.%m.%Y %H:%M")
                if rec.played_at
                else "—"
            )
            score = " : ".join(str(s) for s in rec.score) if rec.score else "—"
            st.markdown(
                f"**{html.escape(rec.map_name or '—')}** · {when} · "
                f"{html.escape(rec.mode or '—')} · **{score}**"
            )
            badges = []
            if rec.result:
                badges.append(
                    _badge(t(_RESULT_KEY[rec.result]), _RESULT_TONE[rec.result])
                )
            badges.append(_badge(t(f"mt_demo_{demo}"), _DEMO_TONE.get(demo, "context")))
            st.markdown(" ".join(badges), unsafe_allow_html=True)
            if demo == sm.DemoState.EXPIRED:
                st.caption(t("mt_expired_hint"))
        with actions:
            _actions(t, rec, demo, entry)


def _actions(t, rec, demo, entry) -> None:
    pid = f"{rec.provider}_{rec.match_id}"
    if demo == sm.DemoState.DOWNLOADED and entry is not None:
        st.button(
            t("mt_open"),
            key=f"mt_open_{pid}",
            type="primary",
            on_click=_open_cb,
            args=(entry["id"],),
        )
        _delete_control(t, entry)
        return
    manual_path = rec.demo_ref if demo == sm.DemoState.MANUAL else None
    if demo == sm.DemoState.MANUAL and manual_path and os.path.exists(manual_path):
        st.button(
            t("mt_import"),
            key=f"mt_open_{pid}",
            type="primary",
            on_click=_import_cb,
            args=(rec, manual_path),
        )
        return
    if demo in (sm.DemoState.READY, sm.DemoState.EXPIRED):
        if st.button(t("mt_download"), key=f"mt_dl_{pid}", type="primary"):
            _download(t, rec)
            st.rerun()  # re-render so Open / Delete / Delete-all reflect the new library
        return


def _open_cb(entry_id: str) -> None:
    # a callback: it runs before the nav widget re-instantiates, which is the
    # only legal moment to re-assign the page key from code
    st.session_state["demo_id"] = entry_id
    st.session_state["page"] = "overview"
    st.session_state["demo_change"] = False


def _import_cb(rec: sm.MatchRecord, path: str) -> None:
    try:
        entry = sources.import_local(rec, path)
    except (OSError, ValueError) as e:
        st.session_state["mt_import_err"] = str(e)
        return
    _open_cb(entry["id"])


def _show_import_error(t) -> None:
    if "mt_import_err" in st.session_state:
        st.error(t("lib_save_failed", err=st.session_state.pop("mt_import_err")))


def _download(t, rec: sm.MatchRecord) -> None:
    if store.find_by_match(rec.provider, rec.match_id) is not None:
        st.toast(t("mt_dl_exists"))
        return
    cfg = _steam_cfg(_accounts())
    status = st.status(t("mt_dl_status"))
    bar = st.progress(0.0)

    def _progress(done: int, total) -> None:
        frac = min(0.99, done / total) if total else min(0.99, done / 60_000_000)
        shown = (
            f"{store.human_size(done)} / {store.human_size(total)}"
            if total
            else store.human_size(done)
        )
        bar.progress(frac, text=shown)

    try:
        with status:
            entry = sources.fetch_demo(rec, cfg, progress=_progress)
    except sm.ProviderError as e:
        status.update(label=t("mt_dl_status"), state="error")
        _show_error(t, (e.key, e.kw))
        return
    status.update(label=t("mt_dl_done", name=entry["name"]), state="complete")
    st.toast(t("mt_dl_done", name=entry["name"]))


# ---------------------------------------------------------------- sections
def _caption_time(t, provider: str) -> None:
    when = _cache_time(provider)
    if not when:
        return
    if _err_of(provider) is None:
        st.caption(t("mt_updated", time=when))
    else:
        st.caption(t("mt_cached", time=when))


def _steam_section(t, accounts: dict) -> None:
    _show_error(t, _err_of("steam"))
    if not sources.steam.helper_installed():
        with st.container(border=True):
            st.subheader(t("mt_helper_title"))
            st.caption(t("mt_helper_what"))
            st.caption(
                t(
                    "mt_helper_version",
                    version=sources.steam.HELPER_VERSION,
                    sha=sources.steam.HELPER_SHA256[:16] + "…",
                )
            )
            if st.button(
                t("mt_helper_install"), key="mt_helper_install", type="primary"
            ):
                _install_helper(t)
            st.caption(t("mt_helper_side_note"))
        if not sources.steam.helper_installed():
            _render_steam_records(t)
            return
    st.caption(t("mt_gc_note"))
    st.caption(t("mt_helper_installed_note", path=store.tools_dir()))
    _render_steam_records(t)


def _filtered(t, records: list[sm.MatchRecord]) -> list[sm.MatchRecord]:
    """The filter row + the records it keeps (the library decides "downloaded")."""
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.segmented_control(
            t("mt_filter"),
            list(_FILTERS),
            format_func=lambda v: t(f"mt_filter_{v}"),
            key="mt_filter",
            default="all",
        )
    flt = st.session_state.get("mt_filter") or "all"
    if flt == "down":
        return [r for r in records if store.find_by_match(r.provider, r.match_id)]
    if flt == "todo":
        return [r for r in records if not store.find_by_match(r.provider, r.match_id)]
    return records


def _render_steam_records(t) -> None:
    records = _sorted(_provider_cache("steam"))
    if not records:
        if _err_of("steam") is None:
            st.info(t("mt_no_data"))
        return
    _caption_time(t, "steam")
    if any(r.demo == sm.DemoState.MANUAL for r in records):
        st.caption(t("mt_replays_note"))
    shown = _filtered(t, records)
    if not shown:
        st.info(t("mt_filter_empty"))
        return
    for rec in shown:
        _match_card(t, rec)


def _install_helper(t) -> None:
    status = st.status(t("mt_helper_installing"))
    try:
        with status:
            sources.steam.install_helper(sources.http.HttpTransport())
    except sm.ProviderError as e:
        _show_error(t, (e.key, e.kw))
        status.update(label=t("mt_helper_installing"), state="error")
        return
    status.update(label=t("mt_helper_done"), state="complete")
    st.toast(t("mt_helper_done"))


# ------------------------------------------------------------------- page
def render(ctx) -> None:
    t = ctx.t
    accounts = store.read_accounts()
    _header(t, accounts)
    _settings(t, accounts)
    accounts = _accounts()
    _show_import_error(t)
    _clear_control(t)
    if "mt_freed" in st.session_state:
        st.success(
            t("lib_freed", size=store.human_size(st.session_state.pop("mt_freed")))
        )
    n = st.session_state.get("mt_count") or sm.DEFAULT_MATCH_COUNT
    if st.session_state.get("mt_refresh"):
        _refresh(t, n)
    _steam_section(t, accounts)
