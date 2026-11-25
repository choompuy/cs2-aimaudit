"""Duel strip: every duel is a chip under its round badge; ←/→ walk the duels, Shift+←/→
the rounds, Home/End the ends (ui/strip.js)."""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence


from ui import component


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
    """JSON-ready model of the strip.

    ``items``: {"round", "result": "kill"|"death", "tip"} per duel; consecutive duels of
    a round share a badge, ``rstarts`` is the first duel of every group, ``sel`` is
    clamped into range."""
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
    component.render(
        "strip", key=key, data=payload,
        trigger="sel", handler=on_select,
    )
