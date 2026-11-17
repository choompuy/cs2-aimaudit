"""
Custom component glue shared by ui/table, ui/strip and ui/roundrep:
declaring a component and wiring its event callback (started as the
roundrep/strip/table triplicate).

No colours or texts here - the CSS/JS payloads stay in the owner modules.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st


def declare(name: str, css: str, js: str):
    # registered on every run on purpose: the registry lives in the Streamlit
    # runtime (AppTest and server restarts start with an empty one), and
    # re-registering an identical definition is cheap and idempotent
    return st.components.v2.component(name, css=css, js=js)


def event_callback(
    key: str, field: str, handler: Callable[[int], None]
) -> Callable[[], None]:
    """
    The on_*_change callback every component event uses: read the widget's
    state dict, pick the event field, hand the int to the handler. A
    missing or None field (first render, no event yet) does nothing.
    """

    def _cb() -> None:
        state = st.session_state.get(key)
        v = state.get(field) if hasattr(state, "get") else None
        if v is not None:
            handler(int(v))

    return _cb
