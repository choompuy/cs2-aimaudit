"""
The Dashboard page: cross-match aggregates (a stub for now).

This is the planned home of the dashboard - aggregates over many demos,
built on top of the per-match summaries cached for the Matches table (see
docs/ARCHITECTURE.md, "Where cross-match aggregates will live": a future
core/aggregate.py over core/matchstats.py). Until that lands the page says
so and points at the Matches list.
"""

import streamlit as st

import shell


def render(ctx) -> None:
    """Draw the Dashboard stub."""
    t = ctx.t
    st.subheader(t("dash_title"))
    st.info(t("dash_soon"))
    st.button(
        t("dash_go_matches"),
        key="dash_go_matches",
        icon=":material/sports_esports:",
        on_click=shell.go_page,
        args=("matches",),
    )
