"""
The HTTP client every source shares: urllib only, no third-party deps.

Rules enforced here (see docs/ARCHITECTURE.md, "sources"):
- AIMAUDIT_OFFLINE=1 makes every call fail fast with OfflineError;
- https only, except Valve demo servers (http://replay<N>.valve.net/...),
  whose URLs are additionally checked against the known replay pattern;
- the User-Agent is the app name; credentials travel in headers, never URLs;
- JSON calls retry on 429 (honouring Retry-After) and 5xx (backoff), at most
  3 attempts, then raise a localized ProviderError;
- downloads stream in chunks to <dest>.part and are renamed into place only
  when complete; a cancel callback between chunks removes the .part.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from sources.model import (
    AuthError,
    DownloadCancelled,
    NetworkError,
    NotFoundError,
    OfflineError,
    ProviderError,
    RateLimitError,
)

USER_AGENT = "cs2-aimaudit"
CHUNK = 1 << 20  # 1 MiB
MAX_ATTEMPTS = 3
MAX_DEMO_BYTES = 4_000_000_000
TIMEOUT_JSON = 15.0
TIMEOUT_DEMO = 60.0
# Valve demo links, e.g. http://replay129.valve.net/730/0036388955_1102521424.dem.bz2
_VALVE_REPLAY = re.compile(
    r"^https?://replay\d+\.valve\.net/730/\d+_\d+\.dem\.bz2$"
)


def offline() -> bool:
    return bool(os.environ.get("AIMAUDIT_OFFLINE"))


def _check_offline() -> None:
    if offline():
        raise OfflineError("AIMAUDIT_OFFLINE is set")


def _scheme_ok(url: str, *, demo: bool) -> bool:
    p = urllib.parse.urlsplit(url)
    if p.scheme == "https":
        return True
    if p.scheme == "http" and demo and _VALVE_REPLAY.match(url):
        return True
    raise NetworkError(f"refusing non-https url: {p.scheme}")


class HttpTransport:
    """Injectable transport; tests pass a fake with the same three methods."""

    def __init__(self, sleeper=time.sleep):
        self._sleep = sleeper
        self.calls: list[tuple] = []

    # ------------------------------------------------------------- plumbing
    def _open(self, url: str, *, data: bytes | None, headers: dict, timeout: float):
        req = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - schemes checked above

    def _once(self, url: str, *, data: bytes | None, headers: dict, timeout: float):
        try:
            with self._open(url, data=data, headers=headers, timeout=timeout) as resp:
                return resp.status, resp.headers or {}, resp.read()
        except urllib.error.HTTPError as e:
            try:
                body = e.read(4096)
            except (OSError, ValueError, http.client.HTTPException):
                # a half-written error body is fine: the status is what matters
                body = b""
            return e.code, e.headers or {}, body
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NetworkError(f"{type(e).__name__}: {e}") from e

    def _json_error(self, status: int, body: bytes, url: str) -> ProviderError:
        detail = ""
        try:
            j = json.loads(body.decode("utf-8", "replace"))
            if isinstance(j, dict):
                detail = str(j.get("message") or j.get("error") or "")[:200]
        except ValueError:
            pass
        if status in (401, 403):
            return AuthError(detail or f"HTTP {status}")
        if status == 404:
            return NotFoundError(detail or "HTTP 404", name=urllib.parse.urlsplit(url).netloc)
        if status == 429:
            return RateLimitError(retry_after=0.0)
        return NetworkError(detail or f"HTTP {status}")

    def _request_json(self, method: str, url: str, *, body: dict | None, headers: dict):
        _check_offline()
        _scheme_ok(url, demo=False)
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        delay = 1.0
        for attempt in range(MAX_ATTEMPTS):
            status, resp_headers, raw = self._once(url, data=data, headers=hdrs, timeout=TIMEOUT_JSON)
            self.calls.append((method, url, status))
            if status == 429:
                if attempt == MAX_ATTEMPTS - 1:
                    raise RateLimitError(retry_after=_retry_after(resp_headers))
                self._sleep(_retry_after(resp_headers) or delay)
                delay *= 2
                continue
            if 500 <= status < 600:
                if attempt == MAX_ATTEMPTS - 1:
                    raise self._json_error(status, raw, url)
                self._sleep(delay)
                delay *= 2
                continue
            if status >= 400:
                raise self._json_error(status, raw, url)
            try:
                return json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError as e:
                raise NetworkError(f"bad JSON from {urllib.parse.urlsplit(url).netloc}") from e
        raise NetworkError("unreachable")

    # ----------------------------------------------------------------- API
    def get_json(self, url: str, headers: dict | None = None) -> dict:
        return self._request_json("GET", url, body=None, headers=headers or {})

    def post_json(self, url: str, body: dict, headers: dict | None = None) -> dict:
        return self._request_json("POST", url, body=body, headers=headers or {})

    def download(
        self,
        url: str,
        dest: str,
        *,
        headers: dict | None = None,
        progress=None,
        cancel=None,
        max_bytes: int = MAX_DEMO_BYTES,
    ) -> int:
        """Stream `url` into `dest` via `dest + ".part"`; returns bytes written.

        `progress(done, total)` fires per chunk (total is None without
        Content-Length); `cancel()` is polled between chunks and aborts with
        DownloadCancelled, removing the .part.
        """
        _check_offline()
        _scheme_ok(url, demo=True)
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        part = dest + ".part"
        done = 0
        try:
            with self._open(url, data=None, headers=hdrs, timeout=TIMEOUT_DEMO) as resp:
                total_raw = resp.headers.get("Content-Length")
                total = int(total_raw) if total_raw and total_raw.isdigit() else None
                if total is not None and total > max_bytes:
                    raise NetworkError(f"file too large: {total} bytes")
                with open(part, "wb") as out:
                    while True:
                        if cancel is not None and cancel():
                            raise DownloadCancelled("cancelled between chunks")
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        done += len(chunk)
                        if done > max_bytes:
                            raise NetworkError(f"file larger than {max_bytes} bytes")
                        out.write(chunk)
                        if progress is not None:
                            progress(done, total)
            if done == 0:
                raise NetworkError("the server sent an empty file")
            os.replace(part, dest)
            return done
        except DownloadCancelled:
            _remove(part)
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            _remove(part)
            raise NetworkError(f"{type(e).__name__}: {e}") from e
        except BaseException:
            _remove(part)
            raise


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _retry_after(headers: dict) -> float:
    v = headers.get("Retry-After") or headers.get("retry-after")
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(n, 300.0))
