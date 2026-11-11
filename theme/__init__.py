"""
CS2-AimAudit theme system. Everything visual derives from theme/tokens.toml.

Public API
- tok("tier.good")            -> resolved value ("#8fd16a") for Styler / inline HTML
- theme.tokens()              -> every flat token of the active theme
- theme.active_theme()        -> "violet" (env AIMAUDIT_THEME overrides)
- theme.available_themes()    -> ["green", "violet"]
- theme.chart_colors()        -> [chart-1 .. chart-6]
- theme.css_variables()       -> ":root { --bg-app: ...; ... }" string
- theme.plotly_template()     -> go.layout.Template built from tokens
- theme.inject_theme()        -> injects CSS variables + theme/overrides.css
                                 once per run via st.markdown (see below)

Streamlit notes
- st.html() (>=1.52) renders inside a sandboxed iframe: CSS written there
  cannot reach the main document. inject_theme() therefore uses
  st.markdown(..., unsafe_allow_html=True), which works on every supported
  version (>=1.56) and styles the app itself.
- Native widgets do not read CSS variables; they take their colours from
  .streamlit/config.toml, which tools/sync_theme.py regenerates from the
  same tokens. Changing the palette = edit tokens.toml + run sync + restart.
"""

from __future__ import annotations

from . import loader
from .loader import (  # noqa: F401  (re-exported public API)
    DEFAULT_THEME,
    ENV_THEME,
    active_theme,
    available_themes,
    config_theme_dict,
    contrast,
    font_faces,
    tokens,
)

_DIR = loader.DIR
_OVERRIDES = _DIR / "overrides.css"


def tok(path: str, theme: str | None = None) -> str:
    """One design token by dotted path: tok("accent"), tok("tier.good")."""
    return loader.token(path, theme)


# Design constants that are not palette tokens. Plotly needs a real colour
# string for "transparent" (charts sit directly on the app surface); keeping
# the literal here keeps rgba() out of UI sources (see tests/check_ui.py).
TRANSPARENT = "rgba(0,0,0,0)"


def chart_colors() -> list[str]:
    """The categorical palette chart-1..chart-6 of the active theme."""
    return [tok(f"chart-{i}") for i in range(1, 7)]


def css_variables() -> str:
    """:root CSS variables for every token ("tier.good" -> --tier-good)."""
    lines = [":root {"]
    for key, val in tokens().items():
        lines.append(f"  --{key.replace('.', '-')}: {val};")
    lines.append("}")
    return "\n".join(lines)


def overrides_css() -> str:
    """theme/overrides.css — data-testid overrides + tiny component classes."""
    return _OVERRIDES.read_text(encoding="utf-8")


def inject_theme() -> None:
    """
    Inject CSS variables and overrides into the app, once per run.

    Call it once near the top of app.py (after st.set_page_config); it is
    cheap (one st.markdown element) and keeps every rerun consistent.
    """
    import streamlit as st

    st.markdown(
        f"<style>\n{css_variables()}\n\n{overrides_css()}\n</style>",
        unsafe_allow_html=True,
    )


def plotly_template():
    """
    The shared Plotly template built from tokens ("aimaudit").

    app.py registers it once (pio.templates["aimaudit"]) and style_figure()
    also sets its attributes explicitly on every figure: the browser merges
    Streamlit's own plotly theme into layout.template, but explicit
    layout.* values always win, so charts look identical on every supported
    Streamlit version.
    """
    import plotly.graph_objects as go

    stack = tok("font.family-stack")
    axis = dict(
        gridcolor=tok("chart-grid"),
        zerolinecolor=tok("chart-grid"),
        linecolor=tok("border-subtle"),
        tickfont=dict(color=tok("chart-axis"), family=stack, size=12),
        title_font=dict(color=tok("text-secondary"), family=stack, size=13),
    )
    layout = go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=stack, size=13, color=tok("text-primary")),
        colorway=chart_colors(),
        hoverlabel=dict(
            bgcolor=tok("surface-3"),
            bordercolor=tok("border-strong"),
            font=dict(family=stack, size=13, color=tok("text-primary")),
        ),
        xaxis=axis,
        yaxis=axis,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(family=stack, color=tok("text-secondary")),
        ),
        coloraxis=dict(
            colorbar=dict(
                tickfont=dict(color=tok("chart-axis"), family=stack),
                outlinecolor=tok("border-subtle"),
            )
        ),
    )
    return go.layout.Template(layout=layout)
