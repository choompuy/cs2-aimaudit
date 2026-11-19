"""
Fetch plumbing shared by tools/fetch_icons.py and tools/fetch_radars.py:
the fixed-UA https GET, the atomic .part write, and the launcher-mode
failure mark (`.fetch_failed`, retried no sooner than a day later).

Both fetchers run only on explicit user action (a button or the launcher
flag); the app itself never touches the network. `present()` stays in
each tool - what "the artifact set is complete" means differs between
the icon manifest and the radar calibration.
"""

from __future__ import annotations

import os
import time
import urllib.request

USER_AGENT = "cs2-aimaudit"
RETRY_AFTER_S = 24 * 3600  # after a failed automatic download, wait this long
FAILED_MARK = ".fetch_failed"


def get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return resp.read()


def write(path: str, data: bytes) -> None:
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def recently_failed(dest: str) -> bool:
    try:
        return time.time() - os.path.getmtime(os.path.join(dest, FAILED_MARK)) < RETRY_AFTER_S
    except OSError:
        return False


def mark_failed(dest: str, err: Exception | str) -> None:
    """Remember a launcher-mode failure so it is not retried for a day."""
    try:
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, FAILED_MARK), "w", encoding="utf-8") as fh:
            fh.write(str(err))
    except OSError:
        pass


def clear_failed(dest: str) -> None:
    try:
        os.remove(os.path.join(dest, FAILED_MARK))
    except OSError:
        pass
