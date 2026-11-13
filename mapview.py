"""
Kills / deaths on the radar - the figure of the Map page.

Pure plotly + core, no Streamlit: app.py passes the translated labels, the
colours and a ready `hover` text per point, so this module stays testable
and renderable on its own. Coordinates: with a radar the points are
placed in radar pixels (the image is 0..size on both axes, y up), without
one they stay in game units on a plain grid with equal axes.
"""

from __future__ import annotations

import base64
from functools import lru_cache

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import core


@lru_cache(maxsize=8)
def _data_uri(path: str, mtime: float) -> str:
    """The radar file as a data URI (cached per file version)."""
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")


def radar_uri(path: str) -> str:
    import os

    return _data_uri(path, os.path.getmtime(path))


def _positions(pts, who: str, radar: dict | None):
    """x, y (plot coordinates) and z of the 'my' / 'opp' side of the points."""
    x = pts[f"{who}_x"].to_numpy(dtype=float)
    y = pts[f"{who}_y"].to_numpy(dtype=float)
    z = pts[f"{who}_z"].to_numpy(dtype=float)
    if radar is None:
        return x, y, z
    px, py = core.world_to_radar(x, y, radar)
    return px, radar["size"] - py, z


def build_figure(
    pts,
    radar: dict | None,
    show: str = "both",
    lines: bool = True,
    level: str = "upper",
    labels: dict | None = None,
    colors: dict | None = None,
) -> go.Figure:
    """
    pts: core.kill_points frame plus a `hover` text column.
    show: "both" | "kills" | "deaths". level: "upper" | "lower" (two-level
    maps with a lower radar). labels: my_kill / victim / my_death / killer.
    colors: kill / death / outline.
    """
    labels = labels or {}
    colors = {
        "kill": "#56b4e9",
        "death": "#e8625a",
        "outline": "#15141c",
        **(colors or {}),
    }
    fig = go.Figure()
    two_level = bool(radar and radar.get("lower"))
    lower = two_level and level == "lower"
    if "hover" not in pts.columns:
        pts = pts.assign(hover="")

    mx, my, mz = _positions(pts, "my", radar)
    ox, oy, oz = _positions(pts, "opp", radar)
    if two_level:
        my_on = core.on_lower_level(mz, radar) == lower
        opp_on = core.on_lower_level(oz, radar) == lower
    else:
        my_on = np.ones(len(pts), dtype=bool)
        opp_on = np.ones(len(pts), dtype=bool)
    opp_known = ~(np.isnan(ox) | np.isnan(oy))

    spec = (
        ("kill", "my_kill", "victim", show in ("both", "kills")),
        ("death", "my_death", "killer", show in ("both", "deaths")),
    )
    for result, my_label, opp_label, wanted in spec:
        if not wanted:
            continue
        sel = (pts["result"] == result).to_numpy()
        colour = colors[result]
        if lines:
            both = sel & my_on & opp_on & opp_known
            lx: list = []
            ly: list = []
            for i in np.nonzero(both)[0]:
                lx += [float(mx[i]), float(ox[i]), None]
                ly += [float(my[i]), float(oy[i]), None]
            if lx:
                fig.add_trace(
                    go.Scatter(
                        x=lx,
                        y=ly,
                        mode="lines",
                        line=dict(color=colour, width=1.5),
                        opacity=0.55,
                        hoverinfo="skip",
                        showlegend=False,
                        legendgroup=result,
                    )
                )
        idx = np.nonzero(sel & opp_known & opp_on)[0]
        if len(idx):
            fig.add_trace(
                go.Scatter(
                    x=ox[idx],
                    y=oy[idx],
                    mode="markers",
                    name=labels.get(opp_label, opp_label),
                    legendgroup=result,
                    marker=dict(
                        symbol="diamond-open", size=11, color=colour, line=dict(width=2)
                    ),
                    text=pts["hover"].to_numpy()[idx],
                    hovertemplate="%{text}<extra></extra>",
                )
            )
        idx = np.nonzero(sel & my_on)[0]
        if len(idx):
            fig.add_trace(
                go.Scatter(
                    x=mx[idx],
                    y=my[idx],
                    mode="markers",
                    name=labels.get(my_label, my_label),
                    legendgroup=result,
                    marker=dict(
                        symbol="circle",
                        size=13,
                        color=colour,
                        line=dict(width=1.5, color=colors["outline"]),
                    ),
                    text=pts["hover"].to_numpy()[idx],
                    hovertemplate="%{text}<extra></extra>",
                )
            )

    if radar is not None:
        size = float(radar["size"])
        path = radar["lower_png"] if lower else radar["png"]
        fig.add_layout_image(
            dict(
                source=radar_uri(path),
                xref="x",
                yref="y",
                x=0,
                y=size,
                sizex=size,
                sizey=size,
                xanchor="left",
                yanchor="top",
                sizing="stretch",
                layer="below",
            )
        )
        fig.update_xaxes(range=[0, size], visible=False, constrain="domain")
        fig.update_yaxes(
            range=[0, size],
            visible=False,
            scaleanchor="x",
            scaleratio=1,
            constrain="domain",
        )
    else:
        fig.update_xaxes(title_text="X")
        fig.update_yaxes(title_text="Y", scaleanchor="x", scaleratio=1)
    return fig


