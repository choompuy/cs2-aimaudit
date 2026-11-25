"""
Unit tests for ui/icons.py: the SVG sanitizer, the manifest loader (hash
verification, tampering, corrupt files), the weapon-name aliases and every
fallback. The icon set is a small fixture written into a temp folder with
AIMAUDIT_ICON_DIR pointing there - the tests never touch the network and
never read the real data/icons.

Run from the repo root: python tests/test_icons.py
"""

import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# without it the loader falls back to the icons installed on this machine
os.environ["AIMAUDIT_DATA_DIR"] = tempfile.mkdtemp(prefix="cs2aa_icons_")
from ui import icons  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


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
        check(f"sanitizer strips {gone!r}", gone not in clean, clean[:120])
    # local references survive: the gradient and its use stay usable
    check("sanitizer keeps local #fragment references",
          'url(#g)' in clean and 'id="g"' in clean, clean)
    use_local = icons.sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
        '<defs><path id="p" d="M0 0h4v4H0z"/></defs>'
        '<use href="#p"/></svg>'
    )
    check("sanitizer keeps a <use> pointing inside the file", "<use " in use_local, use_local)
    use_ext = icons.sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
        '<use href="other.svg#p"/></svg>'
    )
    check("sanitizer drops a <use> pointing outside", "<use" not in use_ext, use_ext)
    # a file without xmlns / viewBox gets them (from width/height)
    patched = icons.sanitize_svg('<svg width="24" height="12"><path d="M0 0"/></svg>')
    check("sanitizer adds xmlns", 'xmlns="http://www.w3.org/2000/svg"' in patched, patched)
    check("sanitizer adds viewBox from width/height", 'viewBox="0 0 24 12"' in patched, patched)
    # idempotent: a clean file passes through byte-for-byte
    once = icons.sanitize_svg(SVG_NOFILL)
    check("sanitizer is idempotent", icons.sanitize_svg(once) == once)
    check("svg_size reads the viewBox", icons.svg_size(once) == (24, 24))
    check("svg_size falls back to width/height",
          icons.svg_size('<svg width="30" height="10"/>') == (30, 10))
    check("svg_size of garbage is None", icons.svg_size("not an svg") is None)


# ------------------------------------------------------------------ loader
def test_loader() -> None:
    with _IconDir() as dest:
        all_icons = icons.load()
        check("load() returns the whole fixture", set(all_icons) == set(FIXTURE), set(all_icons))
        ak = icons.icon("ak47")
        check("icon() gives a base64 data URI",
              ak and ak["uri"].startswith("data:image/svg+xml;base64,"))
        body = base64.b64decode(ak["uri"].split(",", 1)[1]).decode("utf-8")
        check("the data URI decodes to the sanitized file", body == icons.sanitize_svg(SVG_WEAPON))
        check("icon() carries the intrinsic size", (ak["w"], ak["h"]) == (100, 32), ak)
        check("icon() of a missing key is None", icons.icon("negev") is None)
        check("icon() rejects unsafe keys", icons.icon("../ak47") is None)
        # a tampered file is "no icon", never an exception
        with open(os.path.join(dest, "awp.svg"), "w", encoding="utf-8") as fh:
            fh.write(SVG_NOFILL)  # different content, manifest pin still the old one
        icons.reset_cache()
        check("a tampered file answers None", icons.icon("awp") is None)
        # a corrupt manifest disables the whole folder silently
        with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        icons.reset_cache()
        check("a corrupt manifest answers None everywhere", icons.icon("ak47") is None)
        check("a corrupt manifest makes load() empty", icons.load() == {})
    # an empty folder (icons not installed yet) is the same normal state
    with _IconDir(empty=True):
        check("an empty folder answers None", icons.icon("ak47") is None)
        check("an empty folder makes load() empty", icons.load() == {})


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
            ("bumpmine", None),            # explicitly no icon
            ("negev", None),               # not in the fixture
        ):
            got = icons.weapon_key(name)
            check(f"weapon_key({name!r}) == {want!r}", got == want, got)
        info = icons.weapon_info("weapon_ak47")
        check("weapon_info bundles key/uri/size/label",
              info["key"] == "ak47" and info["uri"].startswith("data:") and info["label"] == "AK-47",
              info)
        vals, labels, ic = icons.weapon_cells(["ak47", "usp", "world", None])
        check("weapon_cells values are icon keys where they exist",
              vals == ["ak47", "usp_silencer", "world", ""], vals)
        check("weapon_cells labels are localized", labels == ["AK-47", "USP-S", "World", "N/A"], labels)
        check("weapon_cells icons cover exactly the resolved keys",
              set(ic) == {"ak47", "usp_silencer"}, set(ic))
        p = icons.payload(["ak47", "negev", "headshot"])
        check("payload() keeps only the keys that exist", set(p) == {"ak47", "headshot"}, set(p))
        check("payload() entries are [uri, w, h]",
              p["ak47"][1:] == [100, 32], p["ak47"])
    # without the icon set: keys empty, labels still resolve (tooltips / text)
    with _IconDir(empty=True):
        info = icons.weapon_info("weapon_ak47")
        check("weapon_info without the set: no key/uri, label stays",
              info["key"] == "" and info["uri"] == "" and info["label"] == "AK-47", info)
        vals, labels, ic = icons.weapon_cells(["ak47", "usp"])
        check("weapon_cells without the set: names as values, no icons",
              vals == ["ak47", "usp"] and ic == {}, (vals, ic))
        check("labels without the set still localize", labels == ["AK-47", "USP-S"], labels)


# ------------------------------------------------------------- fetch glue
def test_fetcher_pins() -> None:
    """The mirror pins must stay consistent with the sanitizer: every pin
    is the hash of SANITIZED content, so re-sanitizing a fetched file must
    not change it (idempotence is what makes the loader stable)."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import fetch_icons  # noqa: E402
    finally:
        sys.path.pop(0)
    wanted = set(fetch_icons.WEAPON_SHA) | set(fetch_icons.IMAGE_SHA)
    keys = set(FIXTURE)
    check("the fetcher pins cover every key the app can ask for",
          keys <= wanted, keys - wanted)
    for key, fname in fetch_icons.IMAGE_FILE.items():
        check(f"IMAGE_FILE[{key!r}] is pinned", key in fetch_icons.IMAGE_SHA, fname)
    check("every pin is a sha256 hex digest",
          all(len(h) == 64 and set(h) <= set("0123456789abcdef")
              for h in list(fetch_icons.WEAPON_SHA.values()) + list(fetch_icons.IMAGE_SHA.values())))
    check("the fetcher's mirror commit is pinned",
          fetch_icons.COMMIT and len(fetch_icons.COMMIT) == 40, fetch_icons.COMMIT)
    # present(): the launcher's instant no-op check - true only when the
    # manifest covers EVERY pinned key (patched down to the fixture subset
    # so this needs no network)
    with _IconDir() as dest:
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            old_w, old_i = fetch_icons.WEAPON_SHA, fetch_icons.IMAGE_SHA
            try:
                fetch_icons.WEAPON_SHA = {"ak47": old_w["ak47"]}
                fetch_icons.IMAGE_SHA = {"headshot": old_i["headshot"], "bomb": old_i["bomb"]}
                check("present() is true when every pinned key is there",
                      fetch_icons.present(dest))
                fetch_icons.WEAPON_SHA = dict(old_w)  # more pins than the fixture has
                check("present() is false while pins are missing",
                      not fetch_icons.present(dest))
            finally:
                fetch_icons.WEAPON_SHA, fetch_icons.IMAGE_SHA = old_w, old_i
        finally:
            sys.path.pop(0)


def main() -> None:
    print("ui/icons.py")
    test_sanitizer()
    test_loader()
    test_weapons()
    test_fetcher_pins()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
