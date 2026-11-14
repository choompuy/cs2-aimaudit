"""
Where CS2-AimAudit keeps its own files: ONE folder next to the launcher.

    <app folder>/data/            (next to start.bat)
        demos/         demos you UPLOADED (a browser hides the real path, so
                       an upload has to be copied); plain files, drop-in works
        cache/
            unpacked/  .dem files unpacked from .bz2 / .gz / .zst / .zip
            parsed/    the parsed demo, so re-opening a demo is instant
            pip/       pip's download cache      (start.bat points pip here)
            pycache/   compiled .pyc files       (start.bat points Python here)
            tmp/       the temp folder           (start.bat points TEMP here)
        home/          Streamlit's own ~/.streamlit (start.bat points USERPROFILE here)
        radars/        radar images + map_data.json (start.bat fetches them)
        tools/         the Steam helper (boiler-writter, from the Matches tab)
        library.json   the library: copies AND linked demos (name, path, map ...)
        state.json     the last selected demo and player
        accounts.json  match-import settings (folders, SteamID64 - no secrets)
        matches.json   the cached match lists of the Matches tab

Demos added with "Choose demo file" are LINKED, not copied: the library keeps
their path, the file stays where it is (and when it is moved or deleted the
app says so instead of failing). Removing a linked demo from the library never
touches the user's file.

start.bat redirects TEMP, USERPROFILE, pip's cache and Python's .pyc folder
into this folder, so nothing of CS2-AimAudit lands in %TEMP% or the user
profile: deleting the folder is a complete uninstall. Everything except
demos/ (and the files you linked) is rebuilt on demand, so any part of the
cache can be deleted at any time (also in the app: Library and storage).

The folder can be moved with the AIMAUDIT_DATA_DIR environment variable. When
the app folder is read-only (installed under Program Files) the data goes to
%LOCALAPPDATA%\\CS2-AimAudit instead, and the app says so. Nothing here
touches the network.

This module is plain Python (no Streamlit, no demo parsing) so it can be
tested on its own; core/ and the app build on it.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import pickle
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone

def _app_dir() -> str:
    """
    The folder of the launcher. For a frozen exe (PyInstaller, Nuitka) that is
    the folder of the .exe itself: __file__ would point into the temporary
    folder the bundle unpacks to, and the data would vanish with it.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()
# Last resort when the app folder is read-only: a visible per-user folder (not
# a hidden dot-folder), so it can still be found and deleted by hand.
FALLBACK_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
    "CS2-AimAudit",
)

# longest first, so "match.dem.bz2" splits into ("match", ".dem.bz2")
_SUFFIXES = (
    ".dem.bz2",
    ".dem.gz",
    ".dem.zst",
    ".dem.zip",
    ".dem",
    ".bz2",
    ".gz",
    ".zst",
    ".zip",
)
DEMO_EXTS = (".dem", ".bz2", ".gz", ".zst", ".zip")
# the id marker in a library file name: match__ab12cd34.dem(.bz2)
_ID_RE = re.compile(r"__([0-9a-f]{8})(?=[._]|$)")

PARSED_MAX_BYTES = int(
    float(os.environ.get("AIMAUDIT_PARSED_MAX_MB", "3072")) * 1024 * 1024
)


# ------------------------------------------------------------- locations
def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
        return True
    except OSError:
        return False


@functools.lru_cache(maxsize=16)
def _resolve(env: str | None) -> str:
    cands = (
        [os.path.abspath(os.path.expanduser(env))]
        if env
        else [os.path.join(APP_DIR, "data"), FALLBACK_DIR]
    )
    for c in cands:
        if _writable(c):
            return c
    return cands[0]  # unwritable: callers swallow the OSError, nothing is remembered


def data_dir() -> str:
    """The one folder with everything the app stores (see the module doc)."""
    return _resolve(os.environ.get("AIMAUDIT_DATA_DIR") or None)


def data_dir_is_fallback() -> bool:
    """True when the data is NOT next to the launcher because that folder is read-only."""
    return not os.environ.get("AIMAUDIT_DATA_DIR") and data_dir() != os.path.join(APP_DIR, "data")


def _sub(*parts: str) -> str:
    p = os.path.join(data_dir(), *parts)
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        pass
    return p


def demos_dir() -> str:
    return _sub("demos")


def unpacked_dir() -> str:
    return _sub("cache", "unpacked")


def parsed_dir() -> str:
    return _sub("cache", "parsed")


def radars_dir() -> str:
    return _sub("radars")


def library_path() -> str:
    return os.path.join(data_dir(), "library.json")


def state_path() -> str:
    return os.path.join(data_dir(), "state.json")


def accounts_path() -> str:
    return os.path.join(data_dir(), "accounts.json")


def matches_cache_path() -> str:
    return os.path.join(data_dir(), "matches.json")


def tools_dir() -> str:
    return _sub("tools")


def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    )  # sortable, no ties


# ------------------------------------------------------------- file names
def split_demo_name(name: str) -> tuple[str, str]:
    """'a/b/match.dem.bz2' -> ('match', '.dem.bz2'); unknown extensions are kept."""
    base = os.path.basename(str(name).replace("\\", "/"))
    low = base.lower()
    for suf in _SUFFIXES:
        if low.endswith(suf) and len(base) > len(suf):
            return base[: -len(suf)], base[-len(suf) :].lower()
    stem, ext = os.path.splitext(base)
    return stem or "demo", ext.lower()


def _safe(stem: str) -> str:
    s = _ID_RE.sub("", stem)
    s = re.sub(r"[^\w.\- ]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_{2,}", "_", s).strip(" ._")
    return s[:80] or "demo"


def demo_key(path: str) -> str | None:
    """The 8-hex id marker of a library file (or of its unpacked copy), else None."""
    m = _ID_RE.search(os.path.basename(str(path)))
    return m.group(1) if m else None


# --------------------------------------------------------------- library
def _read_library() -> list[dict]:
    try:
        with open(library_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return []
    items = d.get("demos") if isinstance(d, dict) else None
    if not isinstance(items, list):
        return []
    return [
        e
        for e in items
        if isinstance(e, dict) and e.get("id") and (e.get("file") or e.get("path"))
    ]


def _write_library(entries: list[dict]) -> None:
    try:
        tmp = library_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {"version": 1, "demos": entries}, fh, ensure_ascii=False, indent=1
            )
        os.replace(tmp, library_path())
    except OSError:
        pass


def _hash_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _entry(file: str, digest: str, size: int, orig: str, stem: str) -> dict:
    now = _now()
    return {
        "id": digest[:16],
        "file": file,
        "name": stem,
        "orig": orig,
        "size": int(size),
        "added": now,
        "opened": now,
        "map": None,
        "tickrate": None,
        "players": [],
    }


def _sync() -> list[dict]:
    """
    The library as it is on disk: entries whose file vanished are dropped,
    files copied into demos/ by hand are adopted (named with an id marker so
    the caches can find them), duplicates of a known demo are ignored.
    """
    entries = _read_library()
    d = demos_dir()
    try:
        files = {
            f.name: f
            for f in os.scandir(d)
            if f.is_file()
            and not f.name.startswith(".")
            and f.name.lower().endswith(DEMO_EXTS)
        }
    except OSError:
        return entries
    kept: list[dict] = []
    ids: set[str] = set()
    for e in entries:
        if e["id"] in ids:
            continue
        # a copy whose file vanished leaves the list; a LINKED demo stays (it is
        # shown as missing so the user can locate it again)
        if e.get("path") or e.get("file") in files:
            kept.append(e)
            ids.add(e["id"])
    changed = len(kept) != len(entries)
    known = {e["file"] for e in kept if e.get("file")}
    linked = {os.path.normcase(e["path"]) for e in kept if e.get("path")}
    for name, f in sorted(files.items()):
        if name in known or os.path.normcase(os.path.abspath(f.path)) in linked:
            continue
        try:
            digest = _hash_file(f.path)
            size = f.stat().st_size  # before a rename invalidates the entry
        except OSError:
            continue
        if digest[:16] in ids:
            continue  # the same demo again: nothing new to list
        stem, ext = split_demo_name(name)
        target = name
        if demo_key(name) != digest[:8]:
            target = f"{_safe(stem)}__{digest[:8]}{ext}"
            try:
                os.replace(f.path, os.path.join(d, target))
            except OSError:
                target = name
        kept.append(_entry(target, digest, size, name, _safe(stem)))
        ids.add(digest[:16])
        changed = True
    if changed:
        _write_library(kept)
    return kept


def _decorate(e: dict) -> dict:
    """Add the computed `mode` (copy | link) and `status` (ok | missing) of an entry."""
    e["mode"] = "link" if e.get("path") else "copy"
    e["status"] = "ok"
    if e.get("path"):
        try:
            st = os.stat(e["path"])
        except OSError:
            e["status"] = "missing"
        else:
            if (st.st_size, st.st_mtime_ns) != (e.get("size"), e.get("mtime_ns")):
                # the file changed on disk: remember it (the cache key follows)
                _update(e["id"], size=st.st_size, mtime_ns=st.st_mtime_ns)
                e["size"], e["mtime_ns"] = st.st_size, st.st_mtime_ns
    return e


def list_demos() -> list[dict]:
    """Saved demos (copies and links), most recently opened first."""
    es = [_decorate(e) for e in _sync()]
    es.sort(key=lambda e: e.get("opened") or e.get("added") or "", reverse=True)
    return es


def demo_path(entry: dict) -> str:
    """Where the demo file is: the linked path, or the copy in demos/."""
    if entry.get("path"):
        return entry["path"]
    return os.path.join(demos_dir(), entry["file"])


def get_demo(demo_id: str) -> dict | None:
    e = next((e for e in _read_library() if e["id"] == demo_id), None)
    return _decorate(e) if e else None


# ----------------------------------------------------------------- links
def link_id(path: str) -> str:
    """Library id of a linked demo: a hash of its location (nothing is read)."""
    key = "link:" + os.path.normcase(os.path.abspath(path))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _check_demo_file(p: str) -> os.stat_result:
    if not os.path.isfile(p):
        raise ValueError(f"File not found: {p}")
    if not p.lower().endswith(DEMO_EXTS):
        raise ValueError(f"Not a demo file (.dem, .bz2, .gz, .zst, .zip): {os.path.basename(p)}")
    st = os.stat(p)
    if st.st_size == 0:
        raise ValueError("The file is empty.")
    return st


def _clean_path(path) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(path).strip().strip('"')))


def add_link(path) -> dict:
    """
    Add a demo to the library BY ITS PATH: nothing is copied or read, the
    file stays where it is. Adding the same path again returns the same entry.
    """
    p = _clean_path(path)
    st = _check_demo_file(p)
    if os.path.normcase(os.path.realpath(os.path.dirname(p))) == os.path.normcase(
        os.path.realpath(demos_dir())
    ):
        # already inside the library folder: it belongs to the library as a copy
        # (the sync adopts it, and may rename it to carry its id)
        base = os.path.basename(p)
        for e in _sync():
            if not e.get("path") and base in (e.get("file"), e.get("orig")):
                touch(e["id"])
                return get_demo(e["id"]) or e
        raise ValueError(f"File not found: {p}")
    did = link_id(p)
    for e in _sync():
        if e["id"] == did:
            _update(did, size=st.st_size, mtime_ns=st.st_mtime_ns)
            touch(did)
            return get_demo(did) or e
    stem, _ext = split_demo_name(p)
    entry = _entry(None, did, st.st_size, os.path.basename(p), _safe(stem))
    entry["path"] = p
    entry["mtime_ns"] = st.st_mtime_ns
    entries = _read_library()
    entries.append(entry)
    _write_library(entries)
    return _decorate(entry)


def add_folder(folder) -> list[dict]:
    """Link every demo file directly inside `folder` (not recursive); the entries."""
    d = _clean_path(folder)
    if not os.path.isdir(d):
        raise ValueError(f"Folder not found: {d}")
    out: list[dict] = []
    for f in sorted(os.scandir(d), key=lambda x: x.name.lower()):
        if f.is_file() and not f.name.startswith(".") and f.name.lower().endswith(DEMO_EXTS):
            try:
                out.append(add_link(f.path))
            except (ValueError, OSError):
                continue
    return out


def relink(demo_id: str, path) -> dict:
    """Point a linked demo at a new location (it was moved or renamed)."""
    p = _clean_path(path)
    st = _check_demo_file(p)
    entries = _read_library()
    e = next((x for x in entries if x["id"] == demo_id), None)
    if e is None or not e.get("path"):
        raise ValueError("Only a linked demo can be pointed at another file.")
    new_id = link_id(p)
    if new_id != demo_id and any(x["id"] == new_id for x in entries):
        raise ValueError("That file is already in the library.")
    e.update(path=p, size=st.st_size, mtime_ns=st.st_mtime_ns, orig=os.path.basename(p))
    _write_library(entries)
    return _decorate(e)


def add_demo(source, name: str | None = None) -> dict:
    """
    Save a demo into the library and return its entry.

    `source` is a path or a file-like object (the Streamlit upload); it is
    streamed to demos/ while the content hash is computed, so a demo that is
    already in the library (same bytes, any file name) is not stored twice -
    its entry is returned and marked as just opened.
    """
    close = False
    if isinstance(source, (str, os.PathLike)):
        name = name or os.path.basename(os.fspath(source))
        fh = open(source, "rb")
        close = True
    else:
        fh = source
        name = name or getattr(source, "name", None) or "demo.dem"
    part = os.path.join(demos_dir(), f".incoming-{uuid.uuid4().hex[:8]}.part")
    h = hashlib.sha1()
    size = 0
    try:
        with open(part, "wb") as out:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                size += len(chunk)
    except BaseException:
        _remove(part)
        raise
    finally:
        if close:
            fh.close()
    if size == 0:
        _remove(part)
        raise ValueError("The file is empty.")
    digest = h.hexdigest()
    entries = _sync()
    for e in entries:
        if e["id"] == digest[:16]:
            _remove(part)
            touch(e["id"])
            return get_demo(e["id"]) or e
    stem, ext = split_demo_name(name)
    final = f"{_safe(stem)}__{digest[:8]}{ext}"
    os.replace(part, os.path.join(demos_dir(), final))
    entry = _entry(final, digest, size, os.path.basename(str(name)), _safe(stem))
    entries.append(entry)
    _write_library(entries)
    return entry


def _update(demo_id: str, **fields) -> None:
    entries = _read_library()
    hit = False
    for e in entries:
        if e["id"] == demo_id and any(e.get(k) != v for k, v in fields.items()):
            e.update(fields)
            hit = True
    if hit:
        _write_library(entries)


def touch(demo_id: str) -> None:
    """Mark a demo as just opened (the library lists the latest first)."""
    _update(demo_id, opened=_now())


def update_meta(demo_id: str, **fields) -> None:
    """Remember what a loaded demo turned out to be (map, tick rate, players)."""
    _update(demo_id, **fields)


def _remove(path: str) -> int:
    try:
        n = os.path.getsize(path)
        os.remove(path)
        return n
    except OSError:
        return 0


def delete_demo(demo_id: str) -> int:
    """
    Remove a demo from the library together with its parsed / unpacked caches;
    returns the bytes freed. A COPY (an upload in demos/) is deleted from
    disk. A LINKED demo only loses its list entry and caches: the user's own
    file is never touched.
    """
    freed = 0
    entries = _read_library()
    hit = next((e for e in entries if e["id"] == demo_id), None)
    if hit is None:
        return 0
    if hit.get("path"):
        keys = {
            demo_id[:8],
            link_id(hit["path"])[:8],
            link_id(os.path.realpath(hit["path"]))[:8],
        }
    else:
        freed += _remove(demo_path(hit))
        keys = {demo_key(hit["file"]) or demo_id[:8]}
    for key in keys:
        for folder, pat in (
            (parsed_dir(), re.compile(rf"^{key}-.*\.pkl(\.part)?$")),
            (unpacked_dir(), re.compile(rf".*__{key}[._].*")),
        ):
            try:
                for f in os.listdir(folder):
                    if pat.match(f):
                        freed += _remove(os.path.join(folder, f))
            except OSError:
                pass
    _write_library([e for e in entries if e["id"] != demo_id])
    return freed


def delete_all_demos() -> int:
    """Remove every demo of the library (files + caches); returns the bytes freed."""
    freed = 0
    for e in _read_library():
        freed += delete_demo(e["id"])
    return freed


# ---------------------------------------------------------- parsed cache
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
    except Exception:  # noqa: BLE001 - truncated / from another build: reparse
        _remove(f)
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
    except Exception:  # noqa: BLE001 - a full disk must never break the analysis
        _remove(tmp)
        return False
    id8 = os.path.basename(f).split("-")[0]
    try:
        for other in os.listdir(parsed_dir()):
            full = os.path.join(parsed_dir(), other)
            if other.startswith(f"{id8}-") and full != f:
                _remove(full)
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
            freed += _remove(p)
    return freed


# --------------------------------------------------------- storage panel
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
    }


def clear_cache() -> int:
    """Delete the parsed and unpacked caches (demos stay); returns the bytes freed."""
    freed = 0
    for folder in (parsed_dir(), unpacked_dir()):
        try:
            for n in os.listdir(folder):
                freed += _remove(os.path.join(folder, n))
        except OSError:
            pass
    return freed


# ------------------------------------------------------- match import data
# accounts.json (no secrets - the Steam import needs none) and matches.json
# (the cached match lists) follow the same rules as library.json: atomic
# writes, a corrupt file counts as empty and is recreated.
def _read_json_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_json_file(path: str, d: dict) -> bool:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def read_accounts() -> dict:
    return _read_json_file(accounts_path())


def write_accounts(d: dict) -> bool:
    return _write_json_file(accounts_path(), d)


def read_matches_cache() -> dict:
    return _read_json_file(matches_cache_path())


def write_matches_cache(d: dict) -> bool:
    return _write_json_file(matches_cache_path(), d)


def find_by_match(provider: str, match_id: str) -> dict | None:
    """The library entry a downloaded match is bound to, if any."""
    for e in _read_library():
        m = e.get("match") or {}
        if m.get("provider") == provider and m.get("match_id") == match_id:
            return e
    return None


def mark_analyzed(demo_id: str) -> None:
    """Note when a demo was last parsed (for future cross-match aggregates)."""
    _update(demo_id, analyzed_at=_now())
