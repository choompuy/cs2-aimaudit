"""The session_state keys shared by 2+ modules and the match-level mutators. Page switching
lives in shell.py: it needs the st.Page registry."""

from __future__ import annotations

import streamlit as st

# ------------------------------------------------------------ value domains
NAV_PAGES = ("dashboard", "matches", "settings", "guide")
MATCH_TABS = ("overview", "shooting", "duels", "map", "replay")


class Key:
    """One session_state key; an absent or None value reads as the default."""

    def __init__(self, name: str, default=None):
        self.name, self.default = name, default

    def get(self):
        v = st.session_state.get(self.name)
        return self.default if v is None else v

    def set(self, v) -> None:
        st.session_state[self.name] = v

    def pop(self):
        return st.session_state.pop(self.name, self.default)


# --------------------------------------------------------- the shared keys
PAGE = Key("page", "matches")  # the running page, mirrored by shell.navigation
MATCH = Key("match")  # open library demo id; absent = the Matches list
MATCH_TAB = Key("match_tab", "overview")  # tab inside the match view
LANG = Key("lang")  # widget-owned (the sidebar selectbox): never set by hand
ME = Key("me")  # the focused player's steamid (also the selectbox key)
DU_OPEN = Key("du_open")  # the Duels page's "open the duel window" request
TL_DUEL = Key("tl_duel")  # the duel strip's selected duel index
DEMO_ID = Key("demo_id")  # the demo the pipeline should load next
DEMO_LOADED = Key("demo_loaded")  # the demo the pipeline HAS loaded
PICK_ERR = Key("_pick_err")  # file-dialog failure -> the library panel


# --------------------------------------------------- navigation mutators
def leave_match() -> None:
    """Back to the list; the loaded demo and the filters stay."""
    MATCH.pop()
    for k in ("m", "tab"):
        st.query_params.pop(k, None)


def go_tab(tab: str) -> None:
    """Switch the tab of the open match (the duel window -> 2D replay).
    The URL follows through shell.sync_nav_url at the end of the run."""
    if tab in MATCH_TABS and MATCH.get():
        MATCH_TAB.set(tab)
