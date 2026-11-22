"""
Weapon and status icons (flat white silhouettes, CS2 kill-feed style).

The art is Valve's; it is NOT part of this repository. It lives on the
user's machine in the data folder (next to the radars) after
``tools/fetch_icons.py`` downloaded it - the same position as the radar
images: the repo ships only the script, the loader and the tests (see
docs/ASSETS.md and NOTICE). ``AIMAUDIT_NO_ICONS=1`` opts out of the
download; an empty, missing or broken icons folder changes nothing here -
every call simply answers "no icon" and the UI falls back to text.

No Streamlit at import time (a plain leaf library over ``store`` and
``core``):

    load()                     the manifest, read once and cached
    icon(key)                  {"uri": data-uri, "w": int, "h": int} | None
    weapon_icon(name)          the same for a demo weapon name (aliases!)
    weapon_key(name)           demo weapon name -> icon key | None
    weapon_label(name)         the display name for tooltips (i18n)
    payload(names) / badge_payload(keys)   {key: data-uri} for JS payloads

Security: icon files are never inlined as raw SVG. ``sanitize_svg``
strips scripts, event handlers, foreign objects and external references
and is applied both when the fetcher saves a file and again here when a
file is loaded; ``icon()`` hands out ``<img>``-safe base64 data URIs
only. Colouring happens through the alpha mask (CSS ``mask-image`` /
canvas ``source-in``), never through the file's own ``fill`` - part of
the art (grenades, the bomb, the defuse kit, the timer) carries no
``fill="white"`` and would stay black otherwise.
"""

from __future__ import annotations

import base64
import os
import re
from functools import lru_cache

import store

# --------------------------------------------------------------- sanitizer
# Runs on every icon before it is saved (tools/fetch_icons.py) and again
# when it is loaded here. Idempotent: an already clean file passes through
# byte-for-byte (the fetcher pins the hash of the sanitized content).
_RE_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_RE_FOREIGN = re.compile(r"<foreignObject\b[^>]*>.*?</foreignObject\s*>", re.I | re.S)
_RE_DOCTYPE = re.compile(r"<!DOCTYPE\b[^>]*>", re.I)
_RE_ON_ATTR = re.compile(
    r"\son[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I
)
_RE_HREF = re.compile(
    r"\s(?:xlink:)?href\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I
)
_RE_USE = re.compile(r"<use\b[^>]*(?:/>|>.*?</use\s*>)", re.I | re.S)
_RE_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.I)
_XMLNS = "http://www.w3.org/2000/svg"


def _attr_val(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0] else v


def _drop_external_href(m: re.Match) -> str:
    """Keep local (#fragment) references, drop everything else."""
    return "" if not _attr_val(m.group(1)).startswith("#") else m.group(0)


def _drop_external_use(m: re.Match) -> str:
    """A <use> is fine while it points INSIDE the file (#fragment)."""
    href = re.search(r"(?:xlink:)?href\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", m.group(0), re.I)
    if href and _attr_val(href.group(1)).startswith("#"):
        return m.group(0)
    return ""


def _viewbox(svg_tag: str) -> tuple[int, int] | None:
    m = re.search(r'viewBox\s*=\s*"[^"]*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*"',
                  svg_tag, re.I) or re.search(
        r"viewBox\s*=\s*'[^']*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*'", svg_tag, re.I)
    if not m:
        return None
    try:
        w, h = float(m.group(3)), float(m.group(4))
    except ValueError:
        return None
    return (int(w), int(h)) if w > 0 and h > 0 else None


def _size_attrs(svg_tag: str) -> tuple[int, int] | None:
    w = re.search(r'width\s*=\s*"([\d.]+)(?:px)?"', svg_tag, re.I)
    h = re.search(r'height\s*=\s*"([\d.]+)(?:px)?"', svg_tag, re.I)
    if not (w and h):
        return None
    try:
        return int(float(w.group(1))), int(float(h.group(1)))
    except ValueError:
        return None


def sanitize_svg(data: bytes | str) -> str:
    """
    Strip everything an SVG must not carry to be inlined as an image:
    ``<script>`` blocks, ``on*`` event attributes, ``<foreignObject>``,
    external ``href``/``xlink:href`` and ``<use>`` pointing outside the
    file. Adds the ``xmlns`` (and a ``viewBox`` from width/height) when
    missing, so the file renders everywhere. Returns UTF-8 text.
    """
    s = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    s = _RE_SCRIPT.sub("", s)
    s = _RE_FOREIGN.sub("", s)
    s = _RE_DOCTYPE.sub("", s)
    s = _RE_ON_ATTR.sub("", s)
    s = _RE_HREF.sub(_drop_external_href, s)
    s = _RE_USE.sub(_drop_external_use, s)
    tag = _RE_SVG_TAG.search(s)
    if tag:
        head = tag.group(0)
        new = head
        if "xmlns" not in new:
            new = new[:-1].rstrip() + f' xmlns="{_XMLNS}">'
        if "viewBox" not in new and "viewbox" not in new:
            size = _size_attrs(new)
            if size:
                new = new[:-1].rstrip() + f' viewBox="0 0 {size[0]} {size[1]}">'
        if new != head:
            s = s[: tag.start()] + new + s[tag.end():]
    return s


def svg_size(text: str) -> tuple[int, int] | None:
    """Intrinsic (width, height) of an SVG from its viewBox / size attrs."""
    tag = _RE_SVG_TAG.search(text)
    if not tag:
        return None
    return _viewbox(tag.group(0)) or _size_attrs(tag.group(0))


def _ensure_size(text: str, w: int, h: int) -> str:
    """Give the <svg> tag explicit width/height when they are missing.

    A viewBox-only SVG has no intrinsic size: browsers that lean on it
    (Firefox, and any canvas consumer) cannot rasterize the image at all -
    the 2D replay tints icons through a canvas, so those would silently
    never appear. CSS masks do not care, which is why the kill feed looked
    fine while the map did not. Runs AFTER the sha256 check in
    ``_load_icon``: the pinned hash still validates the file on disk, this
    only rewrites the emitted data URI."""
    m = _RE_SVG_TAG.search(text)
    if not m:
        return text
    tag = m.group(0)
    add = ""
    if not re.search(r"[\s]width\s*=", tag, re.I):
        add += f' width="{w}"'
    if not re.search(r"[\s]height\s*=", tag, re.I):
        add += f' height="{h}"'
    if not add:
        return text
    inner = tag[len("<svg"):-1].rstrip()
    self_close = inner.endswith("/")
    if self_close:
        inner = inner[:-1].rstrip()
    new = "<svg" + inner + add + ("/>" if self_close else ">")
    return text[: m.start()] + new + text[m.end():]


# ------------------------------------------------------------- icon folder
def icons_dir() -> str:
    """The folder the fetcher wrote the icons into (store knows locations)."""
    return store.icons_dir()


def icon_roots() -> list[str]:
    """Folders that may hold the icons, best first: $AIMAUDIT_ICON_DIR, then
    the data folder's icons/ (written by start.bat / tools/fetch_icons.py)."""
    roots: list[str] = []
    env = os.environ.get("AIMAUDIT_ICON_DIR")
    if env:
        roots.append(os.path.abspath(os.path.expanduser(env)))
    roots.append(icons_dir())
    return roots


_SAFE_KEY = re.compile(r"^[a-z0-9_]+$")


@lru_cache(maxsize=1)
def _manifest_path() -> tuple[str | None, dict]:
    """(the first folder with a readable manifest.json, its content).
    A corrupt file, a missing folder or a missing file all answer
    ({}, None-ish) - never an exception."""
    for root in icon_roots():
        try:
            with open(os.path.join(root, "manifest.json"), encoding="utf-8") as fh:
                import json

                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d:
            return root, d
    return None, {}


@lru_cache(maxsize=256)
def _load_icon(key: str) -> dict | None:
    """One icon: read, sanitize again, wrap as a data URI. Any problem
    answering ``None`` means "no icon" - the caller shows text instead."""
    if not _SAFE_KEY.match(key or ""):
        return None
    root, manifest = _manifest_path()
    if not root:
        return None
    meta = manifest.get(key)
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("file") or f"{key}.svg")
    if not _SAFE_KEY.match(name[:-4] if name.endswith(".svg") else name):
        return None
    try:
        with open(os.path.join(root, name), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    text = sanitize_svg(raw)
    try:
        import hashlib

        want = str(meta.get("sha256") or "")
        if want and hashlib.sha256(text.encode("utf-8")).hexdigest() != want:
            return None  # a broken or tampered file is "no icon", not a risk
    except (TypeError, ValueError):
        return None
    w, h = (int(meta.get("w") or 0), int(meta.get("h") or 0))
    if not (w > 0 and h > 0):
        size = svg_size(text)
        if not size:
            return None
        w, h = size
    text = _ensure_size(text, w, h)
    uri = "data:image/svg+xml;base64," + base64.b64encode(
        text.encode("utf-8")
    ).decode("ascii")
    return {"uri": uri, "w": w, "h": h}


def load() -> dict[str, dict]:
    """The whole installed set, {key: {"uri", "w", "h"}} (cached, pure read)."""
    _, manifest = _manifest_path()
    return {k: ic for k in manifest if (ic := _load_icon(k))}


def reset_cache() -> None:
    """Drop the caches (tests switch folders through AIMAUDIT_ICON_DIR)."""
    _manifest_path.cache_clear()
    _load_icon.cache_clear()


def icon(key: str) -> dict | None:
    """One icon by its key: {"uri", "w", "h"} or None."""
    return _load_icon(str(key or ""))


def payload(keys) -> dict[str, list]:
    """{key: [data-uri, w, h]} for JS payloads - only the keys asked for,
    only the ones that exist. Payloads stay small: one entry per distinct
    icon; w/h give the DOM (aspect-ratio + mask) and the canvas their
    geometry without loading the image first."""
    out = {}
    for k in keys:
        ic = icon(k)
        if ic:
            out[str(k)] = [ic["uri"], ic["w"], ic["h"]]
    return out


# ------------------------------------------------------------- weapons
# Demo / GSI weapon name -> icon key (the file stem). norm_weapon already
# strips the "weapon_" prefix and lowercases; these aliases cover the
# names that differ from the HUD file names (old CS:GO names, GSI names,
# non-weapon entries the kill feed still shows an icon for).
WEAPON_ALIASES = {
    "usp": "usp_silencer",
    "usp9": "usp_silencer",
    "p2000": "hkp2000",
    "m4a1s": "m4a1_silencer",
    "mp5": "mp5sd",
    "revolver": "revolver",
    "planted_c4": "c4",
    "c4": "c4",
    "bumpmine": None,  # no silhouette exists: the text name stays
}


def weapon_key(name) -> str | None:
    """A demo weapon name -> the icon key, or None when no icon exists."""
    import core

    w = core.norm_weapon(name or "")
    w = WEAPON_ALIASES.get(w, w)
    if not w or w == str(core.NA):  # an alias may explicitly mean "no icon"
        return None
    if icon(w):
        return w
    if w.startswith("knife"):  # an exotic knife: at least the plain one
        return "knife" if icon("knife") else None
    return None


def weapon_icon(name) -> dict | None:
    """{"uri", "w", "h"} of the weapon's silhouette, or None."""
    k = weapon_key(name)
    return icon(k) if k else None


def weapon_info(name) -> dict:
    """
    Everything a renderer needs for one weapon in one call: {"key", "uri",
    "w", "h", "label"}. key/uri are empty when no icon exists (the label
    still resolves, so tooltips and text fallbacks keep working). Never
    raises - a missing icon set is a normal state, not an error.
    """
    k = weapon_key(name)
    ic = icon(k) if k else None
    return {
        "key": k or "",
        "uri": (ic or {}).get("uri", ""),
        "w": int((ic or {}).get("w") or 0),
        "h": int((ic or {}).get("h") or 0),
        "label": weapon_label(name),
    }


def weapon_label(name) -> str:
    """
    The display name for tooltips / fallback text: t("weapon_<key>") when
    the locale has it, else the normalized name as the demo spells it.
    Reads the language like ui/schema.py does (no Streamlit at import).
    """
    import core
    import i18n

    w = core.norm_weapon(name or "")
    key = WEAPON_ALIASES.get(w, w) or ""
    if not w or w == str(core.NA):
        return "N/A"
    lang = None
    try:
        import state

        lang = state.get_lang()
    except Exception:  # noqa: BLE001 - bare mode (unit tests): en
        lang = None
    t = i18n.make_t(lang or next(iter(i18n.LANGS)))
    for k in (f"weapon_{key}", f"weapon_{w}"):
        if k in i18n.STR.get(lang or "en", {}) or k in i18n.STR["en"]:
            return t(k)
    return w


def badge_payload(keys) -> dict[str, list]:
    """payload() under its intent-revealing name for status icons."""
    return payload(keys)


def weapon_cells(names) -> tuple[list[str], list[str], dict[str, list]]:
    """
    (values, labels, icons) for a column of demo weapon names - the one
    call a table page makes. ``values`` hold the icon key where an icon
    exists (else the normalized name, so sorting still works), ``labels``
    the display names (the tooltip AND the text shown without the icon
    set; "N/A" for no weapon) and ``icons`` the {value: [uri, w, h]} dict
    the payload needs.
    """
    import core

    vals: list[str] = []
    labels: list[str] = []
    keys: list[str] = []
    for n in names:
        w = core.norm_weapon(n or "")
        k = weapon_key(n)
        vals.append((k or w) or "")
        labels.append(weapon_label(n))
        if k:
            keys.append(k)
    return vals, labels, payload(keys)
