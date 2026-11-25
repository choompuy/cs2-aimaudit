"""
Match-import settings and caches (accounts.json, matches.json): the
same atomic-write rules as the library - a corrupt file counts as empty
and is recreated, no secrets are ever stored.
"""

from __future__ import annotations

from store.jsonio import read_json, write_json
from store.library import _read_library, _update
from store.paths import _now, accounts_path, matches_cache_path

__all__ = [
    "find_by_match", "mark_analyzed", "read_accounts", "read_matches_cache",
    "write_accounts", "write_matches_cache",
]


def read_accounts() -> dict:
    return read_json(accounts_path())


def write_accounts(d: dict) -> bool:
    return write_json(accounts_path(), d)


def read_matches_cache() -> dict:
    return read_json(matches_cache_path())


def write_matches_cache(d: dict) -> bool:
    return write_json(matches_cache_path(), d)


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
