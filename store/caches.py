"""
The disk caches of the data folder: the parsed demos (pickle, pruned by
an LRU byte budget), the per-match summary JSONs, and the storage
report / clear actions of the Library-and-storage settings panel. Cache
files are keyed by store.paths.parse_key (<demo id> or location + size
+ mtime), so a replaced file never reads a stale cache.
"""

from __future__ import annotations

import hashlib
import os
import pickle

from store.jsonio import read_json, write_json
from store.library import _unlink_freed, link_id, list_demos
from store.paths import (
    PARSED_MAX_BYTES,
    data_dir,
    data_dir_is_fallback,
    demo_key,
    demos_dir,
    icons_dir,
    log_note,
    matchstats_dir,
    parsed_dir,
    radars_dir,
    unpacked_dir,
)

__all__ = [
    "clear_cache", "clear_icons", "drop_matchstats", "parse_key", "prune_parsed",
    "read_matchstats", "read_parsed", "storage_report", "write_matchstats",
    "write_parsed",
]


def parse_key(path: str) -> str | None:
    """
    Disk-cache key of a demo file the app loads: <id8> or <id8>-<suffix>.
    A copy in demos/ keys on its content id, an unpacked archive on its id plus
    its own name (which holds the archive's size and mtime), any other file
    (a linked plain .dem) on its location plus size and mtime - so a file that
    was replaced on disk never gets a stale cache.
    """
    p = os.path.abspath(str(path))
    base = os.path.basename(p)
    folder = os.path.normcase(os.path.realpath(os.path.dirname(p)))
    marker = demo_key(base)
    if marker and folder == os.path.normcase(os.path.realpath(demos_dir())):
        return marker
    if marker and folder == os.path.normcase(os.path.realpath(unpacked_dir())):
        return f"{marker}-{hashlib.sha1(base.encode('utf-8')).hexdigest()[:6]}"
    try:
        st = os.stat(p)
    except OSError:
        return None
    return f"{link_id(os.path.realpath(p))[:8]}-{st.st_size:x}{st.st_mtime_ns:x}"


def _parsed_file(path: str, code_ver: str) -> str | None:
    key = parse_key(path)
    if not key:
        return None
    return os.path.join(parsed_dir(), f"{key}-{code_ver}.pkl")


def read_parsed(path: str, code_ver: str):
    """The cached parse of a library demo, or None (missing, stale, unreadable)."""
    f = _parsed_file(path, code_ver)
    if not f or not os.path.isfile(f):
        return None
    try:
        with open(f, "rb") as fh:
            obj = pickle.load(fh)  # our own file in our own data folder
    except Exception as e:  # noqa: BLE001 - truncated / from another build: reparse
        # pickle.load may raise anything (Python docs: arbitrary exceptions
        # from __setstate__) - the note records which cache was dropped and why
        log_note("cache", f"dropped unreadable parsed cache: {type(e).__name__}")
        _unlink_freed(f)
        return None
    try:
        os.utime(f)  # most recently used survives the pruning
    except OSError:
        pass
    return obj


def write_parsed(path: str, code_ver: str, obj, max_bytes: int | None = None) -> bool:
    """Cache the parse of a library demo (atomic); older versions are dropped."""
    f = _parsed_file(path, code_ver)
    if not f:
        return False
    tmp = f + ".part"
    try:
        with open(tmp, "wb") as fh:
            pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, f)
    except (OSError, TypeError, pickle.PicklingError) as e:
        # OSError = a full disk (the analysis itself keeps working); the
        # pickling pair = obj suddenly unpicklable (a code change)
        log_note("cache", f"parsed cache write failed: {type(e).__name__}")
        _unlink_freed(tmp)
        return False
    id8 = os.path.basename(f).split("-")[0]
    try:
        for other in os.listdir(parsed_dir()):
            full = os.path.join(parsed_dir(), other)
            if other.startswith(f"{id8}-") and full != f:
                _unlink_freed(full)
    except OSError:
        pass
    prune_parsed(PARSED_MAX_BYTES if max_bytes is None else max_bytes)
    return True


def prune_parsed(max_bytes: int) -> int:
    """Delete the least recently used parsed caches above `max_bytes`; bytes freed."""
    folder = parsed_dir()
    try:
        files = [
            (os.path.getmtime(p), os.path.getsize(p), p)
            for p in (os.path.join(folder, n) for n in os.listdir(folder))
            if p.endswith(".pkl")
        ]
    except OSError:
        return 0
    files.sort(reverse=True)  # newest first
    total, freed = 0, 0
    for _mt, size, p in files:
        total += size
        if total > max_bytes:
            freed += _unlink_freed(p)
    return freed


# The per-match summaries of the Matches table (core/matchstats.py builds
# the content): one small JSON per demo next to the parsed cache, keyed the
# same way (<parse_key>-<code version>.json) so a deleted demo or a code
# change takes the cache with it.
def _matchstats_file(path: str, code_ver: str) -> str | None:
    key = parse_key(path)
    if not key:
        return None
    return os.path.join(matchstats_dir(), f"{key}-{code_ver}.json")


def read_matchstats(path: str, code_ver: str) -> dict | None:
    """The cached match summary (JSON), or None (missing, stale, broken)."""
    f = _matchstats_file(path, code_ver)
    if not f or not os.path.isfile(f):
        return None
    d = read_json(f)
    if not d:
        log_note("cache", "dropped unreadable match stats")
        _unlink_freed(f)
    return d or None


def write_matchstats(path: str, code_ver: str, d: dict) -> bool:
    """Cache the match summary (atomic); older code versions are dropped."""
    f = _matchstats_file(path, code_ver)
    if not f or not write_json(f, d):
        return False
    key = os.path.basename(f).split("-")[0]
    try:
        for other in os.listdir(matchstats_dir()):
            full = os.path.join(matchstats_dir(), other)
            if other.startswith(f"{key}-") and full != f:
                _unlink_freed(full)
    except OSError:
        pass
    return True


def drop_matchstats(path: str, code_ver: str) -> bool:
    """Delete the cached match summary of one demo (the "me" changed: the
    next open recomputes it for the new player); True when a file went."""
    f = _matchstats_file(path, code_ver)
    if not f:
        return False
    return _unlink_freed(f) > 0


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def storage_report() -> dict:
    """Where things are and how big: for the Library and storage panel."""
    linked = [e for e in list_demos() if e["mode"] == "link" and e["status"] == "ok"]
    return {
        "data": data_dir(),
        "fallback": data_dir_is_fallback(),
        "demos_dir": demos_dir(),
        "linked_count": len(linked),
        "linked": sum(int(e.get("size") or 0) for e in linked),
        "demos": dir_size(demos_dir()),
        "parsed": dir_size(parsed_dir()),
        "unpacked": dir_size(unpacked_dir()),
        "radars": dir_size(radars_dir()),
        "icons": dir_size(icons_dir()),
    }


def clear_cache() -> int:
    """Delete the parsed, unpacked and match-summary caches (demos stay);
    returns the bytes freed."""
    freed = 0
    for folder in (parsed_dir(), unpacked_dir(), matchstats_dir()):
        try:
            for n in os.listdir(folder):
                freed += _unlink_freed(os.path.join(folder, n))
        except OSError:
            pass
    return freed


def clear_icons() -> int:
    """Delete the downloaded weapon / status icons (re-downloadable art,
    not cache); returns the bytes freed."""
    freed = 0
    try:
        for n in os.listdir(icons_dir()):
            freed += _unlink_freed(os.path.join(icons_dir(), n))
    except OSError:
        pass
    return freed
