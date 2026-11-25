"""The Dashboard page: a stub for cross-match aggregates (docs/ARCHITECTURE.md)."""

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
