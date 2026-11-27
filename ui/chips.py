"""Toggle chips: a compact multi-select of small buttons (ui/chips.js).

The filter bar uses it for the weapons (kill-feed silhouettes instead of names) and for
hand-picked rounds (numbers tinted by side). A click toggles one chip; the handler gets
the whole new selection."""

from __future__ import annotations

from typing import Any, Callable, Collection, Mapping, Sequence

from ui import component


def chips_payload(
    items: Sequence[Mapping[str, Any]],
    selected: Collection[str],
    *,
    icons: Mapping[str, Sequence] | None = None,
    label: str = "",
) -> dict:
    """JSON-ready model.

    ``items``: {"id", "text", "tip", "ico" (icon key or None), "tone" ("t" | "ct" |
    None), "gap" (a divider before this chip)} per chip. ``selected`` keeps only ids
    that exist, in item order."""
    ids = [str(it["id"]) for it in items]
    sel = set(map(str, selected or ()))
    return {
        "items": [
            {
                "id": str(it["id"]),
                "text": str(it.get("text") or it["id"]),
                "tip": str(it.get("tip") or it.get("text") or it["id"]),
                "ico": str(it["ico"]) if it.get("ico") else "",
                "tone": str(it.get("tone") or ""),
                "gap": bool(it.get("gap")),
            }
            for it in items
        ],
        "sel": [i for i in ids if i in sel],
        "icons": {str(k): list(v) for k, v in (icons or {}).items()},
        "label": str(label or ""),
    }


def chips(
    items: Sequence[Mapping[str, Any]],
    selected: Collection[str],
    *,
    key: str,
    on_change: Callable[[set], None],
    icons: Mapping[str, Sequence] | None = None,
    label: str = "",
) -> None:
    """Render the chips; a click calls on_change(new selection as a set of ids)."""
    payload = chips_payload(items, selected, icons=icons, label=label)
    component.render(
        "chips", key=key, data=payload, trigger="sel", handler=None,
        events={"sel": component.event_callback_set(key, "sel", on_change)},
    )
