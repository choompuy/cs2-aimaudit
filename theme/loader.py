"""
Token loading and resolution for theme/tokens.toml.

Pure Python (stdlib only): no Streamlit, no Plotly, so any test or tool can
check token resolution without the app stack. The public API lives in
theme/__init__.py.

Resolution rules
- [primitive] holds raw literals; a primitive value that names another
  primitive or token is an error.
- [semantic] (with sub-tables like tier / radius / font, flattened to dotted
  keys) is the default token set.
- [themes.<name>] overrides a subset of the same keys; unknown keys fail
  fast with a clear message instead of silently doing nothing.
- A token value may name a primitive ("pink-500") or another semantic token
  ("accent", "text-secondary"); references resolve recursively with cycle
  detection. Anything else (hex, rem, px, font stack) is used literally.
  Aliases keep the palette consistent: chart-1 = "accent" means changing
  the accent automatically changes the first categorical chart colour.
"""

from __future__ import annotations

import functools
import os
import tomllib
from pathlib import Path
from typing import Any

DIR = Path(__file__).resolve().parent
TOKENS_FILE = DIR / "tokens.toml"

DEFAULT_THEME = "violet"
ENV_THEME = "AIMAUDIT_THEME"


def _flatten(table: dict, prefix: str = "") -> dict:
    """Nested TOML tables -> one level of dotted keys ("tier.good")."""
    out: dict[str, Any] = {}
    for key, val in table.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            out.update(_flatten(val, full))
        else:
            out[full] = val
    return out


def _raw() -> dict:
    return tomllib.loads(TOKENS_FILE.read_text(encoding="utf-8"))


def available_themes() -> list[str]:
    """Every theme table declared in tokens.toml, sorted."""
    return sorted(_raw().get("themes", {}))


def active_theme() -> str:
    """
    The theme to use: env AIMAUDIT_THEME, default "violet".

    Unknown names raise immediately: a silently wrong palette is much harder
    to notice than a startup error with the list of valid names.
    """
    name = (os.environ.get(ENV_THEME) or DEFAULT_THEME).strip().lower()
    themes = _raw().get("themes", {})
    if name not in themes:
        raise ValueError(
            f"unknown theme {name!r} (env {ENV_THEME}); expected one of {sorted(themes)}"
        )
    return name


@functools.lru_cache(maxsize=8)
def _tokens_for(theme: str) -> dict:
    raw = _raw()
    primitive = raw.get("primitive", {})
    flat = _flatten(raw.get("semantic", {}))
    override = _flatten(raw.get("themes", {}).get(theme, {}))

    unknown = sorted(set(override) - set(flat))
    if unknown:
        raise ValueError(
            f"[themes.{theme}] overrides unknown tokens: {unknown}; "
            "keys must match [semantic] names"
        )

    merged: dict[str, Any] = {**flat, **override}

    def resolve(value: Any, seen: frozenset[str]) -> Any:
        if not isinstance(value, str):
            return value
        if value in primitive:
            literal = primitive[value]
            if isinstance(literal, str) and (literal in primitive or literal in merged):
                raise ValueError(
                    f"primitive {value!r} resolves to the name {literal!r}; "
                    "primitives must be literals"
                )
            return literal
        if value in merged:
            if value in seen:
                chain = " -> ".join([*sorted(seen), value])
                raise ValueError(f"token reference cycle: {chain}")
            return resolve(merged[value], seen | {value})
        return value

    resolved: dict[str, Any] = {
        key: resolve(val, frozenset({key})) for key, val in merged.items()
    }
    return resolved


def tokens(theme: str | None = None) -> dict:
    """All flat tokens of a theme (default: the active one), as strings."""
    name = active_theme() if theme is None else theme
    if name not in available_themes():
        raise ValueError(
            f"unknown theme {name!r}; expected one of {available_themes()}"
        )
    return {k: str(v) for k, v in _tokens_for(name).items()}


def token(path: str, theme: str | None = None) -> str:
    """One token by dotted path, e.g. token("tier.good")."""
    tk = tokens(theme)
    if path not in tk:
        raise KeyError(f"unknown token {path!r}; have {sorted(tk)}")
    return tk[path]


def font_faces() -> list[dict]:
    """[[fontface]] tables from tokens.toml (family/file/url/weight/style)."""
    return _raw().get("fontface", [])


def config_theme_dict(theme: str | None = None) -> dict:
    """
    The [theme] values .streamlit/config.toml must carry for a theme.

    tools/sync_theme.py writes them; tests/check_ui.py compares the live
    config against the same dict, so "config matches tokens" can never
    drift between the two.
    """
    tk = tokens(theme)
    return {
        "base": "dark",
        "primaryColor": tk["accent"],
        "backgroundColor": tk["bg-app"],
        "secondaryBackgroundColor": tk["surface-1"],
        "textColor": tk["text-primary"],
        "borderColor": tk["border-subtle"],
        "baseRadius": tk["radius.sm"],
        "font": tk["font.family-ui"],
        "chartCategoricalColors": [tk[f"chart-{i}"] for i in range(1, 7)],
    }


# ------------------------------------------------------- WCAG AA contrast
def _srgb_to_linear(channel: int) -> float:
    x = channel / 255.0
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) not in (6, 8):
        raise ValueError(f"contrast() needs a #rrggbb colour, got {hex_color!r}")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return (
        0.2126 * _srgb_to_linear(r)
        + 0.7152 * _srgb_to_linear(g)
        + 0.0722 * _srgb_to_linear(b)
    )


def contrast(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio of two hex colours (1.0 .. 21.0)."""
    la, lb = _relative_luminance(foreground), _relative_luminance(background)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)
