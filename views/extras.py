"""Guide and debug: the reference page and the diagnostics expander."""

import json

import pandas as pd
import streamlit as st

import core


def guide(ctx) -> None:
    """Draw the Guide page."""
    t = ctx.t
    st.markdown(t("guide"))
    # every metric tooltip also reachable without hover (touch
    # devices have none) - one list, reused from the i18n help texts
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


def debug(ctx) -> None:
    """The diagnostics expander under every page."""
    t = ctx.t
    demo = ctx.demo
    shots = ctx.shots
    duels_all = ctx.duels_all
    me = ctx.me
    thr = ctx.thr
    window_s = ctx.window_s
    safe_dist = ctx.safe_dist
    strict_dist = ctx.strict_dist
    with st.expander(f"🐞 {t('debug')}"):
        st.caption(t("dbg_hint"))
        checks = core.data_checks(demo, shots, duels_all)
        icons = {"ok": "✅", "warn": "⚠️", "anomaly": "❗"}
        order = {"anomaly": 0, "warn": 1, "ok": 2}
        if checks and all(c["level"] == "ok" for c in checks):
            st.markdown(f"✅ {t('dbg_ok_all')}")
        for c in sorted(checks, key=lambda c: order[c["level"]]):
            kw = {"n": c.get("n", 0)}
            if "pct" in c:
                kw["pct"] = c["pct"]
            if "med" in c:
                kw["med"] = c["med"] if pd.notna(c["med"]) else 0.0
            kw["vals"] = ", ".join(map(str, c.get("vals") or [])) or "—"
            st.markdown(f"{icons[c['level']]} {t('chk_' + c['id'], **kw)}")
        rep = core.debug_report(
            demo,
            shots,
            duels_all,
            me,
            {
                "thr": thr,
                "window_s": window_s,
                "safe_dist": safe_dist,
                "strict_dist": strict_dist,
            },
        )
        st.download_button(
            t("dbg_download"),
            data=json.dumps(rep, ensure_ascii=False, indent=1, default=str),
            file_name=f"cs2_aimaudit_debug_{me}.json",
            mime="application/json",
            key="dbg_dl",
        )
