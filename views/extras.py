"""The Guide page: the reference text and the metric glossary."""

import streamlit as st


def guide(ctx) -> None:
    """Draw the Guide page."""
    t = ctx.t
    st.markdown(t("guide"))
    # every metric tooltip without hover (touch devices have none)
    with st.expander(t("glossary_title")):
        for _mk, _hk in (
            ("m_acc", "m_acc_help"),
            ("m_shots", "m_shots_help"),
            ("m_hits", "m_hits_help"),
            ("m_hs", "m_hs_help"),
            ("m_risky", "m_risky_help"),
            ("m_moving", "m_moving_help"),
            ("m_speed", "m_speed_help"),
            ("m_cs", "m_cs_help"),
            ("m_err", "m_err_help"),
            ("m_onhead", "m_onhead_help"),
            ("m_first", "m_first_help"),
            ("m_kd", "m_kd_help"),
            ("m_dur", "m_dur_help"),
        ):
            st.markdown(f"**{t(_mk)}** — {t(_hk)}")
