"""
Demo loading: unpacking, paths, state.json, load_demo and data
preparation. Owns DemoParser.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import tempfile
import zipfile

import numpy as np
import pandas as pd
from demoparser2 import DemoParser
import store

from core.model import (
    ALIVE_PROPS,
    BTN_NAMED,
    DUCK_PROPS,
    Demo,
    NA,
    POS,
    PUNCH_PROPS,
    SIDE_PROPS,
    TEAMS,
    TICKRATE,
    _decode_buttons,
    _first_shot_windows,
    _round_col,
    _safe_int,
    _speed_from_positions,
    hg_key,
    is_gun,
    norm_weapon,
)


def build_id_lookup(info: pd.DataFrame):
    """
    From parse_player_info: (float64 -> [candidate steamids], name -> steamid).

    The float lookup may map one float to SEVERAL steamids: when a UInt64
    column with nulls becomes float64, SteamID64s (> 2^53) get rounded and
    two ids that differ in the last digits can collapse to the same float.
    The name map is used to disambiguate such collisions (event and tick
    tables always carry player names).
    """

    flookup: dict[float, list[str]] = {}
    name_to_sid: dict[str, str] = {}
    for sid, name in zip(info["steamid"], info["name"]):
        if pd.isna(sid) or int(sid) == 0:
            continue
        s = str(int(sid))
        flookup.setdefault(float(int(sid)), []).append(s)
        name_to_sid.setdefault(str(name), s)
    return flookup, name_to_sid


def fix_ids(
    s: pd.Series,
    flookup: dict[float, list[str]],
    names: pd.Series | None = None,
    name_to_sid: dict[str, str] | None = None,
) -> pd.Series:
    """
    SteamID column -> exact strings, whatever dtype it arrives in.

    demoparser2 hands event/tick SteamIDs over in several shapes: float64
    (UInt64 with nulls -> float, values > 2^53 rounded to a 16-unit grid),
    plain UInt64, and sometimes ROUNDED FLOATS hidden inside an object /
    nullable column. Converting any of those through Int64 -> str bakes the
    float rounding in: only players whose SteamID happens to be a multiple of
    16 survive, everyone else silently disappears from the stats (seen in the
    wild: a 10-player match where exactly one player kept his hits).

    So: NEVER convert through Int64. Every numeric-ish value is mapped through
    the {float64(sid): [sids]} lookup built from the exact parse_player_info
    table; the accompanying player name disambiguates collisions; anything
    else becomes NA.
    """

    def one(v, nm):
        try:
            if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
                return NA
            f = float(v)
        except (TypeError, ValueError):
            return NA
        if f == 0.0 or f != f:
            return NA
        cands = flookup.get(f)
        if not cands:
            return NA
        if len(cands) == 1:
            return cands[0]
        if nm is not None and name_to_sid:
            want = name_to_sid.get(str(nm))
            if want in cands:
                return want
        return NA

    if names is None or not name_to_sid:
        return s.map(lambda v: one(v, None)).astype(object)
    return pd.Series(
        [one(v, nm) for v, nm in zip(s, names)], index=s.index, dtype=object
    )


def _extract_punch(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize aim_punch_angle into numeric punch_pitch / punch_yaw (degrees).

    demoparser2 may return the QAngle either as one column (string "x y z" or
    a list/tuple) or pre-split into several columns; handle all shapes.
    """

    df = df.copy()
    df["punch_pitch"] = np.nan
    df["punch_yaw"] = np.nan
    cands = [c for c in df.columns if str(c).startswith("aim_punch_angle")]
    if not cands:
        return df

    def _pair(v):
        try:
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return (np.nan, np.nan)
            if isinstance(v, str):
                parts = [
                    float(x)
                    for x in v.replace(",", " ")
                    .replace("[", " ")
                    .replace("]", " ")
                    .split()
                ]
                if len(parts) >= 2:
                    return (parts[0], parts[1])
                if len(parts) == 1:
                    return (parts[0], np.nan)
                return (np.nan, np.nan)
            if isinstance(v, (list, tuple, np.ndarray)):
                a = list(v)
                if len(a) >= 2:
                    return (float(a[0]), float(a[1]))
                if len(a) == 1:
                    return (float(a[0]), np.nan)
            if isinstance(v, (int, float, np.integer, np.floating)):
                f = float(v)
                return (np.nan, np.nan) if np.isnan(f) else (f, np.nan)
        except (ValueError, TypeError):
            return (np.nan, np.nan)
        return (np.nan, np.nan)

    if len(cands) == 1:
        pairs = df[cands[0]].map(_pair)
        df["punch_pitch"] = [p[0] for p in pairs]
        df["punch_yaw"] = [p[1] for p in pairs]
    else:
        # pre-split columns: _0/_x = pitch, _1/_y = yaw
        for c in cands:
            s = str(c).lower()
            if s.endswith(("_0", "_x", "0", "x")):
                df["punch_pitch"] = pd.to_numeric(df[c], errors="coerce")
            elif s.endswith(("_1", "_y", "1", "y")):
                df["punch_yaw"] = pd.to_numeric(df[c], errors="coerce")
    return df


def _norm_alive(df: pd.DataFrame) -> pd.DataFrame:
    """is_alive -> plain bool. Missing/NaN counts as alive (no false cuts)."""
    if "is_alive" not in df.columns:
        return df
    s = df["is_alive"]
    if pd.api.types.is_bool_dtype(s):
        return df
    df = df.copy()
    df["is_alive"] = pd.to_numeric(s, errors="coerce").fillna(1).astype(bool)
    return df


def _add_speed(df: pd.DataFrame, tickrate: int = TICKRATE) -> pd.DataFrame:
    """
    Own `speed` column for the fire-tick table (see the POS note above).

    Fire ticks are parsed together with tick-1, so speed is the exact one-tick
    delta |pos(t) - pos(t-1)| * tickrate. NaN when: no t-1 row, the player was
    dead at either tick, or the delta looks like a teleport (respawn/dormancy).
    """

    df = df.copy()
    if "speed" not in df.columns:
        df["speed"] = np.nan
    if not len(df) or not {"X", "Y", "tick", "steamid"}.issubset(df.columns):
        return df
    d = df.sort_values(["steamid", "tick"], kind="stable")
    g = d.groupby("steamid", sort=False)
    dt = d["tick"].astype(float) - g["tick"].shift(1)
    dx = d["X"] - g["X"].shift(1)
    dy = d["Y"] - g["Y"].shift(1)
    ok = (dt == 1.0) & dx.notna() & dy.notna()
    if "is_alive" in d.columns:
        al = d["is_alive"].astype(float)
        gal = al.groupby(d["steamid"], sort=False)
        ok &= gal.shift(1).fillna(1.0).astype(bool) & al.astype(bool)
    d["speed"] = _speed_from_positions(d, dt, dx, dy, tickrate).where(ok)
    return d


# -------------------------------------------------------------------- load
COMPRESSED_EXTS = {".bz2", ".gz", ".zst", ".zip"}


def _zstandard_module():
    try:
        import zstandard  # optional dependency, only needed for .zst
    except ImportError as e:
        raise ValueError(
            "The .zst demo needs the 'zstandard' package: pip install zstandard"
        ) from e
    return zstandard


def _unpack(ext: str, p: str, tmp: str) -> None:
    """Stream-decompress archive `p` into file `tmp` (may raise on corrupt input)."""
    try:
        if ext == ".bz2":
            with bz2.open(p, "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif ext == ".gz":
            with gzip.open(p, "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif ext == ".zst":
            zstd = _zstandard_module()
            with open(p, "rb") as src, open(tmp, "wb") as dst:
                with zstd.ZstdDecompressor().stream_reader(src) as rdr:
                    shutil.copyfileobj(rdr, dst)
        else:  # .zip
            with zipfile.ZipFile(p) as z:
                members = sorted(n for n in z.namelist() if n.lower().endswith(".dem"))
                if not members:
                    raise ValueError(
                        f"No .dem file inside the archive: {os.path.basename(p)}"
                    )
                with z.open(members[0]) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except ValueError:
        # one of our own clear messages - just drop the partial .part
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    except Exception as e:  # noqa: BLE001 - broken archive, any library error
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ValueError(
            f"Cannot decompress {os.path.basename(p)}: {type(e).__name__}: {e}"
        ) from e


def decompressed_demo(path: str) -> str:
    """
    Path to a plain .dem for `path`, decompressing transparently.

    Demos are often delivered compressed: Valve serves .dem.bz2, FACEIT
    .dem.gz, tournaments .zip. Supported: .bz2 / .gz / .zip (stdlib) and
    .zst (needs the `zstandard` package). Inside a .zip the first *.dem
    member (alphabetically) is used. The decompressed copy is cached in
    the system temp dir under a name derived from source path + size +
    mtime, so re-opening the same archive does not decompress again.
    Anything else is returned unchanged.

    A truncated or corrupt archive raises ValueError (whatever the
    decompressor throws - EOFError, BadZipFile, ZstdError, OSError...):
    the UI only catches ValueError, and a raw traceback is not a friendly
    message. A half-written .part is never left behind either - it can be
    as large as the unpacked demo and nothing prunes the temp dir.
    """

    p = os.path.realpath(path)
    ext = os.path.splitext(p)[1].lower()
    if ext not in COMPRESSED_EXTS:
        return p
    stem = os.path.basename(p)
    stem = stem[: -len(ext)] if stem.lower().endswith(ext) else stem
    try:
        st = os.stat(p)
    except OSError as e:
        raise ValueError(f"Cannot read demo file: {p}") from e
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem)[:80]
    target = os.path.join(
        _unpack_dir(),
        f"cs2_aimaudit_{safe}_{st.st_size}_{int(st.st_mtime)}.dem",
    )
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target  # cached from an earlier run
    tmp = target + ".part"

    def _drop_part() -> None:
        try:
            os.remove(tmp)
        except OSError:
            pass

    try:
        _unpack(ext, p, tmp)
    except ValueError:
        _drop_part()
        raise
    except (
        Exception
    ) as e:  # noqa: BLE001 - truncated/corrupt archive, bad zip, zstd error...
        _drop_part()
        raise ValueError(
            f"Cannot unpack {os.path.basename(p)}: {type(e).__name__}: {e}"
        ) from e
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ValueError(f"Decompressed demo is empty: {os.path.basename(p)}")
    os.replace(tmp, target)  # atomic: no half-written cache on crash
    return target


def _unpack_dir() -> str:
    """cache/unpacked in the data folder; the system temp dir when it is read-only."""
    d = store.unpacked_dir()
    return d if os.path.isdir(d) and os.access(d, os.W_OK) else tempfile.gettempdir()


# ------------------------------------------------------------ app state
# Small local state next to the demo library (store.state_path): the last
# selected demo and player. Everything stays in the data folder; if it is not
# writable the app simply does not remember anything. A state file left by an
# older version in ~/.cs2-aimaudit is read once and then superseded.
def load_state() -> dict:
    """Load the local state file; {} when missing or unreadable."""
    for path in (store.state_path(), os.path.join(store.LEGACY_DIR, "state.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            continue
    return {}


def save_state(d: dict) -> None:
    """Atomically save the state file; silent when the data folder is read-only."""
    try:
        path = store.state_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def tickrate_from_header(header: dict) -> int:
    """
    Tick rate from the demo header: playback_ticks / playback_time.

    The previously hard-coded 64 is wrong for 128-tick demos (FACEIT and
    community servers): every speed and every seconds<->ticks conversion
    would be off by 2x. Falls back to 64 when the header is missing or
    implausible (corrupt header, partial demo).
    """

    try:
        ticks = float(header.get("playback_ticks"))
        secs = float(header.get("playback_time"))
        if ticks > 0 and secs > 0:
            tr = ticks / secs
            if 16.0 <= tr <= 512.0:  # sane window: 32 / 64 / 128 tick all fit
                return int(round(tr))
    except (TypeError, ValueError):
        pass
    return TICKRATE


def _parse_round_sides(
    parser, frames: list[pd.DataFrame], flookup: dict, name_to_sid: dict
) -> dict[int, dict[str, str]]:
    """
    Per-round player sides: team_num read at one tick per round.

    parse_player_info().team_number is a single static value (the team at
    the END of the match), so after the halftime swap - and again in every
    overtime half - the T/CT labels are wrong for half of the rounds. The
    enemy/teammate logic is unaffected (teammates share the static label),
    but anything shown per side needs the per-round value. Returns {} when
    the prop is unavailable (old demos / old parser).
    """

    per_round: dict[int, int] = {}  # round(1-based) -> representative tick
    for df in frames:
        if (
            not len(df)
            or "total_rounds_played" not in df.columns
            or "tick" not in df.columns
        ):
            continue
        r = _round_col(df)
        tk = pd.to_numeric(df["tick"], errors="coerce")
        ok = (r >= 0) & tk.notna()
        for ri, tick in zip(r[ok], tk[ok]):
            ri = int(ri) + 1
            t = int(tick)
            if ri not in per_round or t < per_round[ri]:
                per_round[ri] = t
    if not per_round:
        return {}
    try:
        sd = parser.parse_ticks(SIDE_PROPS, ticks=sorted(per_round.values()))
    except Exception:  # noqa: BLE001 - unknown prop on old parser versions
        return {}
    if sd is None or "team_num" not in getattr(sd, "columns", []) or not len(sd):
        return {}
    sd = sd.copy()
    sd["steamid"] = fix_ids(
        sd["steamid"],
        flookup,
        sd["name"] if "name" in sd.columns else None,
        name_to_sid,
    )
    tick_to_round = {t: r for r, t in per_round.items()}
    out: dict[int, dict[str, str]] = {}
    for row in sd.itertuples(index=False):
        ri = tick_to_round.get(int(row.tick))
        sid = getattr(row, "steamid", NA)
        if ri is None or sid in (NA, None):
            continue
        side = TEAMS.get(_safe_int(row.team_num), "?")
        out.setdefault(ri, {})[sid] = side
    return out


def load_demo(path: str, progress=None) -> Demo:
    say = progress or (lambda *a, **k: None)
    p = DemoParser(path)

    say("players")
    info = p.parse_player_info()
    flookup, name_to_sid = build_id_lookup(info)
    players = pd.DataFrame(
        {
            "sid": info["steamid"]
            .astype("object")
            .map(lambda x: str(int(x)) if pd.notna(x) else NA),
            "name": info["name"].astype("object"),
            "team": info["team_number"]
            .astype("object")
            .map(lambda x: TEAMS.get(int(x), "?") if pd.notna(x) else "?"),
        }
    )
    players = (
        players[players["sid"].isin(["0", NA]) == False]
        .drop_duplicates("sid")
        .reset_index(drop=True)
    )  # noqa: E712
    try:
        header = p.parse_header()
    except Exception:  # noqa: BLE001
        header = {}

    say("events")
    _events = [
        "player_death",
        "weapon_fire",
        "player_hurt",
        # round/match structure:
        # begin_new_match marks the live-match start (FACEIT knife
        # rounds sit before it), round_freeze_end bounds the rounds
        "begin_new_match",
        "round_freeze_end",
    ]
    _other = ["total_rounds_played", "is_warmup_period"]
    ev: dict | None = None
    last_ev_err: Exception | None = None
    # shot/duel context: the named map zone of both players at
    # every event (player=) plus the round clock (other=): round_second =
    # game_time - round_start_time, where round_start_time is the freeze
    # END (verified on real demos). Old parsers/demos may not know the
    # fields - fall back step by step; the columns then simply stay absent
    # and every consumer treats them as missing data, never as false.
    for kw in (
        # first choice also asks for the position (X/Y/Z) of both players
        # at every event: the Map page draws kills / deaths from it. Old
        # parsers that reject it fall through to the next step, the
        # columns stay absent and the page says so instead of guessing.
        {
            "player": ["last_place_name", "X", "Y", "Z"],
            "other": _other + ["game_time", "round_start_time"],
        },
        {
            "player": ["last_place_name"],
            "other": _other + ["game_time", "round_start_time"],
        },
        {"other": _other},
        {},
    ):
        try:
            ev = dict(p.parse_events(_events, **kw))
            break
        except Exception as e:  # noqa: BLE001 - unknown fields on old parsers
            last_ev_err = e
    if ev is None:
        raise last_ev_err  # type: ignore[misc]
    deaths = ev.get("player_death", pd.DataFrame())
    fires = ev.get("weapon_fire", pd.DataFrame())
    hurts = ev.get("player_hurt", pd.DataFrame())
    if fires.empty:
        raise ValueError("no_events")
    for df, pairs in (
        (
            deaths,
            (
                ("attacker_steamid", "attacker_name"),
                ("user_steamid", "user_name"),
                # assists for the impact stats; the same float64/steamid
                # collision rules apply as for the attacker column
                ("assister_steamid", "assister_name"),
            ),
        ),
        (fires, (("user_steamid", "user_name"),)),
        (
            hurts,
            (("attacker_steamid", "attacker_name"), ("user_steamid", "user_name")),
        ),
    ):
        for c, nc in pairs:
            if c in df:
                df[c] = fix_ids(
                    df[c],
                    flookup,
                    df[nc] if nc in df.columns else None,
                    name_to_sid,
                )

    # warmup shots/kills/damage are not part of the match -> drop them
    warmup_rows: dict[str, int] = {}
    for name, df in (("deaths", deaths), ("fires", fires), ("hurts", hurts)):
        if "is_warmup_period" in df.columns and len(df):
            mask = df["is_warmup_period"].fillna(False).astype(bool)
            warmup_rows[name] = int(mask.sum())
            if mask.any():
                if name == "deaths":
                    deaths = deaths[~mask].copy()
                elif name == "fires":
                    fires = fires[~mask].copy()
                else:
                    hurts = hurts[~mask].copy()

    # ------------------------------------------- live match start
    # FACEIT-style demos open with a knife round (often followed by a warmup
    # round) that is NOT flagged is_warmup_period; the live match begins at
    # the LAST begin_new_match. Everything before that tick is dropped and
    # counted per cause for the UI banner. Fallback without the event: the
    # first round segment with zero gun fires and knife-only kills is the
    # knife round.
    excluded: dict[str, int] = {"warmup": 0, "knife_round": 0, "restart": 0}

    def _between(df, lo, hi):
        if df is None or not len(df) or "tick" not in df.columns:
            return pd.DataFrame()
        return df[(df["tick"] >= lo) & (df["tick"] < hi)]

    def _has_gun_fire(df) -> bool:
        return (
            bool(len(df)) and "weapon" in df.columns and df["weapon"].map(is_gun).any()
        )

    def _knife_only(df) -> bool:
        """At least one kill and every kill by knife/bayonet."""
        if not len(df) or "weapon" not in df.columns:
            return False
        w = df["weapon"].map(norm_weapon)
        kn = w.str.contains("knife", na=False) | w.eq("bayonet")
        return bool(kn.any() and kn.all())

    def _has_knife_death(df) -> bool:
        if not len(df) or "weapon" not in df.columns:
            return False
        w = df["weapon"].map(norm_weapon)
        return bool((w.str.contains("knife", na=False) | w.eq("bayonet")).any())

    bnm = ev.get("begin_new_match", pd.DataFrame())
    freeze = ev.get("round_freeze_end", pd.DataFrame())
    fe_rows = []
    if len(freeze) and "tick" in freeze.columns:
        t = pd.to_numeric(freeze["tick"], errors="coerce")
        warm = (
            freeze["is_warmup_period"].fillna(False).astype(bool)
            if "is_warmup_period" in freeze.columns
            else pd.Series(False, index=freeze.index)
        )
        fe_rows = sorted(
            (int(tt), bool(ww)) for tt, ww in zip(t.dropna(), warm[t.notna()])
        )
    live_start = None
    if len(bnm) and "tick" in bnm.columns:
        t = pd.to_numeric(bnm["tick"], errors="coerce").dropna()
        if len(t):
            live_start = int(t.max())

    if live_start is not None and live_start > 0:
        pre = [r for r in fe_rows if r[0] < live_start]
        for i, (fe_tick, fe_warm) in enumerate(pre):
            seg_end = pre[i + 1][0] if i + 1 < len(pre) else live_start
            if fe_warm:
                excluded["warmup"] += 1
            elif not _has_gun_fire(
                _between(fires, fe_tick, seg_end)
            ) and _has_knife_death(_between(deaths, fe_tick, seg_end)):
                excluded["knife_round"] += 1
            else:
                excluded["restart"] += 1
        if len(deaths) and "tick" in deaths.columns:
            deaths = deaths[deaths["tick"] >= live_start].copy()
        if len(fires) and "tick" in fires.columns:
            fires = fires[fires["tick"] >= live_start].copy()
        if len(hurts) and "tick" in hurts.columns:
            hurts = hurts[hurts["tick"] >= live_start].copy()
    elif fe_rows or not any(warmup_rows.values()):
        # no begin_new_match: knife fallback on the FIRST round segment
        # (freeze_end-bounded when available, else the trp=0 rows). The
        # gate is strict - no gun shots and knife-only kills - so a pistol
        # round is never mistaken for a knife round.
        if fe_rows:
            lo = fe_rows[0][0]
            hi = fe_rows[1][0] if len(fe_rows) > 1 else float("inf")
            seg_fires, seg_deaths = _between(fires, lo, hi), _between(deaths, lo, hi)
            later_fires = fires[fires["tick"] >= hi] if len(fires) else fires
            if (
                not _has_gun_fire(seg_fires)
                and _knife_only(seg_deaths)
                and _has_gun_fire(later_fires)
            ):
                excluded["knife_round"] = 1
                deaths = deaths[(deaths["tick"] < lo) | (deaths["tick"] >= hi)].copy()
                fires = fires[(fires["tick"] < lo) | (fires["tick"] >= hi)].copy()
                hurts = hurts[(hurts["tick"] < lo) | (hurts["tick"] >= hi)].copy()
            excluded["warmup"] = sum(1 for _, w in fe_rows if w)
        else:
            r0d = _round_col(deaths)
            f0 = fires[_round_col(fires) == 0] if len(fires) else fires
            f1 = fires[_round_col(fires) > 0] if len(fires) else fires
            if (
                len(deaths)
                and not _has_gun_fire(f0)
                and _knife_only(deaths[r0d == 0])
                and _has_gun_fire(f1)
            ):
                excluded["knife_round"] = 1
                deaths = deaths[_round_col(deaths) != 0].copy()
                fires = fires[_round_col(fires) != 0].copy()
                if len(hurts) and "total_rounds_played" in hurts.columns:
                    hurts = hurts[_round_col(hurts) != 0].copy()
    if not fe_rows and any(warmup_rows.values()):
        # warmup rows were dropped but there are no round boundary events:
        # report the activity without pretending to know the round count
        excluded["warmup"] = max(excluded["warmup"], 1)

    # first live round must be round 1: when the counter kept counting the
    # excluded round (knife round without a begin_new_match restart), shift
    # total_rounds_played so the first remaining round is 0
    if live_start is not None or excluded["knife_round"]:
        trp_min = None
        for df in (deaths, fires, hurts):
            if "total_rounds_played" in df.columns and len(df):
                v = pd.to_numeric(df["total_rounds_played"], errors="coerce")
                v = v[v >= 0]
                if len(v):
                    trp_min = (
                        int(v.min()) if trp_min is None else min(trp_min, int(v.min()))
                    )
        if trp_min:
            for df in (deaths, fires, hurts):
                if "total_rounds_played" in df.columns and len(df):
                    df["total_rounds_played"] = (
                        pd.to_numeric(df["total_rounds_played"], errors="coerce")
                        - trp_min
                    )

    teams = dict(zip(players["sid"], players["team"]))
    # NOTE: boolean-mask filtering an ALREADY-empty DataFrame drops all its
    # columns in pandas ((0, n) -> (0, 0)), so every filter is guarded.
    if not fires.empty:
        fires = fires[fires["weapon"].map(is_gun)].copy()
        fires["weapon"] = fires["weapon"].map(norm_weapon)
    if fires.empty:
        raise ValueError("no_events")
    if len(hurts) and "attacker_steamid" in hurts.columns:
        hurts = hurts[
            hurts["weapon"].map(is_gun)
            & (hurts["attacker_steamid"] != NA)
            & (hurts["attacker_steamid"] != hurts["user_steamid"])
        ].copy()
        same_team = hurts["attacker_steamid"].map(teams).eq(
            hurts["user_steamid"].map(teams)
        ) & hurts["attacker_steamid"].isin(teams)
        hurts = hurts[~same_team].copy()
        hurts["weapon"] = hurts["weapon"].map(norm_weapon)
        hurts["hitgroup"] = hurts["hitgroup"].map(hg_key)

    # per-round sides (halftime swap), see _parse_round_sides
    sides = _parse_round_sides(p, [deaths, fires, hurts], flookup, name_to_sid)

    # Fire ticks AND tick-1: the exact one-tick position delta gives the real
    # speed at the moment of each shot without the parser's velocity props.
    fticks = {int(t) for t in fires["tick"]}
    ticks_list = sorted(fticks | {t - 1 for t in fticks if t > 0})
    say("ticks", n=len(fires))
    td = None
    last_err: Exception | None = None
    for props in (
        POS + BTN_NAMED + ["buttons"] + PUNCH_PROPS + DUCK_PROPS + ALIVE_PROPS,
        POS + PUNCH_PROPS + DUCK_PROPS + ALIVE_PROPS,
        POS + PUNCH_PROPS + ALIVE_PROPS,
        POS + ALIVE_PROPS,
        POS,
    ):
        try:
            td = p.parse_ticks(props, ticks=ticks_list)
            break
        except Exception as e:  # noqa: BLE001 - old demos / unknown props
            last_err = e
    if td is None:
        raise last_err  # type: ignore[misc]
    td["steamid"] = fix_ids(
        td["steamid"],
        flookup,
        td["name"] if "name" in td.columns else None,
        name_to_sid,
    )
    td = td.drop_duplicates(["tick", "steamid"]).reset_index(drop=True)
    td = _decode_buttons(td)
    td = _norm_alive(td)
    td = _extract_punch(td)
    # shooter state at the fire ticks: flash_duration (seconds of
    # blindness left) and velo_modifier (<1 = slowed by recent damage). A
    # separate parse_ticks call: an unknown prop must not take the
    # POS/buttons chain down with it (the buttons lesson). On
    # failure the columns stay absent -> flags come out NA, not False.
    try:
        state = p.parse_ticks(["flash_duration", "velo_modifier"], ticks=ticks_list)
    except Exception:  # noqa: BLE001 - old parser / prop unavailable
        state = None
    if state is not None and len(state) and "steamid" in state.columns:
        state = state.copy()
        state["steamid"] = fix_ids(
            state["steamid"],
            flookup,
            state["name"] if "name" in state.columns else None,
            name_to_sid,
        )
        state = state.drop_duplicates(["tick", "steamid"])
        _keep = [c for c in ("flash_duration", "velo_modifier") if c in state.columns]
        if _keep:
            td = td.merge(
                state[["tick", "steamid"] + _keep], on=["tick", "steamid"], how="left"
            )
    tickrate = tickrate_from_header(header)
    td = _add_speed(td, tickrate)
    # usercmd mouse deltas around first-shot windows: a THIRD
    # separate parse_ticks call - the rows must never merge into `td`
    # (they would change the delta-position speed chain), and an unknown
    # prop must not take anything down with it (the buttons lesson). None
    # -> flick_move comes out NA everywhere, never 0.
    mouse = None
    _mwin = _first_shot_windows(fires)
    if _mwin:
        try:
            md = p.parse_ticks(["usercmd_mouse_dx", "usercmd_mouse_dy"], ticks=_mwin)
        except Exception:  # noqa: BLE001 - old parser / props unavailable
            md = None
        if (
            md is not None
            and len(md)
            and "steamid" in md.columns
            and {"usercmd_mouse_dx", "usercmd_mouse_dy"} <= set(md.columns)
        ):
            md = md.copy()
            md["sid"] = fix_ids(
                md["steamid"],
                flookup,
                md["name"] if "name" in md.columns else None,
                name_to_sid,
            )
            dx = pd.to_numeric(md["usercmd_mouse_dx"], errors="coerce")
            dy = pd.to_numeric(md["usercmd_mouse_dy"], errors="coerce")
            md = pd.DataFrame(
                {
                    "tick": md["tick"].astype(int),
                    "sid": md["sid"],
                    "mag": dx.abs() + dy.abs(),
                }
            )
            md = md.dropna(subset=["sid", "mag"])
            md = md.drop_duplicates(["tick", "sid"])
            if len(md):
                mouse = md.sort_values(["tick", "sid"]).reset_index(drop=True)
    # scoreboard aggregates: the game's own kills_total /
    # deaths_total at the last event tick, for the data_checks
    # cross-check. Verified on the real demos: the game updates them
    # live (a kill is already counted at the tick of the last death of
    # its round) and resets both at begin_new_match, so the sample is
    # directly comparable to our live-only tables. Any failure ->
    # totals stays None and the check reports a skip, never a guess.
    totals = None
    _ev_max = [int(df["tick"].max()) for df in (deaths, fires, hurts) if len(df)]
    if _ev_max:
        try:
            agg = p.parse_ticks(["kills_total", "deaths_total"], ticks=[max(_ev_max)])
        except Exception:  # noqa: BLE001 - old parser / props unavailable
            agg = None
        if agg is not None and len(agg) and "steamid" in agg.columns:
            agg = agg.copy()
            agg["sid"] = fix_ids(
                agg["steamid"],
                flookup,
                agg["name"] if "name" in agg.columns else None,
                name_to_sid,
            )
            agg = agg.drop_duplicates(["sid"])
            keep = ["sid"] + [
                c for c in ("kills_total", "deaths_total") if c in agg.columns
            ]
            if len(keep) > 1:
                totals = (
                    agg[keep]
                    .rename(columns={"kills_total": "kills", "deaths_total": "deaths"})
                    .reset_index(drop=True)
                )
    return Demo(
        path,
        header,
        players,
        deaths,
        fires,
        hurts,
        td,
        excluded,
        tickrate,
        sides,
        totals=totals,
        mouse=mouse,
    )


def _parse_version() -> str:
    """
    Identity of what load_demo produces: the demoparser2 version plus a hash
    of this file, so a cached parse is never used after the parser or the
    parsing code changed (no version number to forget to bump).
    """
    try:
        with open(__file__, "rb") as fh:
            code = hashlib.sha1(fh.read()).hexdigest()[:8]
    except OSError:
        code = "nocode"
    try:
        dp = importlib.metadata.version("demoparser2")
    except Exception:  # noqa: BLE001
        dp = "x"
    return re.sub(r"[^\w.\-]", "_", f"f1-{dp}-{code}")


PARSE_VERSION = _parse_version()


def load_demo_cached(path: str, progress=None) -> tuple["Demo", bool]:
    """
    load_demo through the disk cache of the data folder: (demo, from_cache).

    Only library demos are cached (their file name carries the content id);
    a demo parsed once opens again in a fraction of a second, also after the
    app was restarted. Any problem with the cache just means parsing again.
    """
    cached = store.read_parsed(path, PARSE_VERSION)
    if isinstance(cached, Demo):
        return cached, True
    demo = load_demo(path, progress=progress)
    store.write_parsed(path, PARSE_VERSION, demo)
    return demo, False
