"""
Where CS2-AimAudit keeps its own files: ONE folder next to the launcher.

    <app folder>/data/            (next to start.bat)
        demos/         demos the app downloaded itself; plain files, drop-in
                       works too (an old browser upload was copied here)
        cache/
            unpacked/  .dem files unpacked from .bz2 / .gz / .zst / .zip
            parsed/    the parsed demo, so re-opening a demo is instant
            pip/       pip's download cache      (start.bat points pip here)
            pycache/   compiled .pyc files       (start.bat points Python here)
            tmp/       the temp folder           (start.bat points TEMP here)
        home/          Streamlit's own ~/.streamlit (start.bat points USERPROFILE here)
        radars/        radar images + map_data.json (start.bat fetches them)
        icons/         weapon / status icons + manifest.json (start.bat fetches them)
        logs/          notes.log - support notes (a corrupt cache dropped, a
                       cache write failed); trimmed to the last 200 lines
        tools/         the Steam helper (boiler-writter, from Settings)
        library.json   the library: copies AND linked demos (name, path, map ...)
        state.json     the last selected demo and player
        accounts.json  match-import settings (folders, SteamID64 - no secrets)
        matches.json   the cached match lists of the Matches tab

The `store` package is the ONE place that knows these locations; every
other module asks it (tests/check_ui.py enforces that). The folder can
be moved with AIMAUDIT_DATA_DIR; when the app folder is read-only the
data goes to %LOCALAPPDATA%\\CS2-AimAudit instead, and the app says so.
Plain Python only (no Streamlit, no demo parsing) so it can be tested
on its own; library.py / caches.py / importdata.py build on it.
"""

from __future__ import annotations

import functools
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

__all__ = [
    "LOG_MAX_LINES", "accounts_path", "data_dir", "data_dir_is_fallback", "demo_key",
    "demos_dir", "human_size", "icons_dir", "log_note", "logs_dir",
    "matches_cache_path", "matchstats_dir", "parsed_dir", "radars_dir", "state_path",
    "tools_dir", "unpacked_dir",
]

def _app_dir() -> str:
    """
    The folder of the launcher. For a frozen exe (PyInstaller, Nuitka) that is
    the folder of the .exe itself: __file__ would point into the temporary
    folder the bundle unpacks to, and the data would vanish with it. Not
    frozen, this module lives in the store/ package one level below the app
    folder, so the data sits next to start.bat like before.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def matchstats_dir() -> str:
    """The per-match summary cache (JSON, see core/matchstats.py)."""
    return _sub("cache", "matchstats")


def radars_dir() -> str:
    return _sub("radars")


def icons_dir() -> str:
    """Weapon / status icons + manifest.json (tools/fetch_icons.py)."""
    return _sub("icons")


def logs_dir() -> str:
    """Support notes the user cannot see in the UI (see log_note)."""
    return _sub("logs")


# Best-effort support notes: cache events without a UI surface (a corrupt
# cache was dropped, a write failed because the disk is full). Nothing
# secret ever goes in (file names and exception types only), the log is
# trimmed to the last LOG_MAX_LINES lines, and a failure to log is swallowed:
# a support note must never break the app.
LOG_MAX_LINES = 200


def log_note(tag: str, msg: str) -> None:
    """Append one `<utc iso time> <tag>: <msg>` line to data/logs/notes.log."""
    f = os.path.join(logs_dir(), "notes.log")
    try:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {tag}: {msg}\n"
        try:
            with open(f, encoding="utf-8") as fh:
                old = fh.readlines()
        except OSError:
            old = []
        with open(f, "w", encoding="utf-8") as fh:
            fh.writelines(old[-(LOG_MAX_LINES - 1):] + [line])
    except OSError:
        return


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


def _unlink_freed(path: str) -> int:
    """Delete a file; returns the bytes freed (0 when it was not there)."""
    try:
        n = os.path.getsize(path)
        os.remove(path)
        return n
    except OSError:
        return 0


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
