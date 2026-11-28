"""Hit distribution card (ui/hitbody.js): a body silhouette with every hitgroup shaded by
its share of the hits, three stat cells and the per-region table beside it."""

from __future__ import annotations

from typing import Mapping, Sequence

from ui import component


def view(sub: str, stats: Sequence[tuple[str, str]], regions: Sequence[dict]) -> dict:
    """What one card shows. ``regions``: [{"k": hitgroup, "n": label, "h": hits,
    "d": damage}] in table order; shares and shading are worked out in the front end.
    Hitgroups the silhouette has no shape for ("neck", "other") show in the table only."""
    return {
        "sub": sub,
        "stats": [[str(a), str(b)] for a, b in stats],
        "regions": [dict(r) for r in regions],
    }


def hitbody(
    *,
    key: str,
    title: str,
    cols: Sequence[str],
    base: dict,
    previews: Mapping[str, dict] | None = None,
    channel: str = "",
) -> None:
    """``base``: the card as the page stands. ``previews``: {row key: view} shown while
    that row of the table announcing on ``channel`` (ui.table hover=) is under the
    pointer."""
    component.render(
        "hitbody",
        key=key,
        data={
            "title": title,
            "cols": list(cols),
            "view": base,
            "previews": dict(previews or {}),
            "ch": channel,
        },
        trigger="none",
        handler=None,
    )
