"""
Universal data table: one component for every table in the app.

    data_table(rows, cols, key="ov_grid", selected=2, stripe=[...], on_click=cb)

- bars are the *background* of a cell (not a slider inside it): a column opts
  in with Col(bar="pct" | "rel");
- clicking a header sorts "best value first", then reversed, then resets to
  the original order (Col.best = "high" | "low" decides what "best" means);
- a row click calls on_click(row_index) before the next script run (same
  contract as the old st.dataframe on_select callbacks);
- colours are CSS variables from theme/ (var(--accent) ...), never literals.

Python builds the cell model (raw value, display text, bar fraction, tone);
the JS below only sorts and draws it. Streamlit components v2 (>=1.51).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import streamlit as st

_TONES = ("accent", "opponent", "positive", "negative", "info", "warning", "context")


@dataclass
class Col:
    key: str  # rows[key] -> display source
    label: str
    fmt: str = "{}"  # str.format spec for numbers, e.g. "{:.1f}%"
    kind: str = "num"  # num | text | badge
    bar: str | None = None  # None | "pct" (0..100) | "rel" (share of the column max)
    tone: str = "accent"  # bar colour
    best: str | None = None  # "high" | "low": what sorts first
    help: str | None = None
    sort: str | None = None  # rows[sort] -> raw value used for sorting
    tones: Mapping[str, str] | None = None  # badge colour by raw/display text
    sticky: bool = False
    left: bool | None = None  # force left alignment (default: text -> left)


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


def table_payload(
    rows: Mapping[str, Sequence],
    cols: Sequence[Col],
    *,
    selected: int | None = None,
    stripe: Sequence[str | None] | None = None,
    clickable: bool = False,
    max_height: int = 620,
    empty: str = "",
) -> dict:
    """JSON-ready model of the table (pure function: unit-testable)."""
    n = len(next(iter(rows.values()))) if rows else 0
    col_max: dict[str, float] = {}
    for c in cols:
        if c.bar == "rel":
            vals = [_num(v) for v in rows[c.key]]
            col_max[c.key] = max([v for v in vals if v is not None] or [0.0])
    out_cols = []
    for c in cols:
        numeric = c.kind == "num"
        first = "asc" if (not numeric or c.best == "low") else "desc"
        out_cols.append(
            {
                "k": c.key,
                "l": c.label,
                "t": c.kind,
                "a": "l" if (c.left if c.left is not None else not numeric) else "r",
                "b": bool(c.bar),
                "tn": c.tone if c.tone in _TONES else "accent",
                "h": c.help or "",
                "f": first,
                "s": bool(c.sticky),
            }
        )
    out_rows = []
    for i in range(n):
        cells = []
        for c in cols:
            src = rows[c.key][i]
            if c.kind == "num":
                x = _num(src)
                raw, disp = x, ("—" if x is None else _fmt(c.fmt, x))
                frac = None
                if x is not None and c.bar == "pct":
                    frac = min(1.0, max(0.0, x / 100.0))
                elif x is not None and c.bar == "rel" and col_max.get(c.key, 0) > 0:
                    frac = min(1.0, max(0.0, x / col_max[c.key]))
                tone = None
            else:
                disp = "" if src is None else str(src)
                srt = rows[c.sort][i] if c.sort else src
                raw = (
                    _num(srt)
                    if c.sort and _num(srt) is not None
                    else (None if srt is None else str(srt))
                )
                frac = None
                tone = None
                if c.tones:
                    tone = c.tones.get(str(srt)) or c.tones.get(disp)
            cells.append([raw, disp, frac, tone])
        s = stripe[i] if stripe is not None else None
        out_rows.append({"i": i, "s": s if s in _TONES else None, "c": cells})
    return {
        "cols": out_cols,
        "rows": out_rows,
        "sel": selected,
        "click": bool(clickable),
        "maxh": int(max_height),
        "empty": empty,
    }


def _renderer():
    # registered on every run on purpose: the registry lives in the Streamlit
    # runtime (AppTest and server restarts start with an empty one), and
    # re-registering an identical definition is cheap and idempotent
    return st.components.v2.component("aimaudit_table", css=_CSS, js=_JS)


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
) -> None:
    payload = table_payload(
        rows,
        cols,
        selected=selected,
        stripe=stripe,
        clickable=on_click is not None,
        max_height=max_height,
        empty=empty,
    )
    kwargs: dict = {}
    if on_click is not None:

        def _cb() -> None:
            state = st.session_state.get(key)
            idx = state.get("clicked") if hasattr(state, "get") else None
            if idx is not None:
                on_click(int(idx))

        kwargs["on_clicked_change"] = _cb
    _renderer()(key=key, data=payload, **kwargs)


_CSS = """
:host { display: block; font-family: inherit; }
.aa-wrap {
  overflow: auto;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-1);
}
.aa-t {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-family: inherit;
  font-size: var(--font-size-md);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}
.aa-t th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border-strong);
  padding: 0.5rem 0.75rem;
  text-align: start;
  white-space: nowrap;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}
.aa-t th:not(:last-child) { border-right: 1px solid var(--border-strong); }
.aa-t th:hover, .aa-t th[aria-sort] { color: var(--text-primary); }
.aa-t th:focus-visible, .aa-t tr:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.aa-t th .arrow { display: inline-block; width: 1em; margin-left: 0.25rem; color: var(--accent); visibility: hidden; }
.aa-t th[aria-sort] .arrow { visibility: visible; }
.aa-t td { padding: 0; white-space: nowrap; }
.aa-t td:not(:last-child) { border-right: 1px solid var(--border-subtle); }
.aa-t tbody tr:not(:last-child) td { border-bottom: 1px solid var(--border-subtle); }
.aa-t .cell { display: flex; position: relative; padding: 0.5rem 0.75rem; }
.aa-t th.bar, .aa-t td.bar { min-width: 5rem; }
.aa-t .val { position: relative; z-index: 1; }
.aa-t .bar-fill {
  position: absolute;
  left: 0; top: 0; bottom: 0;
  background: color-mix(in srgb, var(--tone) 20%, transparent);
}
.aa-t .badge {
  position: relative; z-index: 1;
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--radius-pill);
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
  color: var(--tone, var(--text-primary));
  background: color-mix(in srgb, var(--tone, var(--text-secondary)) 16%, transparent);
}
.aa-t tbody tr:hover td { background: var(--surface-2); }
.aa-t tbody tr.clk { cursor: pointer; }
.aa-t tbody tr.sel td { background: var(--surface-selected); }
.aa-t tbody tr.sel td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
.aa-t tbody tr.stripe td:first-child { box-shadow: inset 3px 0 0 var(--stripe); }
.aa-t tbody tr.sel.stripe td:first-child { box-shadow: inset 3px 0 0 var(--stripe); }
.aa-t th.stick, .aa-t td.stick { position: sticky; left: 0; z-index: 2; background: var(--surface-1); }
.aa-t th.stick { z-index: 3; background: var(--surface-2); }
.aa-t tbody tr:hover td.stick { background: var(--surface-2); }
.aa-t tbody tr.sel td.stick { background: var(--surface-selected); }
.aa-empty { padding: 18px; color: var(--text-muted); }
.tone-accent { --tone: var(--accent); }
.tone-opponent { --tone: var(--opponent); }
.tone-positive { --tone: var(--positive); }
.tone-negative { --tone: var(--negative); }
.tone-info { --tone: var(--info); }
.tone-warning { --tone: var(--warning); }
.tone-context { --tone: var(--context); }
.stripe-accent { --stripe: var(--accent); }
.stripe-opponent { --stripe: var(--opponent); }
.stripe-positive { --stripe: var(--positive); }
.stripe-negative { --stripe: var(--negative); }
.stripe-info { --stripe: var(--info); }
.stripe-warning { --stripe: var(--warning); }
.stripe-context { --stripe: var(--context); }
"""

_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement;
  const S = root.__aa || (root.__aa = { sig: "", col: -1, dir: 0, top: 0, left: 0 });
  let host = root.querySelector(".aa-host");
  if (!host) {
    host = document.createElement("div");
    host.className = "aa-host";
    root.appendChild(host);
  }
  const sig = data.cols.map((c) => c.k).join("|");
  if (sig !== S.sig) { S.sig = sig; S.col = -1; S.dir = 0; S.top = 0; S.left = 0; }

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const firstDir = (c) => (c.f === "asc" ? 1 : -1);

  function ordered() {
    const rows = data.rows.slice();
    if (S.col < 0) return rows;
    const ci = S.col, dir = S.dir;
    rows.sort((a, b) => {
      const x = a.c[ci][0], y = b.c[ci][0];
      if (x == null && y == null) return a.i - b.i;
      if (x == null) return 1;
      if (y == null) return -1;
      const r = typeof x === "number" && typeof y === "number"
        ? x - y
        : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" });
      return r === 0 ? a.i - b.i : r * dir;
    });
    return rows;
  }

  function onHeader(ci) {
    const c = data.cols[ci];
    if (S.col !== ci) { S.col = ci; S.dir = firstDir(c); }
    else if (S.dir === firstDir(c)) { S.dir = -S.dir; }
    else { S.col = -1; S.dir = 0; }
    draw();
  }

  function draw() {
    const prev = host.querySelector(".aa-wrap");
    if (prev) { S.top = prev.scrollTop; S.left = prev.scrollLeft; }
    const wrap = el("div", "aa-wrap");
    wrap.style.maxHeight = data.maxh + "px";
    if (!data.rows.length) {
      wrap.appendChild(el("div", "aa-empty", data.empty || ""));
      host.replaceChildren(wrap);
      return;
    }
    const table = el("table", "aa-t");
    const trh = el("tr");
    data.cols.forEach((c, ci) => {
      const th = el("th", [c.a === "l" ? "l" : "", c.b ? "bar" : "", c.s ? "stick" : ""].join(" ").trim());
      th.appendChild(el("span", "lbl", c.l));
      const active = S.col === ci;
      th.appendChild(el("span", "arrow", active && S.dir === 1 ? "\\u25B2" : "\\u25BC"));
      if (active) th.setAttribute("aria-sort", S.dir === 1 ? "ascending" : "descending");
      if (c.h) th.title = c.h;
      th.tabIndex = 0;
      th.addEventListener("click", () => onHeader(ci));
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onHeader(ci); }
      });
      trh.appendChild(th);
    });
    const thead = el("thead");
    thead.appendChild(trh);
    const tbody = el("tbody");
    ordered().forEach((r) => {
      const cls = [];
      if (r.s) cls.push("stripe", "stripe-" + r.s);
      if (r.i === data.sel) cls.push("sel");
      if (data.click) cls.push("clk");
      const tr = el("tr", cls.join(" "));
      if (data.click) {
        tr.tabIndex = 0;
        const go = () => setTriggerValue("clicked", r.i);
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      }
      r.c.forEach((cell, ci) => {
        const c = data.cols[ci];
        const td = el("td", [c.a === "l" ? "l" : "", c.b ? "bar" : "", c.s ? "stick" : ""].join(" ").trim());
        const box = el("div", "cell tone-" + c.tn);
        if (cell[2] != null && cell[2] > 0.004) {
          const fill = el("div", "bar-fill");
          fill.style.width = (cell[2] * 100).toFixed(1) + "%";
          box.appendChild(fill);
        }
        if (c.t === "badge" && cell[1] !== "") {
          box.appendChild(el("span", "badge" + (cell[3] ? " tone-" + cell[3] : ""), cell[1]));
        } else {
          box.appendChild(el("span", "val", cell[1]));
        }
        td.appendChild(box);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    wrap.appendChild(table);
    host.replaceChildren(wrap);
    wrap.scrollTop = S.top;
    wrap.scrollLeft = S.left;
  }
  draw();
}
"""
