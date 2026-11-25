"""
Custom component glue shared by ui/table, ui/strip and ui/roundrep:
declaring a component, wiring its event callback and rendering it with
its payload (started as the roundrep/strip/table triplicate).

The front end of component X is ui/X.js + ui/X.css, with ui/common.js in front.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping

import streamlit as st


_HERE = Path(__file__).resolve().parent


@lru_cache
def _asset(name: str) -> str:
    return (_HERE / name).read_text(encoding="utf-8")


def _declare(name: str):
    # registered on every run on purpose: the registry lives in the Streamlit
    # runtime (AppTest and server restarts start with an empty one), and
    # re-registering an identical definition is cheap and idempotent
    return st.components.v2.component(
        f"aimaudit_{name}",
        css=_asset(f"{name}.css"),
        js=_asset("common.js") + _asset(f"{name}.js"),
    )


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


def event_callback_set(
    key: str, field: str, handler: Callable[[set], None]
) -> Callable[[], None]:
    """The set-flavoured event callback: the component reports a whole
    selection (the table's checkbox column) as one newline-joined string
    of row keys; the handler receives it as a set of strings."""

    def _cb() -> None:
        state = st.session_state.get(key)
        v = state.get(field) if hasattr(state, "get") else None
        if v is not None:
            handler({k for k in str(v).split("\n") if k})

    return _cb


def render(
    name: str,
    *,
    key: str,
    data: Mapping,
    trigger: str,
    handler: Callable[[int], None] | None,
    events: Mapping[str, Callable[[], None]] | None = None,
) -> None:
    """
    Declare the `name` component and render `data` under `key` - the one
    call every component module makes. `handler` (None = no callback)
    receives the int the front end reports in its `trigger` field, wired
    through the on_<trigger>_change callback Streamlit needs. `events`
    adds further on_<field>_change callbacks for components that report
    more than one kind of event (the table: row clicks AND the checkbox
    selection).
    """
    comp = _declare(name)
    kwargs: dict = {}
    if handler is not None:
        kwargs[f"on_{trigger}_change"] = event_callback(key, trigger, handler)
    for field, cb in (events or {}).items():
        kwargs[f"on_{field}_change"] = cb
    comp(key=key, data=data, **kwargs)
