"""The data folder, the demo library, caches, radars, dialogs, match list, summaries."""

import bz2
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import types
import zipfile
from datetime import datetime as _datetime, timezone as _timezone
from pathlib import Path

import pytest

import core
import fetch_radars
import nativedialog
import store
from core import matchstats as _ms
from core.matchlist import build_rows, manual_demo_ready
from fakes import FakeParser, S1, S3, _tiny_png
from sources import model as _sm


def test_data_folder():
    _dd = os.path.abspath(os.environ["AIMAUDIT_DATA_DIR"])
    assert store.data_dir() == _dd, "data folder = AIMAUDIT_DATA_DIR"
    assert all(
            os.path.isdir(d) and os.path.commonpath([d, _dd]) == _dd
            for d in (store.demos_dir(), store.unpacked_dir(), store.parsed_dir(), store.radars_dir())
        ), "sub folders are created inside it"
    _save = (store.paths.APP_DIR, store.paths.FALLBACK_DIR, os.environ.pop("AIMAUDIT_DATA_DIR"))
    try:
        _app = tempfile.mkdtemp(prefix="cs2aa_app_")
        store.paths.APP_DIR, store.paths.FALLBACK_DIR = _app, tempfile.mkdtemp(prefix="cs2aa_home_")
        store.paths._resolve.cache_clear()
        assert store.data_dir() == os.path.join(_app, "data"), "default data folder is <app folder>/data, next to the launcher"
        _ro = tempfile.mkdtemp(prefix="cs2aa_ro_")
        with open(os.path.join(_ro, "data"), "w") as fh:  # a FILE named data: not usable
            fh.write("x")
        store.paths.APP_DIR = _ro
        store.paths._resolve.cache_clear()
        assert store.data_dir() == store.paths.FALLBACK_DIR and store.data_dir_is_fallback(), "app folder not writable -> falls back to the visible per-user folder"
    finally:
        store.paths.APP_DIR, store.paths.FALLBACK_DIR = _save[0], _save[1]
        os.environ["AIMAUDIT_DATA_DIR"] = _save[2]
        store.paths._resolve.cache_clear()
    assert store.data_dir() == _dd and not store.data_dir_is_fallback(), "env folder is back after the test"
    _had_frozen = hasattr(sys, "frozen")
    _old_exe = sys.executable
    try:
        sys.frozen = True
        sys.executable = os.path.join(tempfile.gettempdir(), "aimaudit_app", "CS2-AimAudit.exe")
        assert store.paths._app_dir() == os.path.dirname(sys.executable), "frozen exe: the data folder is next to the .exe, not in the unpack folder"
    finally:
        sys.executable = _old_exe
        if not _had_frozen:
            del sys.frozen
    assert store.paths._app_dir() == os.path.dirname(os.path.dirname(os.path.abspath(store.__file__))), "not frozen: next to the sources"


def test_library_and_caches():
    assert store.list_demos() == [], "empty library lists nothing"
    _b1 = b"PBDEMS2\0" + os.urandom(5000)
    _e1 = store.add_demo(io.BytesIO(_b1), "My Match.dem")
    assert (
        os.path.isfile(store.demo_path(_e1))
        and _e1["file"] == f"My Match__{_e1['id'][:8]}.dem"
        and store.demo_key(_e1["file"]) == _e1["id"][:8]
        and _e1["size"] == len(_b1)
        and _e1["name"] == "My Match"
    ), "upload saved as a plain file with an id marker"
    _e1b = store.add_demo(io.BytesIO(_b1), "another name.dem")
    assert (
        _e1b["id"] == _e1["id"] and len(store.list_demos()) == 1 and len(os.listdir(store.demos_dir())) >= 1
        and not any(f.startswith(".incoming") for f in os.listdir(store.demos_dir()))
    ), "the same bytes under another name are not stored twice"
    _e2 = store.add_demo(io.BytesIO(b"BZh" + os.urandom(2000)), "match.dem.bz2")
    assert _e2["file"] == f"match__{_e2['id'][:8]}.dem.bz2", "compound extension kept (.dem.bz2), marker before it"
    with pytest.raises(ValueError):
        store.add_demo(io.BytesIO(b""), "empty.dem")
    assert not any(f.startswith(".incoming") for f in os.listdir(store.demos_dir())), "no half-written .incoming file left"
    _srcf = os.path.join(tempfile.mkdtemp(), "from disk.dem")
    with open(_srcf, "wb") as fh:
        fh.write(os.urandom(3000))
    _e3 = store.add_demo(_srcf)
    assert os.path.isfile(_srcf) and os.path.isfile(store.demo_path(_e3)) and _e3["name"] == "from disk", "a path is copied, the original stays"
    # a file dropped into demos/ by hand joins the library (named with a marker)
    _hand = os.path.join(store.demos_dir(), "dropped by hand.dem")
    with open(_hand, "wb") as fh:
        fh.write(os.urandom(4000))
    _names = {e["name"] for e in store.list_demos()}
    assert "dropped by hand" in _names, "hand-copied demo is adopted"
    assert (
        not os.path.exists(_hand)
        and any(store.demo_key(f) for f in os.listdir(store.demos_dir()) if f.startswith("dropped by hand"))
    ), "... and renamed with the id marker"
    with open(os.path.join(store.demos_dir(), "copy of first.dem"), "wb") as fh:
        fh.write(_b1)
    assert sum(1 for e in store.list_demos() if e["id"] == _e1["id"]) == 1, "a hand-copied duplicate of a known demo is not listed twice"
    store.touch(_e2["id"])
    assert store.list_demos()[0]["id"] == _e2["id"], "latest opened is listed first"
    store.update_meta(_e1["id"], map="de_mirage", tickrate=64, players=["A", "B"])
    _got = store.get_demo(_e1["id"])
    assert _got["map"] == "de_mirage" and _got["tickrate"] == 64 and _got["players"] == ["A", "B"], "meta of a loaded demo is remembered"
    os.remove(store.demo_path(_e3))
    assert _e3["id"] not in {e["id"] for e in store.list_demos()}, "a deleted file leaves the library"
    _p1 = store.demo_path(_e1)
    assert store.write_parsed("/x/plain.dem", "v1", {"a": 1}) is False, "demo without an id marker is never cached"
    assert store.write_parsed(_p1, "v1", {"a": 1}) and store.read_parsed(_p1, "v1") == {"a": 1}, "write_parsed -> read_parsed round trip"
    assert store.read_parsed(_p1, "v2") is None, "another code version misses"
    store.write_parsed(_p1, "v2", {"a": 2})
    assert store.read_parsed(_p1, "v1") is None and store.read_parsed(_p1, "v2") == {"a": 2}, "a new version replaces the old file"
    with open(os.path.join(store.parsed_dir(), f"{_e1['id'][:8]}-v2.pkl"), "wb") as fh:
        fh.write(b"not a pickle")
    assert (
        store.read_parsed(_p1, "v2") is None
        and not os.path.exists(os.path.join(store.parsed_dir(), f"{_e1['id'][:8]}-v2.pkl"))
    ), "corrupt cache file -> None and removed"
    # data/logs/notes.log: the support notes for events without a UI surface
    _logs = os.path.join(store.logs_dir(), "notes.log")
    _log_txt = Path(_logs).read_text(encoding="utf-8") if os.path.isfile(_logs) else ""
    assert "dropped unreadable parsed cache" in _log_txt, "dropping a corrupt cache is noted in data/logs/notes.log"
    store.log_note("test", "hello")
    _lines = Path(_logs).read_text(encoding="utf-8").splitlines()
    assert _lines[-1].endswith("test: hello"), "log_note appends a tagged line"
    for _i in range(store.LOG_MAX_LINES + 50):
        store.log_note("test", f"filler {_i}")
    _lines = Path(_logs).read_text(encoding="utf-8").splitlines()
    assert len(_lines) == store.LOG_MAX_LINES and _lines[-1].endswith("test: filler 249"), "log_note keeps the log trimmed to LOG_MAX_LINES"
    _real_replace = store.caches.os.replace
    def _full_disk(*_a, **_k):
        raise OSError("disk full")
    store.caches.os.replace = _full_disk
    assert store.write_parsed(_p1, "v5", {"a": 1}) is False, "a failed cache write returns False"
    store.caches.os.replace = _real_replace
    _log_txt = Path(_logs).read_text(encoding="utf-8")
    assert "parsed cache write failed: OSError" in _log_txt, "a failed cache write is noted in the log"
    _p2 = store.demo_path(_e2)
    store.write_parsed(_p1, "v3", b"x" * 4000)
    os.utime(os.path.join(store.parsed_dir(), f"{_e1['id'][:8]}-v3.pkl"), (1, 1))  # oldest
    store.write_parsed(_p2, "v3", b"y" * 4000)
    store.prune_parsed(5000)
    assert store.read_parsed(_p1, "v3") is None and store.read_parsed(_p2, "v3") is not None, "pruning drops the least recently used cache first"
    _up = os.path.join(store.unpacked_dir(), f"cs2_aimaudit_match__{_e2['id'][:8]}.dem_9_9.dem")
    with open(_up, "wb") as fh:
        fh.write(b"u" * 500)
    _rep = store.storage_report()
    assert _rep["data"] == store.data_dir() and _rep["demos"] > 0 and _rep["parsed"] > 0 and _rep["unpacked"] >= 500, "storage report: sizes of demos / caches, and the folder"
    _freed = store.delete_demo(_e2["id"])
    assert (
        _freed > 0
        and not os.path.exists(store.demo_path(_e2))
        and not os.path.exists(_up)
        and store.read_parsed(_p2, "v3") is None
        and os.path.isfile(_p1)
        and _e2["id"] not in {e["id"] for e in store.list_demos()}
    ), "delete: file, parsed cache and unpacked copy are gone, demos of others stay"
    store.write_parsed(_p1, "v4", b"z" * 100)
    _fr = store.clear_cache()
    assert _fr > 0 and store.storage_report()["parsed"] == 0 and os.path.isfile(_p1), "clear cache frees the caches and keeps every demo"
    assert store.human_size(512) == "512 B" and store.human_size(1536) == "1.5 KB", "human_size"
    # radars may lie flat next to map_data.json (what tools/fetch_radars.py writes)
    _flat = tempfile.mkdtemp(prefix="cs2aa_flat_")
    _tiny_png(os.path.join(_flat, "de_flat.png"), 16)
    with open(os.path.join(_flat, "map_data.json"), "w", encoding="utf-8") as fh:
        json.dump({"de_flat": {"pos_x": 0, "pos_y": 0, "scale": 1.0}}, fh)
    _old_env3 = os.environ.get("AIMAUDIT_RADAR_DIR")
    os.environ["AIMAUDIT_RADAR_DIR"] = _flat
    try:
        assert core.load_radar("de_flat") is not None, "radar: flat folder (png next to map_data.json) is found"
    finally:
        if _old_env3 is None:
            os.environ.pop("AIMAUDIT_RADAR_DIR", None)
        else:
            os.environ["AIMAUDIT_RADAR_DIR"] = _old_env3
    assert core.radar_roots()[0] == store.radars_dir(), "radar roots: the data folder's radars come right after AIMAUDIT_RADAR_DIR"


def test_parsed_demo_cache():
    _bzp = os.path.join(tempfile.mkdtemp(), "x.dem.bz2")
    with open(_bzp, "wb") as fh:
        fh.write(bz2.compress(b"PBDEMS2\0" + b"1" * 300))
    assert (
        os.path.dirname(core.decompressed_demo(_bzp)) == os.path.realpath(store.unpacked_dir())
        or os.path.dirname(core.decompressed_demo(_bzp)) == store.unpacked_dir()
    ), "unpacked demos go to <data>/cache/unpacked"
    _cached_path = os.path.join(store.unpacked_dir(), "cached__deadbeef.dem")
    FakeParser.demos[_cached_path] = FakeParser.demos["impact.dem"]
    _d1, _hit1 = core.load_demo_cached(_cached_path)
    assert _hit1 is False and len(_d1.players) > 0, "first open parses (not from cache)"
    FakeParser.demos.pop(_cached_path)  # the parser is gone: only the cache can answer
    _d2, _hit2 = core.load_demo_cached(_cached_path)
    assert _hit2 is True and _d2.players.equals(_d1.players) and _d2.deaths.equals(_d1.deaths) and _d2.sides == _d1.sides, "second open comes from the disk cache, identical"
    assert core.loader.PARSE_VERSION.startswith("f1-") and len(core.loader.PARSE_VERSION) > 8, "parse version names parser + code"
    _other = core.loader.PARSE_VERSION
    try:
        # patch the loader module too: load_demo_cached reads PARSE_VERSION
        # from its own module globals
        core.loader.PARSE_VERSION = "f1-other"
        with pytest.raises(Exception):
            core.load_demo_cached(_cached_path)
    finally:
        core.loader.PARSE_VERSION = _other
    assert os.path.dirname(store.state_path()) == store.data_dir(), "state file lives in the data folder"
    core.save_state({"last_demo": "abc"})
    assert core.load_state() == {"last_demo": "abc"}, "state round trip"


def test_fetch_radars():
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
        assert not fetch_radars.present(_d1), "radars: nothing installed yet"
        assert _quiet_main(["--if-missing", "--dest", _d1]) == 0 and fetch_radars.present(_d1), "radars: launcher mode downloads when missing"
        assert sorted(os.listdir(_d1)) == ["de_a.png", "de_a_lower.png", "map_data.json"], "radars: flat files, unsafe archive members ignored"
        _n = len(_calls)
        assert _quiet_main(["--if-missing", "--dest", _d1]) == 0 and len(_calls) == _n, "radars: already installed -> no download at all"
        # the calibration is written last, so an interrupted run never looks complete
        _order = []
        _real_write = fetch_radars._write
        fetch_radars._write = lambda path, data: (_order.append(os.path.basename(path)), _real_write(path, data))[1]
        try:
            fetch_radars.download_radar_set(os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "r"), "t", 5)
        finally:
            fetch_radars._write = _real_write
        assert _order[-1] == "map_data.json" and len(_order) == 3, "radars: map_data.json is written last"
        # opt-out
        os.environ["AIMAUDIT_NO_RADARS"] = "1"
        _d2 = os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "radars")
        _n = len(_calls)
        assert _quiet_main(["--if-missing", "--dest", _d2]) == 0 and len(_calls) == _n and not fetch_radars.present(_d2), "radars: AIMAUDIT_NO_RADARS=1 -> no download"
        del os.environ["AIMAUDIT_NO_RADARS"]

        # no internet: never blocks the launcher, retries only after a day
        def _offline(url, timeout=60):
            _calls.append((url, timeout))
            raise OSError("no route to host")

        fetch_radars._get = _offline
        _d3 = os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "radars")
        _n = len(_calls)
        assert _quiet_main(["--if-missing", "--dest", _d3]) == 0 and len(_calls) > _n and not fetch_radars.present(_d3), "radars: offline in launcher mode -> exit 0"
        assert _calls[-1][1] <= 20 and os.path.isfile(os.path.join(_d3, fetch_radars.FAILED_MARK)), "radars: ... with a short timeout and a failure mark"
        _n = len(_calls)
        assert _quiet_main(["--if-missing", "--dest", _d3]) == 0 and len(_calls) == _n, "radars: no retry within a day"
        _old = time.time() - 25 * 3600
        os.utime(os.path.join(_d3, fetch_radars.FAILED_MARK), (_old, _old))
        assert _quiet_main(["--if-missing", "--dest", _d3]) == 0 and len(_calls) > _n, "radars: retries after a day"
        assert _quiet_main(["--dest", _d3]) == 1, "radars: manual run offline -> exit 1 (told to the user)"
        fetch_radars._get = _fake_get
        assert _quiet_main(["--dest", _d3]) == 0 and fetch_radars.present(_d3) and not os.path.exists(os.path.join(_d3, fetch_radars.FAILED_MARK)), "radars: manual run ignores the failure mark and clears it"
        # an HTML error page instead of map_data.json must not install anything
        fetch_radars._get = lambda url, timeout=60: b"<html>rate limited</html>"
        _d4 = os.path.join(tempfile.mkdtemp(prefix="cs2aa_fr_"), "radars")
        assert _quiet_main(["--if-missing", "--dest", _d4]) == 0 and not fetch_radars.present(_d4), "radars: garbage answer -> nothing installed, launcher not blocked"
    finally:
        fetch_radars._get = _real_get
        os.environ.pop("AIMAUDIT_NO_RADARS", None)
        if _no_radars is not None:
            os.environ["AIMAUDIT_NO_RADARS"] = _no_radars


def test_linked_demos():
    _e1 = store.add_demo(io.BytesIO(b"PBDEMS2\0" + os.urandom(100)), "copy.dem")
    _ldir = tempfile.mkdtemp(prefix="cs2aa_link_")
    _lp = os.path.join(_ldir, "My Linked Match.dem")
    with open(_lp, "wb") as fh:
        fh.write(b"PBDEMS2\0" + os.urandom(3000))
    _files_before = sorted(os.listdir(store.demos_dir()))
    _L1 = store.add_link(_lp)
    assert (
        _L1["mode"] == "link"
        and _L1["status"] == "ok"
        and store.demo_path(_L1) == os.path.abspath(_lp)
        and _L1["name"] == "My Linked Match"
        and sorted(os.listdir(store.demos_dir())) == _files_before
    ), "link: the entry points at the original file, nothing is copied"
    assert (
        store.add_link(_lp)["id"] == _L1["id"]
        and sum(1 for e in store.list_demos() if e["id"] == _L1["id"]) == 1
    ), "link: the same path again is the same entry (no duplicate)"
    assert store.add_link('  "%s"  ' % _lp)["id"] == _L1["id"], "link: a pasted path in quotes with spaces works"
    _txt = os.path.join(_ldir, "notes.txt")
    open(_txt, "w").write("x")
    _empty = os.path.join(_ldir, "empty.dem")
    open(_empty, "wb").close()
    for _label, _bad in (("missing file", os.path.join(_ldir, "nope.dem")), ("not a demo", _txt), ("empty file", _empty)):
        with pytest.raises(ValueError):
            store.add_link(_bad)
    # the file is moved: the entry stays, says missing, and can be pointed at the new place
    _lp2 = os.path.join(_ldir, "renamed.dem")
    os.rename(_lp, _lp2)
    _miss = store.get_demo(_L1["id"])
    assert _miss is not None and _miss["status"] == "missing" and any(e["id"] == _L1["id"] for e in store.list_demos()), "link: a moved file leaves the entry in the list as missing"
    _re = store.relink(_L1["id"], _lp2)
    assert _re["id"] == _L1["id"] and _re["status"] == "ok" and _re["path"] == os.path.abspath(_lp2), "link: relink keeps the entry (and its id) and finds the file again"
    _other = os.path.join(_ldir, "other.dem")
    with open(_other, "wb") as fh:
        fh.write(b"PBDEMS2\0" + os.urandom(500))
    _L2 = store.add_link(_other)
    with pytest.raises(ValueError):
        store.relink(_L1["id"], _other)
    with pytest.raises(ValueError):
        store.relink(_e1["id"], _lp2)
    # parse keys: location + size + mtime for a plain file, so a replaced file never gets a stale cache
    _k1 = store.parse_key(_lp2)
    with open(_lp2, "ab") as fh:
        fh.write(b"more bytes")
    assert store.parse_key(_lp2) != _k1, "parse key follows the file (replaced on disk -> new key)"
    assert store.get_demo(_L1["id"])["size"] == os.path.getsize(_lp2), "list notices the changed file and updates the size"
    assert store.parse_key(store.demo_path(_e1)) == _e1["id"][:8], "parse key of a copy = its content id"
    _unp = os.path.join(store.unpacked_dir(), f"cs2_aimaudit_m__{_e1['id'][:8]}.dem_5_5.dem")
    assert (store.parse_key(_unp) or "").startswith(_e1["id"][:8] + "-"), "parse key of an unpacked archive = id + its own name"
    _fake_marker = os.path.join(_ldir, "x__deadbeef.dem")
    open(_fake_marker, "wb").write(b"PBDEMS2\0" + b"1" * 20)
    assert (store.parse_key(_fake_marker) or "").startswith(store.link_id(os.path.realpath(_fake_marker))[:8]), "a marker-looking name OUTSIDE the library folders is just a plain file"
    # the user's file is never touched by removing a link
    assert store.write_parsed(_lp2, "v1", {"a": 1}) and store.read_parsed(_lp2, "v1") == {"a": 1}, "write_parsed for a linked demo"
    _freed = store.delete_demo(_L1["id"])
    assert os.path.isfile(_lp2) and store.get_demo(_L1["id"]) is None and store.read_parsed(_lp2, "v1") is None and _freed > 0, "remove a linked demo: entry and cache go, the user's file STAYS"
    # a linked ARCHIVE: its unpacked copy carries the id and goes with the entry
    _arc = os.path.join(_ldir, "arch.dem.bz2")
    with open(_arc, "wb") as fh:
        fh.write(bz2.compress(b"PBDEMS2\0" + b"7" * 400))
    _L3 = store.add_link(_arc)
    _unpacked = core.decompressed_demo(store.demo_path(_L3), tag=_L3["id"][:8])
    assert os.path.dirname(_unpacked) == os.path.realpath(store.unpacked_dir()) or os.path.dirname(_unpacked) == store.unpacked_dir(), "linked archive: unpacked into cache/unpacked with the id in its name"
    assert f"__{_L3['id'][:8]}" in os.path.basename(_unpacked), "... and the tag is in the file name"
    store.write_parsed(_unpacked, "v1", {"x": 1})
    store.delete_demo(_L3["id"])
    assert os.path.isfile(_arc) and not os.path.exists(_unpacked) and store.read_parsed(_unpacked, "v1") is None, "removing the linked archive deletes its unpacked copy and parsed cache, not the archive"
    # two more linked demos (a "whole folder at once" flow was removed with
    # store.add_folder: the UI only links individual files now)
    _fdir = tempfile.mkdtemp(prefix="cs2aa_fold_")
    for _n in ("a.dem", "b.dem.bz2"):
        open(os.path.join(_fdir, _n), "wb").write(b"PBDEMS2\0" + b"2" * 30)
    store.add_link(os.path.join(_fdir, "a.dem"))
    store.add_link(os.path.join(_fdir, "b.dem.bz2"))
    _rep2 = store.storage_report()
    assert _rep2["linked_count"] >= 3 and _rep2["linked"] > 0 and _rep2["fallback"] is False, "storage report counts linked demos separately (and the fallback flag)"
    # a file that already lies inside demos/ is never listed twice
    _inlib = os.path.join(store.demos_dir(), "linked in place.dem")
    open(_inlib, "wb").write(b"PBDEMS2\0" + os.urandom(900))
    _Lin = store.add_link(_inlib)
    _after = [e for e in store.list_demos() if e["name"].startswith("linked in place")]
    assert len(_after) == 1 and _after[0]["mode"] == "copy" and os.path.isfile(store.demo_path(_after[0])) and _Lin["id"] == _after[0]["id"], "choosing a file that already lies in demos/ lists it once, as a copy"


def _write_demo_file(fname):
    """A real temp file (unique bytes per name) for library-entry tests."""
    import tempfile as _tf

    d = _tf.mkdtemp(prefix="cs2aa_ms_")
    p = os.path.join(d, fname)
    with open(p, "wb") as fh:
        fh.write(f"matchstats fake demo {fname}".encode())
    return p


def test_nativedialog():
    _real_run = nativedialog.subprocess.run
    def _fake_run(stdout=b"", rc=0, exc=None):
        def run(*a, **k):
            if exc:
                raise exc
            return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=b"")

        return run
    try:
        nativedialog.subprocess.run = _fake_run(b'banner\n["C:\\\\x\\\\a.dem", "D:\\\\b.dem"]\n')
        assert nativedialog.pick_files() == ["C:\\x\\a.dem", "D:\\b.dem"], "dialog: chosen files come back as a list"
        nativedialog.subprocess.run = _fake_run(b"[]\n")
        assert nativedialog.pick_files() == [], "dialog: cancel -> []"
        nativedialog.subprocess.run = _fake_run(b"", rc=1)
        assert nativedialog.pick_files() is None, "dialog: no tkinter / headless (exit code 1) -> None"
        nativedialog.subprocess.run = _fake_run(b"not json at all")
        assert nativedialog.pick_files() is None, "dialog: garbage output -> None"
        nativedialog.subprocess.run = _fake_run(exc=OSError("no python"))
        assert nativedialog.pick_files() is None, "dialog: cannot start the child -> None"
    finally:
        nativedialog.subprocess.run = _real_run


# core/matchlist.py: the merge behind the Matches table (pure data glue -
# provider records, library entries, cached summaries; no parsing here).
_T0 = _datetime(2024, 6, 1, 12, 0, tzinfo=_timezone.utc)


def test_match_list(demo):
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
    assert len(rows) == 3, "matchlist: no duplicates, 3 rows for 2 records + 2 entries"
    m1 = next(r for r in rows if r.key == "steam:m1")
    assert (
          m1.demo_id == "aaaa1111aaaa1111" and m1.entry is not None and m1.rec is not None
          and m1.demo_state == _sm.DemoState.DOWNLOADED and not m1.missing
    ), "matchlist: the downloaded record is one row, bound to its entry"
    own = next(r for r in rows if r.key == "lib:bbbb2222bbbb2222")
    assert (
          own.provider is None and own.rec is None and own.demo_id == "bbbb2222bbbb2222"
          and own.source == "file"
    ), "matchlist: an own demo is its own row with no provider"
    assert [r.key for r in rows] == ["steam:m1", "steam:m2", "lib:bbbb2222bbbb2222"], "matchlist: sorted newest first (record time, else the entry's)"
    # a library entry whose provider record fell out of the cache keeps the
    # provider binding (still one row, still Steam)
    rows = build_rows(
        [_rec("m9", _T1)],
        [
            _lib("cccc3333cccc3333", match={"provider": "steam", "match_id": "m5", "mode": "Premier", "score": [13, 2], "result": "win"}),
            _lib("dddd4444dddd4444"),
        ],
    )
    assert any(r.key == "steam:m5" and r.demo_id == "cccc3333cccc3333" and r.source == "steam" for r in rows), "matchlist: an entry without a cached record is still a Steam row"
    assert any(r.key == "lib:dddd4444dddd4444" for r in rows), "matchlist: the own demo stays separate"
    # states: ready / expired / manual records carry their DemoState
    rows = build_rows([_rec("r1"), _rec("e1", demo=_sm.DemoState.EXPIRED)], [])
    assert (
          rows[0].demo_state == _sm.DemoState.READY and rows[1].demo_state == _sm.DemoState.EXPIRED
          and rows[0].demo_id is None and rows[0].entry is None
    ), "matchlist: ready and expired records keep their state"
    # a linked demo whose file is gone: missing, still in the library
    rows = build_rows(
        [],
        [_lib("eeee5555eeee5555", mode="link", status="missing", path="Z:/gone.dem")],
    )
    assert rows[0].missing and rows[0].demo_id == "eeee5555eeee5555" and rows[0].demo_state == _sm.DemoState.DOWNLOADED, "matchlist: a moved linked demo is a missing row"
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
    assert m1.stats["kills"] == 20 and m1.score == (16, 14) and m1.result == "win", "matchlist: stats fill the row's empty fields (score/result)"
    assert m1.played_at == _T1, "matchlist: the provider's time wins over the cache's"
    assert m1.mode == "Premier" and m1.map_name == "de_mirage", "matchlist: the provider's own fields win over the cache"
    # an own demo takes everything the cache has: map, score, result, time
    rows = build_rows([], [_lib("bbbb2222bbbb2222")], stats_of=lambda e: dict(_stats))
    own = rows[0]
    assert (
          own.map_name == "de_dust2" and own.score == (16, 14) and own.result == "win"
          and own.played_at == _T2
    ), "matchlist: an own demo reads map/score/result/time from the cache"
    # rows without stats (not analyzed yet): None cells, no crash
    rows = build_rows([_rec("m1", _T1)], [_lib("aaaa1111aaaa1111", match={"provider": "steam", "match_id": "m1"})], stats_of=lambda e: None)
    assert rows[0].stats is None and rows[0].score == (13, 5) and rows[0].result == "win", "matchlist: no stats -> empty cells, provider score/result stand"
    # a broken stats cache (an exception) must not kill the list
    rows = build_rows([], [_lib("ffff6666ffff6666")], stats_of=lambda e: (_ for _ in ()).throw(ValueError("bad json")))
    assert rows[0].stats is None, "matchlist: a broken summary cache is skipped"
    # undated rows keep to the end; a stats time can stand in for a missing one
    rows = build_rows([_rec("x", None)], [_lib("1111111111111111", match={"provider": "steam", "match_id": "x"})],
                      stats_of=lambda e: {"played_at": _T2})
    assert rows[0].played_at == _T2, "matchlist: an undated record sorts by the cache time"
    # manual demos: importable only when the file really sits on disk
    _ml_dir = tempfile.mkdtemp(prefix="cs2aa_matchlist_")
    assert manual_demo_ready(_rec("m", demo=_sm.DemoState.MANUAL, demo_ref=os.path.join(_ml_dir, "nowhere.dem"))) is False, "matchlist: a manual record without a file is not importable"
    with open(os.path.join(_ml_dir, "manual_ok.dem"), "wb") as _fh:
        _fh.write(b"x")
    assert manual_demo_ready(_rec("m", demo=_sm.DemoState.MANUAL, demo_ref=os.path.join(_ml_dir, "manual_ok.dem"))) is True, "matchlist: a manual record with the file on disk is importable"


# core/matchstats.py: the per-match summary cache behind the Matches table
# (existing core formulas only: player_summary, impact_stats, round winners).


def test_match_stats():
    _ms_demo = core.load_demo("match.dem")
    # who "me" is: the entry's remembered me_sid first (the explicit per-match
    # pick, also the "this is not me" correction), else the accounts' SteamID64,
    # else nothing (the match view asks once)
    assert (
        _ms.resolve_me(_ms_demo, {}, {"steam_id64": S1}) == S1
        and _ms.resolve_me(_ms_demo, {}, {"steam_id64": "76569999999999999"}) is None
    ), "matchstats: resolve_me falls back to the accounts' SteamID64"
    assert (
        _ms.resolve_me(_ms_demo, {"me_sid": S3}, {"steam_id64": "76569999999999999"}) == S3
        and _ms.resolve_me(_ms_demo, {"me_sid": "76569999999999999"}, {}) is None
    ), "matchstats: resolve_me reads the entry's me_sid"
    assert _ms.resolve_me(_ms_demo, {"me_sid": S3}, {"steam_id64": S1}) == S3, "matchstats: the per-match pick beats the accounts' SteamID64"
    # the summary of the synthetic match: Alpha (S1) killed Charlie (S3) once
    # across the two rounds of make_match() - 1 kill, 0 deaths, kd 1.0
    _ms_sum = _ms.compute(_ms_demo, S1, played_at=_T0)
    assert (
        _ms_sum["version"] == _ms.STATS_VERSION
        and _ms_sum["me_sid"] == S1
        and _ms_sum["map"] == "de_dust2"
        and _ms_sum["rounds"] == 2
        and _ms_sum["kills"] == 1
        and _ms_sum["deaths"] == 0
        and _ms_sum["kd"] == 1.0
        and _ms_sum["played_at"] == _T0.isoformat()
    ), "matchstats: the summary carries version, me, the numbers and the date"
    assert all(
            v is None or isinstance(v, (int, float))
            for k, v in _ms_sum.items()
            if k in ("kd", "adr", "hs_pct", "accuracy", "open_win_pct", "rounds", "kills", "deaths")
        ), "matchstats: every number cell is a number or None, never NaN"
    assert _ms_sum["score"] is None and _ms_sum["result"] is None, "matchstats: no round winners derivable from the synthetic 1-kill fake -> no score"
    # the score and the result when the rounds decide them: the sides demo
    # (round 1 Alpha is T, round 2 he is CT after the swap) with the winners
    # stubbed to T then CT -> 2:0, a win from Alpha's side
    _ms_real_winners = _ms.round_winners
    _ms.round_winners = lambda demo: {1: "T", 2: "CT"}
    try:
        _ms_win = _ms.compute(core.load_demo("sides.dem"), S1, played_at=_T0)
    finally:
        _ms.round_winners = _ms_real_winners
    assert _ms_win["score"] == [2, 0] and _ms_win["result"] == "win", "matchstats: round winners give the score and the result from my side"
    # the cache: write / read / parse-back, the code version in the file name
    _ms_file = _write_demo_file("stats_test.dem")
    _ms_key = store.parse_key(_ms_file)
    _ms.write_cached(_ms_file, dict(_ms_sum, score=[16, 14], result="win"))
    assert os.path.isfile(os.path.join(store.matchstats_dir(), f"{_ms_key}-{core.loader.PARSE_VERSION}.json")), "matchstats: write_cached lands <parse_key>-<version>.json"
    _back = _ms.read_cached(_ms_file)
    assert _back is not None and _back["me_sid"] == S1 and _back["score"] == [16, 14], "matchstats: read_cached returns the whole summary"
    _cs = _ms.cached_stats(
        {"id": "x", "path": _ms_file, "name": "s", "size": 1, "mode": "link"}
    )
    assert _cs is not None and _cs["played_at"] == _T0 and _cs["score"] == [16, 14], "matchstats: cached_stats parses the date back, keeps the score"
    store.write_matchstats(_ms_file, "oldcodever", {"version": 0})
    assert os.path.isfile(os.path.join(store.matchstats_dir(), f"{_ms_key}-oldcodever.json")), "matchstats: two code versions of one key coexist until the next write"
    _ms.write_cached(_ms_file, dict(_ms_sum))
    assert not any("oldcodever" in f for f in os.listdir(store.matchstats_dir())), "matchstats: a write drops the older code versions of the same key"
    # reset with the demo: delete_demo clears the summary cache too
    _ms_entry = store.add_demo(_write_demo_file("ms_stats.dem"), "ms_stats.dem")
    _ms.write_cached(store.demo_path(_ms_entry), dict(_ms_sum, me_sid="zz"))
    assert _ms.read_cached(store.demo_path(_ms_entry)) is not None, "matchstats: the cache file exists for the demo"
    store.delete_demo(_ms_entry["id"])
    assert _ms.read_cached(store.demo_path(_ms_entry)) is None, "matchstats: delete_demo resets the summary cache"
    # and so does clear_cache
    _ms_entry2 = store.add_demo(_write_demo_file("ms_stats2.dem"), "ms_stats2.dem")
    _ms.write_cached(store.demo_path(_ms_entry2), dict(_ms_sum, me_sid="zz"))
    store.clear_cache()
    assert (
        _ms.read_cached(store.demo_path(_ms_entry2)) is None
        and os.listdir(store.matchstats_dir()) == []
    ), "matchstats: clear_cache resets the summary cache"
