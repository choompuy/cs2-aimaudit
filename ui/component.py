"""
Custom component glue shared by ui/table, ui/strip and ui/roundrep:
declaring a component, wiring its event callback and rendering it with
its payload (started as the roundrep/strip/table triplicate).

No colours or texts here - the CSS/JS payloads stay in the owner modules.
"""

from __future__ import annotations

from typing import Callable, Mapping

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


def render(
    name: str,
    css: str,
    js: str,
    *,
    key: str,
    data: Mapping,
    trigger: str,
    handler: Callable[[int], None] | None,
) -> None:
    """
    Declare the `name` component and render `data` under `key` - the one
    call every component module makes. `handler` (None = no callback)
    receives the int the front end reports in its `trigger` field, wired
    through the on_<trigger>_change callback Streamlit needs.
    """
    comp = declare(name, css=css, js=js)
    kwargs: dict = {}
    if handler is not None:
        kwargs[f"on_{trigger}_change"] = event_callback(key, trigger, handler)
    comp(key=key, data=data, **kwargs)
