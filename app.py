"""
CS2-AimAudit — local CS2 demo analysis. Run: streamlit run app.py

The entry point only: page config, theme, the run context and the page
dispatcher. Navigation is two-level: the sidebar (shell.sidebar) switches
the top-level page, a match is opened from the Matches list and the old
analysis pages are its tabs. The shell (loading / context row / match
header) is shell.py, the filter bar is filters_ui.py, the pages live in
views/, the cached core calls and the shared Ctx in context.py.
"""

import functools
import os
import subprocess

import streamlit as st

import context
import filters_ui
import shell
import store
import theme
import ui.format
import views

st.set_page_config(
    page_title="CS2-AimAudit",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
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
    except (OSError, subprocess.SubprocessError):
        # no git on PATH / not a repo / the call timed out: footer still renders
        return "dev"


def main() -> None:
    """One run: URL state -> nav migration -> sidebar -> the active page."""
    filters_ui.load_url_state()
    shell.load_nav_url()
    shell.migrate_nav()
    ctx = context.Ctx()
    shell.sidebar(ctx)
    # the fmt helpers (ui/format.py) read the run's translator and names
    ui.format.configure(ctx.t, ctx.name_of)

    page = st.session_state["page"]
    if page == "matches" and st.session_state.get("match"):
        if store.get_demo(st.session_state["match"]) is None:
            # the match's demo left the library (deleted in Settings or by
            # hand): show the list instead of blowing up
            shell.leave_match()
        else:
            _match(ctx)
            shell.sync_nav_url()
            _footer(ctx.t)
            return
    # the pages without a demo must open on an empty library; their filter
    # widget states are re-assigned so nothing is lost on the way back
    filters_ui.preserve_filters()
    if page == "matches":
        views.matches.render(ctx)
    elif page == "settings":
        views.settings.render(ctx)
    elif page == "dashboard":
        views.dashboard.render(ctx)
    else:
        views.extras.guide(ctx)
    shell.sync_nav_url()
    _footer(ctx.t)


def _match(ctx) -> None:
    """The match view: the only pipeline that loads a demo."""
    shell.match_shell(ctx)
    shell.open_demo(ctx)
    shell.match_stats(ctx)  # the Matches table's summary (free: demo parsed)
    shell.context_bar(ctx)
    ui.format.configure(ctx.t, ctx.name_of)
    filters_ui.render(ctx)
    context.page_metrics(ctx)

    tab = ctx.tab
    if tab == "overview":
        views.overview.render(ctx)
    elif tab == "shooting":
        views.shooting.render(ctx)
    elif tab == "duels":
        views.duels.render(ctx)
    if tab == "map":
        views.map.render(ctx)
    if tab == "replay":
        views.replay.render(ctx)
    # a Duels row click asks for the duel window: it is a modal over the page
    if tab == "duels" and st.session_state.pop("du_open", False):
        views.replay.open_duel_dialog(ctx)
    views.extras.debug(ctx)


def _footer(t) -> None:
    st.divider()
    st.caption(
        f"CS2-AimAudit · {_app_version()} · {t('footer_privacy')} · "
        f"[{t('footer_issues')}](https://github.com/choompuy/cs2-aimaudit/issues)"
    )


main()
