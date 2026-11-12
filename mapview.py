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


# ------------------------------------------------------------ duel replay
CONE_LEN = 380.0  # world units, the view wedge of a player
CONE_HALF = 45.0  # degrees: a 90 degree guide, not the exact field of view
TRACER_LEN = 700.0  # world units, the aim line of a shot


def plot_xy(x, y, radar: dict | None):
    """World (x, y) -> plot coordinates (radar pixels with y up, or raw units)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if radar is None:
        return x, y
    px, py = core.world_to_radar(x, y, radar)
    return px, radar["size"] - py


def build_replay_figure(
    frames: dict,
    radar: dict | None,
    event_tick: int,
    tickrate: int,
    shots: dict | None = None,
    hits: list | None = None,
    keys=(),
    labels: dict | None = None,
    colors: dict | None = None,
    max_frames: int = 400,
    speeds=(1.0, 0.75, 0.5, 0.25, 0.1),
) -> go.Figure | None:
    """
    Animated 2D replay of ONE duel: the two duellists on the radar, their
    view wedges, aim lines of shots, hits, deaths, and (when `keys` is
    given) a panel with the keys each of them holds. Plain Plotly frames,
    so Play / Pause / the slider run in the browser without Streamlit reruns.

    frames: {"me": df, "opp": df} from core.replay_data (X, Y, yaw, speed,
    is_alive, key columns). shots: {"me": [ticks], "opp": [ticks]}. hits:
    [(tick, "me" | "opp")] = who hit the other. labels: me / opp (names),
    time, keys {key: text}. colors: me / opp / dead / off / outline and the
    ui_* colours of the controls. Returns None without data for both.
    """
    roles = ("me", "opp")
    labels = labels or {}
    colors = {
        "me": "#e8507f",
        "opp": "#7c4fd0",
        "dead": "#e8625a",
        "off": "rgba(167,164,181,0.25)",
        "outline": "#f2f1f7",
        "ui_bg": "#2a2833",
        "ui_fg": "#f2f1f7",
        "ui_border": "#3d3a4a",
        **(colors or {}),
    }
    shots = shots or {}
    hits = hits or []
    keys = list(keys)
    if any(frames.get(r) is None or not len(frames[r]) for r in roles):
        return None

    t0 = min(int(frames[r]["tick"].min()) for r in roles)
    t1 = max(int(frames[r]["tick"].max()) for r in roles)
    grid = np.arange(t0, t1 + 1)
    D: dict = {}
    for r in roles:
        g = frames[r].drop_duplicates("tick").set_index("tick").reindex(grid)
        wx = pd.to_numeric(g["X"], errors="coerce").ffill().bfill().to_numpy()
        wy = pd.to_numeric(g["Y"], errors="coerce").ffill().bfill().to_numpy()
        if np.isnan(wx).all():
            return None
        px, py = plot_xy(wx, wy, radar)
        alive = (
            g["is_alive"].astype(float).ffill().fillna(1.0).astype(bool).to_numpy()
            if "is_alive" in g.columns
            else np.ones(len(grid), dtype=bool)
        )
        D[r] = {
            "wx": wx,
            "wy": wy,
            "wz": (
                pd.to_numeric(g["Z"], errors="coerce").ffill().bfill().to_numpy()
                if "Z" in g.columns
                else np.zeros(len(grid))
            ),
            "px": px,
            "py": py,
            "yaw": pd.to_numeric(g["yaw"], errors="coerce").ffill().bfill().to_numpy(),
            "alive": alive,
            "speed": (
                pd.to_numeric(g["speed"], errors="coerce").to_numpy()
                if "speed" in g.columns
                else np.full(len(grid), np.nan)
            ),
            "keys": {
                k: (pd.to_numeric(g[k], errors="coerce").fillna(0).to_numpy() != 0)
                for k in keys
                if k in g.columns
            },
        }
    keys = [k for k in keys if all(k in D[r]["keys"] for r in roles)]
    nk = len(keys)

    # the level a two-level map is drawn on: where "me" stood at the event
    lower = False
    img = radar["png"] if radar else None
    if radar and radar.get("lower"):
        ev = int(np.clip(event_tick - t0, 0, len(grid) - 1))
        lower = bool(core.on_lower_level([D["me"]["wz"][ev]], radar)[0])
        img = radar["lower_png"] if lower else radar["png"]

    flash = max(1, int(0.12 * tickrate))
    hit_show = max(1, int(0.45 * tickrate))
    trail_n = max(2, int(1.0 * tickrate))
    unit = 1.0 if radar is None else radar["size"] / core.RADAR_SIZE / radar["scale"]

    def cone(r, i):
        d = D[r]
        if not d["alive"][i] or np.isnan(d["yaw"][i]):
            return [], []
        a, h = np.radians(d["yaw"][i]), np.radians(CONE_HALF)
        ang = np.linspace(a - h, a + h, 9)
        cx = np.concatenate(
            [[d["wx"][i]], d["wx"][i] + CONE_LEN * np.cos(ang), [d["wx"][i]]]
        )
        cy = np.concatenate(
            [[d["wy"][i]], d["wy"][i] + CONE_LEN * np.sin(ang), [d["wy"][i]]]
        )
        px, py = plot_xy(cx, cy, radar)
        return px.tolist(), py.tolist()

    def tracers(r, T):
        xs: list = []
        ys: list = []
        d = D[r]
        for st in shots.get(r, ()):
            if not (T - flash < st <= T and t0 <= st <= t1):
                continue
            j = st - t0
            a = np.radians(d["yaw"][j])
            px, py = plot_xy(
                [d["wx"][j], d["wx"][j] + TRACER_LEN * np.cos(a)],
                [d["wy"][j], d["wy"][j] + TRACER_LEN * np.sin(a)],
                radar,
            )
            xs += [float(px[0]), float(px[1]), None]
            ys += [float(py[0]), float(py[1]), None]
        return xs, ys

    def hit_points(T):
        xs: list = []
        ys: list = []
        cs: list = []
        for ht, att in hits:
            if T - hit_show < ht <= T and t0 <= ht <= t1:
                vic = "opp" if att == "me" else "me"
                j = ht - t0
                xs.append(float(D[vic]["px"][j]))
                ys.append(float(D[vic]["py"][j]))
                cs.append(colors[att])
        return xs, ys, cs

    def speed_text(r, i):
        d = D[r]
        name = labels.get(r, r)
        v = d["speed"][i]
        if not d["alive"][i]:
            return f"{name} ✕"
        return f"{name} · {v:.0f} u/s" if np.isfinite(v) else name

    def dynamic(i):
        """Data of the traces that change per frame, in trace order."""
        T = int(grid[i])
        out = []
        for r in roles:  # trails
            lo = max(0, i - trail_n)
            out.append(
                go.Scatter(
                    x=D[r]["px"][lo : i + 1].tolist(), y=D[r]["py"][lo : i + 1].tolist()
                )
            )
        for r in roles:  # view wedges
            cx, cy = cone(r, i)
            out.append(go.Scatter(x=cx, y=cy))
        for r in roles:  # aim lines of shots
            tx, ty = tracers(r, T)
            out.append(go.Scatter(x=tx, y=ty))
        hx, hy, hc = hit_points(T)
        out.append(go.Scatter(x=hx, y=hy, marker=dict(color=hc)))
        for r in roles:  # the players
            out.append(
                go.Scatter(
                    x=[float(D[r]["px"][i])],
                    y=[float(D[r]["py"][i])],
                    text=[speed_text(r, i)],
                )
            )
        dx_: list = []
        dy_: list = []
        for r in roles:  # deaths
            if not D[r]["alive"][i]:
                dx_.append(float(D[r]["px"][i]))
                dy_.append(float(D[r]["py"][i]))
        out.append(go.Scatter(x=dx_, y=dy_))
        if nk:
            for r in roles:  # keys held
                on = [bool(D[r]["keys"][k][i]) and bool(D[r]["alive"][i]) for k in keys]
                out.append(
                    go.Scatter(
                        marker=dict(
                            color=[colors[r] if v else colors["off"] for v in on]
                        )
                    )
                )
        return out

    step = max(1, int(np.ceil(len(grid) / max_frames)))
    idx = list(range(0, len(grid), step))
    ev_i = int(event_tick - t0)
    if 0 <= ev_i < len(grid) and ev_i not in idx:
        idx = sorted(idx + [ev_i])

    fig = make_subplots(
        rows=1,
        cols=2 if nk else 1,
        column_widths=[0.74, 0.26] if nk else [1.0],
        horizontal_spacing=0.05,
    )
    first = dynamic(idx[0])
    # static: the whole path of each duellist, faint
    for r in roles:
        fig.add_trace(
            go.Scatter(
                x=D[r]["px"].tolist(),
                y=D[r]["py"].tolist(),
                mode="lines",
                line=dict(color=colors[r], width=1, dash="dot"),
                opacity=0.35,
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    for r, d in zip(roles, first[0:2]):  # trails
        fig.add_trace(
            go.Scatter(
                x=d.x,
                y=d.y,
                mode="lines",
                line=dict(color=colors[r], width=3),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    for r, d in zip(roles, first[2:4]):  # view wedges
        fig.add_trace(
            go.Scatter(
                x=d.x,
                y=d.y,
                mode="lines",
                fill="toself",
                fillcolor=colors[r],
                opacity=0.28,
                line=dict(color=colors[r], width=1),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    for r, d in zip(roles, first[4:6]):  # aim lines
        fig.add_trace(
            go.Scatter(
                x=d.x,
                y=d.y,
                mode="lines",
                line=dict(color=colors[r], width=2.5),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    d = first[6]  # hits
    fig.add_trace(
        go.Scatter(
            x=d.x,
            y=d.y,
            mode="markers",
            marker=dict(
                symbol="star",
                size=17,
                color=d.marker.color,
                line=dict(width=1.5, color=colors["outline"]),
            ),
            hoverinfo="skip",
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    for r, d in zip(roles, first[7:9]):  # players (labels on opposite sides)
        fig.add_trace(
            go.Scatter(
                x=d.x,
                y=d.y,
                text=d.text,
                mode="markers+text",
                textposition="top center" if r == "me" else "bottom center",
                textfont=dict(color=colors["outline"], size=12),
                marker=dict(
                    size=15,
                    color=colors[r],
                    line=dict(width=2, color=colors["outline"]),
                ),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    d = first[9]  # deaths
    fig.add_trace(
        go.Scatter(
            x=d.x,
            y=d.y,
            mode="markers",
            marker=dict(
                symbol="x",
                size=18,
                color=colors["dead"],
                line=dict(width=3, color=colors["dead"]),
            ),
            hoverinfo="skip",
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    n_dyn = 10
    if nk:
        for j, (r, d) in enumerate(zip(roles, first[10:12])):
            fig.add_trace(
                go.Scatter(
                    x=[j] * nk,
                    y=list(range(nk)),
                    mode="markers",
                    marker=dict(symbol="square", size=17, color=d.marker.color),
                    hoverinfo="skip",
                    showlegend=False,
                ),
                row=1,
                col=2,
            )
        n_dyn = 12
    dyn_traces = list(range(2, 2 + n_dyn))

    fig.frames = [
        go.Frame(name=str(int(grid[i])), data=dynamic(i), traces=dyn_traces)
        for i in idx
    ]

    # view box: both paths with a margin, never tighter than ~900 units
    allx = np.concatenate([D[r]["px"] for r in roles])
    ally = np.concatenate([D[r]["py"] for r in roles])
    span = max(float(allx.max() - allx.min()), float(ally.max() - ally.min()))
    span = max(span + 500.0 * unit, 900.0 * unit)
    cx, cy = float((allx.max() + allx.min()) / 2), float((ally.max() + ally.min()) / 2)
    xr = [cx - span / 2, cx + span / 2]
    yr = [cy - span / 2, cy + span / 2]
    if radar is not None:
        size = float(radar["size"])
        fig.add_layout_image(
            dict(
                source=radar_uri(img),
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
        fig.update_xaxes(range=xr, visible=False, constrain="domain", row=1, col=1)
        fig.update_yaxes(
            range=yr,
            visible=False,
            scaleanchor="x",
            scaleratio=1,
            constrain="domain",
            row=1,
            col=1,
        )
    else:
        fig.update_xaxes(range=xr, title_text="X", row=1, col=1)
        fig.update_yaxes(
            range=yr, title_text="Y", scaleanchor="x", scaleratio=1, row=1, col=1
        )
    if nk:
        short = lambda n: (n[:11] + "…") if len(n) > 12 else n  # noqa: E731
        fig.update_xaxes(
            range=[-0.6, 1.6],
            tickvals=[0, 1],
            ticktext=[short(labels.get("me", "")), short(labels.get("opp", ""))],
            side="top",
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=11),
            row=1,
            col=2,
        )
        fig.update_yaxes(
            range=[nk - 0.5, -0.5],
            tickvals=list(range(nk)),
            ticktext=[labels.get("keys", {}).get(k, k) for k in keys],
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=11),
            row=1,
            col=2,
        )

    def dur(sp):
        return int(round(1000.0 * step / tickrate / sp))

    # redraw=True on every frame: with redraw=False plotly.js morphs the
    # traces instead of re-rendering them, and points a frame removes
    # (shot tracers, hit stars, death crosses before the death) stay on
    # screen forever. The scene is small enough that a full redraw per
    # frame is cheap.
    play = [
        dict(
            label=f"▶ {sp:g}×",
            method="animate",
            args=[
                None,
                dict(
                    frame=dict(duration=dur(sp), redraw=True),
                    transition=dict(duration=0),
                    fromcurrent=True,
                    mode="immediate",
                ),
            ],
        )
        for sp in speeds
    ]
    play.append(
        dict(
            label=labels.get("pause", "Pause"),
            method="animate",
            args=[
                [None],
                dict(
                    frame=dict(duration=0, redraw=True),
                    transition=dict(duration=0),
                    mode="immediate",
                ),
            ],
        )
    )
    # every step carries its time: plotly.js picks WHICH steps get a
    # visible label by pixel stride and just renders that step's text -
    # labelling only some steps leaves mostly-empty labels on the stride
    steps = [
        dict(
            method="animate",
            label=f"{(int(grid[i]) - event_tick) / tickrate:+.1f}",
            args=[
                [str(int(grid[i]))],
                dict(
                    mode="immediate",
                    frame=dict(duration=0, redraw=True),
                    transition=dict(duration=0),
                ),
            ],
        )
        for i in idx
    ]
    fig.update_layout(
        hovermode=False,
        dragmode="pan",
        updatemenus=[
            dict(
                type="buttons",
                direction="left",
                showactive=False,
                x=0.0,
                xanchor="left",
                y=1.0,
                yanchor="bottom",
                pad=dict(t=0, b=6),
                buttons=play,
                bgcolor=colors["ui_bg"],
                bordercolor=colors["ui_border"],
                font=dict(color=colors["ui_fg"], size=12),
            )
        ],
        sliders=[
            dict(
                active=0,
                x=0.0,
                y=0.0,
                len=1.0,
                xanchor="left",
                yanchor="top",
                pad=dict(t=16, b=0),
                steps=steps,
                bgcolor=colors["ui_bg"],
                bordercolor=colors["ui_border"],
                activebgcolor=colors["me"],
                tickcolor=colors["ui_border"],
                font=dict(color=colors["ui_fg"], size=10),
                currentvalue=dict(
                    prefix=(labels.get("time", "t") + ": "),
                    suffix=" s",
                    font=dict(color=colors["ui_fg"], size=13),
                    xanchor="left",
                ),
            )
        ],
    )
    return fig
