"""
The Settings page: analysis sliders, accounts (Steam helper included),
library and storage, privacy.

Everything here moved from other places (the sidebar's analysis expander,
the Matches page's accounts block and helper card, shell's library panel):
the widget keys stayed the same (thr, mt_steamid, lib_clear, del_* ...) so
the smoke scenarios keep working. The values live in session_state /
accounts.json / library.json, never only in widget state - the sliders must
survive a Settings -> Matches -> match trip (shell.preserve_analysis_settings
re-assigns the keys on the pages where the widgets do not render).
"""

from __future__ import annotations

import os

import streamlit as st

import shell
import sources
import sources.http
import sources.steam
import store
from sources import model as sm
from views import _shared


def render(ctx) -> None:
    """Draw the Settings page."""
    t = ctx.t
    st.subheader(t("set_title"))
    _analysis(t)
    _accounts(t)
    _library(t)
    _privacy(t)


def _analysis(t) -> None:
    """The advanced tuning sliders (moved from the sidebar unchanged:
    same ranges, same help texts, same keys - so nothing is lost)."""
    with st.container(border=True):
        st.markdown(f"**{t('settings_box')}**")
        st.slider(t("thr_label"), 0, 250, 40, help=t("thr_help"), key="thr")
        st.slider(
            t("win_label"), 1.0, 6.0, 3.0, 0.5, help=t("win_help"), key="window_s"
        )
        st.slider(
            t("safe_dist_label"),
            100,
            2000,
            500,
            50,
            help=t("safe_dist_help"),
            key="safe_dist",
        )
        st.slider(
            t("strict_dist_label"),
            500,
            4000,
            1500,
            100,
            help=t("strict_dist_help"),
            key="strict_dist",
        )
        st.button(
            t("reset_defaults"), on_click=_reset_analysis, key="btn_reset_set"
        )


def _reset_analysis() -> None:
    # pop, don't assign: a value written via the Session State API on a
    # keyed widget with a default trips Streamlit's duplication warning;
    # absent keys simply fall back to the slider defaults
    for k in shell.ANALYSIS_DEFAULTS:
        st.session_state.pop(k, None)


def _accounts(t) -> None:
    """SteamID64, the replays folder, the share-code import and the Steam
    helper - everything account-related (moved from the Matches page)."""
    with st.container(border=True):
        st.markdown(f"**{t('set_accounts')}**")
        accounts = store.read_accounts()
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
        _helper(t)
        # save what the fields hold (a typed value persists on its rerun)
        _persist_accounts()


def _persist_accounts() -> dict:
    """Save what the account widgets hold, then return the live values."""
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
    return accounts


_steam_cfg = _shared.steam_cfg
_show_error = _shared.show_error


def _add_by_share_code(t) -> None:
    code = (st.session_state.get("mt_share_code") or "").strip()
    if not code:
        return
    accounts = _persist_accounts()
    cfg = _steam_cfg(accounts)
    try:
        with st.spinner(t("mt_share_add")):
            rec = sources.steam.SteamProvider(
                cfg, transport=sources.http.HttpTransport()
            ).match_by_share_code(code)
    except sm.ProviderError as e:
        _show_error(t, (e.key, e.kw))
        return
    records = [r for r in sources.read_cache("steam") if r.match_id != rec.match_id]
    records.insert(0, rec)
    sources.write_cache("steam", records)
    st.toast(t("mt_share_done", name=rec.map_name or rec.match_id))


def _helper(t) -> None:
    """The Steam helper (boiler-writter) install card."""
    if sources.steam.helper_installed():
        st.caption(t("mt_helper_installed_note", path=store.tools_dir()))
        return
    with st.container(border=True):
        st.markdown(f"**{t('mt_helper_title')}**")
        st.caption(t("mt_helper_what"))
        st.caption(
            t(
                "mt_helper_version",
                version=sources.steam.HELPER_VERSION,
                sha=sources.steam.HELPER_SHA256[:16] + "…",
            )
        )
        if st.button(t("mt_helper_install"), key="mt_helper_install", type="primary"):
            _install_helper(t)
        st.caption(t("mt_helper_side_note"))


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


def _library(t) -> None:
    """The storage panel (moved from the demo-change block unchanged)."""
    with st.container(border=True):
        st.markdown(f"**{t('lib_manage')}**")
        shell.library_panel(t, store.list_demos())


def _privacy(t) -> None:
    with st.container(border=True):
        st.markdown(f"**{t('set_privacy')}**")
        st.caption(t("set_privacy_text"))
        if os.environ.get("AIMAUDIT_OFFLINE"):
            st.caption(t("set_offline_on"))
        else:
            st.caption(t("set_offline_off"))
