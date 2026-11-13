"""
Duel strip: fast duel / round navigation for the Timeline page.

    duel_strip(items, selected, *, key, on_select, round_label, labels)

- every duel is a chip (✓ kill / ✕ death) grouped under its round badge;
  clicking a chip — or a round badge, which opens the round's first duel —
  jumps straight to that duel, so any duel is <=2 actions away (scroll the
  strip + click, or Shift+←/→ to the round, then ←/→);
- ◀ / ▶ walk ALL duels with wrap-around: the whole match from one key;
- « / » jump between rounds; Home / End go to the first / last duel;
- the same actions from the keyboard anywhere on the page: ← / → duel,
  Shift+← / → round, Home / End ends. The listener yields inside inputs,
  selects and grids, so native widgets keep their arrow keys;
- colours are CSS variables from theme/ only (var(--accent) ...), like
  ui/table.py; Python builds the payload (strip_payload is pure and
  unit-tested), the JS only draws it and reports back through the "sel"
  trigger. Streamlit components v2 (>=1.51).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence

import streamlit as st


def _int_or_none(v: Any) -> int | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def strip_payload(
    items: Sequence[Mapping[str, Any]],
    selected: int | None,
    *,
    round_label: Callable[[int | None, int], str],
    labels: Mapping[str, str] | None = None,
) -> dict:
    """JSON-ready model of the strip (pure function: unit-testable).

    ``items``: one dict per duel — {"round": int|None, "result":
    "kill"|"death", "tip": str}. Duels are grouped by their round
    (consecutive runs of the same round share one badge), ``rstarts`` is
    the first duel index of every group so the JS can jump between
    rounds without its own grouping logic, and ``sel`` is clamped into
    range (an empty strip selects 0).
    """
    out_items = []
    for i, it in enumerate(items):
        res = str(it.get("result") or "")
        out_items.append(
            {
                "i": i,
                "r": _int_or_none(it.get("round")),
                "w": "kill" if res == "kill" else "death",
                "tip": str(it.get("tip") or ""),
            }
        )
    groups: list[dict] = []
    for it in out_items:
        if groups and groups[-1]["r"] == it["r"]:
            groups[-1]["items"].append(it)
        else:
            groups.append({"r": it["r"], "items": [it]})
    for g in groups:
        g["tip"] = round_label(g["r"], len(g["items"]))
    n = len(out_items)
    sel = _int_or_none(selected)
    if sel is None or n == 0 or not (0 <= sel < n):
        sel = 0
    lab = {k: str(v or "") for k, v in (labels or {}).items()}
    return {
        "groups": groups,
        "rstarts": [g["items"][0]["i"] for g in groups],
        "sel": sel,
        "n": n,
        "lab": lab,
    }


def _renderer():
    # registered on every run: the registry lives in the Streamlit
    # runtime (AppTest and server restarts start with an empty one), and
    # re-registering an identical definition is cheap and idempotent
    return st.components.v2.component("aimaudit_strip", css=_CSS, js=_JS)


def duel_strip(
    items: Sequence[Mapping[str, Any]],
    selected: int | None,
    *,
    key: str,
    on_select: Callable[[int], None] | None = None,
    round_label: Callable[[int | None, int], str],
    labels: Mapping[str, str] | None = None,
) -> None:
    """Render the duel strip; a chip click / hotkey calls on_select(i)."""
    payload = strip_payload(items, selected, round_label=round_label, labels=labels)
    kwargs: dict = {}
    if on_select is not None:

        def _cb() -> None:
            state = st.session_state.get(key)
            idx = state.get("sel") if hasattr(state, "get") else None
            if idx is not None:
                on_select(int(idx))

        kwargs["on_sel_change"] = _cb
    _renderer()(key=key, data=payload, **kwargs)


_CSS = """
:host { display: block; }
.aa-strip {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-family: inherit;
  color: var(--text-primary);
}
.aa-sbtn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.7rem;
  height: 1.7rem;
  padding: 0;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: var(--font-size-sm);
  line-height: 1;
  cursor: pointer;
}
.aa-sbtn:hover { color: var(--accent); border-color: var(--accent); }
.aa-sbtn:focus-visible,
.aa-chip:focus-visible,
.aa-rd:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.aa-track {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  overflow-x: auto;
  padding: 0.25rem 0.35rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-1);
  scrollbar-width: thin;
}
.aa-rd {
  flex: none;
  display: inline-flex;
  align-items: center;
  padding: 0.05rem 0.45rem;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  background: var(--surface-2);
  color: var(--text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
}
.aa-rd:hover { color: var(--accent); border-color: var(--accent); }
.aa-rd.cur {
  color: var(--accent);
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}
.aa-chip {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.55rem;
  height: 1.55rem;
  padding: 0;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  font-family: inherit;
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
  line-height: 1;
  cursor: pointer;
}
.aa-chip.k { color: var(--positive); }
.aa-chip.d { color: var(--negative); }
.aa-chip:hover { border-color: var(--accent); }
.aa-chip.cur {
  border-color: var(--accent);
  background: var(--surface-selected);
  box-shadow: inset 0 0 0 1px var(--accent);
}
.aa-pos {
  flex: none;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
"""

_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement;
  // per-instance state: the keydown listener is added once and reads the
  // latest payload through S; S.cur tracks the selection optimistically so
  // several quick key presses before a rerun still all count
  const S = root.__aa || (root.__aa = { data: null, cur: 0, onKey: null });
  S.data = data;
  S.cur = data.sel;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  function go(i) {
    const n = S.data.n;
    if (!n) return;
    i = ((i % n) + n) % n;
    if (i !== S.cur) {
      S.cur = i;
      setTriggerValue("sel", i);
    }
  }

  function step(d) { go(S.cur + d); }

  function rstep(d) {
    const rs = S.data.rstarts;
    if (!rs.length) return;
    let g = rs.length - 1;
    for (let k = 0; k < rs.length; k++) {
      if (rs[k] <= S.cur) g = k;
      else break;
    }
    go(rs[(g + d + rs.length) % rs.length]);
  }

  function onKey(e) {
    // the strip is gone (page switched): remove the listener for good
    if (!root.isConnected) {
      document.removeEventListener("keydown", S.onKey);
      S.onKey = null;
      return;
    }
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const k = e.key;
    if (k !== "ArrowLeft" && k !== "ArrowRight" && k !== "Home" && k !== "End") return;
    const d = S.data;
    if (!d || !d.n) return;
    // yield inside anything that owns its arrow keys: text inputs,
    // selectboxes (combobox/listbox), data grids, editable areas
    const ae = document.activeElement;
    if (ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" ||
               ae.tagName === "SELECT" || ae.isContentEditable)) return;
    if (ae && ae.closest && ae.closest(
      '[role="combobox"],[role="listbox"],[role="grid"],[role="spinbutton"],[contenteditable]'
    )) return;
    e.preventDefault();
    if (k === "Home") { go(0); return; }
    if (k === "End") { go(d.n - 1); return; }
    if (e.shiftKey) { rstep(k === "ArrowLeft" ? -1 : 1); return; }
    step(k === "ArrowLeft" ? -1 : 1);
  }
  if (!S.onKey) {
    S.onKey = onKey;
    document.addEventListener("keydown", onKey);
  }

  // parentElement carries Streamlit's injected <style> (and anything
  // else it owns): never replaceChildren the root itself - only the
  // div this component owns, exactly like ui/table.py does
  let host = root.querySelector(".aa-strip-host");
  if (!host) {
    host = el("div", "aa-strip-host");
    root.appendChild(host);
  }
  const strip = el("div", "aa-strip");
  strip.setAttribute("role", "group");
  if (data.lab.duel) strip.setAttribute("aria-label", data.lab.duel);

  const btn = (cls, ch, title, fn) => {
    const b = el("button", "aa-sbtn " + cls, ch);
    b.type = "button";
    if (title) {
      b.title = title;
      b.setAttribute("aria-label", title);
    }
    b.addEventListener("click", fn);
    return b;
  };

  strip.appendChild(btn("rr", "\\u00AB", data.lab.rprev, () => rstep(-1)));
  strip.appendChild(btn("pp", "\\u25C0", data.lab.prev, () => step(-1)));
  const track = el("div", "aa-track");
  data.groups.forEach((g) => {
    const cur = g.items.some((it) => it.i === data.sel);
    const badge = el("button", "aa-rd" + (cur ? " cur" : ""),
                     g.r == null ? "?" : String(g.r));
    badge.type = "button";
    badge.title = g.tip;
    badge.setAttribute("aria-label", g.tip);
    badge.addEventListener("click", () => go(g.items[0].i));
    track.appendChild(badge);
    g.items.forEach((it) => {
      const chip = el("button",
        "aa-chip " + (it.w === "kill" ? "k" : "d") + (it.i === data.sel ? " cur" : ""),
        it.w === "kill" ? "\\u2713" : "\\u2715");
      chip.type = "button";
      chip.title = it.tip;
      chip.setAttribute("aria-label", it.tip);
      chip.addEventListener("click", () => go(it.i));
      track.appendChild(chip);
    });
  });
  strip.appendChild(track);
  strip.appendChild(btn("nn", "\\u25B6", data.lab.next, () => step(1)));
  strip.appendChild(btn("ff", "\\u00BB", data.lab.rnext, () => rstep(1)));
  const pos = el("div", "aa-pos", data.n ? (data.sel + 1) + " / " + data.n : "");
  if (data.lab.first) pos.title = data.lab.first;
  strip.appendChild(pos);
  host.replaceChildren(strip);

  // keep the selected chip centered in the track without scrolling the
  // page (scrollIntoView would drag every scrollable ancestor along)
  const cur = track.querySelector(".aa-chip.cur");
  if (cur) {
    track.scrollLeft = Math.max(
      0, cur.offsetLeft - (track.clientWidth - cur.offsetWidth) / 2
    );
  }
}
"""
