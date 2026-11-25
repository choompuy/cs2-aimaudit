"""load_demo (the phases live in core/phases.py), state.json and the parsed-demo cache."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import re

import pandas as pd
from demoparser2 import DemoParser

import store
from core.ids import fix_ids
from core.model import NA, SIDE_PROPS, TEAMS, Demo, _round_col, _safe_int
from core.phases import (
    _build_rounds,
    _drop_warmup,
    _exclude_preliminary,
    _filter_gun_events,
    _parse_events_frames,
    _parse_mouse,
    _parse_players,
    _parse_tick_table,
    _parse_totals,
)

__all__ = ["load_demo", "load_demo_cached", "load_state", "save_state"]


# ------------------------------------------------------------ app state
def load_state() -> dict:
    """Load the local state file; {} when missing or unreadable."""
    return store.read_json(store.state_path())


def save_state(d: dict) -> None:
    """Atomically save the state file; silent when the data folder is read-only."""
    store.write_json(store.state_path(), d)


def _parse_round_sides(
    parser, frames: list[pd.DataFrame], flookup: dict, name_to_sid: dict
) -> dict[int, dict[str, str]]:
    """{round: {sid: side}} from team_num at one tick per round: parse_player_info's team
    is the one at the END of the match. {} when the prop is unavailable."""

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
        for ri, tick in zip(r[ok], tk[ok], strict=True):
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
    """Parse a demo into the Demo container, phase by phase."""
    say = progress or (lambda *a, **k: None)
    p = DemoParser(path)

    say("players")
    players, flookup, name_to_sid, header = _parse_players(p)

    say("events")
    ev, deaths, fires, hurts = _parse_events_frames(p, flookup, name_to_sid)

    deaths, fires, hurts, warmup_rows = _drop_warmup(deaths, fires, hurts)
    (
        deaths,
        fires,
        hurts,
        excluded,
        live_start,
        fe_rows,
    ) = _exclude_preliminary(ev, deaths, fires, hurts, warmup_rows)
    fires, hurts = _filter_gun_events(players, deaths, fires, hurts)

    # per-round sides (halftime swap), see _parse_round_sides
    sides = _parse_round_sides(p, [deaths, fires, hurts], flookup, name_to_sid)

    td, _ticks_list, tickrate = _parse_tick_table(
        p, fires, flookup, name_to_sid, header, say
    )
    mouse = _parse_mouse(p, fires, flookup, name_to_sid)
    totals = _parse_totals(p, deaths, fires, hurts, flookup, name_to_sid)
    round_windows, round_info = _build_rounds(
        p, deaths, fires, hurts, fe_rows, live_start, tickrate
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
        round_windows=round_windows,
        round_info=round_info,
    )


def _parse_version() -> str:
    """The demoparser2 version plus a hash of the core package: a cached parse is never
    used after the parser or the parsing code changed."""
    try:
        h = hashlib.sha1()
        import glob as _glob

        for _f in sorted(_glob.glob(os.path.join(os.path.dirname(__file__), "*.py"))):
            with open(_f, "rb") as fh:
                h.update(os.path.basename(_f).encode())
                h.update(fh.read())
        code = h.hexdigest()[:8]
    except OSError:
        code = "nocode"
    try:
        dp = importlib.metadata.version("demoparser2")
    except importlib.metadata.PackageNotFoundError:
        dp = "x"
    return re.sub(r"[^\w.\-]", "_", f"f1-{dp}-{code}")


PARSE_VERSION = _parse_version()


def load_demo_cached(path: str, progress=None) -> tuple["Demo", bool]:
    """load_demo through the disk cache: (demo, from_cache). Only library demos are cached;
    any problem with the cache means parsing again."""
    cached = store.read_parsed(path, PARSE_VERSION)
    if isinstance(cached, Demo):
        return cached, True
    demo = load_demo(path, progress=progress)
    store.write_parsed(path, PARSE_VERSION, demo)
    return demo, False
