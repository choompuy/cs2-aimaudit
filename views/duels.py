"""The Duels page: the duel list (a row opens the duel window).

Only what the player can train on: where the duels happened is on the Map page
(its tooltips name the zone)."""

import pandas as pd
import streamlit as st

import filters_ui
import state
from ui import icons
from ui import schema
from ui.format import fmt_first, fmt_result


def _duel_flags(ddf: pd.DataFrame, t) -> list[str]:
    """The per-duel flag column: low confidence / noscope / smoke / ..."""
    flags = []
    for r in ddf.itertuples():
        fl = []
        if r.low_conf:
            fl.append(t("flag_lowconf"))
        if r.noscope:
            fl.append(t("flag_noscope"))
        if r.thrusmoke:
            fl.append(t("flag_thrusmoke"))
        if r.attackerblind:
            fl.append(t("flag_attackerblind"))
        if r.penetrated:
            fl.append(t("flag_penetrated"))
        flags.append(", ".join(fl))
    return flags


def _duel_cells(
    ddf: pd.DataFrame, name_of: dict, t, w_vals, w_labels
) -> dict:
    """The duels grid's data columns (schema.table maps them to cols)."""
    return {
        "round": ddf["round"].to_numpy(),
        "round_sec": ddf["round_sec"].to_numpy(),
        "result": [fmt_result(v) for v in ddf["result"]],
        "opponent": [name_of.get(s, s) for s in ddf["opponent"]],
        "weapon": w_vals,
        "weapon_disp": w_labels,
        "hs": ["✓" if v else "" for v in ddf["headshot"]],
        "distance": ddf["distance"].to_numpy(),
        "zone": [z if z else "—" for z in ddf["my_zone"]],
        "first": [fmt_first(v) for v in ddf["first"]],
        "aim_err": ddf["my_err"].to_numpy(),
        "my_shots": ddf["my_shots"].to_numpy(),
        "opp_shots": ddf["opp_shots"].to_numpy(),
        "duration": ddf["duration_s"].to_numpy(),
        "speed": ddf["my_speed"].to_numpy(),
        "errhw": ddf["my_err_hw"].to_numpy(),
        "side": ddf["my_side"].to_numpy(),
        "flags": _duel_flags(ddf, t),
        "tick": ddf["tick"].to_numpy(),
    }


def _duels_grid(ctx, du_full: bool) -> None:
    """The duel list table (row click opens the timeline window)."""
    t, ddf = ctx.t, ctx.my_duels
    w_vals, w_labels, w_icons = icons.weapon_cells(ddf["weapon"])
    du_cells = _duel_cells(ddf, ctx.name_of, t, w_vals, w_labels)

    def _duel_click_row(i):
        """Open that duel's window (at the bottom of the run)."""
        if 0 <= i < len(ctx.my_duels):
            state.TL_DUEL.set(i)
            state.DU_OPEN.set(True)

    schema.table(
        "duels",
        {
            **du_cells,
            "result_raw": ddf["result"].astype(str).to_numpy(),
            "first_raw": ddf["first"].astype(str).to_numpy(),
            "hs_raw": ddf["headshot"].astype(int).to_numpy(),
        },
        key="du_grid",
        on_click=_duel_click_row,
        all_columns=du_full,
        icons=w_icons,
    )
    st.caption(t("du_click_hint"))


def render(ctx) -> None:
    """Draw the Duels page."""
    t = ctx.t
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(t("du_title"), help=t("du_caption"))
        filters_ui.result_control(t)
        du_full = st.toggle(t("all_columns"), key="du_full", persist_state="session")
    if not len(ctx.my_duels):
        st.info(t("du_none"))
    else:
        _duels_grid(ctx, du_full)
