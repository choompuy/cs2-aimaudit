"""The shared look of every chart: palette, template, style_figure."""

import plotly.io as pio

import theme

# Roles, not decoration (colour-blind-safe): accent = you, grey = context, red =
# bad/risky, blue = good. Meaning never rides on colour alone.

PAL = {
    "you": theme.tok("accent"),
    "ctx": theme.tok("context"),
    "bad": theme.tok("negative"),
    "good": theme.tok("info"),
}
# for traces without a semantic role
CHART_COLORS = theme.chart_colors()

pio.templates["aimaudit"] = theme.plotly_template()
PLOTLY_TEMPLATE = pio.templates["aimaudit"]


def style_figure(
    fig, height=380, margin=None, x_title=None, y_title=None, hover=None, legend_y=1.12
):
    """The one place every chart gets its look. Applied explicitly, not only through the
    template: the browser merges Streamlit's plotly theme into it, explicit layout
    values win."""
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
