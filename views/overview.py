"""The Overview page: both teams, both modes, one table."""

import streamlit as st

from ui import schema

# One table for both teams (Team column + subtle team tint); ~8 columns
# by default, "All columns" adds the detail metrics; the selected
# player's row is highlighted (★ + tint) and clicking any row selects
# that player - same as the select box above.


def render(ctx) -> None:
    """Draw the Overview page."""
    t = ctx.t
    me = ctx.me
    summary = ctx.summary
    filters = ctx.filters
    st.subheader(t("ov_title"))
    st.caption(t("ov_caption"))
    if filters.side or filters.rounds or filters.rounds_set:
        st.caption(t("ov_scope_note"))
    _tcol, _ = st.columns([1, 2])
    with _tcol:
        ov_full = st.toggle(t("all_columns"), key="ov_full")
    st.caption(t("ov_click_hint"))

    team_order = ["T", "CT"] + sorted(set(summary["team"]) - {"T", "CT"})
    ov = (
        summary.assign(
            _n=summary["player"].astype(str).str.lower(),
            _t=summary["team"].map(
                lambda x: team_order.index(x) if x in team_order else len(team_order)
            ),
        )
        .sort_values(["_t", "_n"])
        .reset_index(drop=True)
    )
    ov_sids = ov["sid"].tolist()
    sel_row = ov_sids.index(me) if me in ov_sids else None

    ov_cells = {
        "player": [
            ("★ " if s == me else "") + str(p) for s, p in zip(ov["sid"], ov["player"])
        ],
        "team": ov["team"].to_numpy(),
        "kdr": ov["kd_ratio"].to_numpy(),
        "kills": ov["kills"].to_numpy(),
        "deaths": ov["deaths"].to_numpy(),
        "shots": ov["shots"].to_numpy(),
        "hits": ov["hits"].to_numpy(),
        "accuracy": ov["accuracy"].to_numpy(),
        "hs_hits": ov["hs_hits"].to_numpy(),
        "hs_kills": ov["hs_kills"].to_numpy(),
        "moving": ov["moving"].to_numpy(),
        "risky": ov["risky"].to_numpy(),
        "first_shot": ov["first_shot"].to_numpy(),
        "first_acc": ov["first_acc"].to_numpy(),
        "open_rate": ov["open_rate"].to_numpy(),
        "sides": ov["sides"].to_numpy(),
        "aim_err": ov["aim_err"].to_numpy(),
        "duration": ov["duration"].to_numpy(),
    }

    def _pick_player_row(i):
        """Grid row click -> select that player (callback, runs before widgets)."""
        if 0 <= i < len(ov_sids):
            st.session_state["me"] = ov_sids[i]

    schema.table(
        "overview",
        {**ov_cells, "player_raw": ov["player"].astype(str).to_numpy()},
        key="ov_grid",
        selected=sel_row,
        on_click=_pick_player_row,
        all_columns=ov_full,
    )
