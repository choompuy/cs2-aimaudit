"""Kills / deaths on the radar, the figure of the Map page. Pure plotly: radar pixels (y
up) with a radar, game units on a plain grid without."""

from __future__ import annotations

import base64
from functools import lru_cache

import numpy as np
import plotly.graph_objects as go

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


class _MapCtx:
    """The per-call context the map trace builders share."""

    __slots__ = (
        "pts", "mx", "my", "ox", "oy", "my_on", "opp_on", "opp_known",
        "colors", "labels", "lines",
    )

    def __init__(self, pts, mx, my, ox, oy, my_on, opp_on, opp_known, colors, labels, lines):
        self.pts = pts
        self.mx, self.my, self.ox, self.oy = mx, my, ox, oy
        self.my_on, self.opp_on, self.opp_known = my_on, opp_on, opp_known
        self.colors, self.labels, self.lines = colors, labels, lines


def _link_traces(fig: go.Figure, ctx: _MapCtx, sel, result: str) -> None:
    """The killer->victim connecting lines (both markers on this level)."""
    if not ctx.lines:
        return
    both = sel & ctx.my_on & ctx.opp_on & ctx.opp_known
    lx: list = []
    ly: list = []
    for i in np.nonzero(both)[0]:
        lx += [float(ctx.mx[i]), float(ctx.ox[i]), None]
        ly += [float(ctx.my[i]), float(ctx.oy[i]), None]
    if lx:
        fig.add_trace(
            go.Scatter(
                x=lx,
                y=ly,
                mode="lines",
                line=dict(color=ctx.colors[result], width=1.5),
                opacity=0.55,
                hoverinfo="skip",
                showlegend=False,
                legendgroup=result,
            )
        )


def _opp_markers(fig: go.Figure, ctx: _MapCtx, sel, result: str, opp_label: str) -> None:
    """The opponent markers (diamonds): where the duel happened."""
    idx = np.nonzero(sel & ctx.opp_known & ctx.opp_on)[0]
    if not len(idx):
        return
    fig.add_trace(
        go.Scatter(
            x=ctx.ox[idx],
            y=ctx.oy[idx],
            mode="markers",
            name=ctx.labels.get(opp_label, opp_label),
            legendgroup=result,
            marker=dict(
                symbol="diamond-open", size=11, color=ctx.colors[result], line=dict(width=2)
            ),
            text=ctx.pts["hover"].to_numpy()[idx],
            hovertemplate="%{text}<extra></extra>",
        )
    )


def _my_markers(fig: go.Figure, ctx: _MapCtx, sel, result: str, my_label: str) -> None:
    """The player's own markers (circles with an outline)."""
    idx = np.nonzero(sel & ctx.my_on)[0]
    if not len(idx):
        return
    fig.add_trace(
        go.Scatter(
            x=ctx.mx[idx],
            y=ctx.my[idx],
            mode="markers",
            name=ctx.labels.get(my_label, my_label),
            legendgroup=result,
            marker=dict(
                symbol="circle",
                size=13,
                color=ctx.colors[result],
                line=dict(width=1.5, color=ctx.colors["outline"]),
            ),
            text=ctx.pts["hover"].to_numpy()[idx],
            hovertemplate="%{text}<extra></extra>",
        )
    )


def _radar_background(fig: go.Figure, radar: dict, lower: bool) -> None:
    """The radar image under the traces + the square locked axes."""
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


def build_figure(
    pts,
    radar: dict | None,
    show: str = "both",
    lines: bool = True,
    level: str = "upper",
    labels: dict | None = None,
    colors: dict | None = None,
) -> go.Figure:
    """pts: core.kill_points plus a `hover` column. show: both | kills | deaths. level:
    upper | lower."""
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
    ctx = _MapCtx(
        pts, mx, my, ox, oy, my_on, opp_on, opp_known, colors, labels, lines
    )

    spec = (
        ("kill", "my_kill", "victim", show in ("both", "kills")),
        ("death", "my_death", "killer", show in ("both", "deaths")),
    )
    for result, my_label, opp_label, wanted in spec:
        if not wanted:
            continue
        sel = (pts["result"] == result).to_numpy()
        _link_traces(fig, ctx, sel, result)
        _opp_markers(fig, ctx, sel, result, opp_label)
        _my_markers(fig, ctx, sel, result, my_label)

    if radar is not None:
        _radar_background(fig, radar, lower)
    else:
        fig.update_xaxes(title_text="X")
        fig.update_yaxes(title_text="Y", scaleanchor="x", scaleratio=1)
    return fig
