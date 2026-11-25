"""CS2-AimAudit — local CS2 demo analysis. Run: streamlit run app.py

The entry point only: page config, theme, the run context and the page dispatch."""

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
# here and not in shell: shell must not import views
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
            # the demo left the library: show the list
            state.MATCH.pop()
        else:
            _match(ctx)
            return
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
