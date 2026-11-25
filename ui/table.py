"""
Universal data table: one component for every table in the app.

    data_table(rows, cols, key="ov_grid", selected=2, stripe=[...], on_click=cb)

- bars are the *background* of a cell (not a slider inside it): a column opts
  in with Col(bar="pct" | "rel");
- clicking a header sorts "best value first", then reversed, then resets to
  the original order (Col.best = "high" | "low" decides what "best" means);
- a row click calls on_click(row_index) before the next script run (same
  contract as the old st.dataframe on_select callbacks);
- Col(grade=...) colours the value itself by quality, red -> text -> green,
  from the tier tokens ("auto" = position inside the column, or fixed
  thresholds); one mechanism for every column;
- colours are CSS variables from theme/ (var(--accent) ...), never literals.

Python builds the cell model (raw value, display text, bar fraction, tone);
ui/table.js only sorts and draws it. Streamlit components v2 (>=1.51).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Collection, Mapping, Sequence


from ui import component

_TONES = ("accent", "opponent", "positive", "negative", "info", "warning", "context")


@dataclass
class Col:
    key: str  # rows[key] -> display source
    label: str
    fmt: str = "{}"  # str.format spec for numbers, e.g. "{:.1f}%"
    kind: str = "num"  # num | text | badge | icon
    bar: str | None = None  # None | "pct" (0..100) | "rel" (share of the column max)
    tone: str = "accent"  # bar colour
    best: str | None = None  # "high" | "low": what sorts first
    help: str | None = None
    sort: str | None = None  # rows[sort] -> raw value used for sorting
    tones: Mapping[str, str] | None = None  # badge colour by raw/display text
    sticky: bool = False
    # rows[title_by] -> the label of an icon cell: its tooltip AND its text
    # fallback when the icon set is not installed ("N/A" for no weapon)
    title_by: str | None = None
    # colour of the VALUE by quality, from tier tokens (bad -> poor -> mid -> text -> good -> elite):
    #   None                  no colouring
    #   "auto"                position inside this column (best value = greenest);
    #                         "best" decides the direction, needs >= 3 distinct values
    #   (bad, good)           linear between two fixed values; their order sets the direction
    #   (bad, ok, good)       like above, but `ok` is the neutral text colour:
    #                         below it the colour slides to red, above it to green
    grade: str | tuple[float, ...] | None = None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _fmt(spec: str, x: float) -> str:
    try:
        return spec.format(x)
    except (ValueError, TypeError):
        return spec.format(int(round(x)))


_AUTO_MIN_VALUES = 3


def _grade(
    x: float | None, grade, lo: float | None, hi: float | None, best: str | None
):
    """Quality 0.0 (worst colour) .. 1.0 (best colour); None = do not colour."""
    if x is None or grade is None:
        return None
    if grade == "auto":
        if lo is None or hi is None or hi <= lo:
            return None
        s = (x - lo) / (hi - lo)
        return round(min(1.0, max(0.0, 1.0 - s if best == "low" else s)), 3)
    bad, good = grade[0], grade[-1]
    if bad == good:
        return None
    p = (x - bad) / (good - bad)  # progress along bad -> good
    if len(grade) == 3:
        po = (grade[1] - bad) / (good - bad)  # where "ok" sits on that line
        if 0.0 < po < 1.0:
            p = 0.6 * p / po if p <= po else 0.6 + 0.4 * (p - po) / (1.0 - po)
    return round(min(1.0, max(0.0, p)), 3)


def _rel_maxima(rows: Mapping[str, Sequence], cols: Sequence[Col]) -> dict[str, float]:
    """Per-column max of the numeric values (bar='rel' denominators)."""
    out: dict[str, float] = {}
    for c in cols:
        if c.bar == "rel":
            vals = [_num(v) for v in rows[c.key]]
            out[c.key] = max([v for v in vals if v is not None] or [0.0])
    return out


def _auto_ranges(
    rows: Mapping[str, Sequence], cols: Sequence[Col]
) -> dict[str, tuple[float | None, float | None]]:
    """Per-column (lo, hi) for grade='auto' (None until enough values)."""
    out: dict[str, tuple[float | None, float | None]] = {}
    for c in cols:
        if c.grade == "auto" and c.kind == "num":
            vals = sorted({v for v in (_num(v) for v in rows[c.key]) if v is not None})
            out[c.key] = (
                (vals[0], vals[-1]) if len(vals) >= _AUTO_MIN_VALUES else (None, None)
            )
    return out


def _col_payload(c: Col) -> dict:
    """The header entry for one column."""
    numeric = c.kind == "num"
    first = "asc" if (not numeric or c.best == "low") else "desc"
    return {
        "k": c.key,
        "l": c.label,
        "t": c.kind,
        "b": bool(c.bar),
        "tn": c.tone if c.tone in _TONES else "accent",
        "h": c.help or "",
        "f": first,
        "s": bool(c.sticky),
    }


def _num_cell(c: Col, src, col_max, col_rng) -> list:
    """A numeric cell: value, display, bar fraction, grade score."""
    x = _num(src)
    raw, disp = x, ("—" if x is None else _fmt(c.fmt, x))
    frac = None
    if x is not None and c.bar == "pct":
        frac = min(1.0, max(0.0, x / 100.0))
    elif x is not None and c.bar == "rel" and col_max.get(c.key, 0) > 0:
        frac = min(1.0, max(0.0, x / col_max[c.key]))
    lo, hi = col_rng.get(c.key, (None, None))
    return [raw, disp, frac, None, _grade(x, c.grade, lo, hi, c.best)]


def _icon_cell(c: Col, rows: Mapping[str, Sequence], i: int, src) -> list:
    """An icon cell: raw is the icon key (or the plain name when no icon
    exists), disp is the localized name - the tooltip, and the visible
    text when the icon set is missing ("N/A" for no weapon)."""
    name = "" if src is None else str(src)
    disp = (
        str(rows[c.title_by][i])
        if c.title_by and rows[c.title_by][i] is not None
        else name
    ) or "—"
    return [name or None, disp, None, None, None]


def _text_cell(c: Col, rows: Mapping[str, Sequence], i: int, src) -> list:
    """A text/badge cell: sort value, display, optional tone mapping."""
    disp = "" if src is None else str(src)
    srt = rows[c.sort][i] if c.sort else src
    raw = (
        _num(srt)
        if c.sort and _num(srt) is not None
        else (None if srt is None else str(srt))
    )
    tone = None
    if c.tones:
        tone = c.tones.get(str(srt)) or c.tones.get(disp)
    return [raw, disp, None, tone, None]


def table_payload(
    rows: Mapping[str, Sequence],
    cols: Sequence[Col],
    *,
    selected: int | None = None,
    stripe: Sequence[str | None] | None = None,
    clickable: bool = False,
    max_height: int = 620,
    empty: str = "",
    icons: Mapping[str, Sequence] | None = None,
    checkable: Sequence[bool] | None = None,
    row_keys: Sequence[str] | None = None,
    checked: Collection[str] | None = None,
) -> dict:
    """JSON-ready model of the table (pure function: unit-testable).

    ``icons`` is {icon key: [data-uri, w, h]} for every icon the icon-kind
    cells of THIS table reference (ui/icons.py builds it); it travels with
    the payload so the renderer can deduplicate and the SVG never lands
    in the DOM as anything but an <img>/mask source.

    ``checkable`` (one bool per row) adds the checkbox column: the rows
    marked True get a checkbox, the header one toggles all of them at
    once. Every checkable row needs a stable ``row_keys`` entry; the
    current selection is ``checked`` (a set of those keys), and each
    change reports the whole selection back through the "checked" event."""
    n = len(next(iter(rows.values()))) if rows else 0
    col_max = _rel_maxima(rows, cols)
    col_rng = _auto_ranges(rows, cols)
    out_cols = [_col_payload(c) for c in cols]
    has_checks = bool(checkable) and any(checkable)
    out_rows = []
    for i in range(n):
        cells = []
        for c in cols:
            src = rows[c.key][i]
            if c.kind == "num":
                cells.append(_num_cell(c, src, col_max, col_rng))
            elif c.kind == "icon":
                cells.append(_icon_cell(c, rows, i, src))
            else:
                cells.append(_text_cell(c, rows, i, src))
        s = stripe[i] if stripe is not None else None
        key = row_keys[i] if row_keys is not None and i < len(row_keys) else str(i)
        ck = bool(checkable[i]) if checkable is not None and i < len(checkable) else False
        out_rows.append(
            {"i": i, "s": s if s in _TONES else None, "c": cells, "k": key, "ck": ck}
        )
    return {
        "cols": out_cols,
        "rows": out_rows,
        "sel": selected,
        "click": bool(clickable),
        "maxh": int(max_height),
        "empty": empty,
        "icons": {str(k): list(v) for k, v in (icons or {}).items()},
        "chk": has_checks,
        "checked": sorted(k for k in (checked or set()) if k),
    }


def data_table(
    rows: Mapping[str, Sequence],
    cols: Sequence[Col],
    *,
    key: str,
    selected: int | None = None,
    stripe: Sequence[str | None] | None = None,
    on_click: Callable[[int], None] | None = None,
    max_height: int = 620,
    empty: str = "",
    icons: Mapping[str, Sequence] | None = None,
    checkable: Sequence[bool] | None = None,
    row_keys: Sequence[str] | None = None,
    checked: Collection[str] | None = None,
    on_check: Callable[[set], None] | None = None,
) -> None:
    payload = table_payload(
        rows,
        cols,
        selected=selected,
        stripe=stripe,
        clickable=on_click is not None,
        max_height=max_height,
        empty=empty,
        icons=icons,
        checkable=checkable,
        row_keys=row_keys,
        checked=checked,
    )
    events = {}
    if on_check is not None:
        events["checked"] = component.event_callback_set(key, "checked", on_check)
    component.render(
        "table", key=key, data=payload,
        trigger="clicked", handler=on_click, events=events,
    )
