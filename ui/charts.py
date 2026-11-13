"""
The shared look of every chart: palette, template, style_figure.

Registering the "aimaudit" plotly template at import time is the one
deliberate import side effect (idempotent, no Streamlit, no I/O beyond
theme/tokens.toml which theme/ reads anyway).
"""

import plotly.io as pio

import theme

# One semantic palette everywhere, colour-blind-safe (Okabe–Ito based),
# tuned for the dark theme. Roles, not decoration: accent = you (your
# lines and bars), grey = context (misses, baselines, thresholds),
# red = bad/risky, blue = good. Meaning never rides on colour
# alone: series keep their label, marker shape or dash too.
# Values come from theme/tokens.toml — the single source of truth.

PAL = {
    "you": theme.tok("accent"),
    "ctx": theme.tok("context"),
    "bad": theme.tok("negative"),
    "good": theme.tok("info"),
}
# Order for traces without a semantic role (the timeline keys row); the
# same list lives in .streamlit/config.toml (theme.chartCategoricalColors,
# kept in sync by tools/sync_theme.py) so theme and code agree.
CHART_COLORS = theme.chart_colors()

# The shared Plotly template built from the theme tokens, registered once
# so the name "aimaudit" resolves anywhere; style_figure() additionally
# applies the object and its key attributes explicitly — see the note
# inside it for why that matters.
pio.templates["aimaudit"] = theme.plotly_template()
PLOTLY_TEMPLATE = pio.templates["aimaudit"]


def style_figure(
    fig, height=380, margin=None, x_title=None, y_title=None, hover=None, legend_y=1.12
):
    """
    The one place every chart gets its look: height, margins,
    font, the categorical palette, a horizontal legend above the plot,
    axis titles, and — where the caller passes one — a shared hovertemplate
    so hover always shows units. Semantic per-trace colours come from PAL
    at trace setup.

    The token look is applied explicitly (template + layout attributes),
    not only through the template: the browser merges Streamlit's own
    plotly theme into layout.template, but explicit layout.* values always
    win, so charts render identically on every supported Streamlit version.
    """
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        height=height,
        margin=margin or dict(t=10, r=10),
        paper_bgcolor=theme.TRANSPARENT,
        plot_bgcolor=theme.TRANSPARENT,
        font=dict(
            size=13,
            family=theme.tok("font.family-stack"),
            color=theme.tok("text-primary"),
        ),
        colorway=CHART_COLORS,
        hoverlabel=dict(
            bgcolor=theme.tok("surface-3"),
            bordercolor=theme.tok("border-strong"),
            font=dict(color=theme.tok("text-primary")),
        ),
        legend=dict(orientation="h", y=legend_y),
    )
    for update_axis in (fig.update_xaxes, fig.update_yaxes):
        update_axis(
            gridcolor=theme.tok("chart-grid"),
            zerolinecolor=theme.tok("chart-grid"),
            tickfont=dict(color=theme.tok("chart-axis")),
        )
    if x_title is not None:
        fig.update_xaxes(title_text=x_title)
    if y_title is not None:
        fig.update_yaxes(title_text=y_title)
    if hover is not None:
        fig.update_traces(hovertemplate=hover)
    return fig
