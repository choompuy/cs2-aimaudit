"""store (data folder, library, caches, linked demos), tools/fetch_radars, nativedialog, match list merge, matchstats cache.

Split out of tests/test_core.py (R5); runs at import in
the runner's order - see core_sections/__init__.py.
"""

# --- R5 imports (generated; keep in sync with the providers) ---
from ._shared import (  # noqa: F401
    FAIL,
    FakeParser,
    PASS,
    Path,
    S1,
    S3,
    bz2,
    check,
    core,
    os,
    store,
    sys,
    tempfile,
    zipfile,
)
from .s05_live_impact import _json, _tiny_png  # noqa: F401
# ---- the data folder, the demo library and the disk caches (store.py)
print("== store: data folder ==")
import io  # noqa: E402

_dd = os.path.abspath(os.environ["AIMAUDIT_DATA_DIR"])
check("data folder = AIMAUDIT_DATA_DIR", store.data_dir() == _dd, store.data_dir())
check(
    "sub folders are created inside it",
    all(
        os.path.isdir(d) and os.path.commonpath([d, _dd]) == _dd
        for d in (store.demos_dir(), store.unpacked_dir(), store.parsed_dir(), store.radars_dir())
    ),
)
_save = (store.APP_DIR, store.FALLBACK_DIR, os.environ.pop("AIMAUDIT_DATA_DIR"))
try:
    _app = tempfile.mkdtemp(prefix="cs2aa_app_")
    store.APP_DIR, store.FALLBACK_DIR = _app, tempfile.mkdtemp(prefix="cs2aa_home_")
    store._resolve.cache_clear()
    check(
        "default data folder is <app folder>/data, next to the launcher",
        store.data_dir() == os.path.join(_app, "data"),
        store.data_dir(),
    )
    _ro = tempfile.mkdtemp(prefix="cs2aa_ro_")
    with open(os.path.join(_ro, "data"), "w") as fh:  # a FILE named data: not usable
        fh.write("x")
    store.APP_DIR = _ro
    store._resolve.cache_clear()
    check(
        "app folder not writable -> falls back to the visible per-user folder",
        store.data_dir() == store.FALLBACK_DIR and store.data_dir_is_fallback(),
        store.data_dir(),
    )
finally:
    store.APP_DIR, store.FALLBACK_DIR = _save[0], _save[1]
    os.environ["AIMAUDIT_DATA_DIR"] = _save[2]
    store._resolve.cache_clear()
check("env folder is back after the test", store.data_dir() == _dd and not store.data_dir_is_fallback())

_had_frozen = hasattr(sys, "frozen")
_old_exe = sys.executable
try:
    sys.frozen = True
    sys.executable = os.path.join(tempfile.gettempdir(), "aimaudit_app", "CS2-AimAudit.exe")
    check(
        "frozen exe: the data folder is next to the .exe, not in the unpack folder",
        store._app_dir() == os.path.dirname(sys.executable),
        store._app_dir(),
    )
finally:
    sys.executable = _old_exe
    if not _had_frozen:
        del sys.frozen
check("not frozen: next to the sources", store._app_dir() == os.path.dirname(os.path.abspath(store.__file__)))

print("== store: demo library ==")
check("empty library lists nothing", store.list_demos() == [])
_b1 = b"PBDEMS2\0" + os.urandom(5000)
_e1 = store.add_demo(io.BytesIO(_b1), "My Match.dem")
check(
    "upload saved as a plain file with an id marker",
    os.path.isfile(store.demo_path(_e1))
    and _e1["file"] == f"My Match__{_e1['id'][:8]}.dem"
    and store.demo_key(_e1["file"]) == _e1["id"][:8]
    and _e1["size"] == len(_b1)
    and _e1["name"] == "My Match",
    _e1,
)
_e1b = store.add_demo(io.BytesIO(_b1), "another name.dem")
check(
    "the same bytes under another name are not stored twice",
    _e1b["id"] == _e1["id"] and len(store.list_demos()) == 1 and len(os.listdir(store.demos_dir())) >= 1
    and not any(f.startswith(".incoming") for f in os.listdir(store.demos_dir())),
)
_e2 = store.add_demo(io.BytesIO(b"BZh" + os.urandom(2000)), "match.dem.bz2")
check(
    "compound extension kept (.dem.bz2), marker before it",
    _e2["file"] == f"match__{_e2['id'][:8]}.dem.bz2",
    _e2["file"],
)
try:
    store.add_demo(io.BytesIO(b""), "empty.dem")
    check("empty upload -> ValueError", False)
except ValueError:
    check("empty upload -> ValueError", True)
check(
    "no half-written .incoming file left",
    not any(f.startswith(".incoming") for f in os.listdir(store.demos_dir())),
)
_srcf = os.path.join(tempfile.mkdtemp(), "from disk.dem")
with open(_srcf, "wb") as fh:
    fh.write(os.urandom(3000))
_e3 = store.add_demo(_srcf)
check(
    "a path is copied, the original stays",
    os.path.isfile(_srcf) and os.path.isfile(store.demo_path(_e3)) and _e3["name"] == "from disk",
)
# a file dropped into demos/ by hand joins the library (named with a marker)
_hand = os.path.join(store.demos_dir(), "dropped by hand.dem")
with open(_hand, "wb") as fh:
    fh.write(os.urandom(4000))
_names = {e["name"] for e in store.list_demos()}
check("hand-copied demo is adopted", "dropped by hand" in _names, _names)
check(
    "... and renamed with the id marker",
    not os.path.exists(_hand)
    and any(store.demo_key(f) for f in os.listdir(store.demos_dir()) if f.startswith("dropped by hand")),
)
with open(os.path.join(store.demos_dir(), "copy of first.dem"), "wb") as fh:
    fh.write(_b1)
check(
    "a hand-copied duplicate of a known demo is not listed twice",
    sum(1 for e in store.list_demos() if e["id"] == _e1["id"]) == 1,
)
store.touch(_e2["id"])
check("latest opened is listed first", store.list_demos()[0]["id"] == _e2["id"])
store.update_meta(_e1["id"], map="de_mirage", tickrate=64, players=["A", "B"])
_got = store.get_demo(_e1["id"])
check(
    "meta of a loaded demo is remembered",
    _got["map"] == "de_mirage" and _got["tickrate"] == 64 and _got["players"] == ["A", "B"],
)
os.remove(store.demo_path(_e3))
check("a deleted file leaves the library", _e3["id"] not in {e["id"] for e in store.list_demos()})

print("== store: parsed cache, unpack cache, delete, storage panel ==")
_p1 = store.demo_path(_e1)
check("demo without an id marker is never cached", store.write_parsed("/x/plain.dem", "v1", {"a": 1}) is False)
check("write_parsed -> read_parsed round trip", store.write_parsed(_p1, "v1", {"a": 1}) and store.read_parsed(_p1, "v1") == {"a": 1})
check("another code version misses", store.read_parsed(_p1, "v2") is None)
store.write_parsed(_p1, "v2", {"a": 2})
check(
    "a new version replaces the old file",
    store.read_parsed(_p1, "v1") is None and store.read_parsed(_p1, "v2") == {"a": 2},
)
with open(os.path.join(store.parsed_dir(), f"{_e1['id'][:8]}-v2.pkl"), "wb") as fh:
    fh.write(b"not a pickle")
check(
    "corrupt cache file -> None and removed",
    store.read_parsed(_p1, "v2") is None
    and not os.path.exists(os.path.join(store.parsed_dir(), f"{_e1['id'][:8]}-v2.pkl")),
)
# data/logs/notes.log: the support notes for events without a UI surface
_logs = os.path.join(store.logs_dir(), "notes.log")
_log_txt = Path(_logs).read_text(encoding="utf-8") if os.path.isfile(_logs) else ""
check(
    "dropping a corrupt cache is noted in data/logs/notes.log",
    "dropped unreadable parsed cache" in _log_txt,
    _log_txt[-200:],
)
store.log_note("test", "hello")
_lines = Path(_logs).read_text(encoding="utf-8").splitlines()
check("log_note appends a tagged line", _lines[-1].endswith("test: hello"), _lines[-1])
for _i in range(store.LOG_MAX_LINES + 50):
    store.log_note("test", f"filler {_i}")
_lines = Path(_logs).read_text(encoding="utf-8").splitlines()
check(
    "log_note keeps the log trimmed to LOG_MAX_LINES",
    len(_lines) == store.LOG_MAX_LINES and _lines[-1].endswith("test: filler 249"),
    f"{len(_lines)} lines, last={_lines[-1] if _lines else ''}",
)
_real_replace = store.os.replace
def _full_disk(*_a, **_k):
    raise OSError("disk full")
store.os.replace = _full_disk
check("a failed cache write returns False", store.write_parsed(_p1, "v5", {"a": 1}) is False)
store.os.replace = _real_replace
_log_txt = Path(_logs).read_text(encoding="utf-8")
check(
    "a failed cache write is noted in the log",
    "parsed cache write failed: OSError" in _log_txt,
    _log_txt[-120:],
)
_p2 = store.demo_path(_e2)
store.write_parsed(_p1, "v3", b"x" * 4000)
os.utime(os.path.join(store.parsed_dir(), f"{_e1['id'][:8]}-v3.pkl"), (1, 1))  # oldest
store.write_parsed(_p2, "v3", b"y" * 4000)
store.prune_parsed(5000)
check(
    "pruning drops the least recently used cache first",
    store.read_parsed(_p1, "v3") is None and store.read_parsed(_p2, "v3") is not None,
)
_up = os.path.join(store.unpacked_dir(), f"cs2_aimaudit_match__{_e2['id'][:8]}.dem_9_9.dem")
with open(_up, "wb") as fh:
    fh.write(b"u" * 500)
_rep = store.storage_report()
check(
    "storage report: sizes of demos / caches, and the folder",
    _rep["data"] == _dd and _rep["demos"] > 0 and _rep["parsed"] > 0 and _rep["unpacked"] >= 500,
    _rep,
)
_freed = store.delete_demo(_e2["id"])
check(
    "delete: file, parsed cache and unpacked copy are gone, demos of others stay",
    _freed > 0
    and not os.path.exists(store.demo_path(_e2))
    and not os.path.exists(_up)
    and store.read_parsed(_p2, "v3") is None
    and os.path.isfile(_p1)
    and _e2["id"] not in {e["id"] for e in store.list_demos()},
)
store.write_parsed(_p1, "v4", b"z" * 100)
_fr = store.clear_cache()
check(
    "clear cache frees the caches and keeps every demo",
    _fr > 0 and store.storage_report()["parsed"] == 0 and os.path.isfile(_p1),
)
check("human_size", store.human_size(512) == "512 B" and store.human_size(1536) == "1.5 KB")

# radars may lie flat next to map_data.json (what tools/fetch_radars.py writes)
_flat = tempfile.mkdtemp(prefix="cs2aa_flat_")
_tiny_png(os.path.join(_flat, "de_flat.png"), 16)
with open(os.path.join(_flat, "map_data.json"), "w", encoding="utf-8") as fh:
    _json.dump({"de_flat": {"pos_x": 0, "pos_y": 0, "scale": 1.0}}, fh)
_old_env3 = os.environ.get("AIMAUDIT_RADAR_DIR")
os.environ["AIMAUDIT_RADAR_DIR"] = _flat
try:
    check("radar: flat folder (png next to map_data.json) is found", core.load_radar("de_flat") is not None)
finally:
    if _old_env3 is None:
        os.environ.pop("AIMAUDIT_RADAR_DIR", None)
    else:
        os.environ["AIMAUDIT_RADAR_DIR"] = _old_env3
check(
    "radar roots: the data folder's radars come right after AIMAUDIT_RADAR_DIR",
    core.radar_roots()[0] == store.radars_dir(),
    core.radar_roots()[:2],
)

print("== core: unpack cache in the data folder, parsed demo cache ==")
_bzp = os.path.join(tempfile.mkdtemp(), "x.dem.bz2")
with open(_bzp, "wb") as fh:
    fh.write(bz2.compress(b"PBDEMS2\0" + b"1" * 300))
check(
    "unpacked demos go to <data>/cache/unpacked",
    os.path.dirname(core.decompressed_demo(_bzp)) == os.path.realpath(store.unpacked_dir())
    or os.path.dirname(core.decompressed_demo(_bzp)) == store.unpacked_dir(),
    core.decompressed_demo(_bzp),
)
_cached_path = os.path.join(store.unpacked_dir(), "cached__deadbeef.dem")
FakeParser.demos[_cached_path] = FakeParser.demos["impact.dem"]
_d1, _hit1 = core.load_demo_cached(_cached_path)
check("first open parses (not from cache)", _hit1 is False and len(_d1.players) > 0)
FakeParser.demos.pop(_cached_path)  # the parser is gone: only the cache can answer
_d2, _hit2 = core.load_demo_cached(_cached_path)
check(
    "second open comes from the disk cache, identical",
    _hit2 is True and _d2.players.equals(_d1.players) and _d2.deaths.equals(_d1.deaths) and _d2.sides == _d1.sides,
)
check("parse version names parser + code", core.PARSE_VERSION.startswith("f1-") and len(core.PARSE_VERSION) > 8)
_other = core.PARSE_VERSION
try:
    # patch the loader module too: load_demo_cached reads PARSE_VERSION
    # from its own module globals
    core.PARSE_VERSION = core.loader.PARSE_VERSION = "f1-other"
    try:
        core.load_demo_cached(_cached_path)
        check("changed parser/code version -> cache not used", False)
    except Exception:  # noqa: BLE001 - the fake parser is gone, so a miss raises
        check("changed parser/code version -> cache not used", True)
finally:
    core.PARSE_VERSION = core.loader.PARSE_VERSION = _other
check("state file lives in the data folder", os.path.dirname(store.state_path()) == store.data_dir())
core.save_state({"last_demo": "abc"})
check("state round trip", core.load_state() == {"last_demo": "abc"})

# ---- tools/fetch_radars.py: the launcher step that installs the radars
print("== tools/fetch_radars.py ==")
# parents[2]: this module sits one level deeper than the original test_core.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import contextlib  # noqa: E402
import time  # noqa: E402

import fetch_radars  # noqa: E402


def _radar_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("radars/de_a.png", b"png-a")
        zf.writestr("radars/de_a_lower.png", b"png-a-low")
        zf.writestr("radars/../evil.png", b"x")  # path trick: must be ignored
        zf.writestr("radars/BAD NAME.png", b"x")
        zf.writestr("readme.txt", b"x")
    return buf.getvalue()


_calls = []


def _fake_get(url, timeout=60):
    _calls.append((url, timeout))
    if url.endswith("map_data.json"):
        return b'{"de_a": {"pos_x": 0, "pos_y": 0, "scale": 1}}'
    return _radar_zip()


def _quiet_main(args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fetch_radars.main(args)


_real_get = fetch_radars._get
_no_radars = os.environ.pop("AIMAUDIT_NO_RADARS", None)
try:
    fetch_radars._get = _fake_get
    _d1 = os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "radars")
    check("radars: nothing installed yet", not fetch_radars.present(_d1))
    check("radars: launcher mode downloads when missing", _quiet_main(["--if-missing", "--dest", _d1]) == 0 and fetch_radars.present(_d1))
    check(
        "radars: flat files, unsafe archive members ignored",
        sorted(os.listdir(_d1)) == ["de_a.png", "de_a_lower.png", "map_data.json"],
        sorted(os.listdir(_d1)),
    )
    _n = len(_calls)
    check("radars: already installed -> no download at all", _quiet_main(["--if-missing", "--dest", _d1]) == 0 and len(_calls) == _n)
    # the calibration is written last, so an interrupted run never looks complete
    _order = []
    _real_write = fetch_radars._write
    fetch_radars._write = lambda path, data: (_order.append(os.path.basename(path)), _real_write(path, data))[1]
    try:
        fetch_radars.download_radar_set(os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "r"), "t", 5)
    finally:
        fetch_radars._write = _real_write
    check("radars: map_data.json is written last", _order[-1] == "map_data.json" and len(_order) == 3, _order)
    # opt-out
    os.environ["AIMAUDIT_NO_RADARS"] = "1"
    _d2 = os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "radars")
    _n = len(_calls)
    check("radars: AIMAUDIT_NO_RADARS=1 -> no download", _quiet_main(["--if-missing", "--dest", _d2]) == 0 and len(_calls) == _n and not fetch_radars.present(_d2))
    del os.environ["AIMAUDIT_NO_RADARS"]

    # no internet: never blocks the launcher, retries only after a day
    def _offline(url, timeout=60):
        _calls.append((url, timeout))
        raise OSError("no route to host")

    fetch_radars._get = _offline
    _d3 = os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "radars")
    _n = len(_calls)
    check("radars: offline in launcher mode -> exit 0", _quiet_main(["--if-missing", "--dest", _d3]) == 0 and len(_calls) > _n and not fetch_radars.present(_d3))
    check("radars: ... with a short timeout and a failure mark", _calls[-1][1] <= 20 and os.path.isfile(os.path.join(_d3, fetch_radars.FAILED_MARK)))
    _n = len(_calls)
    check("radars: no retry within a day", _quiet_main(["--if-missing", "--dest", _d3]) == 0 and len(_calls) == _n)
    _old = time.time() - 25 * 3600
    os.utime(os.path.join(_d3, fetch_radars.FAILED_MARK), (_old, _old))
    check("radars: retries after a day", _quiet_main(["--if-missing", "--dest", _d3]) == 0 and len(_calls) > _n)
    check("radars: manual run offline -> exit 1 (told to the user)", _quiet_main(["--dest", _d3]) == 1)
    fetch_radars._get = _fake_get
    check(
        "radars: manual run ignores the failure mark and clears it",
        _quiet_main(["--dest", _d3]) == 0 and fetch_radars.present(_d3) and not os.path.exists(os.path.join(_d3, fetch_radars.FAILED_MARK)),
    )
    # an HTML error page instead of map_data.json must not install anything
    fetch_radars._get = lambda url, timeout=60: b"<html>rate limited</html>"
    _d4 = os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "radars")
    check("radars: garbage answer -> nothing installed, launcher not blocked", _quiet_main(["--if-missing", "--dest", _d4]) == 0 and not fetch_radars.present(_d4))
finally:
    fetch_radars._get = _real_get
    os.environ.pop("AIMAUDIT_NO_RADARS", None)
    if _no_radars is not None:
        os.environ["AIMAUDIT_NO_RADARS"] = _no_radars

# ---- linked demos: the library keeps the PATH, nothing is copied
print("== store: linked demos ==")
import types  # noqa: E402

_ldir = tempfile.mkdtemp(prefix="cs2aa_link_")
_lp = os.path.join(_ldir, "My Linked Match.dem")
with open(_lp, "wb") as fh:
    fh.write(b"PBDEMS2\0" + os.urandom(3000))
_files_before = sorted(os.listdir(store.demos_dir()))
_L1 = store.add_link(_lp)
check(
    "link: the entry points at the original file, nothing is copied",
    _L1["mode"] == "link"
    and _L1["status"] == "ok"
    and store.demo_path(_L1) == os.path.abspath(_lp)
    and _L1["name"] == "My Linked Match"
    and sorted(os.listdir(store.demos_dir())) == _files_before,
    _L1,
)
check(
    "link: the same path again is the same entry (no duplicate)",
    store.add_link(_lp)["id"] == _L1["id"]
    and sum(1 for e in store.list_demos() if e["id"] == _L1["id"]) == 1,
)
check("link: a pasted path in quotes with spaces works", store.add_link('  "%s"  ' % _lp)["id"] == _L1["id"])
_txt = os.path.join(_ldir, "notes.txt")
open(_txt, "w").write("x")
_empty = os.path.join(_ldir, "empty.dem")
open(_empty, "wb").close()
for _label, _bad in (("missing file", os.path.join(_ldir, "nope.dem")), ("not a demo", _txt), ("empty file", _empty)):
    try:
        store.add_link(_bad)
        check(f"link: {_label} -> ValueError", False)
    except ValueError:
        check(f"link: {_label} -> ValueError", True)

# the file is moved: the entry stays, says missing, and can be pointed at the new place
_lp2 = os.path.join(_ldir, "renamed.dem")
os.rename(_lp, _lp2)
_miss = store.get_demo(_L1["id"])
check(
    "link: a moved file leaves the entry in the list as missing",
    _miss is not None and _miss["status"] == "missing" and any(e["id"] == _L1["id"] for e in store.list_demos()),
)
_re = store.relink(_L1["id"], _lp2)
check(
    "link: relink keeps the entry (and its id) and finds the file again",
    _re["id"] == _L1["id"] and _re["status"] == "ok" and _re["path"] == os.path.abspath(_lp2),
)
_other = os.path.join(_ldir, "other.dem")
with open(_other, "wb") as fh:
    fh.write(b"PBDEMS2\0" + os.urandom(500))
_L2 = store.add_link(_other)
try:
    store.relink(_L1["id"], _other)
    check("link: relink onto a file that is already listed -> ValueError", False)
except ValueError:
    check("link: relink onto a file that is already listed -> ValueError", True)
try:
    store.relink(_e1["id"], _lp2)
    check("link: only a linked demo can be relinked", False)
except ValueError:
    check("link: only a linked demo can be relinked", True)

# parse keys: location + size + mtime for a plain file, so a replaced file never gets a stale cache
_k1 = store.parse_key(_lp2)
with open(_lp2, "ab") as fh:
    fh.write(b"more bytes")
check("parse key follows the file (replaced on disk -> new key)", store.parse_key(_lp2) != _k1)
check(
    "list notices the changed file and updates the size",
    store.get_demo(_L1["id"])["size"] == os.path.getsize(_lp2),
)
check("parse key of a copy = its content id", store.parse_key(store.demo_path(_e1)) == _e1["id"][:8])
_unp = os.path.join(store.unpacked_dir(), f"cs2_aimaudit_m__{_e1['id'][:8]}.dem_5_5.dem")
check("parse key of an unpacked archive = id + its own name", (store.parse_key(_unp) or "").startswith(_e1["id"][:8] + "-"))
_fake_marker = os.path.join(_ldir, "x__deadbeef.dem")
open(_fake_marker, "wb").write(b"PBDEMS2\0" + b"1" * 20)
check(
    "a marker-looking name OUTSIDE the library folders is just a plain file",
    (store.parse_key(_fake_marker) or "").startswith(store.link_id(os.path.realpath(_fake_marker))[:8]),
)

# the user's file is never touched by removing a link
check("write_parsed for a linked demo", store.write_parsed(_lp2, "v1", {"a": 1}) and store.read_parsed(_lp2, "v1") == {"a": 1})
_freed = store.delete_demo(_L1["id"])
check(
    "remove a linked demo: entry and cache go, the user's file STAYS",
    os.path.isfile(_lp2) and store.get_demo(_L1["id"]) is None and store.read_parsed(_lp2, "v1") is None and _freed > 0,
    _freed,
)

# a linked ARCHIVE: its unpacked copy carries the id and goes with the entry
_arc = os.path.join(_ldir, "arch.dem.bz2")
with open(_arc, "wb") as fh:
    fh.write(bz2.compress(b"PBDEMS2\0" + b"7" * 400))
_L3 = store.add_link(_arc)
_unpacked = core.decompressed_demo(store.demo_path(_L3), tag=_L3["id"][:8])
check(
    "linked archive: unpacked into cache/unpacked with the id in its name",
    os.path.dirname(_unpacked) == os.path.realpath(store.unpacked_dir()) or os.path.dirname(_unpacked) == store.unpacked_dir(),
)
check("... and the tag is in the file name", f"__{_L3['id'][:8]}" in os.path.basename(_unpacked), _unpacked)
store.write_parsed(_unpacked, "v1", {"x": 1})
store.delete_demo(_L3["id"])
check(
    "removing the linked archive deletes its unpacked copy and parsed cache, not the archive",
    os.path.isfile(_arc) and not os.path.exists(_unpacked) and store.read_parsed(_unpacked, "v1") is None,
)

# two more linked demos (a "whole folder at once" flow was removed with
# store.add_folder: the UI only links individual files now)
_fdir = tempfile.mkdtemp(prefix="cs2aa_fold_")
for _n in ("a.dem", "b.dem.bz2"):
    open(os.path.join(_fdir, _n), "wb").write(b"PBDEMS2\0" + b"2" * 30)
store.add_link(os.path.join(_fdir, "a.dem"))
store.add_link(os.path.join(_fdir, "b.dem.bz2"))
_rep2 = store.storage_report()
check(
    "storage report counts linked demos separately (and the fallback flag)",
    _rep2["linked_count"] >= 3 and _rep2["linked"] > 0 and _rep2["fallback"] is False,
    _rep2,
)
# a file that already lies inside demos/ is never listed twice
_inlib = os.path.join(store.demos_dir(), "linked in place.dem")
open(_inlib, "wb").write(b"PBDEMS2\0" + os.urandom(900))
_Lin = store.add_link(_inlib)
_after = [e for e in store.list_demos() if e["name"].startswith("linked in place")]
check(
    "choosing a file that already lies in demos/ lists it once, as a copy",
    len(_after) == 1 and _after[0]["mode"] == "copy" and os.path.isfile(store.demo_path(_after[0])) and _Lin["id"] == _after[0]["id"],
    [(e["name"], e["mode"]) for e in _after],
)

print("== nativedialog ==")
import nativedialog  # noqa: E402

_real_run = nativedialog.subprocess.run


def _fake_run(stdout=b"", rc=0, exc=None):
    def run(*a, **k):
        if exc:
            raise exc
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=b"")

    return run


try:
    nativedialog.subprocess.run = _fake_run(b'banner\n["C:\\\\x\\\\a.dem", "D:\\\\b.dem"]\n')
    check("dialog: chosen files come back as a list", nativedialog.pick_files() == ["C:\\x\\a.dem", "D:\\b.dem"], nativedialog.pick_files())
    nativedialog.subprocess.run = _fake_run(b"[]\n")
    check("dialog: cancel -> []", nativedialog.pick_files() == [])
    nativedialog.subprocess.run = _fake_run(b"", rc=1)
    check("dialog: no tkinter / headless (exit code 1) -> None", nativedialog.pick_files() is None)
    nativedialog.subprocess.run = _fake_run(b"not json at all")
    check("dialog: garbage output -> None", nativedialog.pick_files() is None)
    nativedialog.subprocess.run = _fake_run(exc=OSError("no python"))
    check("dialog: cannot start the child -> None", nativedialog.pick_files() is None)
finally:
    nativedialog.subprocess.run = _real_run


def _write_demo_file(fname):
    """A real temp file (unique bytes per name) for library-entry tests."""
    import tempfile as _tf

    d = _tf.mkdtemp(prefix="cs2aa_ms_")
    p = os.path.join(d, fname)
    with open(p, "wb") as fh:
        fh.write(f"matchstats fake demo {fname}".encode())
    return p


# ------------------------------------------------------------ match list
# core/matchlist.py: the merge behind the Matches table (pure data glue -
# provider records, library entries, cached summaries; no parsing here).
from datetime import datetime as _datetime, timezone as _timezone

from sources import model as _sm
from core.matchlist import build_rows, manual_demo_ready


def _rec(mid, when=None, result="win", score=(13, 5), demo=_sm.DemoState.READY, **kw):
    return _sm.MatchRecord(
        provider="steam",
        match_id=mid,
        played_at=when,
        map_name="de_mirage",
        mode="Premier",
        score=score,
        result=result,
        demo=demo,
        **kw,
    )


def _lib(id_, *, match=None, status="ok", mode="copy", added=None, path=None, mtime_ns=None, **kw):
    e = {
        "id": id_,
        "file": f"x__{id_[:8]}.dem",
        "name": f"demo {id_}",
        "size": 100,
        "added": added or "2024-05-01T10:00:00+00:00",
        "map": None,
        "status": status,
        "mode": mode,
    }
    if match:
        e["match"] = match
    if path:
        e["path"] = path
    if mtime_ns:
        e["mtime_ns"] = mtime_ns
    e.update(kw)
    return e


_T0 = _datetime(2024, 6, 1, 12, 0, tzinfo=_timezone.utc)
_T1 = _datetime(2024, 6, 2, 12, 0, tzinfo=_timezone.utc)
_T2 = _datetime(2024, 6, 3, 12, 0, tzinfo=_timezone.utc)

# a downloaded steam demo and its provider record merge into ONE row
rows = build_rows(
    [_rec("m1", _T1), _rec("m2", _T0, result="loss", score=(9, 13))],
    [
        _lib("aaaa1111aaaa1111", match={"provider": "steam", "match_id": "m1", "mode": "Premier", "score": [13, 5], "result": "win"}),
        _lib("bbbb2222bbbb2222"),  # your own demo: its own row
    ],
)
check("matchlist: no duplicates, 3 rows for 2 records + 2 entries", len(rows) == 3, [r.key for r in rows])
m1 = next(r for r in rows if r.key == "steam:m1")
check("matchlist: the downloaded record is one row, bound to its entry",
      m1.demo_id == "aaaa1111aaaa1111" and m1.entry is not None and m1.rec is not None
      and m1.demo_state == _sm.DemoState.DOWNLOADED and not m1.missing,
      (m1.demo_id, m1.demo_state))
own = next(r for r in rows if r.key == "lib:bbbb2222bbbb2222")
check("matchlist: an own demo is its own row with no provider",
      own.provider is None and own.rec is None and own.demo_id == "bbbb2222bbbb2222"
      and own.source == "file",
      (own.key, own.source))
check("matchlist: sorted newest first (record time, else the entry's)",
      [r.key for r in rows] == ["steam:m1", "steam:m2", "lib:bbbb2222bbbb2222"],
      [r.key for r in rows])

# a library entry whose provider record fell out of the cache keeps the
# provider binding (still one row, still Steam)
rows = build_rows(
    [_rec("m9", _T1)],
    [
        _lib("cccc3333cccc3333", match={"provider": "steam", "match_id": "m5", "mode": "Premier", "score": [13, 2], "result": "win"}),
        _lib("dddd4444dddd4444"),
    ],
)
check("matchlist: an entry without a cached record is still a Steam row",
      any(r.key == "steam:m5" and r.demo_id == "cccc3333cccc3333" and r.source == "steam" for r in rows),
      [r.key for r in rows])
check("matchlist: the own demo stays separate", any(r.key == "lib:dddd4444dddd4444" for r in rows))

# states: ready / expired / manual records carry their DemoState
rows = build_rows([_rec("r1"), _rec("e1", demo=_sm.DemoState.EXPIRED)], [])
check("matchlist: ready and expired records keep their state",
      rows[0].demo_state == _sm.DemoState.READY and rows[1].demo_state == _sm.DemoState.EXPIRED
      and rows[0].demo_id is None and rows[0].entry is None,
      [(r.key, r.demo_state) for r in rows])

# a linked demo whose file is gone: missing, still in the library
rows = build_rows(
    [],
    [_lib("eeee5555eeee5555", mode="link", status="missing", path="Z:/gone.dem")],
)
check("matchlist: a moved linked demo is a missing row",
      rows[0].missing and rows[0].demo_id == "eeee5555eeee5555" and rows[0].demo_state == _sm.DemoState.DOWNLOADED,
      rows[0])

# stats wiring: the callable feeds the summary cells, no parsing anywhere
_stats = {"kills": 20, "deaths": 10, "kd": 2.0, "score": (16, 14), "result": "win",
          "map": "de_dust2", "played_at": _T2, "open_win_pct": 55.0}


def _stats_of(entry):
    return dict(_stats, me_sid="7656") if entry["id"] == "aaaa1111aaaa1111" else None


rows = build_rows(
    [_rec("m1", _T1, score=None, result=None)],
    [_lib("aaaa1111aaaa1111", match={"provider": "steam", "match_id": "m1"})],
    stats_of=_stats_of,
)
m1 = rows[0]
check("matchlist: stats fill the row's empty fields (score/result)",
      m1.stats["kills"] == 20 and m1.score == (16, 14) and m1.result == "win",
      (m1.score, m1.result))
check("matchlist: the provider's time wins over the cache's",
      m1.played_at == _T1, m1.played_at)
check("matchlist: the provider's own fields win over the cache",
      m1.mode == "Premier" and m1.map_name == "de_mirage", (m1.mode, m1.map_name))
# an own demo takes everything the cache has: map, score, result, time
rows = build_rows([], [_lib("bbbb2222bbbb2222")], stats_of=lambda e: dict(_stats))
own = rows[0]
check("matchlist: an own demo reads map/score/result/time from the cache",
      own.map_name == "de_dust2" and own.score == (16, 14) and own.result == "win"
      and own.played_at == _T2,
      (own.map_name, own.score, own.result))

# rows without stats (not analyzed yet): None cells, no crash
rows = build_rows([_rec("m1", _T1)], [_lib("aaaa1111aaaa1111", match={"provider": "steam", "match_id": "m1"})], stats_of=lambda e: None)
check("matchlist: no stats -> empty cells, provider score/result stand",
      rows[0].stats is None and rows[0].score == (13, 5) and rows[0].result == "win", rows[0].score)

# a broken stats cache (an exception) must not kill the list
rows = build_rows([], [_lib("ffff6666ffff6666")], stats_of=lambda e: (_ for _ in ()).throw(ValueError("bad json")))
check("matchlist: a broken summary cache is skipped", rows[0].stats is None, rows[0].stats)

# undated rows keep to the end; a stats time can stand in for a missing one
rows = build_rows([_rec("x", None)], [_lib("1111111111111111", match={"provider": "steam", "match_id": "x"})],
                  stats_of=lambda e: {"played_at": _T2})
check("matchlist: an undated record sorts by the cache time", rows[0].played_at == _T2, rows[0].played_at)

# manual demos: importable only when the file really sits on disk
_ml_dir = tempfile.mkdtemp(prefix="cs2aa_matchlist_")
check("matchlist: a manual record without a file is not importable",
      manual_demo_ready(_rec("m", demo=_sm.DemoState.MANUAL, demo_ref=os.path.join(_ml_dir, "nowhere.dem"))) is False)
with open(os.path.join(_ml_dir, "manual_ok.dem"), "wb") as _fh:
    _fh.write(b"x")
check("matchlist: a manual record with the file on disk is importable",
      manual_demo_ready(_rec("m", demo=_sm.DemoState.MANUAL, demo_ref=os.path.join(_ml_dir, "manual_ok.dem"))) is True)



# ------------------------------------------------------------ match stats
# core/matchstats.py: the per-match summary cache behind the Matches table
# (existing core formulas only: player_summary, impact_stats, round winners).
from core import matchstats as _ms

_ms_demo = core.load_demo("match.dem")

# who "me" is: the accounts' SteamID64 when she played, else the entry's
# remembered me_sid, else nothing (the match view asks once)
check(
    "matchstats: resolve_me prefers the accounts' SteamID64",
    _ms.resolve_me(_ms_demo, {}, {"steam_id64": S1}) == S1
    and _ms.resolve_me(_ms_demo, {}, {"steam_id64": "76569999999999999"}) is None,
)
check(
    "matchstats: resolve_me falls back to the entry's me_sid",
    _ms.resolve_me(_ms_demo, {"me_sid": S3}, {"steam_id64": "76569999999999999"}) == S3
    and _ms.resolve_me(_ms_demo, {"me_sid": "76569999999999999"}, {}) is None,
)

# the summary of the synthetic match: Alpha (S1) killed Charlie (S3) once
# across the two rounds of make_match() - 1 kill, 0 deaths, kd 1.0
_ms_sum = _ms.compute(_ms_demo, S1, played_at=_T0)
check(
    "matchstats: the summary carries version, me, the numbers and the date",
    _ms_sum["version"] == _ms.STATS_VERSION
    and _ms_sum["me_sid"] == S1
    and _ms_sum["map"] == "de_dust2"
    and _ms_sum["rounds"] == 2
    and _ms_sum["kills"] == 1
    and _ms_sum["deaths"] == 0
    and _ms_sum["kd"] == 1.0
    and _ms_sum["played_at"] == _T0.isoformat(),
    {k: _ms_sum[k] for k in ("version", "me_sid", "map", "rounds", "kills", "deaths", "kd", "played_at")},
)
check(
    "matchstats: every number cell is a number or None, never NaN",
    all(
        v is None or isinstance(v, (int, float))
        for k, v in _ms_sum.items()
        if k in ("kd", "adr", "hs_pct", "accuracy", "open_win_pct", "rounds", "kills", "deaths")
    ),
    _ms_sum,
)
check(
    "matchstats: no round winners derivable from the synthetic 1-kill fake -> no score",
    _ms_sum["score"] is None and _ms_sum["result"] is None,
    (_ms_sum["score"], _ms_sum["result"]),
)

# the score and the result when the rounds decide them: the sides demo
# (round 1 Alpha is T, round 2 he is CT after the swap) with the winners
# stubbed to T then CT -> 2:0, a win from Alpha's side
_ms_real_winners = _ms.round_winners
_ms.round_winners = lambda demo: {1: "T", 2: "CT"}
try:
    _ms_win = _ms.compute(core.load_demo("sides.dem"), S1, played_at=_T0)
finally:
    _ms.round_winners = _ms_real_winners
check(
    "matchstats: round winners give the score and the result from my side",
    _ms_win["score"] == [2, 0] and _ms_win["result"] == "win",
    (_ms_win["score"], _ms_win["result"]),
)

# the cache: write / read / parse-back, the code version in the file name
_ms_file = _write_demo_file("stats_test.dem")
_ms_key = store.parse_key(_ms_file)
_ms.write_cached(_ms_file, dict(_ms_sum, score=[16, 14], result="win"))
check(
    "matchstats: write_cached lands <parse_key>-<version>.json",
    os.path.isfile(os.path.join(store.matchstats_dir(), f"{_ms_key}-{core.PARSE_VERSION}.json")),
    sorted(os.listdir(store.matchstats_dir())),
)
_back = _ms.read_cached(_ms_file)
check(
    "matchstats: read_cached returns the whole summary",
    _back is not None and _back["me_sid"] == S1 and _back["score"] == [16, 14],
    _back,
)
_cs = _ms.cached_stats(
    {"id": "x", "path": _ms_file, "name": "s", "size": 1, "mode": "link"}
)
check(
    "matchstats: cached_stats parses the date back, keeps the score",
    _cs is not None and _cs["played_at"] == _T0 and _cs["score"] == [16, 14],
    (_cs["played_at"], _cs["score"]),
)
store.write_matchstats(_ms_file, "oldcodever", {"version": 0})
check(
    "matchstats: two code versions of one key coexist until the next write",
    os.path.isfile(os.path.join(store.matchstats_dir(), f"{_ms_key}-oldcodever.json")),
    sorted(os.listdir(store.matchstats_dir())),
)
_ms.write_cached(_ms_file, dict(_ms_sum))
check(
    "matchstats: a write drops the older code versions of the same key",
    not any("oldcodever" in f for f in os.listdir(store.matchstats_dir())),
    sorted(os.listdir(store.matchstats_dir())),
)

# reset with the demo: delete_demo clears the summary cache too
_ms_entry = store.add_demo(_write_demo_file("ms_stats.dem"), "ms_stats.dem")
_ms.write_cached(store.demo_path(_ms_entry), dict(_ms_sum, me_sid="zz"))
check(
    "matchstats: the cache file exists for the demo",
    _ms.read_cached(store.demo_path(_ms_entry)) is not None,
)
store.delete_demo(_ms_entry["id"])
check(
    "matchstats: delete_demo resets the summary cache",
    _ms.read_cached(store.demo_path(_ms_entry)) is None,
    sorted(os.listdir(store.matchstats_dir())),
)
# and so does clear_cache
_ms_entry2 = store.add_demo(_write_demo_file("ms_stats2.dem"), "ms_stats2.dem")
_ms.write_cached(store.demo_path(_ms_entry2), dict(_ms_sum, me_sid="zz"))
store.clear_cache()
check(
    "matchstats: clear_cache resets the summary cache",
    _ms.read_cached(store.demo_path(_ms_entry2)) is None
    and os.listdir(store.matchstats_dir()) == [],
    sorted(os.listdir(store.matchstats_dir())),
)

print(f"\n{PASS} passed, {FAIL} failed")
if __name__ == "__main__":
    sys.exit(1 if FAIL else 0)
