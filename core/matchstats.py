"""The per-match summary cache behind the Matches table: one small JSON per demo, keyed
like the parsed cache, so the table never parses demos."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd

import store
from core.loader import PARSE_VERSION
from core.duels import build_duels
from core.impact import impact_stats
from core.openings import round_winners
from core.shots import shots_table
from core.summary import player_summary

# the structure of the JSON (content changes go through the loader hash)
STATS_VERSION = 1

# fixed thresholds: the cache must not follow the analysis sliders
_WINDOW_S = 3.0
_THR = 40
_SAFE = 500
_STRICT = 1500


def resolve_me(demo, entry: dict, accounts: dict) -> str | None:
    """Who "me" is: the pick remembered for THIS match, else the accounts' SteamID64, if
    that player is in the demo. None: the UI asks."""
    players = set(demo.players["sid"])
    me = entry.get("me_sid")
    if me and me in players:
        return me
    sid64 = str(accounts.get("steam_id64") or "").strip()
    if sid64 and sid64 in players:
        return sid64
    return None


def played_at_of(entry: dict) -> datetime | None:
    """When the match was: the provider's time, else the file's mtime."""
    m = entry.get("match") if isinstance(entry.get("match"), dict) else {}
    iso = m.get("played_at")
    if isinstance(iso, str):
        try:
            return datetime.fromisoformat(iso)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(
            os.path.getmtime(store.demo_path(entry)), tz=timezone.utc
        )
    except OSError:
        return None


def compute(demo, me_sid: str, played_at: datetime | None = None) -> dict:
    """The summary of one player's match from the parsed demo."""
    shots = shots_table(demo)
    duels = build_duels(demo, _WINDOW_S)
    summary = player_summary(demo, duels, shots, _THR, _SAFE, _STRICT)
    row = summary[summary["sid"] == me_sid]
    impact = impact_stats(demo)
    irow = impact[impact["sid"] == me_sid]

    def _cell(col, cast=float):
        if not len(row) or col not in row.columns:
            return None
        v = row[col].iloc[0]
        try:
            v = cast(v)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(v) else v

    # the score from the round winners, counted from my side of the field
    winners = round_winners(demo)
    mine = theirs = 0
    for r, w in winners.items():
        if w == demo.side_at(r, me_sid):
            mine += 1
        else:
            theirs += 1
    rounds = len(demo.round_windows) or None
    score = [mine, theirs] if winners else None
    result = None
    if winners:
        result = "win" if mine > theirs else "loss" if mine < theirs else "tie"

    # ADR: my enemy gun damage (demo.hurts is enemy-only already) per round
    adr = None
    h = demo.hurts
    if rounds and len(h) and {"attacker_steamid", "dmg_health"} <= set(h.columns):
        dmg = pd.to_numeric(
            h.loc[h["attacker_steamid"] == me_sid, "dmg_health"], errors="coerce"
        ).sum()
        adr = round(float(dmg) / rounds, 1)

    def _impact(col):
        if not len(irow) or col not in irow.columns:
            return None
        v = irow[col].iloc[0]
        try:
            return round(float(v), 1)
        except (TypeError, ValueError):
            return None

    kills = _cell("kills", int)
    deaths = _cell("deaths", int)
    return {
        "version": STATS_VERSION,
        "map": demo.header.get("map_name") or None,
        "rounds": rounds,
        "score": score,
        "result": result,
        "kills": kills,
        "deaths": deaths,
        "kd": _cell("kd_ratio"),
        "adr": adr,
        "hs_pct": _cell("hs_kills"),
        "accuracy": _cell("accuracy"),
        "open_win_pct": _impact("open_rate"),
        "me_sid": me_sid,
        "played_at": played_at.isoformat() if played_at else None,
    }


def read_cached(path: str) -> dict | None:
    """The cached summary of a demo file (None when missing or stale)."""
    return store.read_matchstats(path, PARSE_VERSION)


def write_cached(path: str, stats: dict) -> bool:
    """Cache a demo's summary; older code versions are dropped."""
    return store.write_matchstats(path, PARSE_VERSION, stats)


def ensure(demo, entry: dict, me_sid: str) -> None:
    """Compute and cache the summary unless it is there for this very player."""
    path = store.demo_path(entry)
    if (read_cached(path) or {}).get("me_sid") != me_sid:
        write_cached(path, compute(demo, me_sid, played_at=played_at_of(entry)))


def drop_cached(path: str) -> bool:
    """Forget a demo's cached summary (the "me" changed and the numbers
    must be recomputed for the new player); True when a cache file went."""
    return store.drop_matchstats(path, PARSE_VERSION)


def cached_stats(entry: dict) -> dict | None:
    """The stats_of callable of core.build_rows: the cached summary of a
    library entry, with the date and score parsed back from JSON."""
    stats = read_cached(store.demo_path(entry))
    if not stats:
        return None
    iso = stats.get("played_at")
    if isinstance(iso, str):
        try:
            stats["played_at"] = datetime.fromisoformat(iso)
        except ValueError:
            stats["played_at"] = None
    return stats
