"""
Match-import settings and caches (accounts.json, matches.json): the
same atomic-write rules as the library - a corrupt file counts as empty
and is recreated, no secrets are ever stored.
"""

from __future__ import annotations

import json
import os

from store.library import _read_library, _update
from store.paths import _now, accounts_path, matches_cache_path


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
