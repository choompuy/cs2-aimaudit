"""
The session_state registry: key-name constants, typed accessors for the
keys shared by 2+ modules, and the navigation mutators (go_page /
go_match / go_tab / leave_match, moved from shell.py so state never
routes through the shell).

Rule (docs/ARCHITECTURE.md): a module does not touch another module's
session_state key by a string literal - the shared keys go through this
registry. Key VALUES and widget names never change (the smoke tests and
copied links pin them).

A leaf like store.py: streamlit appears only inside the thin
st.session_state wrappers below, never to draw UI.
"""

from __future__ import annotations

import streamlit as st

# ------------------------------------------------------------ value domains
NAV_PAGES = ("dashboard", "matches", "settings", "guide")
MATCH_TABS = ("overview", "shooting", "duels", "map", "replay")
# single-level page ids a pre-redesign session may still carry
LEGACY_PAGES = {"overview", "shooting", "duels", "map", "replay", "timeline"}

# --------------------------------------------------------- the shared keys
PAGE = "page"  # sidebar level
MATCH = "match"  # open library demo id; absent = the Matches list
MATCH_TAB = "match_tab"  # tab inside the match view
LANG = "lang"  # UI language ("en" | "ru"): WIDGET-OWNED (the sidebar
# selectbox key="lang" owns the value - nobody may assign it by hand,
# so the registry offers only the reader)
ME = "me"  # the focused player's steamid (also the selectbox key)
DU_OPEN = "du_open"  # the Duels page's "open the duel window" request
TL_DUEL = "tl_duel"  # the duel strip's selected duel index
DEMO_ID = "demo_id"  # the demo the pipeline should load next
DEMO_LOADED = "demo_loaded"  # the demo the pipeline HAS loaded
PICK_ERR = "_pick_err"  # file-dialog failure -> the library panel


# ----------------------------------------------- typed accessors (shared)
def get_page() -> str:
    """Sidebar level; a fresh or broken session reads as the Matches list."""
    return st.session_state.get(PAGE) or "matches"


def set_page(v: str) -> None:
    st.session_state[PAGE] = v


def has_match() -> bool:
    return bool(st.session_state.get(MATCH))


def get_match() -> str | None:
    m = st.session_state.get(MATCH)
    return str(m) if m else None


def set_match(demo_id: str) -> None:
    st.session_state[MATCH] = demo_id


def clear_match() -> None:
    st.session_state.pop(MATCH, None)


def get_match_tab() -> str:
    return st.session_state.get(MATCH_TAB) or "overview"


def set_match_tab(tab: str) -> None:
    st.session_state[MATCH_TAB] = tab


def get_lang() -> str | None:
    return st.session_state.get(LANG)


def get_me() -> str | None:
    m = st.session_state.get(ME)
    return str(m) if m else None


def set_me(v: str) -> None:
    st.session_state[ME] = v


def set_du_open(v: bool) -> None:
    st.session_state[DU_OPEN] = v


def pop_du_open() -> bool:
    """Read-and-clear the duel-window request (a one-shot flag)."""
    return bool(st.session_state.pop(DU_OPEN, False))


def get_tl_duel() -> int | None:
    v = st.session_state.get(TL_DUEL)
    return v if isinstance(v, int) else None


def set_tl_duel(i: int) -> None:
    st.session_state[TL_DUEL] = int(i)


def get_demo_id() -> str | None:
    d = st.session_state.get(DEMO_ID)
    return str(d) if d else None


def set_demo_id(demo_id: str) -> None:
    st.session_state[DEMO_ID] = demo_id


def get_demo_loaded() -> str | None:
    d = st.session_state.get(DEMO_LOADED)
    return str(d) if d else None


def set_demo_loaded(demo_id: str) -> None:
    st.session_state[DEMO_LOADED] = demo_id


def set_pick_err(msg: str) -> None:
    st.session_state[PICK_ERR] = msg


def pop_pick_err() -> str | None:
    return st.session_state.pop(PICK_ERR, None)


# --------------------------------------------------- navigation mutators
def go_page(page: str) -> None:
    """The one switch for the top level (the sidebar buttons)."""
    set_page(page if page in NAV_PAGES else "matches")
    if get_page() == "matches":
        # the nav item means the LIST; an open match is left via its own
        # back button, so the item always lands on the list
        clear_match()


def go_match(demo_id: str, tab: str = "overview") -> None:
    """The one way to open a match: a list row, a fresh download. No other
    code may set `match` / `demo_id` by hand."""
    set_page("matches")
    set_match(demo_id)
    set_demo_id(demo_id)
    if tab in MATCH_TABS:
        set_match_tab(tab)


def leave_match() -> None:
    """«← Матчи»: back to the list; its filters and the loaded demo stay."""
    clear_match()


def go_tab(tab: str) -> None:
    """Switch the tab of the open match (the duel window -> 2D replay)."""
    if tab in MATCH_TABS and has_match():
        set_match_tab(tab)
