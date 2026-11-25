"""Small helpers shared by the pages."""

from __future__ import annotations

import streamlit as st

from sources import model as sm


def steam_cfg(accounts: dict) -> sm.SteamConfig:
    return sm.SteamConfig(
        steam_id64=accounts.get("steam_id64") or "",
        replays_dir=accounts.get("replays_dir") or "",
    )


def show_error(t, e) -> None:
    """Render a (key, kwargs) ProviderError pair, falling back to the
    bare key when the stored kwargs do not fit the current locale."""
    if e is None:
        return
    key, kw = e
    try:
        st.error(t(key, **kw))
    except (KeyError, ValueError, IndexError):
        st.error(t(key))
