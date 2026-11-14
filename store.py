"""
Where CS2-AimAudit keeps its own files: ONE folder next to the launcher.

    <app folder>/data/            (next to start.bat)
        demos/         the demo library: every demo you added, as a plain file
        cache/
            unpacked/  .dem files unpacked from .bz2 / .gz / .zst / .zip
            parsed/    the parsed demo, so re-opening a demo is instant
        radars/        radar images + map_data.json (tools/fetch_radars.py)
        library.json   the index of the demos (name, map, size, dates)
        state.json     the last selected demo and player

Everything except demos/ is rebuilt on demand, so the cache can be deleted at
any time (also from the app: Library and storage -> Clear cache). Demos can
be copied into demos/ by hand: they join the library on the next page load.

The folder can be moved with the AIMAUDIT_DATA_DIR environment variable. When
the app folder is not writable (installed system-wide) the data goes to
~/.cs2-aimaudit instead. Nothing here touches the network.

This module is plain Python (no Streamlit, no demo parsing) so it can be
tested on its own; the core/ package and app.py build on it.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import pickle
import re
import tempfile
import uuid
from datetime import datetime, timezone

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DIR = os.path.join(os.path.expanduser("~"), ".cs2-aimaudit")

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
        else [os.path.join(APP_DIR, "data"), LEGACY_DIR]
    )
    for c in cands:
        if _writable(c):
            return c
    return cands[0]  # unwritable: callers swallow the OSError, nothing is remembered


def data_dir() -> str:
    """The one folder with everything the app stores (see the module doc)."""
    return _resolve(os.environ.get("AIMAUDIT_DATA_DIR") or None)


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
    return [e for e in items if isinstance(e, dict) and e.get("id") and e.get("file")]


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
        if e["file"] in files and e["id"] not in ids:
            kept.append(e)
            ids.add(e["id"])
    changed = len(kept) != len(entries)
    known = {e["file"] for e in kept}
    for name, f in sorted(files.items()):
        if name in known:
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


def list_demos() -> list[dict]:
    """Saved demos, most recently opened first."""
    es = _sync()
    es.sort(key=lambda e: e.get("opened") or e.get("added") or "", reverse=True)
    return es


def demo_path(entry: dict) -> str:
    return os.path.join(demos_dir(), entry["file"])


def get_demo(demo_id: str) -> dict | None:
    return next((e for e in _read_library() if e["id"] == demo_id), None)


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
    """Remove a demo, its parsed and unpacked caches; returns the bytes freed."""
    freed = 0
    entries = _read_library()
    hit = next((e for e in entries if e["id"] == demo_id), None)
    if hit is None:
        return 0
    freed += _remove(demo_path(hit))
    key = demo_key(hit["file"]) or demo_id[:8]
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


# ---------------------------------------------------------- parsed cache
def _parsed_file(path: str, code_ver: str) -> str | None:
    key = demo_key(path)
    if not key:
        return None  # not a library demo: nothing to key the cache on
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
    key = demo_key(path)
    try:
        for other in os.listdir(parsed_dir()):
            full = os.path.join(parsed_dir(), other)
            if other.startswith(f"{key}-") and full != f:
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
    return {
        "data": data_dir(),
        "demos_dir": demos_dir(),
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
