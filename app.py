"""
CS2-AimAudit — local CS2 demo analysis. Run: streamlit run app.py

The entry point only: page config, theme, the run context and the page
dispatcher. The shell (sidebar / library / loading / context row / nav)
is shell.py, the filter bar is filters_ui.py, the pages live in views/,
the cached core calls and the shared Ctx in context.py.
"""

import functools
import os
import subprocess

import streamlit as st

import context
import filters_ui
import shell
import theme
import ui.format
import views

st.set_page_config(
    page_title="CS2-AimAudit",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# The design system: CSS variables + overrides (theme/overrides.css) are
# injected here once per run; charts and inline HTML read tokens through
# theme.tok(); native widgets take their colours from .streamlit/config.toml,
# which tools/sync_theme.py regenerates from the same theme/tokens.toml.
theme.inject_theme()


@functools.lru_cache(maxsize=1)
def _app_version() -> str:
    """Short commit of the running checkout for the footer ('dev' outside a repo)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return out.stdout.strip() or "dev"
    except Exception:  # noqa: BLE001 - no git / no repo: footer still renders
        return "dev"


def main() -> None:
    """One full run: shell -> filters -> metrics -> nav -> the page."""
    filters_ui.load_url_state()
    ctx = context.Ctx()
    shell.sidebar(ctx)
    shell.open_demo(ctx)
    shell.context_bar(ctx)
    # the fmt helpers (ui/format.py) read the run's translator and names
    ui.format.configure(ctx.t, ctx.name_of)
    filters_ui.render(ctx)
    context.page_metrics(ctx)
    shell.nav(ctx)

    page = ctx.page
    if page == "overview":
        views.overview.render(ctx)
    elif page == "shooting":
        views.shooting.render(ctx)
    elif page == "duels":
        views.duels.render(ctx)
    if page == "map":
        views.map.render(ctx)
    if page == "timeline":
        views.timeline.render(ctx)
    if page == "guide":
        views.extras.guide(ctx)
    views.extras.debug(ctx)

    t = ctx.t
    st.divider()
    st.caption(
        f"CS2-AimAudit · {_app_version()} · {t('footer_privacy')} · "
        f"[{t('footer_issues')}](https://github.com/choompuy/cs2-aimaudit/issues)"
    )


main()
