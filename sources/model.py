"""
The match-import data model: records, demo states, configs, errors.

Pure Python (no Streamlit, no network): sources/ modules and the tests build
on it. Errors carry an i18n key plus kwargs so the UI can render a localized,
specific message instead of a raw exception string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_MATCH_COUNT = 5
MATCH_COUNT_CHOICES = (5, 10, 20)
# Valve deletes demos roughly a month after the match; past this age a demo
# is flagged "expired" (still worth a try - the server decides)
DEMO_KEEP_DAYS = 30


class DemoState:
    """What the app can do with the demo of a match."""

    READY = "ready"  # downloadable automatically
    MANUAL = "manual"  # the file exists on disk (the replays folder), not imported yet
    EXPIRED = "expired"  # older than the ~30-day retention
    DOWNLOADED = "downloaded"  # already in the demo library
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MatchRecord:
    provider: str  # "steam"
    match_id: str
    played_at: datetime | None = None
    map_name: str | None = None
    mode: str | None = None
    score: tuple[int, int] | None = None  # (mine, theirs)
    result: str | None = None  # "win" | "loss" | "tie" | None
    demo: str = DemoState.UNKNOWN
    demo_ref: str | None = None  # Valve demo URL / the on-disk replays path
    page_url: str | None = None
    extra: dict = field(default_factory=dict)

    def age_days(self, now: datetime | None = None) -> float | None:
        if self.played_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        played = self.played_at
        if played.tzinfo is None:
            played = played.replace(tzinfo=timezone.utc)
        return (now - played).total_seconds() / 86400.0

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "match_id": self.match_id,
            "played_at": self.played_at.isoformat() if self.played_at else None,
            "map_name": self.map_name,
            "mode": self.mode,
            "score": list(self.score) if self.score else None,
            "result": self.result,
            "demo": self.demo,
            "demo_ref": self.demo_ref,
            "page_url": self.page_url,
            "extra": self.extra or {},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MatchRecord":
        played = d.get("played_at")
        if isinstance(played, str):
            try:
                played = datetime.fromisoformat(played)
            except ValueError:
                played = None
        score = d.get("score")
        return cls(
            provider=str(d.get("provider") or ""),
            match_id=str(d.get("match_id") or ""),
            played_at=played,
            map_name=d.get("map_name"),
            mode=d.get("mode"),
            score=tuple(score) if score and len(score) == 2 else None,
            result=d.get("result"),
            demo=d.get("demo") or DemoState.UNKNOWN,
            demo_ref=d.get("demo_ref"),
            page_url=d.get("page_url"),
            extra=dict(d.get("extra") or {}),
        )


@dataclass
class SteamConfig:
    steam_id64: str = ""  # optional; the helper's STEAMID: line wins when present
    replays_dir: str = ""  # optional override of the CS2 replays folder


class ProviderError(Exception):
    """A source failure. `key` names an i18n string, `kw` its placeholders."""

    key = "src_err_generic"

    def __init__(self, message: str = "", *, key: str | None = None, **kw):
        super().__init__(message or (key or self.key))
        self.key = key or self.key
        self.kw = kw


class AuthError(ProviderError):
    key = "src_err_auth"


class NotFoundError(ProviderError):
    key = "src_err_not_found"

    def __init__(self, message: str = "", *, name: str = "", **kw):
        super().__init__(message, key=self.key, name=name, **kw)


class RateLimitError(ProviderError):
    key = "src_err_rate"

    def __init__(self, retry_after: float = 0.0, **kw):
        super().__init__(key=self.key, retry_after=retry_after, **kw)
        self.retry_after = retry_after


class NetworkError(ProviderError):
    key = "src_err_net"


class OfflineError(ProviderError):
    key = "src_err_offline"


class DownloadCancelled(ProviderError):
    key = "src_err_cancelled"


class ShareCodeError(ProviderError):
    key = "src_err_sharecode"


class HelperError(ProviderError):
    """boiler-writter failed; exit codes map to specific i18n keys."""

    CODE_KEYS = {
        2: "src_err_helper_args",
        3: "src_err_helper_comm",
        4: "src_err_helper_connected",
        5: "src_err_helper_restart",
        6: "src_err_helper_steam",
        7: "src_err_helper_steam",
        8: "src_err_helper_none",
        9: "src_err_helper_write",
    }

    def __init__(self, code: int, message: str = ""):
        key = self.CODE_KEYS.get(code, "src_err_helper")
        super().__init__(message or f"boiler-writter exit code {code}", key=key, code=code)
        self.code = code
