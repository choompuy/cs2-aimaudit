"""
The Matches page: every match of every source in one table.

No network happens just by opening the page - only the local caches are
read (the provider lists and the per-match summaries); every request
(Refresh / Download) is an explicit press. A row click opens the match when
the demo is in the library, and otherwise runs the row's own action
(download / import / locate the file). Accounts and the Steam helper live
on the Settings page.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import streamlit as st

import core
import nativedialog
import shell
import state
import store
import sources
import sources.http
import sources.inbox
import sources.steam
from sources import model as sm
from ui.format import map_name
from views import _shared

_PERIODS = ("7", "30", "all")
_RESULTS = ("all", "win", "loss", "tie")
# how many rows the table draws before the "Show more" button
PAGE_ROWS = 25


# ------------------------------------------------------------- shared bits
_steam_cfg = _shared.steam_cfg
_show_error = _shared.show_error


def _all_records() -> list[sm.MatchRecord]:
    """Every cached provider list (Steam today), newest first."""
    out: list[sm.MatchRecord] = []
    for p in sorted(store.read_matches_cache().get("providers") or {}):
        out.extend(sources.read_cache(p))
    return _sorted(out)


def _sorted(records: list[sm.MatchRecord]) -> list[sm.MatchRecord]:
    """By date, newest first; undated records keep to the end."""
    return sources.sorted_records(records)


# ------------------------------------------------------------- URL filters
def _load_url_filters() -> None:
    """Once per session: restore the list filters from a copied link."""
    if st.session_state.get("mflt_url_loaded"):
        return
    st.session_state["mflt_url_loaded"] = True
    q = st.query_params
    if q.get("fp") in _PERIODS:
        st.session_state["mflt_period"] = q["fp"]
    if q.get("fres") in _RESULTS:
        st.session_state["mflt_result"] = q["fres"]
    for key, sk in (("fmap", "mflt_map"), ("fkind", "mflt_kind")):
        v = q.get(key)
        if v and v != "all":
            st.session_state[sk] = v


def _sync_url_filters() -> None:
    """Keep the list filters shareable (?fp&fmap&fkind&fres)."""
    want = {
        "fp": st.session_state.get("mflt_period", "all"),
        "fmap": st.session_state.get("mflt_map", "all"),
        "fkind": st.session_state.get("mflt_kind", "all"),
        "fres": st.session_state.get("mflt_result", "all"),
    }
    for k, v in want.items():
        if v and v != "all":
            if st.query_params.get(k) != v:
                st.query_params[k] = v
        elif k in st.query_params:
            del st.query_params[k]


# -------------------------------------------------------------- demo adding
def _after_pick(paths, t) -> None:
    """Link the chosen files (a native dialog gave their real paths) and
    open the last one as the match."""
    if paths is None:
        state.set_pick_err(t("lib_no_dialog"))
        return
    added, errs = [], []
    for p in paths:
        try:
            added.append(store.add_link(p))
        except (ValueError, OSError) as e:
            errs.append(str(e))
    if added:
        shell.go_match(added[-1]["id"])
        st.session_state["_added"] = (
            added[-1]["name"] if len(added) == 1 else t("lib_n_added", n=len(added))
        )
    if errs:
        state.set_pick_err("  ".join(errs))


def _pick_file(t) -> None:
    _after_pick(
        nativedialog.pick_files(
            t("lib_pick_file"), t("lib_dialog_demos"), t("lib_dialog_all")
        ),
        t,
    )


def _add_match(t) -> None:
    """One button: the native file dialog links the demos by their path -
    nothing is copied (an upload would only duplicate the file)."""
    st.button(
        t("mt_add"),
        key="mt_add",
        on_click=_pick_file,
        args=(t,),
        help=t("lib_pick_help"),
        icon=":material/add_circle:",
        width="content",
    )


# ------------------------------------------------------------------ refresh
def _refresh(t, n: int) -> None:
    accounts = store.read_accounts()
    errs = {}
    cfg = _steam_cfg(accounts)
    try:
        records = sources.list_matches("steam", cfg, n)
        known = {r.match_id for r in records}
        for rec in sources.inbox.replays_records(cfg.replays_dir):
            if rec.match_id not in known:
                records.append(rec)
                known.add(rec.match_id)
        sources.write_cache("steam", _sorted(records))
    except sm.ProviderError as e:
        errs["steam"] = (e.key, e.kw)
    st.session_state["mt_errs"] = errs


# ----------------------------------------------------------------- actions
def _open_match(demo_id: str) -> None:
    shell.go_match(demo_id)


def _import_cb(rec: sm.MatchRecord, path: str) -> None:
    try:
        entry = sources.import_local(rec, path)
    except (OSError, ValueError) as e:
        st.session_state["mt_import_err"] = str(e)
        return
    shell.go_match(entry["id"])


def _show_import_error(t) -> None:
    if "mt_import_err" in st.session_state:
        st.error(t("lib_save_failed", err=st.session_state.pop("mt_import_err")))


def _download(t, rec: sm.MatchRecord) -> None:
    """The row's download, run AFTER the table drew its "Downloading…"
    state: the cell stays busy for the whole transfer while the status +
    progress line underneath tracks the real numbers."""
    cfg = _steam_cfg(store.read_accounts())
    status = st.status(t("mt_dl_status"))
    bar = st.progress(0.0)

    def _progress(done: int, total) -> None:
        frac = min(0.99, done / total) if total else min(0.99, done / 60_000_000)
        shown = (
            f"{store.human_size(done)} / {store.human_size(total)}"
            if total
            else store.human_size(done)
        )
        bar.progress(frac, text=shown)

    try:
        with status:
            entry = sources.fetch_demo(rec, cfg, progress=_progress)
    except sm.ProviderError as e:
        status.update(label=t("mt_dl_status"), state="error")
        st.session_state["_dl_err"] = (e.key, e.kw)
    else:
        status.update(label=t("mt_dl_done", name=entry["name"]), state="complete")
        st.session_state["_dl_done"] = entry["name"]
        # straight into the match: opening it computes the summary, so the
        # analysis starts right after the download instead of waiting for
        # an extra click on the row
        shell.go_match(entry["id"])
    st.session_state.pop("mt_dl", None)
    st.rerun()


def _wants_download(row: core.MatchRow) -> bool:
    """A provider row whose demo can be fetched right now."""
    return (
        row.entry is None
        and row.rec is not None
        and not core.manual_demo_ready(row.rec)
        and row.rec.demo in (sm.DemoState.READY, sm.DemoState.EXPIRED)
    )


def action_of(t, row: core.MatchRow) -> str:
    """The Action cell: what a click on this row does."""
    if st.session_state.get("mt_dl") == row.key:
        return t("mt_dl_busy")
    if row.entry is not None:
        if row.missing:
            return t("act_locate")
        # not analyzed yet: the click opens the match, which computes the
        # summary on the way in (the demo is parsed there anyway)
        return t("mt_open") if row.stats else t("act_analyze")
    if row.rec is None:
        return "—"
    if core.manual_demo_ready(row.rec):
        return t("mt_import")
    if _wants_download(row):
        return t("mt_download")
    return "—"


def _row_action(t, row: core.MatchRow) -> None:
    """A row click: open the match when the demo is there, else the row's
    own action (an explicit press, so downloads may run)."""
    if row.entry is not None:
        if row.missing:
            shell.locate_demo(t, row.demo_id)  # a linked demo that moved
        else:
            _open_match(row.demo_id)
            st.rerun()  # the callback only set the state; show the match now
        return
    if row.rec is None:
        return
    if core.manual_demo_ready(row.rec):
        _import_cb(row.rec, str(row.rec.demo_ref))
        st.rerun()
        return
    if _wants_download(row):
        # flag only: the render continues so THIS table draw already shows
        # "Downloading…" in the cell; the download itself runs after the
        # table (see render), keeping one render path and no remount
        if store.find_by_match(row.rec.provider, row.rec.match_id) is not None:
            st.toast(t("mt_dl_exists"))
            return
        st.session_state["mt_dl"] = row.key


# ------------------------------------------------------------- row helpers
def _map_disp(name: str | None) -> str:
    """'de_mirage' -> 'Mirage'; unknown names stay as they are."""
    return map_name(name) or "—"


def _rel_date(when: datetime | None, t) -> str:
    if when is None:
        return "—"
    now = datetime.now().astimezone()
    w = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    d = now - w
    if d < timedelta(minutes=1):
        return t("rd_now")
    if d < timedelta(hours=1):
        return t("rd_min_ago", n=int(d.total_seconds() // 60))
    if d < timedelta(hours=24):
        return t("rd_h_ago", n=int(d.total_seconds() // 3600))
    if d < timedelta(hours=48):
        return t("rd_yesterday")
    return w.astimezone().strftime("%d.%m.%y")


def _score_disp(row: core.MatchRow, t) -> str:
    if row.score is None:
        return ""
    a, b = row.score
    letter = {
        "win": t("res_l_win"),
        "loss": t("res_l_loss"),
        "tie": t("res_l_tie"),
    }.get(row.result or "", "")
    return f"{a}:{b} {letter}".strip()


def _stat(row: core.MatchRow, key: str):
    return (row.stats or {}).get(key)


def _kind_disp(kind: str, t) -> str:
    """The Source cell: the match pool (competitive / premier / faceit /
    manual upload) - a kind without its own string still reads sensibly."""
    key = f"kind_{kind}"
    label = t(key)
    return label if label != key else kind.capitalize()


# ------------------------------------------------------------------ filters
def _limit_reset() -> None:
    st.session_state["mt_limit"] = PAGE_ROWS


def _mflt_reset() -> None:
    for k, d in (
        ("mflt_period", "all"),
        ("mflt_map", "all"),
        ("mflt_kind", "all"),
        ("mflt_result", "all"),
    ):
        st.session_state[k] = d
    _limit_reset()


def _keep(row: core.MatchRow, now: datetime) -> bool:
    period = st.session_state.get("mflt_period", "all")
    if period != "all":
        if row.played_at is None:
            return False
        w = (
            row.played_at
            if row.played_at.tzinfo
            else row.played_at.replace(tzinfo=timezone.utc)
        )
        if now - w > timedelta(days=int(period)):
            return False
    mp = st.session_state.get("mflt_map", "all")
    if mp != "all" and row.map_name != mp:
        return False
    res = st.session_state.get("mflt_result", "all")
    if res != "all" and row.result != res:
        return False
    kd = st.session_state.get("mflt_kind", "all")
    if kd != "all" and row.kind != kd:
        return False
    return True


def _filters(t, rows: list[core.MatchRow], now: datetime) -> list[core.MatchRow]:
    maps = sorted({r.map_name for r in rows if r.map_name})
    present = {r.kind for r in rows}
    kinds = [k for k in core.KINDS if k in present]
    # a stale session / URL selection that this library does not have
    if st.session_state.get("mflt_map") not in ["all"] + maps:
        st.session_state["mflt_map"] = "all"
    if st.session_state.get("mflt_kind") not in ["all"] + kinds:
        st.session_state["mflt_kind"] = "all"
    if st.session_state.get("mflt_period") not in _PERIODS:
        st.session_state["mflt_period"] = "all"
    if st.session_state.get("mflt_result") not in _RESULTS:
        st.session_state["mflt_result"] = "all"
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.segmented_control(
            t("mflt_period"),
            list(_PERIODS),
            format_func=lambda v: t(f"mflt_p_{v}"),
            key="mflt_period",
            on_change=_limit_reset,
            persist_state="session",
        )
        st.segmented_control(
            t("mflt_result"),
            list(_RESULTS),
            format_func=lambda v: t("all") if v == "all" else t(f"mt_result_{v}"),
            key="mflt_result",
            on_change=_limit_reset,
            persist_state="session",
        )
        st.selectbox(
            t("mflt_map"),
            ["all"] + maps,
            format_func=lambda m: t("all") if m == "all" else _map_disp(m),
            key="mflt_map",
            on_change=_limit_reset,
            persist_state="session",
        )
        st.selectbox(
            t("mflt_mode"),
            ["all"] + kinds,
            format_func=lambda k: t("all") if k == "all" else t(f"kind_{k}"),
            key="mflt_kind",
            on_change=_limit_reset,
            persist_state="session",
        )
        st.button(t("mflt_reset"), key="mflt_reset", on_click=_mflt_reset)
    return [r for r in rows if _keep(r, now)]


# ------------------------------------------------------------------- table
def _table(t, rows: list[core.MatchRow], empty: str = "") -> None:
    def _row_click(i: int) -> None:
        # the component's trigger callback runs before the script: remember
        # the row, the render below executes the action
        st.session_state["mt_action_row"] = int(i)

    cells = {
        "mmap": [_map_disp(r.map_name) for r in rows],
        "mdate": [_rel_date(r.played_at, t) for r in rows],
        "mscore": [_score_disp(r, t) for r in rows],
        "result_raw": [r.result or "" for r in rows],
        "mkills": [_stat(r, "kills") for r in rows],
        "mdeaths": [_stat(r, "deaths") for r in rows],
        "mkdr": [_stat(r, "kd") for r in rows],
        "macc": [_stat(r, "accuracy") for r in rows],
        "msrc": [_kind_disp(r.kind, t) for r in rows],
        "src_raw": [r.kind for r in rows],
        "mact": [action_of(t, r) for r in rows],
        # the renderer's first click on a text column sorts ascending, so
        # the raw date column carries NEGATIVE timestamps (newest first)
        "date_sort": [-r.played_at.timestamp() if r.played_at else None for r in rows],
    }
    from ui import schema

    schema.table("matches", cells, key="mt_grid", on_click=_row_click, empty=empty)


# ---------------------------------------------------------- demo deletion
def _ask_clear() -> None:
    st.session_state["mt_clear_pending"] = True


def _cancel_clear() -> None:
    st.session_state.pop("mt_clear_pending", None)


def _confirm_clear() -> None:
    freed = store.delete_all_demos()
    st.session_state.pop("mt_clear_pending", None)
    for k in ("demo_id", "demo_loaded", "match"):
        st.session_state.pop(k, None)
    st.session_state["mt_freed"] = freed


def _clear_control(t) -> None:
    """Delete-all with a confirm step; None when the library is empty."""
    lib = store.list_demos()
    if st.session_state.get("mt_clear_pending"):
        with st.container(border=True):
            st.warning(t("mt_clear_q", n=len(lib)))
            c1, c2 = st.columns(2)
            c1.button(
                t("mt_clear_yes"),
                key="mt_clear_yes",
                type="primary",
                on_click=_confirm_clear,
            )
            c2.button(t("lib_delete_no"), key="mt_clear_no", on_click=_cancel_clear)
    elif lib:
        st.button(
            t("mt_clear"),
            key="mt_clear",
            on_click=_ask_clear,
            help=t("lib_delete_help"),
        )


def _start_card(t) -> None:
    """Nothing here yet: the same three-step card the empty library always
    showed (get a demo / add it / read the report)."""
    with st.container(border=True):
        st.subheader(t("start_title"))
        s1, s2, s3 = st.columns(3)
        s1.markdown(f"**1 · {t('start_1')}**")
        s1.caption(t("start_1_t"))
        s2.markdown(f"**2 · {t('start_2')}**")
        s2.caption(t("start_2_t"))
        s3.markdown(f"**3 · {t('start_3')}**")
        s3.caption(t("start_3_t"))
        st.caption(t("start_matches_hint"))
        with st.expander(t("demo_how_title")):
            st.markdown(t("demo_how"))


# ------------------------------------------------------------------- page
def _notices(t) -> None:
    """The one-shot notices a previous run left in session state: import
    errors, freed space, pick warnings, added/downloaded toasts."""
    _show_import_error(t)
    errs = st.session_state.pop("mt_errs", None) or {}
    _show_error(t, errs.get("steam"))
    if "mt_freed" in st.session_state:
        st.success(
            t("lib_freed", size=store.human_size(st.session_state.pop("mt_freed")))
        )
    _pe = state.pop_pick_err()
    if _pe is not None:
        st.warning(_pe)
    if "_added" in st.session_state:
        st.toast(t("lib_added", name=st.session_state.pop("_added")))
    if "_dl_done" in st.session_state:
        st.toast(t("mt_dl_done", name=st.session_state.pop("_dl_done")))
    if "_dl_err" in st.session_state:
        _show_error(t, st.session_state.pop("_dl_err"))


def _title_row(t) -> None:
    """The title row: heading, Refresh / setup and Add match on the right."""
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.subheader(t("mt_title"))
        if not sources.steam.helper_installed():
            st.button(
                t("mt_configure"),
                key="mt_configure",
                icon=":material/settings:",
                on_click=shell.go_page,
                args=("settings",),
                width="content",
            )
        st.button(
            t("mt_refresh"),
            key="mt_refresh",
            icon=":material/refresh:",
            width="content",
        )
        _add_match(t)


def _run_flagged_download(t, rows) -> None:
    """The flagged download runs now - the table already showed its busy
    state, the status/progress line under it tracks the real numbers."""
    if not st.session_state.get("mt_dl"):
        return
    key = st.session_state["mt_dl"]
    row = next((r for r in rows if r.key == key), None)
    if row is not None and row.rec is not None:
        _download(t, row.rec)
    else:
        st.session_state.pop("mt_dl", None)


def render(ctx) -> None:
    t = ctx.t
    _load_url_filters()
    accounts = store.read_accounts()
    if st.session_state.get("mt_refresh"):
        _refresh(t, accounts.get("match_count") or sm.DEFAULT_MATCH_COUNT)
    _notices(t)
    _title_row(t)

    rows = core.build_rows(
        _all_records(), store.list_demos(), stats_of=core.matchstats.cached_stats
    )
    if not rows:
        # the same table chrome with its empty state, then the how-to card
        _table(t, [], empty=t("no_file"))
        _start_card(t)
        _sync_url_filters()
        return

    now = datetime.now().astimezone()
    kept = _filters(t, rows, now)
    limit = st.session_state.get("mt_limit") or PAGE_ROWS
    page_rows = kept[:limit]
    st.caption(t("mflt_shown", x=len(page_rows), y=len(rows)))
    # a row click's action resolves BEFORE the table draws: a download
    # only flags mt_dl here (no rerun), so this very render already shows
    # "Downloading…" in the Action cell instead of the stale "Download"
    i = st.session_state.pop("mt_action_row", None)
    if i is not None and 0 <= i < len(page_rows):
        _row_action(t, page_rows[i])
    _table(t, page_rows, empty=t("mflt_empty"))
    if len(kept) > len(page_rows):
        st.button(
            t("mt_more", n=len(kept) - len(page_rows)),
            key="mt_more",
            icon=":material/expand_more:",
            on_click=lambda: st.session_state.update(
                mt_limit=st.session_state.get("mt_limit", PAGE_ROWS) + PAGE_ROWS
            ),
        )
    _clear_control(t)
    _run_flagged_download(t, rows)
    _sync_url_filters()
