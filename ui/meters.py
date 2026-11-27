"""Metric tiles with a fill bar (ui/meters.js): the value, a bar on a fixed scale, a tick
at the norm and the colour of the grade, so a bad number reads as bad at a glance.

A tile without a grade (a plain count) shows the value only; a group tile shows several
related counts side by side."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ui import component
from ui.table import _grade


@dataclass(frozen=True)
class Spec:
    """How one metric is judged: best "high" | "low", grade (bad, ok, good) and the bar's
    scale (lo, hi). None grade = a plain count (no bar, no colour)."""

    best: str = "high"
    grade: tuple[float, float, float] | None = None
    scale: tuple[float, float] = (0.0, 100.0)
    unit: str = "%"


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _fmt_norm(v: float, unit: str) -> str:
    return f"{v:.0f}{unit}" if abs(v) >= 10 or v == int(v) else f"{v:.1f}{unit}"


def tile(
    label: str,
    text: str,
    value=None,
    spec: Spec | None = None,
    *,
    help: str = "",
    sub: str = "",
    norm_label: str = "",
) -> dict:
    """One tile. ``value`` is the raw number the grade and the bar read (None: text only);
    ``norm_label`` is the translated "norm ≥ {v}" / "norm ≤ {v}" template."""
    x = _num(value)
    out = {"l": label, "v": text, "h": help, "s": sub, "f": None, "q": None, "m": None, "n": ""}
    if spec is None or spec.grade is None:
        return out
    lo, hi = spec.scale
    span = (hi - lo) or 1.0
    ok = spec.grade[1]
    out["m"] = round(min(1.0, max(0.0, (ok - lo) / span)), 3)
    if norm_label:
        out["n"] = norm_label.format(v=_fmt_norm(ok, spec.unit))
    if x is not None:
        out["f"] = round(min(1.0, max(0.0, (x - lo) / span)), 3)
        out["q"] = _grade(x, tuple(spec.grade), None, None, spec.best)
    return out


def group(label: str, parts, *, help: str = "", span: int = 1) -> dict:
    """One tile for several related counts (multi-kills, trades, assists): a row of small
    value-over-label cells, no bar. ``span``: grid columns the tile takes (1-2)."""
    return {
        "l": label, "v": "", "h": help, "s": "", "f": None, "q": None, "m": None, "n": "",
        "p": [[str(a), str(b)] for a, b in parts],
        "w": max(1, min(2, int(span))),
    }


def meters(tiles: Sequence[dict], *, key: str, title: str = "") -> None:
    """Render a row of tiles (wraps on narrow screens)."""
    component.render(
        "meters", key=key, data={"tiles": list(tiles), "title": title},
        trigger="none", handler=None,
    )
