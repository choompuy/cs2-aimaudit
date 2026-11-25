"""
Unit tests for ui/icons.py: the SVG sanitizer, the manifest loader (hash
verification, tampering, corrupt files), the weapon-name aliases and every
fallback. The icon set is a small fixture written into a temp folder with
AIMAUDIT_ICON_DIR pointing there - the tests never touch the network and
never read the real data/icons.
"""

import base64
import hashlib
import json
import os
import sys
import tempfile

from ui import icons


# ---------------------------------------------------------------- fixture
# Tiny but real silhouettes: a wide one (a "weapon"), a square one (a
# "badge") and one with NO fill attribute at all (grenade-style art: the
# tint must not depend on the file's own fill).
SVG_WEAPON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 32" '
    'width="100" height="32"><path fill="white" d="M0 10h100v12H0z"/></svg>'
)
SVG_BADGE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'width="24" height="24"><circle fill="white" cx="12" cy="12" r="9"/></svg>'
)
SVG_NOFILL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'width="24" height="24"><path d="M12 2l4 8-4 12-4-12z"/></svg>'
)
FIXTURE = {
    "ak47": SVG_WEAPON,
    "awp": SVG_WEAPON,
    "knife": SVG_NOFILL,
    "usp_silencer": SVG_WEAPON,
    "hkp2000": SVG_WEAPON,
    "c4": SVG_BADGE,
    "headshot": SVG_BADGE,
    "skull": SVG_NOFILL,
    "bomb": SVG_BADGE,
    "bomb_explode": SVG_BADGE,
    "defuse": SVG_BADGE,
    "timer": SVG_BADGE,
    "hourglass": SVG_BADGE,
    "wallbang": SVG_BADGE,
    "noscope": SVG_BADGE,
    "smoke_kill": SVG_BADGE,
    "flashed_kill": SVG_BADGE,
}


def write_fixture(dest: str, entries: dict | None = None) -> str:
    """A verified icon set: every file sanitized, the manifest pinning the
    hash of the sanitized content (exactly what tools/fetch_icons.py writes)."""
    manifest = {}
    for key, svg in (entries if entries is not None else FIXTURE).items():
        text = icons.sanitize_svg(svg)
        with open(os.path.join(dest, f"{key}.svg"), "w", encoding="utf-8") as fh:
            fh.write(text)
        w, h = icons.svg_size(text)
        manifest[key] = {
            "file": f"{key}.svg", "w": w, "h": h,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source": "mirror",
        }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return dest


class _IconDir:
    """AIMAUDIT_ICON_DIR pointed at a fresh fixture, cache reset on entry
    and on exit (other tests must never see this folder)."""

    def __init__(self, entries: dict | None = None, empty: bool = False):
        self.entries, self.empty = entries, empty
        self._tmp = None
        self._old = None

    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()  # noqa: SIM115 - closed in __exit__
        if not self.empty:
            write_fixture(self._tmp.name, self.entries)
        self._old = os.environ.get("AIMAUDIT_ICON_DIR")
        os.environ["AIMAUDIT_ICON_DIR"] = self._tmp.name
        icons.reset_cache()
        return self._tmp.name

    def __exit__(self, *exc) -> None:
        if self._old is None:
            os.environ.pop("AIMAUDIT_ICON_DIR", None)
        else:
            os.environ["AIMAUDIT_ICON_DIR"] = self._old
        icons.reset_cache()
        self._tmp.cleanup()


# --------------------------------------------------------------- sanitizer
def test_sanitizer() -> None:
    dirty = (
        '<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<script type="text/javascript">alert(1)</script>'
        '<foreignObject><body onload="x()"><p>hi</p></body></foreignObject>'
        '<a href="https://evil.example/x.svg"><rect onmouseover="steal()" '
        'x="1" y="2" width="4" height="4"/></a>'
        '<use xlink:href="https://evil.example/u#frag"/>'
        '<linearGradient id="g"><stop offset="0"/></linearGradient>'
        '<rect fill="url(#g)" width="4" height="4"/>'
        '</svg>'
    )
    clean = icons.sanitize_svg(dirty)
    for gone in ("<script", "foreignObject", "onmouseover", "alert(1)",
                 "evil.example", "<!DOCTYPE", "xlink:href=\"https"):
        assert gone not in clean, f"sanitizer strips {gone!r}"
    # local references survive: the gradient and its use stay usable
    assert 'url(#g)' in clean and 'id="g"' in clean, "sanitizer keeps local #fragment references"
    use_local = icons.sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
        '<defs><path id="p" d="M0 0h4v4H0z"/></defs>'
        '<use href="#p"/></svg>'
    )
    assert "<use " in use_local, "sanitizer keeps a <use> pointing inside the file"
    use_ext = icons.sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
        '<use href="other.svg#p"/></svg>'
    )
    assert "<use" not in use_ext, "sanitizer drops a <use> pointing outside"
    # a file without xmlns / viewBox gets them (from width/height)
    patched = icons.sanitize_svg('<svg width="24" height="12"><path d="M0 0"/></svg>')
    assert 'xmlns="http://www.w3.org/2000/svg"' in patched, "sanitizer adds xmlns"
    assert 'viewBox="0 0 24 12"' in patched, "sanitizer adds viewBox from width/height"
    # idempotent: a clean file passes through byte-for-byte
    once = icons.sanitize_svg(SVG_NOFILL)
    assert icons.sanitize_svg(once) == once, "sanitizer is idempotent"
    assert icons.svg_size(once) == (24, 24), "svg_size reads the viewBox"
    assert icons.svg_size('<svg width="30" height="10"/>') == (30, 10), "svg_size falls back to width/height"
    assert icons.svg_size("not an svg") is None, "svg_size of garbage is None"


# ------------------------------------------------------------------ loader
def test_loader() -> None:
    with _IconDir() as dest:
        all_icons = icons.bundle()
        assert set(all_icons) == set(FIXTURE), "load() returns the whole fixture"
        ak = icons.icon("ak47")
        assert ak and ak["uri"].startswith("data:image/svg+xml;base64,"), "icon() gives a base64 data URI"
        body = base64.b64decode(ak["uri"].split(",", 1)[1]).decode("utf-8")
        assert body == icons.sanitize_svg(SVG_WEAPON), "the data URI decodes to the sanitized file"
        assert (ak["w"], ak["h"]) == (100, 32), "icon() carries the intrinsic size"
        assert icons.icon("negev") is None, "icon() of a missing key is None"
        assert icons.icon("../ak47") is None, "icon() rejects unsafe keys"
        # a tampered file is "no icon", never an exception
        with open(os.path.join(dest, "awp.svg"), "w", encoding="utf-8") as fh:
            fh.write(SVG_NOFILL)  # different content, manifest pin still the old one
        icons.reset_cache()
        assert icons.icon("awp") is None, "a tampered file answers None"
        # a corrupt manifest disables the whole folder silently
        with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        icons.reset_cache()
        assert icons.icon("ak47") is None, "a corrupt manifest answers None everywhere"
        assert icons.bundle() == {}, "a corrupt manifest makes bundle() empty"
    # an empty folder (icons not installed yet) is the same normal state
    with _IconDir(empty=True):
        assert icons.icon("ak47") is None, "an empty folder answers None"
        assert icons.bundle() == {}, "an empty folder makes bundle() empty"


# ------------------------------------------------------- weapons and cells
def test_weapons() -> None:
    with _IconDir():
        for name, want in (
            ("weapon_ak47", "ak47"),
            ("AK47", "ak47"),              # norm_weapon lowercases
            ("usp", "usp_silencer"),       # the alias table
            ("usp9", "usp_silencer"),
            ("p2000", "hkp2000"),
            ("m4a1s", None),               # no m4a1_silencer in the fixture
            ("planted_c4", "c4"),
            ("c4", "c4"),
            ("knife_karambit", "knife"),   # an exotic knife falls back to the plain one
            ("bumpmine", None),            # no silhouette exists
            ("negev", None),               # not in the fixture
        ):
            got = icons.weapon_key(name)
            assert got == want, f"weapon_key({name!r}) == {want!r}"
        assert icons.weapon_label("weapon_ak47") == "AK-47", "weapon_label is localized"
        assert set(icons.bundle()) == set(FIXTURE), "bundle() is the whole set"
        assert icons.weapon_labels()["negev"] == "Negev", "weapon_labels() names every weapon"
        for name, want in (
            ("weapon_ak47", "ak47"), ("USP-S", "usp_silencer"), ("High Explosive Grenade", "hegrenade"),
            ("C4 Explosive", "c4"), ("Karambit", "knife_karambit"), ("Some New Knife", "knife"),
            ("", ""), (None, ""),
        ):
            got = icons.weapon_id(name)
            assert got == want, f"weapon_id({name!r}) == {want!r}"
        vals, labels, ic = icons.weapon_cells(["ak47", "usp", "world", None])
        assert vals == ["ak47", "usp_silencer", "world", ""], "weapon_cells values are icon keys where they exist"
        assert labels == ["AK-47", "USP-S", "World", "N/A"], "weapon_cells labels are localized"
        assert set(ic) == {"ak47", "usp_silencer"}, "weapon_cells icons cover exactly the resolved keys"
        p = icons.payload(["ak47", "negev", "headshot"])
        assert set(p) == {"ak47", "headshot"}, "payload() keeps only the keys that exist"
        assert p["ak47"][1:] == [100, 32], "payload() entries are [uri, w, h]"
    # without the icon set: keys empty, labels still resolve (tooltips / text)
    with _IconDir(empty=True):
        vals, labels, ic = icons.weapon_cells(["ak47", "usp"])
        assert vals == ["ak47", "usp_silencer"] and ic == {}, "weapon_cells without the set: names as values, no icons"
        assert labels == ["AK-47", "USP-S"], "labels without the set still localize"


# ------------------------------------------------------------- fetch glue
def test_fetcher_pins() -> None:
    """The mirror pins must stay consistent with the sanitizer: every pin
    is the hash of SANITIZED content, so re-sanitizing a fetched file must
    not change it (idempotence is what makes the loader stable)."""
    try:
        import fetch_icons  # noqa: E402
    finally:
        sys.path.pop(0)
    wanted = set(fetch_icons.WEAPON_SHA) | set(fetch_icons.IMAGE_SHA)
    keys = set(FIXTURE)
    assert keys <= wanted, "the fetcher pins cover every key the app can ask for"
    for key, fname in fetch_icons.IMAGE_FILE.items():
        assert key in fetch_icons.IMAGE_SHA, f"IMAGE_FILE[{key!r}] is pinned"
    assert all(len(h) == 64 and set(h) <= set("0123456789abcdef")
              for h in list(fetch_icons.WEAPON_SHA.values()) + list(fetch_icons.IMAGE_SHA.values())), "every pin is a sha256 hex digest"
    assert fetch_icons.COMMIT and len(fetch_icons.COMMIT) == 40, "the fetcher's mirror commit is pinned"
    # present(): the launcher's instant no-op check - true only when the
    # manifest covers EVERY pinned key (patched down to the fixture subset
    # so this needs no network)
    with _IconDir() as dest:
        try:
            old_w, old_i = fetch_icons.WEAPON_SHA, fetch_icons.IMAGE_SHA
            try:
                fetch_icons.WEAPON_SHA = {"ak47": old_w["ak47"]}
                fetch_icons.IMAGE_SHA = {"headshot": old_i["headshot"], "bomb": old_i["bomb"]}
                assert fetch_icons.present(dest), "present() is true when every pinned key is there"
                fetch_icons.WEAPON_SHA = dict(old_w)  # more pins than the fixture has
                assert not fetch_icons.present(dest), "present() is false while pins are missing"
            finally:
                fetch_icons.WEAPON_SHA, fetch_icons.IMAGE_SHA = old_w, old_i
        finally:
            sys.path.pop(0)
