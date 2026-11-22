"""
A minimal protobuf wire-format reader for CMsgGCCStrike15_v2_MatchList.

boiler-writter writes the raw GC message; this module pulls out what the
Matches page needs without a protobuf dependency. Field numbers come from
the csgo proto definition
(https://github.com/SteamDatabase/Protobufs, csgo/cstrike15_gcmessages.proto):

    CMsgGCCStrike15_v2_MatchList
      4  repeated CDataGCCStrike15_v2_MatchInfo matches
    CDataGCCStrike15_v2_MatchInfo
      1  uint64 matchid, 2 uint32 matchtime,
      3  WatchableMatchInfo watchablematchinfo,
      4  roundstats_legacy, 5 repeated roundstatsall
    WatchableMatchInfo
      1 uint32 server_ip, 2 uint32 tv_port, 8 uint32 game_type,
      9 string game_mapgroup, 10 string game_map, 12 uint64 match_id
    CMsgGCCStrike15_v2_MatchmakingServerRoundStats
      1 uint64 reservationid, 2 reservation, 3 string map, 4 int32 round,
      11 int32 match_result, 12 repeated int32 team_scores,
      15 int32 match_duration, 27 bool b_switched_teams, 31 uint32 max_rounds
    CMsgGCCStrike15_v2_MatchmakingGC2ServerReserve
      1 repeated uint32 account_ids, 2 uint32 game_type

Reading notes (verified against real MatchList captures - the CSDM
fixtures and the per-player MVP sums, which equal the score of the
player's slot group):
- the demo URL sits in the `map` field of the LAST roundstats entry;
- team_scores[0] is the score of the accounts in slots 0-4 of the same
  round's reservation, team_scores[1] the other team's;
- match_result is a slot-team index: 1 = the slots 0-4 team won,
  2 = the slots 5-9 team won, 0/3 = draw. It does NOT depend on
  b_switched_teams: Valve never sends field 27 in match lists (real
  captures 2016-2024 leave it out entirely, and in 2026 Premier data it
  is set on every round including the first, so it only works as a
  Premier hint);
- the map comes from the reservation's game_type of the last roundstats
  entry: the low byte is the mode (8 competitive, 10 wingman), the upper
  bits the map id (hard-coded in the game).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sources.model import ProviderError

_WT_VARINT = 0
_WT_I64 = 1
_WT_LEN = 2
_WT_I32 = 5


def _parse_error(detail: str) -> ProviderError:
    return ProviderError(detail, key="src_err_gc")


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        if i >= len(buf):
            raise _parse_error("truncated varint")
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            raise _parse_error("varint too long")


def _signed32(v: int) -> int:
    return v - (1 << 32) if v >= (1 << 31) else v


def _fields(buf: bytes):
    """Yield (field_number, wire_type, value): int for varint, bytes for LEN."""
    i = 0
    n = len(buf)
    while i < n:
        key, i = _varint(buf, i)
        fno, wt = key >> 3, key & 7
        if fno == 0:
            raise _parse_error("field number 0")
        if wt == _WT_VARINT:
            value, i = _varint(buf, i)
        elif wt == _WT_I64:
            if i + 8 > n:
                raise _parse_error("truncated i64")
            value = int.from_bytes(buf[i : i + 8], "little")
            i += 8
        elif wt == _WT_LEN:
            ln, i = _varint(buf, i)
            if ln > n - i:
                raise _parse_error("length beyond buffer")
            value = buf[i : i + ln]
            i += ln
        elif wt == _WT_I32:
            if i + 4 > n:
                raise _parse_error("truncated i32")
            value = int.from_bytes(buf[i : i + 4], "little")
            i += 4
        else:
            raise _parse_error(f"wire type {wt}")
        yield fno, wt, value


def _packed_varints(blob: bytes) -> list[int]:
    out = []
    i = 0
    while i < len(blob):
        v, i = _varint(blob, i)
        out.append(v)
    return out


def _repeated_uint32(wt: int, value, acc: list) -> None:
    """A repeated varint field may arrive packed (one LEN blob) or unpacked."""
    if wt == _WT_LEN:
        acc.extend(_packed_varints(value))
    elif wt == _WT_VARINT:
        acc.append(value)


def _str(b: bytes) -> str:
    return b.decode("utf-8", "replace")


@dataclass
class RoundStats:
    reservationid: int = 0
    map: str = ""
    match_result: int = 0
    team_scores: list[int] = field(default_factory=list)
    match_duration: int = 0
    b_switched_teams: bool = False
    max_rounds: int = 0
    account_ids: list[int] = field(default_factory=list)
    game_type: int = 0


@dataclass
class GcMatch:
    match_id: int = 0
    matchtime: int = 0
    game_map: str = ""
    game_mapgroup: str = ""
    game_type: int = 0
    server_ip: int = 0
    tv_port: int = 0
    roundstatsall: list[RoundStats] = field(default_factory=list)
    roundstats_legacy: RoundStats | None = None

    def last_round(self) -> RoundStats | None:
        # the same precedence CS Demo Manager uses
        return self.roundstats_legacy or (
            self.roundstatsall[-1] if self.roundstatsall else None
        )

    def first_round(self) -> RoundStats | None:
        return self.roundstatsall[0] if self.roundstatsall else self.roundstats_legacy


def _parse_reservation(value: bytes, rs: RoundStats) -> None:
    """Roundstats field 2: MatchmakingGC2ServerReserve (account ids,
    game type)."""
    for f2, w2, v2 in _fields(value):
        if f2 == 1:
            _repeated_uint32(w2, v2, rs.account_ids)
        elif f2 == 2 and w2 == _WT_VARINT:
            rs.game_type = v2


def _parse_team_scores(wt: int, value, rs: RoundStats) -> None:
    """Roundstats field 12: team scores as a packed list or a single
    varint."""
    if wt == _WT_LEN:
        rs.team_scores.extend(_signed32(v) for v in _packed_varints(value))
    elif wt == _WT_VARINT:
        rs.team_scores.append(_signed32(value))


def _parse_roundstats(buf: bytes) -> RoundStats:
    rs = RoundStats()
    for fno, wt, value in _fields(buf):
        if fno == 1 and wt == _WT_VARINT:
            rs.reservationid = value
        elif fno == 2 and wt == _WT_LEN:  # MatchmakingGC2ServerReserve
            _parse_reservation(value, rs)
        elif fno == 3 and wt == _WT_LEN:
            rs.map = _str(value)
        elif fno == 11 and wt == _WT_VARINT:
            rs.match_result = _signed32(value)
        elif fno == 12:
            _parse_team_scores(wt, value, rs)
        elif fno == 15 and wt == _WT_VARINT:
            rs.match_duration = _signed32(value)
        elif fno == 27 and wt == _WT_VARINT:
            rs.b_switched_teams = bool(value)
        elif fno == 31 and wt == _WT_VARINT:
            rs.max_rounds = value
    return rs


def _parse_watchable(buf: bytes, m: GcMatch) -> None:
    for fno, wt, value in _fields(buf):
        if fno == 1 and wt == _WT_VARINT:
            m.server_ip = value
        elif fno == 2 and wt == _WT_VARINT:
            m.tv_port = value
        elif fno == 8 and wt == _WT_VARINT:
            m.game_type = value
        elif fno == 9 and wt == _WT_LEN:
            m.game_mapgroup = _str(value)
        elif fno == 10 and wt == _WT_LEN:
            m.game_map = _str(value)


def _parse_match_info(buf: bytes) -> GcMatch:
    m = GcMatch()
    for fno, wt, value in _fields(buf):
        if fno == 1 and wt == _WT_VARINT:
            m.match_id = value
        elif fno == 2 and wt == _WT_VARINT:
            m.matchtime = value
        elif fno == 3 and wt == _WT_LEN:
            _parse_watchable(value, m)
        elif fno == 4 and wt == _WT_LEN:
            m.roundstats_legacy = _parse_roundstats(value)
        elif fno == 5 and wt == _WT_LEN:
            m.roundstatsall.append(_parse_roundstats(value))
    return m


def parse_matchlist(data: bytes) -> list[GcMatch]:
    """All matches of a CMsgGCCStrike15_v2_MatchList payload."""
    if not data:
        raise _parse_error("empty match list")
    out = []
    for fno, wt, value in _fields(data):
        if fno == 4 and wt == _WT_LEN:
            out.append(_parse_match_info(value))
    return out


# ------------------------------------------------------- player-side view
def steamid64_to_account_id(steamid64: str | int) -> int | None:
    try:
        sid = int(str(steamid64).strip())
    except (TypeError, ValueError):
        return None
    if sid <= 76561197960265728:
        return None
    return sid - 76561197960265728


def my_group(account_ids: list[int], account_id: int) -> int | None:
    """0 = the first five slots (team CT at the end), 1 = the rest."""
    try:
        idx = account_ids.index(account_id)
    except ValueError:
        return None
    return 0 if idx < 5 else 1


def winners_group(match_result: int) -> int | None:
    """The winning slot group; None = draw or unknown."""
    if match_result == 1:
        return 0
    if match_result == 2:
        return 1
    return None
