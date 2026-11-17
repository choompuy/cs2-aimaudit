"""
Steam / Valve Matchmaking provider.

Three paths, newest first:

1. boiler-writter (github.com/akiver/boiler-writter, MIT): a console tool
   that asks the RUNNING Steam client's Game Coordinator for
   CMsgGCCStrike15_v2_MatchList and writes it to a file. No Steam password
   is ever involved; for a few seconds the Steam status shows CS2 running.
   The helper is installed only by an explicit button press: a pinned
   release is downloaded from GitHub, verified against a pinned SHA-256 and
   unpacked into data/tools/.
2. A share code (CSGO-…) for a specific (possibly older) match: decoded to
   (matchId, outcomeId, tokenId) and fed to the same helper.
3. The CS2 replays folder (Watch -> Your Matches), scanned by inbox.py -
   no network, no credentials.

Demo links live on Valve's replay servers for about a month and may be
plain http:// - HttpTransport only accepts them for the exact
replay<N>.valve.net pattern.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

import store
import sources.steam_gc as steam_gc
from sources.model import (
    DEMO_KEEP_DAYS,
    DemoState,
    HelperError,
    MatchRecord,
    ProviderError,
    ShareCodeError,
    SteamConfig,
)

HELPER_VERSION = "1.7.0"
HELPER_URL = (
    "https://github.com/akiver/boiler-writter/releases/download/"
    f"v{HELPER_VERSION}/boiler-writter-win-{HELPER_VERSION}.zip"
)
HELPER_SHA256 = "eda6ce361215c4c0427332e55811ffd621755b264ae7df0f5385ef5916331141"
HELPER_TIMEOUT_S = 40

# The map id sits in the upper 24 bits of the reservation's game_type
# (hard-coded in CS, the table matches CS Demo Manager's); the low byte is
# the mode: 8 = competitive 5v5, 10 = wingman
_MAP_BY_ID = {
    1 << 0: "de_warden",
    1 << 1: "de_dust2",
    1 << 2: "de_train",
    1 << 3: "de_ancient",
    1 << 4: "de_inferno",
    1 << 5: "de_nuke",
    1 << 6: "de_vertigo",
    1 << 7: ("de_mirage", "de_debris"),
    1 << 8: "cs_office",
    1 << 9: "de_poseidon",
    1 << 10: "de_eldorado",
    1 << 11: "de_sanctum",
    1 << 12: "de_cache",
    1 << 13: "de_stronghold",
    1 << 14: "de_boulder",
    1 << 15: "de_anubis",
    1 << 16: "de_tuscan",
    1 << 18: "de_fachwerk",
    1 << 19: "cs_shelter",
    1 << 20: "de_overpass",
    1 << 21: "de_cobblestone",
    1 << 22: "de_canals",
}


def _map_from_game_type(game_type: int, players: int) -> str | None:
    map_id = (game_type >> 8) & 0xFFFFFF
    if not map_id:
        return None
    name = _MAP_BY_ID.get(map_id)
    if isinstance(name, tuple):
        return name[1] if game_type & 0xFF == 10 or players <= 4 else name[0]
    return name


# ------------------------------------------------------------- share codes
_SHARE_DICT = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"
_SHARE_RE = re.compile(r"^CSGO(-?["
                       + re.escape(_SHARE_DICT) + r"]{5}){5}$")


def encode_share_code(match_id: int, reservation_id: int, tv_port: int) -> str:
    blob = (
        int(match_id).to_bytes(8, "little")
        + int(reservation_id).to_bytes(8, "little")
        + (int(tv_port) & 0xFFFF).to_bytes(2, "little")
    )
    n = int.from_bytes(blob, "big")
    chars = []
    for _ in range(25):
        n, rem = divmod(n, len(_SHARE_DICT))
        chars.append(_SHARE_DICT[rem])
    s = "".join(chars)
    return f"CSGO-{s[0:5]}-{s[5:10]}-{s[10:15]}-{s[15:20]}-{s[20:25]}"


def decode_share_code(code: str) -> tuple[int, int, int]:
    """'CSGO-…' -> (match_id, reservation_id, tv_port)."""
    s = (code or "").strip()
    if not _SHARE_RE.match(s):
        raise ShareCodeError(f"bad share code {code!r}")
    digits = s[len("CSGO") :].replace("-", "")
    n = 0
    for ch in reversed(digits):
        n = n * len(_SHARE_DICT) + _SHARE_DICT.index(ch)
    blob = n.to_bytes(18, "big")
    return (
        int.from_bytes(blob[0:8], "little"),
        int.from_bytes(blob[8:16], "little"),
        int.from_bytes(blob[16:18], "little"),
    )


# ------------------------------------------------------------------ helper
def helper_path() -> str:
    return os.path.join(store.tools_dir(), "boiler-writter.exe")


def _helper_marker() -> str:
    return os.path.join(store.tools_dir(), ".boiler-writter.version")


def helper_installed() -> bool:
    try:
        with open(_helper_marker(), encoding="utf-8") as fh:
            return fh.read().strip() == f"{HELPER_VERSION} {HELPER_SHA256}"
    except OSError:
        return False


def install_helper(transport) -> str:
    """Download + verify + unpack the helper; returns the exe path."""
    from sources.http import _check_offline

    _check_offline()
    dest_zip = os.path.join(store.tools_dir(), ".boiler-writter.zip.part")
    transport.download(HELPER_URL, dest_zip)
    try:
        h = hashlib.sha256()
        with open(dest_zip, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != HELPER_SHA256:
            raise ProviderError("sha256 mismatch", key="mt_helper_bad_hash")
        with zipfile.ZipFile(dest_zip) as zf:
            members = [m for m in zf.namelist() if re.match(r"^bin/[^/]+$", m)]
            if not members:
                raise ProviderError("no bin/ in the archive", key="src_err_no_helper")
            for m in members:
                with zf.open(m) as src, open(
                    os.path.join(store.tools_dir(), os.path.basename(m)), "wb"
                ) as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        with open(_helper_marker(), "w", encoding="utf-8") as fh:
            fh.write(f"{HELPER_VERSION} {HELPER_SHA256}")
        if not os.path.isfile(helper_path()):
            raise ProviderError("boiler-writter.exe missing after unpack", key="src_err_no_helper")
        return helper_path()
    finally:
        try:
            os.remove(dest_zip)
        except OSError:
            pass


def default_runner(cmd: list[str], timeout: float):
    """subprocess.run with no console window popping up on Windows."""
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags
    )


def _steamid_from_stdout(stdout: str) -> str | None:
    for line in (stdout or "").splitlines():
        if line.startswith("STEAMID:"):
            sid = line[len("STEAMID:"):].strip()
            if sid.isdigit():
                return sid
    return None


# ---------------------------------------------------------------- provider
class SteamProvider:
    def __init__(self, cfg: SteamConfig, *, transport, runner=default_runner):
        self.cfg = cfg
        self.transport = transport
        self.runner = runner

    # ----------------------------------------------------------- plumbing
    def _require_helper(self) -> str:
        if not helper_installed() or not os.path.isfile(helper_path()):
            raise ProviderError("helper not installed", key="src_err_no_helper")
        return helper_path()

    def _fetch_matchlist(self, args: list[str] | None = None) -> tuple[bytes, str | None]:
        exe = self._require_helper()
        out = os.path.join(store.tools_dir(), "matches.info")
        try:
            os.remove(out)
        except OSError:
            pass
        cmd = [exe, out] + [str(a) for a in (args or [])]
        try:
            proc = self.runner(cmd, HELPER_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ProviderError(f"{type(e).__name__}: {e}", key="src_err_helper_comm") from e
        code = getattr(proc, "returncode", 1)
        if code != 0:
            raise HelperError(code)
        steamid = _steamid_from_stdout(getattr(proc, "stdout", "") or "")
        try:
            with open(out, "rb") as fh:
                data = fh.read()
        except OSError:
            raise ProviderError("matches.info missing", key="src_err_helper_write") from None
        return data, steamid

    # --------------------------------------------------------------- list
    def list_matches(self, n: int) -> list[MatchRecord]:
        data, steamid = self._fetch_matchlist()
        return self._records(steam_gc.parse_matchlist(data), steamid)[:n]

    def match_by_share_code(self, code: str) -> MatchRecord:
        match_id, outcome_id, token_id = decode_share_code(code)
        data, steamid = self._fetch_matchlist([match_id, outcome_id, token_id])
        matches = steam_gc.parse_matchlist(data)
        if not matches:
            raise ProviderError("no matches", key="src_err_helper_none")
        rec = self._records(matches, steamid)[0]
        return rec

    # ----------------------------------------------------------- records
    def _records(self, matches: list[steam_gc.GcMatch], steamid: str | None) -> list[MatchRecord]:
        my_account = None
        for sid in (steamid, self.cfg.steam_id64):
            if sid:
                my_account = steam_gc.steamid64_to_account_id(sid)
                if my_account is not None:
                    break
        out = []
        for m in matches:
            rec = self._record(m, my_account)
            if rec is not None:
                out.append(rec)
        return out

    def _record(self, m: steam_gc.GcMatch, my_account: int | None) -> MatchRecord | None:
        if not m.match_id:
            return None
        last = m.last_round()
        first = m.first_round()
        played = (
            datetime.fromtimestamp(m.matchtime, tz=timezone.utc) if m.matchtime else None
        )
        accounts = (last.account_ids if last else []) or []
        players = [a for a in accounts if a]
        mode = None
        if players and len(players) <= 4:
            mode = "Wingman"
        elif first is not None and first.b_switched_teams:
            mode = "Premier"
        else:
            mode = "Competitive"
        result = None
        score = None
        if last is not None and my_account is not None:
            grp = steam_gc.my_group(accounts, my_account)
            winners = steam_gc.winners_group(last.match_result)
            if grp is not None:
                ts = last.team_scores or []
                if len(ts) >= 2:
                    score = (ts[grp], ts[1 - grp])
                if winners is None:
                    result = "tie" if last.match_result in (0, 3) else None
                else:
                    result = "win" if grp == winners else "loss"
        demo_url = (last.map if last else "") or ""
        demo_ref = demo_url if demo_url.startswith("http") else None
        age = None
        if played is not None:
            age = (datetime.now(timezone.utc) - played).total_seconds() / 86400.0
        if age is not None and age > DEMO_KEEP_DAYS:
            demo = DemoState.EXPIRED
        elif demo_ref:
            demo = DemoState.READY
        else:
            demo = DemoState.UNKNOWN
        map_name = m.game_map or None
        if not map_name and last is not None:
            map_name = _map_from_game_type(last.game_type, len(players))
        extra = {"players": len(players)}
        if last is not None:
            extra["duration_s"] = last.match_duration
            extra["team_scores"] = list(last.team_scores[:2])
        if m.tv_port:
            extra["share_code"] = encode_share_code(m.match_id, last.reservationid if last else 0, m.tv_port)
        return MatchRecord(
            provider="steam",
            match_id=str(m.match_id),
            played_at=played,
            map_name=map_name,
            mode=mode,
            score=score,
            result=result,
            demo=demo,
            demo_ref=demo_ref,
            page_url=None,
            extra=extra,
        )

    # ----------------------------------------------------------- download
    def fetch_demo(self, rec: MatchRecord, *, progress=None, cancel=None) -> dict:
        url = rec.demo_ref
        if not url:
            raise ProviderError("no demo url", key="src_err_no_demo")
        ext = ".dem.bz2"
        low = url.lower()
        for candidate in (".dem.bz2", ".dem.gz", ".dem.zst", ".dem.zip", ".dem"):
            if low.endswith(candidate):
                ext = candidate
                break
        dest = os.path.join(
            store.demos_dir(), f".incoming-{os.urandom(4).hex()}.steam"
        )
        try:
            self.transport.download(url, dest, progress=progress, cancel=cancel)
            return store.add_demo(dest, f"steam_{rec.match_id}{ext}")
        finally:
            try:
                os.remove(dest)
            except OSError:
                pass


# ----------------------------------------------------- Web API (optional)
def next_share_code(transport, api_key: str, steamid64: str, auth_code: str, known_code: str) -> str | None:
    """
    Steam Web API GetNextMatchSharingCode: the share code after `known_code`.

    Returns None at the end of the history (HTTP 202). A bad known code raises
    ShareCodeError; a bad auth code or an unavailable backend raises AuthError
    without retry storms (the transport's retries are bounded to 3 attempts).

    Deviation from the app's "keys never in URLs" rule, documented on purpose:
    Valve's Web API has no header authentication - the key can only travel as
    a query parameter. This optional helper is not reachable from the UI.
    """
    from sources.model import AuthError

    url = (
        "https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
        f"?key={api_key}&steamid={steamid64}"
        f"&steamidkey={auth_code}&knowncode={known_code}"
    )
    try:
        data = transport.get_json(url)
    except ProviderError as e:
        if isinstance(e, AuthError):
            raise
        raise ShareCodeError(str(e)) from e
    code = ((data.get("result") or {}).get("nextcode")) or ""
    return code or None
