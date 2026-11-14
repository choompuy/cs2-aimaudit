"""
Match import: the facade the Matches page talks to.

    sources.list_matches("steam", cfg, 5)         -> list[MatchRecord]
    sources.fetch_demo(rec, cfg, progress=...)     -> library entry
    sources.import_local(rec, path)                -> library entry

The page never builds transports or clients itself, so tests replace these
three functions (or the class under sources.steam) to run the whole UI
without a network. Everything here is offline-safe: opening the page reads
only the local cache; network calls happen on explicit actions.

Layer: this package is a leaf like core/ - no Streamlit, no views/shell
knowledge (enforced by tests/check_ui.py). It may import store.py for paths.
"""

from __future__ import annotations

import store

from sources import http, model
from sources.model import (
    DEFAULT_MATCH_COUNT,
    DEMO_KEEP_DAYS,
    MATCH_COUNT_CHOICES,
    AuthError,
    DemoState,
    DownloadCancelled,
    HelperError,
    MatchRecord,
    NetworkError,
    NotFoundError,
    OfflineError,
    ProviderError,
    RateLimitError,
    ShareCodeError,
    SteamConfig,
)

__all__ = [
    "DEFAULT_MATCH_COUNT",
    "DEMO_KEEP_DAYS",
    "MATCH_COUNT_CHOICES",
    "AuthError",
    "DemoState",
    "DownloadCancelled",
    "HelperError",
    "MatchRecord",
    "NetworkError",
    "NotFoundError",
    "OfflineError",
    "ProviderError",
    "RateLimitError",
    "ShareCodeError",
    "SteamConfig",
    "http",
    "model",
    "import_local",
    "list_matches",
    "fetch_demo",
]


def _live_transport() -> http.HttpTransport:
    return http.HttpTransport()


def list_matches(provider: str, cfg, n: int, *, transport=None) -> list[MatchRecord]:
    """The `n` most recent matches of one provider (network!)."""
    if transport is None:
        transport = _live_transport()
    if provider == "steam":
        from sources.steam import SteamProvider

        return SteamProvider(cfg, transport=transport).list_matches(n)
    raise ProviderError(f"unknown provider {provider!r}", key="src_err_generic")


def _bind(entry: dict, rec: MatchRecord) -> dict:
    store_match = {
        "provider": rec.provider,
        "match_id": rec.match_id,
        "map": rec.map_name,
        "played_at": rec.played_at.isoformat() if rec.played_at else None,
        "score": list(rec.score) if rec.score else None,
        "result": rec.result,
    }
    store.update_meta(entry["id"], match=store_match)
    return store.get_demo(entry["id"]) or entry


def fetch_demo(
    rec: MatchRecord,
    cfg,
    *,
    transport=None,
    progress=None,
    cancel=None,
) -> dict:
    """Download the demo of `rec` into the library; returns its entry."""
    if transport is None:
        transport = _live_transport()
    if rec.provider != "steam":
        raise ProviderError(f"unknown provider {rec.provider!r}")
    from sources.steam import SteamProvider

    entry = SteamProvider(cfg, transport=transport).fetch_demo(
        rec, progress=progress, cancel=cancel
    )
    return _bind(entry, rec)


def import_local(rec: MatchRecord, path: str) -> dict:
    """Add a demo that already sits on disk (the replays folder) to the library."""
    entry = store.add_demo(path)
    return _bind(entry, rec)
