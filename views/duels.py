"""The Duels page: the duel list, the openings table, the strip."""

import pandas as pd
import streamlit as st

import core
from ui import schema
from ui.format import fmt_first, fmt_result

# ~8 columns by default, "All columns" reveals the detail metrics and
# the technical tick column; numbers carry their units; the opponent name
# stays pinned when the full table scrolls horizontally.


def render(ctx) -> None:
    """Draw the Duels page."""
    t = ctx.t
    me = ctx.me
    my_duels = ctx.my_duels
    name_of = ctx.name_of
    openings = ctx.openings
    filters = ctx.filters
    st.subheader(t("du_title"))
    st.caption(t("du_caption"))
    if not len(my_duels):
        st.info(t("du_none"))
    else:
        ddf = my_duels
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
        _tcol, _ = st.columns([1, 2])
        with _tcol:
            du_full = st.toggle(t("all_columns"), key="du_full")
        du_cells = {
            "round": ddf["round"].to_numpy(),
            "round_sec": ddf["round_sec"].to_numpy(),
            "result": [fmt_result(v) for v in ddf["result"]],
            "opponent": [name_of.get(s, s) for s in ddf["opponent"]],
            "weapon": ddf["weapon"].to_numpy(),
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
            "flags": flags,
            "tick": ddf["tick"].to_numpy(),
        }

        def _duel_click_row(i):
            """Grid row click -> open that duel in the Timeline."""
            if 0 <= i < len(my_duels):
                st.session_state["tl_duel"] = int(i)
                st.session_state["page"] = "timeline"

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
        )
        st.caption(t("du_click_hint"))

        # duels by named map zone - "where do my fights actually
        # happen". The zone comes from the death event (last_place_name at
        # the kill tick); duels without a known zone are skipped, not
        # lumped into a fake "(unknown)" row.
        _zd = (
            my_duels[my_duels["my_zone"].astype(str).str.len() > 0]
            if len(my_duels)
            else my_duels
        )
        if len(_zd):
            zt = (
                _zd.groupby("my_zone")
                .agg(
                    kills=("result", lambda s: int((s == "kill").sum())),
                    deaths=("result", lambda s: int((s == "death").sum())),
                )
                .reset_index()
            )
            zt["kd"] = zt["kills"] - zt["deaths"]
            zt = zt.sort_values(["deaths", "kills"], ascending=[False, False])
            st.divider()
            st.markdown(f"**{t('zone_title')}**")
            st.caption(t("zone_caption"))
            st.dataframe(
                pd.DataFrame(
                    {
                        t("col_zone"): zt["my_zone"].to_numpy(),
                        t("col_kills"): zt["kills"].to_numpy(),
                        t("col_deaths"): zt["deaths"].to_numpy(),
                        t("zone_kd"): zt["kd"].to_numpy(),
                    }
                ),
                column_config={
                    t("col_zone"): st.column_config.TextColumn(
                        t("col_zone"),
                        pinned=True,
                        width="medium",
                        help=t("col_zone_help"),
                    ),
                    t("col_kills"): st.column_config.NumberColumn(
                        t("col_kills"), format="%d"
                    ),
                    t("col_deaths"): st.column_config.NumberColumn(
                        t("col_deaths"), format="%d"
                    ),
                    t("zone_kd"): st.column_config.NumberColumn(
                        t("zone_kd"), format="%+d"
                    ),
                },
                hide_index=True,
                width="stretch",
            )

    # opening duels - how every round started, for the whole match (both
    # teams; ★ marks the selected player). Independent of the player's
    # own duels above: shown even when they have none. Only the round
    # filter applies - weapon/side filters have no meaning for a
    # kill-by-kill view of the round structure.
    op = openings
    if len(op):
        if filters.rounds is not None:
            _lo, _hi = filters.rounds
            op = op[(op["round"] >= _lo) & (op["round"] <= _hi)]
        if filters.rounds_set:
            op = op[op["round"].isin(filters.rounds_set)]
    if len(op):
        st.divider()
        st.markdown(f"**{t('open_title')}**")
        st.caption(t("open_caption"))
        st.dataframe(
            pd.DataFrame(
                {
                    t("col_round"): op["round"].to_numpy(),
                    t("col_round_sec"): op["round_sec"].to_numpy(),
                    t("col_killer"): [
                        ("★ " if s == me else "") + name_of.get(s, s)
                        for s in op["killer"]
                    ],
                    t("col_victim"): [
                        ("★ " if s == me else "") + name_of.get(s, s)
                        for s in op["victim"]
                    ],
                    t("col_weapon"): ["—" if w == core.NA else w for w in op["weapon"]],
                    t("col_hs"): ["✓" if v else "" for v in op["headshot"]],
                    t("col_zone_k"): [z if z else "—" for z in op["killer_zone"]],
                    t("col_zone_v"): [z if z else "—" for z in op["victim_zone"]],
                }
            ),
            column_config={
                t("col_round"): st.column_config.NumberColumn(
                    t("col_round"), format="%d"
                ),
                t("col_round_sec"): st.column_config.NumberColumn(
                    t("col_round_sec"), format="%.1f s", help=t("col_round_sec_help")
                ),
                t("col_killer"): st.column_config.TextColumn(
                    t("col_killer"), width="medium"
                ),
                t("col_victim"): st.column_config.TextColumn(
                    t("col_victim"), width="medium"
                ),
                t("col_weapon"): st.column_config.TextColumn(
                    t("col_weapon"), width="small"
                ),
                t("col_hs"): st.column_config.Column(t("col_hs"), width="small"),
                t("col_zone_k"): st.column_config.TextColumn(t("col_zone_k")),
                t("col_zone_v"): st.column_config.TextColumn(t("col_zone_v")),
            },
            hide_index=True,
            width="stretch",
        )
