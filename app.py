"""
CS2-AimAudit — local CS2 demo analysis. Run: streamlit run app.py

The entry point only: page config, theme, the run context and the page
dispatcher. Navigation is native (shell.navigation registers the pages
through st.navigation; the sidebar rail is st.page_link): a match is
opened from the Matches list and the old analysis pages are its tabs.
The shell (loading / context row / match header) is shell.py, the filter
bar is filters_ui.py, the pages live in views/, the cached core calls
and the shared Ctx in context.py.
"""

import functools
import os
import subprocess

import streamlit as st

import context
import filters_ui
import shell
import state
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


# --------------------------------------------------------- the page bodies
# app.py owns the dispatch (it may import views; shell must not, so the
# import graph stays one-way). shell.navigation wraps each body in the
# st.Page that records the running page in the registry key.
def _page_dashboard(ctx) -> None:
    views.dashboard.render(ctx)


def _page_settings(ctx) -> None:
    views.settings.render(ctx)


def _page_guide(ctx) -> None:
    views.extras.guide(ctx)


def _page_matches(ctx) -> None:
    """The Matches page: the open match's view, or the list (?m decides)."""
    shell.apply_match_url()
    if state.MATCH.get():
        if store.get_demo(state.MATCH.get()) is None:
            # the match's demo left the library (deleted in Settings or by
            # hand): show the list instead of blowing up
            state.MATCH.pop()
        else:
            _match(ctx)
            return
    # the pages without a demo must open on an empty library; their filter
    # widget states are re-assigned so nothing is lost on the way back
    views.matches.render(ctx)


_PAGE_FNS = {
    "dashboard": _page_dashboard,
    "matches": _page_matches,
    "settings": _page_settings,
    "guide": _page_guide,
}


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
    if tab == "duels" and state.DU_OPEN.pop():
        views.replay.open_duel_dialog(ctx)
    views.extras.debug(ctx)


def main() -> None:
    """One run: URL state -> the pages -> the active page -> the URL sync."""
    filters_ui.load_url_state()
    ctx = context.Ctx()
    pg = shell.navigation(ctx, _PAGE_FNS)
    # the fmt helpers (ui/format.py) read the run's translator and names
    ui.format.configure(ctx.t, ctx.name_of)
    pg.run()
    shell.sync_nav_url()
    _footer(ctx.t)


def _footer(t) -> None:
    st.divider()
    st.caption(
        f"CS2-AimAudit · {_app_version()} · {t('footer_privacy')} · "
        f"[{t('footer_issues')}](https://github.com/choompuy/cs2-aimaudit/issues)"
    )


main()
