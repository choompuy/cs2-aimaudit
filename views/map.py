"""The Map page: kills and deaths on the radar."""

import streamlit as st

import core
import mapview
import theme
from ui.charts import PAL, style_figure
from ui.format import num


def render(ctx) -> None:
    """Draw the Map page."""
    _map_fragment(ctx)


@st.fragment
def _map_fragment(ctx):
    t = ctx.t
    demo = ctx.demo
    filters = ctx.filters
    me = ctx.me
    name_of = ctx.name_of
    """Where the selected player killed and died, on the radar (Map page)."""
    st.subheader(t("map_title"))
    has_pos = {"attacker_X", "attacker_Y", "user_X", "user_Y"} <= set(
        demo.deaths.columns
    )
    if not has_pos:
        st.info(t("map_no_pos"))
        return
    pts = core.kill_points(demo, me, filters)
    if not len(pts):
        st.info(t("map_none"))
        return
    map_name = str(demo.header.get("map_name") or "")
    radar = core.load_radar(map_name)

    st.session_state.setdefault("map_show", "both")
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    show = (
        c1.segmented_control(
            t("map_show"),
            ["both", "kills", "deaths"],
            format_func=lambda v: t(f"map_show_{v}"),
            key="map_show",
        )
        or "both"
    )
    lines = c2.toggle(t("map_lines"), value=True, key="map_lines")
    level = "upper"
    if radar and radar.get("lower"):
        st.session_state.setdefault("map_level", "upper")
        level = (
            st.segmented_control(
                t("map_level"),
                ["upper", "lower"],
                format_func=lambda v: t(f"map_level_{v}"),
                key="map_level",
            )
            or "upper"
        )

    def _hover(r) -> str:
        return t(
            "map_hover_kill" if r["result"] == "kill" else "map_hover_death",
            rd=int(r["round"]),
            opp=name_of.get(r["opponent"], r["opponent"]),
            weapon=str(r["weapon"]),
            dist=num(r["distance"], 0),
            hs=t("map_hover_hs") if r["headshot"] else "",
        )

    pts = pts.assign(hover=[_hover(r) for _, r in pts.iterrows()])
    fig = mapview.build_figure(
        pts,
        radar,
        show=show,
        lines=lines,
        level=level,
        labels={
            "my_kill": t("map_leg_my_kill"),
            "victim": t("map_leg_victim"),
            "my_death": t("map_leg_my_death"),
            "killer": t("map_leg_killer"),
        },
        colors={
            "kill": PAL["good"],
            "death": PAL["bad"],
            "outline": theme.tok("bg-app"),
        },
    )
    style_figure(fig, height=640, margin=dict(t=48, r=10, l=10, b=10), legend_y=1.08)
    st.plotly_chart(fig, width="stretch")
    st.caption(
        t(
            "map_count",
            k=int((pts["result"] == "kill").sum()),
            d=int((pts["result"] == "death").sum()),
        )
    )
    if radar is None:
        st.info(t("map_no_radar", map=map_name or "?"))
    st.caption(t("map_howto"))
