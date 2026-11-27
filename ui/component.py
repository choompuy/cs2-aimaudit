"""Custom component glue shared by ui/table, ui/chips, ui/meters and ui/roundrep.

The front end of component X is ui/X.js + ui/X.css, with ui/common.js in front."""

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
    # on every run on purpose: the registry lives in the Streamlit runtime (AppTest and
    # restarts start with an empty one)
    return st.components.v2.component(
        f"aimaudit_{name}",
        css=_asset(f"{name}.css"),
        js=_asset("common.js") + _asset(f"{name}.js"),
    )


def event_callback(
    key: str, field: str, handler: Callable[[int], None]
) -> Callable[[], None]:
    """The on_*_change callback: hand the event's int to the handler."""

    def _cb() -> None:
        state = st.session_state.get(key)
        v = state.get(field) if hasattr(state, "get") else None
        if v is not None:
            handler(int(v))

    return _cb


def event_callback_set(
    key: str, field: str, handler: Callable[[set], None]
) -> Callable[[], None]:
    """The same for a whole selection (newline-joined row keys -> a set)."""

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
    """Declare the component and render `data`. `handler` receives the int of the `trigger`
    field, `events` adds further on_<field>_change callbacks."""
    comp = _declare(name)
    kwargs: dict = {}
    if handler is not None:
        kwargs[f"on_{trigger}_change"] = event_callback(key, trigger, handler)
    for field, cb in (events or {}).items():
        kwargs[f"on_{field}_change"] = cb
    comp(key=key, data=data, **kwargs)
