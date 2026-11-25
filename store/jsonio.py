"""JSON files of the data folder: atomic writes, a broken file reads as empty."""

from __future__ import annotations

import json
import os

from store.paths import _unlink_freed, log_note

__all__ = ["read_json", "write_json"]


def read_json(path: str) -> dict:
    """The object in a JSON file; {} when missing, unreadable or not an object."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_json(path: str, d: dict) -> bool:
    """Atomic write; False (and a note in the log) when it failed."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as e:  # a full disk / not JSON-able
        log_note("json", f"{os.path.basename(path)} write failed: {type(e).__name__}")
        _unlink_freed(tmp)
        return False
    return True
