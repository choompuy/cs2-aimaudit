"""The theme system: everything visual derives from theme/tokens.toml.

inject_theme() uses st.markdown: st.html() renders in a sandboxed iframe. Native widgets
read .streamlit/config.toml: after a palette change run tools/sync_theme.py and restart."""

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


# Plotly needs a real colour string; here it stays out of the UI sources
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
    """Inject the CSS variables and overrides, once per run."""
    import streamlit as st

    st.markdown(
        f"<style>\n{css_variables()}\n\n{overrides_css()}\n</style>",
        unsafe_allow_html=True,
    )


def plotly_template():
    """The shared Plotly template built from tokens ("aimaudit")."""
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
