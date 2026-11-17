"""
Unit tests for the sources/ package (match import): the HTTP transport,
the Steam provider, protobuf parsing, share codes, downloads and privacy.
No real network and no real Steam: urllib and the helper process are faked,
every check runs against fixtures built in-memory.

Run from the repo root: python tests/test_sources.py
"""

import datetime
import io
import json
import os
import re
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import store  # noqa: E402
from sources import http as shttp  # noqa: E402
from sources import model as sm  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


class _DataDir:
    """A throw-away data folder for one block of checks."""

    def __enter__(self):
        self.old = os.environ.get("AIMAUDIT_DATA_DIR")
        os.environ["AIMAUDIT_DATA_DIR"] = os.path.realpath(
            tempfile.mkdtemp(prefix="cs2aa_sources_")
        )
        return self

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("AIMAUDIT_DATA_DIR", None)
        else:
            os.environ["AIMAUDIT_DATA_DIR"] = self.old
        return False


# --------------------------------------------------------------- fakes
class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None):
        self._body = body if isinstance(body, (bytes, bytearray)) else body.encode()
        self.status = status
        self.headers = headers or {}

    def read(self, n=-1):
        if n is None or n < 0:
            out, self._body = self._body, b""
        else:
            out, self._body = self._body[:n], self._body[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrllib:
    """Replaces urllib.request.urlopen; routes are (method, url) -> answer.

    An answer is a FakeResponse, an HTTPError, or a callable(req, timeout)
    returning one - and `sleeps` collects every backoff the client takes.
    """

    def __init__(self):
        self.routes = {}
        self.hits = []
        self.sleeps = []

    def add(self, method, url, answer):
        self.routes[(method, url)] = answer

    def __call__(self, req, timeout=None):
        method = req.get_method()
        url = req.full_url
        self.hits.append((method, url))
        answer = self.routes.get((method, url))
        if answer is None:
            answer = self.routes.get((None, url))
        if callable(answer) and not isinstance(answer, (FakeResponse, urllib.error.HTTPError)):
            answer = answer(req, timeout)
        if isinstance(answer, urllib.error.HTTPError):
            raise answer
        return answer


def _http_error(code, body=b"", headers=None):
    m = Message()
    for k, v in (headers or {}).items():
        m[k] = v
    return urllib.error.HTTPError(
        "https://api.steampowered.com/x", code, "err", m, io.BytesIO(body)
    )


def _json_body(d):
    return json.dumps(d).encode()


def _transport_with(fake):
    t = shttp.HttpTransport(sleeper=fake.sleeps.append)
    t._opener = fake  # for introspection in tests
    return t, fake


def _patch_urlopen(fake):
    """Replace urllib.request.urlopen for the transport under test."""
    original = urllib.request.urlopen

    def _call(req, timeout=None):
        return fake(req, timeout)

    urllib.request.urlopen = _call
    return original


class _UrwlibPatch:
    def __init__(self, fake):
        self.fake = fake
        self.original = None

    def __enter__(self):
        self.original = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: self.fake(req, timeout)
        return self.fake

    def __exit__(self, *exc):
        urllib.request.urlopen = self.original
        return False


# ------------------------------------------------------------ http tests
def test_offline_gate():
    print("== sources: AIMAUDIT_OFFLINE ==")
    old = os.environ.get("AIMAUDIT_OFFLINE")
    os.environ["AIMAUDIT_OFFLINE"] = "1"
    try:
        t = shttp.HttpTransport(sleeper=lambda s: None)
        for fn in (
            lambda: t.get_json("https://api.steampowered.com/x"),
            lambda: t.post_json("https://api.steampowered.com/x", {}),
        ):
            try:
                fn()
                check("offline blocks json calls", False)
            except sm.OfflineError:
                check("offline blocks json calls", True)
        try:
            t.download("https://replay1.valve.net/730/1_2.dem.bz2", os.path.join(tempfile.mkdtemp(), "x"))
            check("offline blocks downloads", False)
        except sm.OfflineError:
            check("offline blocks downloads", True)
    finally:
        if old is None:
            os.environ.pop("AIMAUDIT_OFFLINE", None)
        else:
            os.environ["AIMAUDIT_OFFLINE"] = old


def test_scheme_gate():
    print("== sources: https enforcement ==")
    for url in ("http://example.com/x", "ftp://x", "http://replay1.valve.net/730/a_b.dem.bz2"):
        try:
            shttp._scheme_ok(url, demo=False)
            check(f"json refuses {url}", False)
        except sm.NetworkError:
            check(f"json refuses {url}", True)
    check(
        "demo allows the valve replay pattern",
        shttp._scheme_ok("http://replay129.valve.net/730/003638895521671676017_1102521424.dem.bz2", demo=True)
        and shttp._scheme_ok("https://github.com/akiver/boiler-writter/releases/download/v1.7.0/bw.zip", demo=True),
    )
    try:
        shttp._scheme_ok("http://evil.valve.net/730/1_2.dem.bz2", demo=True)
        check("demo refuses a wrong valve host", False)
    except sm.NetworkError:
        check("demo refuses a wrong valve host", True)


def test_json_retries():
    print("== sources: retries, rate limit, errors ==")
    fake = FakeUrllib()
    t = shttp.HttpTransport(sleeper=fake.sleeps.append)
    with _UrwlibPatch(fake):
        url = "https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
        fake.add("GET", url, _http_error(429, b"{}", {"Retry-After": "7"}))
        try:
            t.get_json(url)
            check("429 after 3 attempts raises RateLimitError", False)
        except sm.RateLimitError as e:
            check(
                "429 after 3 attempts raises RateLimitError",
                len(fake.hits) == 3 and e.retry_after == 7.0,
                (len(fake.hits), e.retry_after),
            )
            check("429 backoff honours Retry-After", fake.sleeps == [7.0, 7.0], fake.sleeps)

        fake.hits.clear()
        fake.sleeps.clear()
        fake.add("GET", url, _http_error(503, b"{}"))
        try:
            t.get_json(url)
            check("5xx after 3 attempts raises NetworkError", False)
        except sm.NetworkError:
            check("5xx after 3 attempts raises NetworkError", len(fake.hits) == 3, len(fake.hits))
            check("5xx backoff is exponential", fake.sleeps == [1.0, 2.0], fake.sleeps)

        for code, exc in ((401, sm.AuthError), (403, sm.AuthError), (404, sm.NotFoundError)):
            fake.hits.clear()
            fake.add("GET", url, _http_error(code, _json_body({"message": "nope"})))
            try:
                t.get_json(url)
                check(f"{code} raises {exc.__name__}", False)
            except exc as e:
                check(f"{code} raises {exc.__name__}", "nope" in str(e))

        fake.hits.clear()
        fake.add("GET", url, FakeResponse(_json_body({"player_id": "p1"})))
        check("200 returns parsed json", t.get_json(url) == {"player_id": "p1"})

        fake.hits.clear()
        fake.add("GET", url, FakeResponse(b"not json"))
        try:
            t.get_json(url)
            check("garbage json raises NetworkError", False)
        except sm.NetworkError:
            check("garbage json raises NetworkError", True)

        # the User-Agent is the app name and the key rides in a header
        seen = {}

        def _capture(req, timeout=None):
            seen["ua"] = req.headers.get("User-agent")
            seen["auth"] = req.headers.get("Authorization")
            return FakeResponse(_json_body({}))

        fake.add("POST", "https://api.steampowered.com/x", _capture)
        t.post_json(
            "https://api.steampowered.com/x",
            {"knowncode": "x"},
            headers={"Authorization": "Bearer KEY"},
        )
        check(
            "UA + bearer header, no key in the URL",
            seen["ua"] == "cs2-aimaudit"
            and seen["auth"] == "Bearer KEY"
            and "KEY" not in fake.hits[-1][1],
            seen,
        )


def test_download_stream():
    print("== sources: streaming download ==")
    payload = os.urandom(3 * 1024 * 1024 + 17)  # > 3 chunks
    fake = FakeUrllib()
    t = shttp.HttpTransport(sleeper=fake.sleeps.append)
    dest = os.path.join(tempfile.mkdtemp(), "demo.dem.bz2")
    with _UrwlibPatch(fake):
        fake.add(
            "GET",
            "https://replay129.valve.net/730/1_x.dem.bz2",
            FakeResponse(payload, headers={"Content-Length": str(len(payload))}),
        )
        seen = []
        size = t.download(
            "https://replay129.valve.net/730/1_x.dem.bz2",
            dest,
            progress=lambda done, total: seen.append((done, total)),
        )
        check("downloaded size matches", size == len(payload))
        with open(dest, "rb") as fh:
            check("downloaded bytes match", fh.read() == payload)
        check("no .part left behind", not os.path.exists(dest + ".part"))
        check("progress fired per 1 MiB chunk", len(seen) == 4 and seen[-1][0] == len(payload) and seen[-1][1] == len(payload), seen)
        check("progress is monotonic", [d for d, _ in seen] == sorted(d for d, _ in seen))

        # cancel between chunks: .part removed, DownloadCancelled raised
        fake.add(
            "GET",
            "https://replay129.valve.net/730/1_y.dem.bz2",
            FakeResponse(b"x" * (5 * 1024 * 1024)),
        )
        calls = {"n": 0}

        def _cancel():
            calls["n"] += 1
            return calls["n"] > 2

        dest2 = os.path.join(tempfile.mkdtemp(), "y.dem.gz")
        try:
            t.download("https://replay129.valve.net/730/1_y.dem.bz2", dest2, cancel=_cancel)
            check("cancel raises DownloadCancelled", False)
        except sm.DownloadCancelled:
            check("cancel raises DownloadCancelled", True)
        check("cancelled download leaves no file", not os.path.exists(dest2) and not os.path.exists(dest2 + ".part"))

        # size guard
        fake.add(
            "GET",
            "https://replay129.valve.net/730/1_z.dem.bz2",
            FakeResponse(b"x" * 1024, headers={"Content-Length": "999999999999"}),
        )
        dest3 = os.path.join(tempfile.mkdtemp(), "z.dem.gz")
        try:
            t.download("https://replay129.valve.net/730/1_z.dem.bz2", dest3)
            check("oversized file refused", False)
        except sm.NetworkError:
            check("oversized file refused", not os.path.exists(dest3) and not os.path.exists(dest3 + ".part"))

        # empty body
        fake.add("GET", "https://replay129.valve.net/730/1_e.dem.bz2", FakeResponse(b""))
        dest4 = os.path.join(tempfile.mkdtemp(), "e.dem.gz")
        try:
            t.download("https://replay129.valve.net/730/1_e.dem.bz2", dest4)
            check("empty download refused", False)
        except sm.NetworkError:
            check("empty download refused", True)


def test_store_files():
    print("== sources: data files (accounts/secrets/cache) ==")
    with _DataDir():
        check("accounts empty by default", store.read_accounts() == {})
        store.write_accounts({"version": 1, "replays_dir": "C:/replays"})
        check("accounts round-trip", store.read_accounts()["replays_dir"] == "C:/replays")
        with open(store.accounts_path(), "w", encoding="utf-8") as fh:
            fh.write("{corrupt")
        check("corrupt accounts count as empty", store.read_accounts() == {})
        with open(store.matches_cache_path(), "w", encoding="utf-8") as fh:
            fh.write("[1, 2]")
        check("non-dict cache counts as empty", store.read_matches_cache() == {})
        rec = sm.MatchRecord(
            provider="steam",
            match_id="m1",
            played_at=sm.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
            score=(13, 5),
            result="win",
        )
        store.write_matches_cache({"version": 1, "providers": {"steam": [rec.to_dict()]}})
        back = store.read_matches_cache()
        r2 = sm.MatchRecord.from_dict(back["providers"]["steam"][0])
        check("MatchRecord round-trips through json", r2 == rec, (r2, rec))


def test_no_sockets_on_import():
    print("== sources: privacy (no sockets) ==")
    calls = []

    class _NoSocket(socket.socket):
        def __init__(self, *a, **kw):
            calls.append("socket")
            super().__init__(*a, **kw)

    orig_socket = socket.socket
    orig_urlopen = urllib.request.urlopen
    # keep the module objects the rest of the file already imported: pop,
    # re-import under the patches, then restore, so no class identity drifts
    saved = {
        m: sys.modules.pop(m)
        for m in list(sys.modules)
        if m == "sources" or m.startswith("sources.")
    }
    socket.socket = _NoSocket
    urllib.request.urlopen = lambda *a, **kw: calls.append("urlopen") or None
    try:
        import sources  # noqa: F401

        check("importing sources opens no sockets", not calls, calls)
    finally:
        socket.socket = orig_socket
        urllib.request.urlopen = orig_urlopen
        for m in list(sys.modules):
            if m == "sources" or m.startswith("sources."):
                del sys.modules[m]
        sys.modules.update(saved)


# --------------------------------------------------------------- fakes
class FakeTransport:
    """Stands in for HttpTransport: routed JSON answers + scripted downloads."""

    def __init__(self):
        self.routes = {}
        self.hits = []
        self.downloads = []

    def add(self, method, url, answer):
        self.routes[(method, url)] = answer

    def _json(self, method, url, body=None, headers=None):
        self.hits.append((method, url, dict(headers or {})))
        answer = self.routes.get((method, url))
        if answer is None:
            raise sm.NetworkError(f"no route for {method} {url}")
        if callable(answer):
            answer = answer(body)
        if isinstance(answer, urllib.error.HTTPError):
            raise self._translate(answer)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @staticmethod
    def _translate(e):
        # the same status -> error mapping HttpTransport applies
        if e.code in (401, 403):
            return sm.AuthError(f"HTTP {e.code}")
        if e.code == 404:
            return sm.NotFoundError("HTTP 404")
        if e.code == 429:
            return sm.RateLimitError()
        return sm.NetworkError(f"HTTP {e.code}")

    def get_json(self, url, headers=None):
        return self._json("GET", url, headers=headers)

    def post_json(self, url, body, headers=None):
        return self._json("POST", url, body=body, headers=headers)

    def download(self, url, dest, *, headers=None, progress=None, cancel=None):
        self.downloads.append(url)
        payload = b"FAKE_DEMO_BYTES_" + url.encode()[:32]
        with open(dest, "wb") as fh:
            fh.write(payload)
        if progress:
            progress(len(payload), len(payload))
        return len(payload)


PID = "11111111-2222-3333-4444-555555555555"
OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _faceit_players():
    return {"player_id": PID, "nickname": "Nick", "games": {"cs2": {"skill_level": 5}}}


def _faceit_item(mid, started, my_faction, winner_team, s1, s2, mode="5v5"):
    t1, t2 = "team-alpha", "team-bravo"
    players1 = [{"player_id": PID if my_faction == 1 else OTHER}] * 2
    players2 = [{"player_id": PID if my_faction == 2 else OTHER}] * 2
    winner = None
    if winner_team == 1:
        winner = t1
    elif winner_team == 2:
        winner = t2
    return {
        "match_id": mid,
        "game_id": "cs2",
        "game_mode": mode,
        "status": "finished",
        "started_at": started,
        "finished_at": started + 3600,
        "faceit_url": f"https://www.faceit.com/en/cs2/match/{mid}",
        "competition_name": "CS2 5v5 EU",
        "region": "EU",
        "results": {"score": {"faction1": s1, "faction2": s2}, "winner": winner},
        "teams": {
            "faction1": {"team_id": t1, "players": players1},
            "faction2": {"team_id": t2, "players": players2},
        },
    }


def _faceit_client(token="", nick="Nick", player_id=""):
    from sources.faceit import FaceitClient

    tr = FakeTransport()
    cfg = sm.FaceitConfig(
        nickname=nick, api_key="KEY", downloads_token=token, player_id=player_id
    )
    return FaceitClient(cfg, transport=tr), tr, cfg


# ------------------------------------------------------------- steam tests
def _enc_varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _enc_field(fno: int, value) -> bytes:
    if isinstance(value, int):
        return _enc_varint((fno << 3) | 0) + _enc_varint(value)
    if isinstance(value, str):
        value = value.encode()
    return _enc_varint((fno << 3) | 2) + _enc_varint(len(value)) + value


def _enc_packed(fno: int, values) -> bytes:
    return _enc_field(fno, b"".join(_enc_varint(v) for v in values))


def _enc_reservation(accounts, game_type=8):
    return _enc_packed(1, accounts) + _enc_field(2, game_type)


def _enc_roundstats(
    reservationid=1, map_="", match_result=0, team_scores=(), b_switched=False,
    accounts=(), duration=2400, max_rounds=24, game_type=8,
):
    out = _enc_field(1, reservationid)
    out += _enc_field(2, _enc_reservation(accounts, game_type))
    if map_:
        out += _enc_field(3, map_)
    if match_result:
        out += _enc_field(11, match_result)
    if team_scores:
        out += _enc_packed(12, list(team_scores))
    if duration:
        out += _enc_field(15, duration)
    if b_switched:
        out += _enc_field(27, 1)
    if max_rounds:
        out += _enc_field(31, max_rounds)
    return out


def _enc_watchable(server_ip=167772161, tv_port=27020, game_type=8, game_map="de_mirage"):
    return (
        _enc_field(1, server_ip)
        + _enc_field(2, tv_port)
        + _enc_field(8, game_type)
        + _enc_field(10, game_map)
    )


def _enc_match(
    matchid, matchtime, accounts, demo_url="http://replay129.valve.net/730/1_2.dem.bz2",
    match_result=0, team_scores=(), b_switched_last=False, b_switched_first=False,
    game_map="de_mirage", tv_port=27020, game_type=8,
):
    info = _enc_field(1, matchid) + _enc_field(2, matchtime) + _enc_field(3, _enc_watchable(tv_port=tv_port, game_map=game_map))
    rs = [
        _enc_roundstats(
            reservationid=100 + matchid % 7, map_=demo_url, match_result=match_result,
            team_scores=team_scores, b_switched=b_switched_last, accounts=accounts,
            game_type=game_type,
        )
    ]
    if b_switched_first:
        info += _enc_field(5, _enc_roundstats(accounts=accounts, b_switched=True, map_="", max_rounds=24))
    for r in rs:
        info += _enc_field(5, r)
    return info


def _enc_matchlist(matches):
    return b"".join(_enc_field(4, m) for m in matches)


MY_SID = "76561197960265729"  # account id 1


class FakeRunner:
    """Replaces subprocess.run for boiler-writter; writes the scripted file."""

    def __init__(self, *, exit_code=0, stdout="", file_bytes=b"", hang=False):
        self.exit_code = exit_code
        self.stdout = stdout
        self.file_bytes = file_bytes
        self.hang = hang
        self.calls = []

    def __call__(self, cmd, timeout):
        import types

        self.calls.append((list(cmd), timeout))
        if self.hang:
            import subprocess as _sp

            raise _sp.TimeoutExpired(cmd, timeout)
        if self.exit_code == 0 and len(cmd) > 1:
            with open(cmd[1], "wb") as fh:
                fh.write(self.file_bytes)
        return types.SimpleNamespace(returncode=self.exit_code, stdout=self.stdout, stderr="")


def _steam_provider(file_bytes=b"", stdout=f"STEAMID:{MY_SID}\n", exit_code=0, steam_id64="", hang=False):
    from sources.steam import SteamProvider

    tr = FakeTransport()
    runner = FakeRunner(file_bytes=file_bytes, stdout=stdout, exit_code=exit_code, hang=hang)
    cfg = sm.SteamConfig(steam_id64=steam_id64)
    return SteamProvider(cfg, transport=tr, runner=runner), runner, tr


def _fake_install_helper():
    from sources import steam as ssteam

    with open(ssteam.helper_path(), "wb") as fh:
        fh.write(b"MZFAKE")
    with open(ssteam._helper_marker(), "w", encoding="utf-8") as fh:
        fh.write(f"{ssteam.HELPER_VERSION} {ssteam.HELPER_SHA256}")


def test_steam_gc_parse():
    print("== steam: gc protobuf ==")
    from sources import steam_gc

    accounts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    data = _enc_matchlist([
        _enc_match(7001, 1730000000, accounts, match_result=1, team_scores=(13, 5)),
        _enc_match(7002, 1730000100, accounts, match_result=2, team_scores=(16, 14), b_switched_last=True),
    ])
    matches = steam_gc.parse_matchlist(data)
    check("two matches parsed", len(matches) == 2)
    m1 = matches[0]
    check("matchid/time", m1.match_id == 7001 and m1.matchtime == 1730000000)
    check("map from watchable", m1.game_map == "de_mirage" and m1.tv_port == 27020)
    check("demo url sits in the last roundstats map", m1.last_round().map == "http://replay129.valve.net/730/1_2.dem.bz2")
    check("accounts parsed (packed)", m1.last_round().account_ids == accounts)
    check("team scores parsed", m1.last_round().team_scores == [13, 5])
    check("match result parsed", m1.last_round().match_result == 1)
    check("winners group is the match_result index",
          steam_gc.winners_group(1) == 0 and steam_gc.winners_group(2) == 1 and steam_gc.winners_group(0) is None)

    # unpacked repeated (one field per varint) must parse the same
    unpacked = _enc_field(1, 1) + _enc_field(1, 2) + _enc_field(1, 3)
    rs = steam_gc._parse_roundstats(_enc_field(2, unpacked + _enc_field(2, 8)))
    check("unpacked repeated accepted", rs.account_ids == [1, 2, 3] and rs.game_type == 8)

    for bad, why in ((b"", "empty"), (b"\x08", "truncated"), (b"\xff" * 9, "varint too long"), (b"\x0f", "wire type 7")):
        try:
            steam_gc.parse_matchlist(bad)
            check(f"garbage ({why}) -> ProviderError", False)
        except sm.ProviderError as e:
            check(f"garbage ({why}) -> ProviderError", e.key == "src_err_gc")


def test_steam_records():
    print("== steam: records, win/loss ==")
    accounts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    import time as _time

    with _DataDir():
        _fake_install_helper()
        now = int(_time.time()) - 600
        data = _enc_matchlist([
            # my account (1) in slots 0-4, match_result=1 (that team won): win, score (13, 5)
            _enc_match(7001, now, accounts, match_result=1, team_scores=(13, 5)),
            # match_result=2 (the slots 5-9 team won): loss, score (7, 13); the
            # first-round flag marks Premier
            _enc_match(7002, now - 100, accounts, match_result=2, team_scores=(7, 13), b_switched_last=True, b_switched_first=True),
            # draw
            _enc_match(7003, now - 200, accounts, match_result=3, team_scores=(12, 12)),
            # wingman: 4 accounts, my team won
            _enc_match(7004, now - 300, [1, 2, 11, 12], match_result=1, team_scores=(9, 4)),
            # very old match -> expired
            _enc_match(7005, now - 40 * 86400, accounts, match_result=1, team_scores=(13, 2)),
            # real captures carry no watchable game_map: the map comes from
            # the reservation game_type (0x1008 = competitive on de_inferno)
            _enc_match(7006, now - 400, accounts, match_result=1, team_scores=(13, 8), game_map="", game_type=4104),
        ])
        prov, runner, _ = _steam_provider(file_bytes=data)
        recs = prov.list_matches(10)
        check("six steam records", len(recs) == 6, len(recs))
        r1, r2, r3, r4, r5, r6 = recs
        check("group0 wins with match_result=1", r1.result == "win" and r1.score == (13, 5), (r1.result, r1.score))
        check("match_result=2 -> my loss", r2.result == "loss" and r2.score == (7, 13), (r2.result, r2.score))
        check("premier detected by the first roundstats flag", r2.mode == "Premier", r2.mode)
        check("draw -> tie", r3.result == "tie" and r3.score == (12, 12))
        check("wingman mode for <= 4 players", r4.mode == "Wingman", r4.mode)
        check("competitive default", r1.mode == "Competitive")
        check("old match -> expired", r5.demo == sm.DemoState.EXPIRED)
        check("fresh match with url -> ready", r1.demo == sm.DemoState.READY)
        check("map carried", r1.map_name == "de_mirage")
        check("map from the reservation game_type", r6.map_name == "de_inferno", r6.map_name)
        check("share code in extra", r1.extra.get("share_code", "").startswith("CSGO-"))
        check("runner got [exe, out]", len(runner.calls) == 1 and runner.calls[0][0][1].endswith("matches.info"))

        # my account unknown -> result/score None but the list still shows
        data2 = _enc_matchlist([_enc_match(7001, now, accounts, match_result=1, team_scores=(13, 5))])
        prov2, _, _ = _steam_provider(file_bytes=data2, stdout="")
        rec = prov2.list_matches(10)[0]
        check("no steamid -> no result, raw scores in extra", rec.result is None and rec.score is None and rec.extra["team_scores"] == [13, 5])

        # steamid from the config when the helper did not report one
        prov3, _, _ = _steam_provider(file_bytes=data2, stdout="", steam_id64=MY_SID)
        rec3 = prov3.list_matches(10)[0]
        check("steamid from the config works", rec3.result == "win", rec3.result)


def test_steam_helper_errors():
    print("== steam: helper errors ==")
    with _DataDir():
        _fake_install_helper()
        prov, _, _ = _steam_provider(exit_code=6)
        try:
            prov.list_matches(5)
            check("exit 6 -> Steam not running", False)
        except sm.HelperError as e:
            check("exit 6 -> Steam not running", e.key == "src_err_helper_steam")
        prov2, _, _ = _steam_provider(exit_code=8)
        try:
            prov2.list_matches(5)
            check("exit 8 -> no matches", False)
        except sm.HelperError as e:
            check("exit 8 -> no matches", e.key == "src_err_helper_none")
        prov3, _, _ = _steam_provider(file_bytes=b"", exit_code=0)
        try:
            prov3.list_matches(5)
            check("empty file -> ProviderError", False)
        except sm.ProviderError as e:
            check("empty file -> ProviderError", e.key == "src_err_gc")
        prov4, _, _ = _steam_provider(hang=True)
        try:
            prov4.list_matches(5)
            check("timeout -> friendly error", False)
        except sm.ProviderError as e:
            check("timeout -> friendly error", e.key == "src_err_helper_comm")


def test_steam_sharecode():
    print("== steam: share codes ==")
    from sources import steam as ssteam

    # reference: ValvePython/csgo sharecode.py, verbatim semantics
    dictionary = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"

    def _vp_decode(code):
        import re as _re

        if not _re.match(r"^(CSGO)?(-?[%s]{5}){5}$" % dictionary, code):
            raise ValueError("Invalid share code")
        c = _re.sub("CSGO-|-", "", code)[::-1]
        a = 0
        for ch in c:
            a = a * len(dictionary) + dictionary.index(ch)
        r = 0
        for n in range(0, 144, 8):
            r = (r << 8) + ((a >> n) & 0xFF)
        return {"matchid": r & (2**64 - 1), "outcomeid": (r >> 64) & (2**64 - 1), "token": (r >> 128) & 0xFFFF}

    rng = iter(__import__("random").Random(20261114).getrandbits(64) for _ in iter(int, 1))
    ok_roundtrip = True
    ok_reference = True
    for i in range(200):
        mid, oid, tv = next(rng), next(rng), next(rng) & 0xFFFF
        code = ssteam.encode_share_code(mid, oid, tv)
        back = ssteam.decode_share_code(code)
        if back != (mid, oid, tv):
            ok_roundtrip = False
            break
        if _vp_decode(code) != {"matchid": mid, "outcomeid": oid, "token": tv}:
            ok_reference = False
            break
    check("share code round-trip (200 random)", ok_roundtrip)
    check("share code matches the ValvePython reference", ok_reference)

    for bad in ("", "CSGO-aaaaa-aaaaa-aaaaa-aaaaa-aaaa", "CSGO-bMGEk-GdDzw-bTO2R-hbGti-bdOe", "XXXX-bMGEk-GdDzw-bTO2R-hbGti-bdOeO"):
        try:
            ssteam.decode_share_code(bad)
            check(f"rejects {bad[:20]!r}", False)
        except sm.ShareCodeError:
            check(f"rejects {bad[:20]!r}", True)

    # match_by_share_code feeds the decoded ids to the helper
    with _DataDir():
        _fake_install_helper()
        accounts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        data = _enc_matchlist([_enc_match(987654321, 1730000000, accounts, match_result=1, team_scores=(13, 9))])
        code = ssteam.encode_share_code(987654321, 101, 27020)
        prov, runner, _ = _steam_provider(file_bytes=data)
        rec = prov.match_by_share_code(code)
        check("share code match decoded", rec.match_id == "987654321")
        args = runner.calls[0][0][2:]
        check("helper got matchId outcomeId tokenId", args == ["987654321", "101", "27020"], args)


def test_steam_helper_install():
    print("== steam: helper install ==")
    import zipfile as _zf

    from sources import steam as ssteam

    with _DataDir():
        buf = io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("bin/", "")
            zf.writestr("bin/boiler-writter.exe", b"MZFAKEEXE")
            zf.writestr("bin/steam_api64.dll", b"DLL")
            zf.writestr("bin/steam_appid.txt", b"730")
        good_zip = buf.getvalue()
        import hashlib as _hl

        good_sha = _hl.sha256(good_zip).hexdigest()
        tr = FakeTransport()
        tr.download = lambda url, dest, **kw: open(dest, "wb").write(good_zip) or len(good_zip)  # noqa: E731

        orig_sha = ssteam.HELPER_SHA256
        try:
            # wrong pinned hash -> nothing installed
            ssteam.HELPER_SHA256 = "0" * 64
            try:
                ssteam.install_helper(tr)
                check("hash mismatch refuses to install", False)
            except sm.ProviderError as e:
                check("hash mismatch refuses to install", e.key == "mt_helper_bad_hash")
            check("nothing extracted on mismatch", not os.path.exists(ssteam.helper_path()) and not ssteam.helper_installed())

            # correct hash -> exe + dll + marker
            ssteam.HELPER_SHA256 = good_sha
            path = ssteam.install_helper(tr)
            check("exe extracted next to its dll", os.path.isfile(path) and os.path.isfile(os.path.join(store.tools_dir(), "steam_api64.dll")))
            check("installed marker matches version+sha", ssteam.helper_installed())
            with open(ssteam.helper_path(), "rb") as fh:
                check("exe bytes intact", fh.read() == b"MZFAKEEXE")
        finally:
            ssteam.HELPER_SHA256 = orig_sha

        # the pinned upstream hash is the real v1.7.0 zip hash (verified
        # against the release page when the pin was made)
        check(
            "pinned helper constants",
            ssteam.HELPER_VERSION == "1.7.0"
            and ssteam.HELPER_URL.startswith("https://github.com/akiver/boiler-writter/releases/download/v1.7.0/"),
        )

        # provider refuses to run without the helper
        prov, _, _ = _steam_provider(file_bytes=b"x")
        try:
            prov.list_matches(5)
            check("provider requires the helper", False)
        except sm.ProviderError as e:
            check("provider requires the helper", e.key == "src_err_no_helper")


def test_steam_download():
    print("== steam: demo download ==")
    with _DataDir():
        import hashlib as _hl
        import zipfile as _zf

        from sources import steam as ssteam

        buf = io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("bin/boiler-writter.exe", b"MZ")
            zf.writestr("bin/steam_api64.dll", b"D")
            zf.writestr("bin/steam_appid.txt", b"730")
        good = buf.getvalue()
        ssteam.HELPER_SHA256 = _hl.sha256(good).hexdigest()
        tr = FakeTransport()
        tr.download = lambda url, dest, **kw: open(dest, "wb").write(good) or len(good)  # noqa: E731
        ssteam.install_helper(tr)

        accounts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        data = _enc_matchlist([_enc_match(7001, 1730000000, accounts, match_result=2, team_scores=(9, 13))])
        prov, runner, tr2 = _steam_provider(file_bytes=data)

        def _dl(url, dest, *, headers=None, progress=None, cancel=None):
            tr2.downloads.append(url)
            payload = b"STEAMDEMO" * 1000
            with open(dest, "wb") as fh:
                fh.write(payload)
            if progress:
                progress(len(payload), len(payload))
            return len(payload)

        tr2.download = _dl
        recs = prov.list_matches(5)
        seen = []
        entry = prov.fetch_demo(recs[0], progress=lambda d, t: seen.append((d, t)))
        check("demo downloaded into the library", store.get_demo(entry["id"]) is not None)
        check("file named steam_<matchid>.dem.bz2", entry["file"].startswith("steam_7001") and entry["file"].endswith(".dem.bz2"), entry["file"])
        check("the valve replay url was used", tr2.downloads == ["http://replay129.valve.net/730/1_2.dem.bz2"], tr2.downloads)
        check("progress fired", seen and seen[-1][0] == 9000, seen)
        ssteam.HELPER_SHA256 = "eda6ce361215c4c0427332e55811ffd621755b264ae7df0f5385ef5916331141"


def test_steam_webapi_sharecode():
    print("== steam: web api next share code ==")
    from sources import steam as ssteam

    tr = FakeTransport()
    url_base = "https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
    tr.add("GET", f"{url_base}?key=K&steamid=765&steamidkey=auth&knowncode=CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
           {"result": {"nextcode": "CSGO-FFFFF-GGGGG-HHHHH-IIIII-JJJJJ"}})
    got = ssteam.next_share_code(tr, "K", "765", "auth", "CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
    check("next code returned", got == "CSGO-FFFFF-GGGGG-HHHHH-IIIII-JJJJJ")
    tr.add("GET", f"{url_base}?key=K&steamid=765&steamidkey=auth&knowncode=CSGO-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ",
           {"result": {}})
    check("empty result -> None", ssteam.next_share_code(tr, "K", "765", "auth", "CSGO-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ") is None)
    tr.add("GET", f"{url_base}?key=BAD&steamid=765&steamidkey=auth&knowncode=x", _http_error(403))
    try:
        ssteam.next_share_code(tr, "BAD", "765", "auth", "x")
        check("403 -> AuthError", False)
    except sm.AuthError:
        check("403 -> AuthError", True)


# ------------------------------------------------------------- inbox tests
def test_inbox():
    print("== inbox: replays folder ==")
    import time as _time

    from sources import inbox

    with _DataDir():
        rep = os.path.join(store.data_dir(), "replays")
        os.makedirs(rep, exist_ok=True)
        dem = os.path.join(rep, "match730_003699338391708905193_1528262269.dem")
        with open(dem, "wb") as fh:
            fh.write(b"REPLAY")
        os.utime(dem, (_time.time() - 7200,) * 2)
        with open(dem + ".info", "w", encoding="utf-8") as fh:
            json.dump({"demo": {"url": "http://replay128.valve.net/730/003699338391708905193_1528262269.dem.bz2"}}, fh)
        recs = inbox.replays_records(replays_dir=rep)
        check("one replays record", len(recs) == 1, [r.match_id for r in recs])
        r = recs[0]
        check("match id from the replay url", r.match_id == "003699338391708905193", r.match_id)
        check("state is on-disk, path carried", r.demo == sm.DemoState.MANUAL and r.demo_ref == dem)
        check("mtime became played_at", r.played_at is not None and r.played_at.tzinfo is not None)

        # a .dem without a parseable sidecar keeps a stable pseudo id
        os.remove(dem + ".info")
        with open(dem + ".info", "w", encoding="utf-8") as fh:
            fh.write("{broken json")
        recs2 = inbox.replays_records(replays_dir=rep)
        check("broken sidecar -> pseudo id, no crash", len(recs2) == 1 and recs2[0].match_id.startswith("replays:"), [r.match_id for r in recs2])

        # info carrying an explicit match id
        with open(dem + ".info", "w", encoding="utf-8") as fh:
            json.dump({"match": {"matchId": 42}, "demo": {}}, fh)
        recs3 = inbox.replays_records(replays_dir=rep)
        check("explicit matchId wins", len(recs3) == 1 and recs3[0].match_id == "42")

        check("override folder must exist", inbox.cs2_replays_dirs(override=rep) == [rep])
        check("non-windows default scan stays empty here", inbox.cs2_replays_dirs() == [] or os.name == "nt")

        # a folder the user emptied of demos yields no records
        empty = os.path.join(store.data_dir(), "empty-replays")
        os.makedirs(empty, exist_ok=True)
        check("empty folder -> no records", inbox.replays_records(replays_dir=empty) == [])


def test_no_sockets_in_app():
    """Opening the app on the Matches page must not open a network socket.

    Streamlit's AppTest itself creates two AF_UNIX sockets for its asyncio
    self-pipe; only internet-family sockets and urlopen count as network.
    """
    print("== sources: privacy (app start) ==")
    calls = []

    class _NoInet(socket.socket):
        def __init__(self, *a, **kw):
            family = a[0] if a else kw.get("family")
            if family not in (socket.AF_INET, socket.AF_INET6):
                super().__init__(*a, **kw)
                return
            calls.append(("socket", family))
            super().__init__(*a, **kw)

    orig_socket = socket.socket
    orig_urlopen = urllib.request.urlopen
    socket.socket = _NoInet
    urllib.request.urlopen = lambda *a, **kw: calls.append(("urlopen", None))
    try:
        from streamlit.testing.v1 import AppTest

        with _DataDir():
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
            at.session_state["page"] = "matches"
            at.run()
            clean = not at.exception
        check("the Matches page renders without exceptions", clean)
        check("no network sockets while the app runs", not calls, calls)
    finally:
        socket.socket = orig_socket
        urllib.request.urlopen = orig_urlopen


def main():
    test_offline_gate()
    test_scheme_gate()
    test_json_retries()
    test_download_stream()
    test_store_files()
    test_no_sockets_on_import()
    test_steam_gc_parse()
    test_steam_records()
    test_steam_helper_errors()
    test_steam_sharecode()
    test_steam_helper_install()
    test_steam_download()
    test_steam_webapi_sharecode()
    test_inbox()
    test_no_sockets_in_app()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
