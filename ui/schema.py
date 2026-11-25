"""
Table schema: ui/tables.toml is the single source of truth for the grid
tables (formats, sort directions, attributes, grading rules), the same way
theme/tokens.toml is for the palette.

    schema.table("overview", rows, key="ov_grid", selected=..., on_click=...)

replaces the hand-written Col(...) lists in app.py: a new column or table
is a few lines of TOML, not UI code. This module only loads and merges the
data - pure functions, no Streamlit at import time. Labels follow the app
language (the registry key `lang`, read per call so a language switch is
picked up on the next rerun).

validate() checks every rule the file must hold; tests/check_ui.py runs it
and feeds it deliberately broken specs (negative tests).
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import i18n
from ui.table import _TONES, Col, data_table

_DIR = Path(__file__).resolve().parent

# every attr a [metric.*] or [table.*.override.*] entry may set
_ATTRS = (
    "label",
    "help",
    "kind",
    "fmt",
    "best",
    "grade",
    "bar",
    "tone",
    "sort_by",
    "sticky",
    "tones",
    "title_by",
)
_KINDS = ("num", "text", "badge", "icon")
_BARS = ("none", "pct", "rel")
_BESTS = ("none", "high", "low")
# tone names the renderer understands (ui/table.py); _-prefixed there, but
# this is the same package


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(_DIR / "tables.toml", "rb") as fh:
        return tomllib.load(fh)


def _translator():
    """The app's t(): the language lives in the registry key `lang`."""
    lang = None
    try:
        import state

        lang = state.get_lang()
    except Exception:  # noqa: BLE001 - bare mode (validate/unit tests): en
        lang = None
    return i18n.make_t(lang or next(iter(i18n.LANGS)))


def _norm(v, none_word):
    """TOML has no null: 'none' (or absent) means None."""
    return None if v is None or v == none_word else v


def _grade(v):
    v = _norm(v, "none")
    if v is None or v == "auto":
        return v
    return tuple(v) if isinstance(v, (list, tuple)) else v


def _merged(spec: dict, table_id: str, col_id: str) -> dict:
    """Metric defaults with the table's override on top."""
    metric = spec["metric"].get(col_id)
    if metric is None:
        raise KeyError(f"[table.{table_id}] column {col_id!r} has no [metric.{col_id}]")
    out = dict(metric)
    out.update(spec["table"][table_id].get("override", {}).get(col_id, {}))
    return out


def build_cols(table_id: str, *, all_columns: bool = False, t=None) -> list[Col]:
    """Col list for a table: table order, metric + override per column."""
    spec = _load()
    if table_id not in spec.get("table", {}):
        raise KeyError(f"unknown table {table_id!r}")
    tbl = spec["table"][table_id]
    shown = tbl["columns"] if all_columns else tbl.get("basic", tbl["columns"])
    if t is None:
        t = _translator()
    cols = []
    for col_id in shown:
        m = _merged(spec, table_id, col_id)
        cols.append(
            Col(
                key=col_id,
                label=t(m.get("label", f"col_{col_id}")),
                fmt=m.get("fmt", "{}"),
                kind=m.get("kind", "num"),
                bar=_norm(m.get("bar"), "none"),
                tone=m.get("tone", "accent"),
                best=_norm(m.get("best"), "none"),
                help=t(m["help"]) if m.get("help") else None,
                sort=_norm(m.get("sort_by"), "none"),
                tones=m.get("tones"),
                sticky=bool(m.get("sticky", False)),
                grade=_grade(m.get("grade")),
                title_by=_norm(m.get("title_by"), "none"),
            )
        )
    return cols


def columns(table_id: str) -> list[str]:
    """Every data column a table displays (in order) - what callers build
    their rows dict against."""
    return list(_load()["table"][table_id]["columns"])


def table(
    table_id: str,
    rows: dict,
    *,
    key: str,
    selected: int | None = None,
    on_click=None,
    all_columns: bool = False,
    t=None,
    empty: str = "",
    icons: dict | None = None,
    checkable: "Sequence[bool] | None" = None,
    row_keys: "Sequence[str] | None" = None,
    checked=None,
    on_check=None,
) -> None:
    """One call instead of a hand-written data_table(...): cols, stripe and
    height come from tables.toml; selected/on_click are runtime values.
    ``icons`` is the {key: [data-uri, w, h]} set for the icon columns.
    ``checkable`` / ``row_keys`` / ``checked`` / ``on_check`` add the
    checkbox column (see ui.table.data_table) - under the same
    `selectable` gate as selected/on_click."""
    spec = _load()["table"].get(table_id)
    if spec is None:
        raise KeyError(f"unknown table {table_id!r}")
    if (
        selected is not None
        or on_click is not None
        or checkable is not None
        or on_check is not None
    ) and not spec.get("selectable", False):
        raise ValueError(f"[table.{table_id}] does not take selected/on_click")
    stripe = None
    sp = spec.get("stripe")
    if sp:
        mp = sp.get("map", {})
        dflt = sp.get("default")
        stripe = [mp.get(str(v), dflt) for v in rows[sp["field"]]]
    data_table(
        rows,
        build_cols(table_id, all_columns=all_columns, t=t),
        key=key,
        selected=selected,
        stripe=stripe,
        on_click=on_click,
        max_height=spec.get("max_height", 620),
        empty=empty,
        icons=icons,
        checkable=checkable,
        row_keys=row_keys,
        checked=checked,
        on_check=on_check,
    )


def _fmt_ok(spec: str) -> bool:
    """A format the renderer could apply to a number (float first, then
    the rounded-int fallback ui.table._fmt uses)."""
    try:
        spec.format(12.3456)
        return True
    except (ValueError, TypeError):
        pass
    try:
        spec.format(12)
        return True
    except (ValueError, TypeError):
        return False


def _grade_errs(v, where, errs):
    if v is None or v == "auto" or v == "none":
        return
    if not isinstance(v, (list, tuple)) or len(v) not in (2, 3):
        errs.append(f"{where}: grade must be none/auto/[bad, good]/[bad, ok, good]")
        return
    if not all(isinstance(x, (int, float)) for x in v):
        errs.append(f"{where}: grade thresholds must be numbers")
        return
    bad, good = v[0], v[-1]
    if bad == good:
        errs.append(f"{where}: grade bad == good")
    if len(v) == 3 and not (min(v[0], v[2]) < v[1] < max(v[0], v[2])):
        errs.append(f"{where}: grade ok is not between bad and good")


def _keys(lang):
    return set(i18n.STR.get(lang, {}))


def _tone_ok(name):
    if name not in _TONES:
        return False
    try:
        import theme

        for th in theme.available_themes():
            theme.tok(name, th)
        return True
    except Exception:  # noqa: BLE001 - unknown token -> invalid tone
        return False


def _metric_display_errs(mid: str, m: dict, kind: str, errs: list[str]) -> None:
    """fmt/bar/best/grade: valid values, and only meaningful for kind=num."""
    where = f"[metric.{mid}]"
    if "fmt" in m:
        if kind != "num":
            errs.append(f"{where}: fmt is ignored for kind={kind}")
        elif not _fmt_ok(m["fmt"]):
            errs.append(f"{where}: fmt {m['fmt']!r} cannot format a number")
    if m.get("bar", "none") not in _BARS:
        errs.append(f"{where}: bar must be one of {_BARS}")
    elif m.get("bar", "none") != "none" and kind != "num":
        errs.append(f"{where}: bar is ignored for kind={kind}")
    if m.get("best", "none") not in _BESTS:
        errs.append(f"{where}: best must be one of {_BESTS}")
    elif m.get("best", "none") != "none" and kind != "num":
        errs.append(f"{where}: best is ignored for kind={kind}")
    _grade_errs(m.get("grade"), where, errs)
    if m.get("grade", "none") not in ("none", "auto") and kind != "num":
        errs.append(f"{where}: grade is ignored for kind={kind}")


def _metric_tone_errs(mid: str, m: dict, kind: str, errs: list[str]) -> None:
    """tone/tones: tokens must resolve, tones only for badge columns."""
    where = f"[metric.{mid}]"
    if m.get("tone", "accent") and not _tone_ok(m.get("tone", "accent")):
        errs.append(f"{where}: unknown tone/token {m.get('tone')!r}")
    if m.get("tones") is None:
        return
    if kind == "num":
        errs.append(f"{where}: tones is ignored for kind=num")
    elif kind == "icon":
        errs.append(f"{where}: tones is ignored for kind=icon")
    else:
        for val, tone in m["tones"].items():
            if not _tone_ok(tone):
                errs.append(f"{where}: tones[{val!r}] unknown tone {tone!r}")


def _validate_metric(mid: str, m: dict, errs: list[str], en: set) -> None:
    """Rules a single [metric.*] entry must hold (appends to errs)."""
    where = f"[metric.{mid}]"
    for attr in m:
        if attr not in _ATTRS:
            errs.append(f"{where}: unknown attr {attr!r}")

    def i18n_ok(k):
        return k in en

    label = m.get("label", f"col_{mid}")
    if not i18n_ok(label):
        errs.append(f"{where}: label key {label!r} missing in en")
    if m.get("help") and not i18n_ok(m["help"]):
        errs.append(f"{where}: help key {m['help']!r} missing in en")
    kind = m.get("kind", "num")
    if kind not in _KINDS:
        errs.append(f"{where}: kind must be one of {_KINDS}")
        kind = "num"
    _metric_display_errs(mid, m, kind, errs)
    _metric_tone_errs(mid, m, kind, errs)
    if not isinstance(m.get("sticky", False), bool):
        errs.append(f"{where}: sticky must be a boolean")
    if kind == "icon":
        # the tooltip / fallback text column is what makes an icon cell
        # usable without the icon set - it must resolve in every table
        # that shows the column (checked below, like sort_by)
        tb = _norm(m.get("title_by"), "none")
        if tb is None:
            errs.append(f"{where}: kind=icon needs title_by (the tooltip column)")
    elif m.get("title_by") is not None and _norm(m.get("title_by"), "none") is not None:
        errs.append(f"{where}: title_by is ignored for kind={kind}")


def _table_colset_errs(where: str, tbl: dict, metrics: dict) -> list[str]:
    """columns/basic/extra/override: membership and no duplicates."""
    errs: list[str] = []
    cols = tbl.get("columns", [])
    if not cols:
        errs.append(f"{where}: no columns")
    if len(set(cols)) != len(cols):
        errs.append(f"{where}: duplicate columns")
    for c in cols:
        if c not in metrics:
            errs.append(f"{where}: column {c!r} has no [metric.{c}]")
    basic = tbl.get("basic")
    if basic is not None:
        if len(set(basic)) != len(basic):
            errs.append(f"{where}: duplicate basic columns")
        for c in basic:
            if c not in cols:
                errs.append(f"{where}: basic column {c!r} not in columns")
    extra = tbl.get("extra", [])
    if len(set(extra)) != len(extra):
        errs.append(f"{where}: duplicate extra columns")
    if set(extra) & set(cols):
        errs.append(f"{where}: extra columns must not be displayed")
    for c, o in tbl.get("override", {}).items():
        if c not in cols:
            errs.append(f"{where}.override: {c!r} not a column of this table")
        for attr in o:
            if attr not in _ATTRS:
                errs.append(f"{where}.override.{c}: unknown attr {attr!r}")
    return errs


def _validate_table(tid: str, tbl: dict, metrics: dict, errs: list[str]) -> None:
    """Rules a single [table.*] entry must hold (appends to errs)."""
    where = f"[table.{tid}]"
    errs += _table_colset_errs(where, tbl, metrics)
    avail = set(tbl.get("columns", [])) | set(tbl.get("extra", []))
    _validate_stripe(where, tbl.get("stripe"), avail, errs)
    mh = tbl.get("max_height", 620)
    if not (isinstance(mh, int) and mh > 0):
        errs.append(f"{where}: max_height must be a positive int")
    if not isinstance(tbl.get("selectable", False), bool):
        errs.append(f"{where}: selectable must be a boolean")
    # sort_by and title_by must resolve inside this table (checked per
    # column, the metric alone does not know which tables show it)
    for c in tbl.get("columns", []):
        if c not in metrics:
            continue
        m = _merged({"metric": metrics, "table": {tid: tbl}}, tid, c)
        sb = _norm(m.get("sort_by"), "none")
        if sb is not None and sb not in avail:
            errs.append(f"{where}: sort_by {sb!r} of column {c!r} unknown")
        tb = _norm(m.get("title_by"), "none")
        if tb is not None and tb not in avail:
            errs.append(f"{where}: title_by {tb!r} of column {c!r} unknown")


def _validate_stripe(where: str, sp: dict | None, avail: set, errs: list[str]) -> None:
    """Stripe rules: the field and every mapped tone must resolve."""
    if not sp:
        return
    if sp.get("field") not in avail:
        errs.append(f"{where}: stripe field {sp.get('field')!r} unknown")
    for val, tone in sp.get("map", {}).items():
        if not _tone_ok(tone):
            errs.append(f"{where}: stripe {val!r} unknown tone {tone!r}")
    if sp.get("default") is not None and not _tone_ok(sp["default"]):
        errs.append(f"{where}: stripe default unknown tone")


def validate(spec: dict | None = None) -> list[str]:
    """Every rule tables.toml must hold; [] means ok. Accepts an already
    parsed spec so tests can feed deliberately broken ones."""
    errs: list[str] = []
    if spec is None:
        spec = _load()
    metrics = spec.get("metric", {})
    tables = spec.get("table", {})
    en = _keys("en")
    for mid, m in metrics.items():
        _validate_metric(mid, m, errs, en)
    for tid, tbl in tables.items():
        _validate_table(tid, tbl, metrics, errs)
    return errs
