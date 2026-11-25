"""
The unified match list: one row per match, from every source.

Pure data glue (no Streamlit): the cached provider lists (Steam ...), the
demo library (copies and linked files, provider-bound or your own) and the
per-match summary cache are merged into the rows the Matches table draws.
The table itself NEVER parses demos - the summary numbers arrive through
`stats_of(entry) -> dict | None` (core/matchstats.py's cache; tests stub
it), so rendering the list stays cheap and offline.

Merge key: (provider, match_id) whenever it is known - a downloaded Steam
demo and its provider-list entry are ONE row. A library entry without a
provider record (your own demo file) is its own row, keyed by its library
id. There are no duplicates. Sorting is by match time: the provider's
time, else the summary cache's time, else the file's mtime (the library
entry's `added` at worst); undated rows keep to the end.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from sources.model import DemoState, MatchRecord

__all__ = ["KINDS", "MatchRow", "build_rows", "kind_of", "manual_demo_ready"]

# the Source column's two values today (a provider id or your own file)
SOURCE_FILE = "file"

# the match pools the table/filter know: the official modes by name, FACEIT
# by provider, everything else your own upload (a custom demo has no pool)
KINDS = ("competitive", "premier", "faceit", "manual")


def kind_of(provider: str | None, mode: str | None) -> str:
    """The pool a match belongs to (one of KINDS): FACEIT by provider or
    mode, Premier/Competitive by the mode name, an unnamed official source
    still counts as matchmaking, and a demo with no provider and no known
    mode is a manual upload."""
    m = str(mode or "").lower()
    if "faceit" in m or provider == "faceit":
        return "faceit"
    if "premier" in m:
        return "premier"
    if "competitive" in m:
        return "competitive"
    if provider:
        return "competitive"
    return "manual"


@dataclass
class MatchRow:
    """One row of the Matches table: everything the actions need."""

    key: str  # unique: "<provider>:<match_id>" | "lib:<demo_id>"
    demo_id: str | None  # library entry id when the demo is in the library
    provider: str | None  # "steam" | None (your own demo file)
    match_id: str | None
    rec: MatchRecord | None  # the cached provider record, when one exists
    entry: dict | None  # the library entry, when the demo is in it
    map_name: str | None
    mode: str | None
    played_at: datetime | None
    score: tuple[int, int] | None  # (mine, theirs), when known
    result: str | None  # "win" | "loss" | "tie" | None
    demo_state: str  # a DemoState value; DOWNLOADED once the demo is in
    missing: bool  # a linked demo whose file is gone (locate it again)
    stats: dict | None  # the cached per-match summary, when computed

    @property
    def source(self) -> str:
        """The Source badge: the provider id, or "file" for your own demo."""
        return self.provider or SOURCE_FILE

    @property
    def kind(self) -> str:
        """The pool this match belongs to (one of KINDS)."""
        return kind_of(self.provider, self.mode)


def _iso(v) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def _entry_time(entry: dict) -> datetime | None:
    """A library entry's stand-in for the match time: the linked file's
    mtime when known (the entry records it), else when it was added."""
    mt = entry.get("mtime_ns")
    if isinstance(mt, int) and mt > 0:
        return datetime.fromtimestamp(mt / 1e9, tz=timezone.utc)
    return _iso(entry.get("added"))


def _entry_match(entry: dict) -> dict:
    m = entry.get("match")
    return m if isinstance(m, dict) else {}


def _score(v) -> tuple[int, int] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            a, b = int(v[0]), int(v[1])
        except (TypeError, ValueError):
            return None
        return (a, b)
    return None


def _stats_of(stats_of, entry: dict | None):
    """The cached per-match summary for a library entry (never parses
    anything; None simply leaves the summary cells empty; a broken cache
    file is no reason to die)."""
    if entry is None or stats_of is None:
        return None
    try:
        return stats_of(entry)
    except Exception:  # noqa: BLE001 - a broken cache file is no reason to die
        return None


def _record_row(rec: MatchRecord, entry: dict | None, stats) -> MatchRow:
    """A row for a cached provider record, merged with its library entry
    (entry None -> nothing downloaded for it yet). Each cell's chain is one
    fallback source per or: the record, the cached stats, the entry."""
    em = _entry_match(entry or {})
    en = entry or {}
    st = stats or {}
    return MatchRow(
        key=f"{rec.provider}:{rec.match_id}",
        demo_id=entry["id"] if entry is not None else None,
        provider=rec.provider,
        match_id=rec.match_id,
        rec=rec,
        entry=entry,
        map_name=rec.map_name or en.get("map") or st.get("map"),
        mode=rec.mode or em.get("mode"),
        played_at=rec.played_at
        or _iso(st.get("played_at"))
        or (_entry_time(entry) if entry is not None else None),
        score=rec.score or _score(st.get("score")) or _score(em.get("score")),
        result=rec.result or st.get("result") or em.get("result"),
        demo_state=(
            DemoState.DOWNLOADED if entry is not None else rec.demo
        ),
        missing=bool(entry is not None and entry.get("status") == "missing"),
        stats=stats,
    )


def _library_row(e: dict, stats) -> MatchRow:
    """A row for a library entry no cached record claims: provider-
    downloaded demos whose list entry fell out of the cache, and your own
    files."""
    m = _entry_match(e)
    st = stats or {}
    provider = str(m.get("provider")) if m.get("provider") else None
    mid = str(m.get("match_id")) if m.get("match_id") else None
    return MatchRow(
        key=f"{provider}:{mid}" if provider and mid else f"lib:{e['id']}",
        demo_id=e["id"],
        provider=provider,
        match_id=mid,
        rec=None,
        entry=e,
        map_name=e.get("map") or st.get("map"),
        mode=m.get("mode"),
        played_at=_iso(m.get("played_at")) or _iso(st.get("played_at")) or _entry_time(e),
        score=_score(m.get("score")) or _score(st.get("score")),
        result=m.get("result") or st.get("result"),
        demo_state=DemoState.DOWNLOADED,
        missing=bool(e.get("status") == "missing"),
        stats=stats,
    )


def build_rows(
    records: list[MatchRecord],
    lib: list[dict],
    stats_of=None,
) -> list[MatchRow]:
    """
    Merge the provider caches and the library into the table's rows.

    `stats_of(entry) -> dict | None` supplies the cached per-match summary
    for a library entry (never parses anything; None simply leaves the
    summary cells empty). Newest first; undated rows at the end.
    """
    # the library's provider-bound entries, by (provider, match_id)
    by_match: dict[tuple[str, str], dict] = {}
    for e in lib:
        m = _entry_match(e)
        p, mid = m.get("provider"), m.get("match_id")
        if p and mid:
            by_match[(str(p), str(mid))] = e

    rows: list[MatchRow] = []
    claimed: set[str] = set()  # library entry ids already merged into a row

    # 1) every cached provider record, merged with its library entry
    for rec in records:
        entry = by_match.get((rec.provider, rec.match_id))
        if entry is not None:
            claimed.add(entry["id"])
        rows.append(_record_row(rec, entry, _stats_of(stats_of, entry)))

    # 2) the library entries no cached record claims
    for e in lib:
        if e["id"] in claimed:
            continue
        rows.append(_library_row(e, _stats_of(stats_of, e)))

    rows.sort(
        key=lambda r: r.played_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return rows


def manual_demo_ready(rec: MatchRecord) -> bool:
    """A "manual" record whose demo file really sits on disk (importable)."""
    return (
        rec.demo == DemoState.MANUAL
        and bool(rec.demo_ref)
        and os.path.isfile(str(rec.demo_ref))
    )
