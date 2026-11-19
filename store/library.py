"""
The demo library (library.json): copies in data/demos AND demos linked
by path (the file stays where it is - removing a linked demo never
touches it). Owns adding (link / copy / upload), the disk sync that
adopts files dropped into demos/ by hand, entry metadata and deletion
(including the caches that belong to a demo).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

from store.paths import (
    DEMO_EXTS,
    _now,
    _safe,
    _unlink_freed,
    demo_key,
    demos_dir,
    library_path,
    matchstats_dir,
    parsed_dir,
    split_demo_name,
    unpacked_dir,
)


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


def _scan_demo_files(d: str) -> dict | None:
    """The demo files on disk (None when the folder cannot be read)."""
    try:
        return {
            f.name: f
            for f in os.scandir(d)
            if f.is_file()
            and not f.name.startswith(".")
            and f.name.lower().endswith(DEMO_EXTS)
        }
    except OSError:
        return None


def _keep_entries(entries: list[dict], files: dict) -> tuple[list[dict], set[str]]:
    """Entries that still exist: a copy whose file vanished leaves the
    list; a LINKED demo stays (it is shown as missing so the user can
    locate it again)."""
    kept: list[dict] = []
    ids: set[str] = set()
    for e in entries:
        if e["id"] in ids:
            continue
        if e.get("path") or e.get("file") in files:
            kept.append(e)
            ids.add(e["id"])
    return kept, ids


def _adopt_files(d: str, files: dict, kept: list[dict], ids: set[str]) -> None:
    """Files copied into demos/ by hand are adopted (named with an id
    marker so the caches can find them); duplicates of a known demo are
    ignored."""
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


def _sync() -> list[dict]:
    """
    The library as it is on disk: entries whose file vanished are dropped,
    files copied into demos/ by hand are adopted (named with an id marker so
    the caches can find them), duplicates of a known demo are ignored.
    """
    entries = _read_library()
    d = demos_dir()
    files = _scan_demo_files(d)
    if files is None:
        return entries
    kept, ids = _keep_entries(entries, files)
    changed = len(kept) != len(entries)
    before = len(kept)
    _adopt_files(d, files, kept, ids)
    if len(kept) != before:
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
        _unlink_freed(part)
        raise
    finally:
        if close:
            fh.close()
    if size == 0:
        _unlink_freed(part)
        raise ValueError("The file is empty.")
    digest = h.hexdigest()
    entries = _sync()
    for e in entries:
        if e["id"] == digest[:16]:
            _unlink_freed(part)
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
        freed += _unlink_freed(demo_path(hit))
        keys = {demo_key(hit["file"]) or demo_id[:8]}
    for key in keys:
        for folder, pat in (
            (parsed_dir(), re.compile(rf"^{key}-.*\.pkl(\.part)?$")),
            (unpacked_dir(), re.compile(rf".*__{key}[._].*")),
            (matchstats_dir(), re.compile(rf"^{key}-.*\.json(\.part)?$")),
        ):
            try:
                for f in os.listdir(folder):
                    if pat.match(f):
                        freed += _unlink_freed(os.path.join(folder, f))
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
