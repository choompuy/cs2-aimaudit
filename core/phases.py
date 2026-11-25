"""The parse phases of load_demo (core/loader.py orchestrates): players, events, warmup /
pre-live exclusion, the fire-tick table, mouse, totals, round windows."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    # annotation-only: phases receive the parser load_demo created
    from demoparser2 import DemoParser

from core.ids import (
    _add_speed,
    _extract_punch,
    _norm_alive,
    build_id_lookup,
    fix_ids,
)
from core.model import (
    ALIVE_PROPS,
    BTN_NAMED,
    DUCK_PROPS,
    NA,
    POS,
    PUNCH_PROPS,
    TEAMS,
    TICKRATE,
    _decode_buttons,
    _first_shot_windows,
    _round_col,
    build_round_info,
    build_round_windows,
    hg_key,
    is_gun,
    norm_weapon,
)

__all__ = ["tickrate_from_header"]


def tickrate_from_header(header: dict) -> int:
    """playback_ticks / playback_time of the header (128-tick demos exist); 64 when missing
    or implausible."""

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


def _parse_ticks_tiered(
    parser: DemoParser,
    tiers,
    ticks,
    players: list | None = None,
):
    """parse_ticks over prop-set tiers, richest first: the first that parses wins, the LAST
    error is re-raised."""
    df = None
    last_err: Exception | None = None
    for props in tiers:
        try:
            if players is not None:
                df = parser.parse_ticks(props, ticks=ticks, players=players)
            else:
                df = parser.parse_ticks(props, ticks=ticks)
            break
        except Exception as e:  # noqa: BLE001 - old demos / unknown props
            last_err = e
    if df is None:
        raise last_err  # type: ignore[misc]
    return df


# ------------------------------------------------------------- load phases
def _parse_players(p):
    """Phase 1: the player table, the id lookups, the demo header."""
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
    return players, flookup, name_to_sid, header


def _parse_events_frames(p, flookup, name_to_sid):
    """Phase 2: the event frames with steamids fixed in place."""
    _events = [
        "player_death",
        "weapon_fire",
        "player_hurt",
        # begin_new_match marks the live start, round_freeze_end bounds the rounds
        "begin_new_match",
        "round_freeze_end",
    ]
    _other = ["total_rounds_played", "is_warmup_period"]
    ev: dict | None = None
    last_ev_err: Exception | None = None
    # map zones (player=) and the round clock (other=); round_start_time is the freeze
    # END. Parsers that do not know a field fall back step by step, the columns stay
    # absent = unknown.
    for kw in (
        # with the positions of both players (the Map page)
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
                # the same float64 steamid collisions as the attacker column
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
    return ev, deaths, fires, hurts


def _drop_warmup(deaths, fires, hurts):
    """Phase 3a: is_warmup_period rows are not part of the match."""
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
    return deaths, fires, hurts, warmup_rows


def _between(df, lo, hi):
    """An event frame cut to [lo, hi) ticks (empty when unusable)."""
    if df is None or not len(df) or "tick" not in df.columns:
        return pd.DataFrame()
    return df[(df["tick"] >= lo) & (df["tick"] < hi)]


def _has_gun_fire(df) -> bool:
    return bool(len(df)) and "weapon" in df.columns and df["weapon"].map(is_gun).any()


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


def _round_bounds(ev):
    """([(freeze_end_tick, is_warmup)], live-match start tick or None)."""
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
            (int(tt), bool(ww)) for tt, ww in zip(t.dropna(), warm[t.notna()], strict=True)
        )
    live_start = None
    bnm = ev.get("begin_new_match", pd.DataFrame())
    if len(bnm) and "tick" in bnm.columns:
        t = pd.to_numeric(bnm["tick"], errors="coerce").dropna()
        if len(t):
            live_start = int(t.max())
    return fe_rows, live_start


def _count_pre_live(fe_rows, live_start, fires, deaths):
    """Why each pre-live round segment was dropped (warmup / knife / restart)."""
    excluded: dict[str, int] = {"warmup": 0, "knife_round": 0, "restart": 0}
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
    return excluded


def _keep_from_tick(deaths, fires, hurts, tick):
    """Keep only the rows at/after `tick` (the live-match start)."""
    out = []
    for df in (deaths, fires, hurts):
        if len(df) and "tick" in df.columns:
            df = df[df["tick"] >= tick].copy()
        out.append(df)
    return tuple(out)


def _knife_fallback(fe_rows, deaths, fires, hurts):
    """No begin_new_match: the FIRST segment is a knife round only with no gun shots and
    knife-only kills, so a pistol round is never mistaken for one."""
    excluded: dict[str, int] = {"warmup": 0, "knife_round": 0, "restart": 0}
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
    return deaths, fires, hurts, excluded


def _shift_first_round(deaths, fires, hurts, live_start, excluded) -> None:
    """Shift total_rounds_played in place when the counter kept counting the excluded round."""
    if live_start is None and not excluded["knife_round"]:
        return
    trp_min = None
    for df in (deaths, fires, hurts):
        if "total_rounds_played" in df.columns and len(df):
            v = pd.to_numeric(df["total_rounds_played"], errors="coerce")
            v = v[v >= 0]
            if len(v):
                trp_min = int(v.min()) if trp_min is None else min(trp_min, int(v.min()))
    if trp_min:
        for df in (deaths, fires, hurts):
            if "total_rounds_played" in df.columns and len(df):
                df["total_rounds_played"] = (
                    pd.to_numeric(df["total_rounds_played"], errors="coerce") - trp_min
                )


def _exclude_preliminary(ev, deaths, fires, hurts, warmup_rows):
    """Phase 3b: drop everything before the LAST begin_new_match (FACEIT knife / warmup
    rounds are not flagged as warmup), else the knife fallback.

    Returns (deaths, fires, hurts, excluded, live_start, fe_rows) with fe_rows =
    [(freeze_end_tick, is_warmup)]."""
    fe_rows, live_start = _round_bounds(ev)

    if live_start is not None and live_start > 0:
        excluded = _count_pre_live(fe_rows, live_start, fires, deaths)
        deaths, fires, hurts = _keep_from_tick(deaths, fires, hurts, live_start)
    elif fe_rows or not any(warmup_rows.values()):
        deaths, fires, hurts, excluded = _knife_fallback(fe_rows, deaths, fires, hurts)
    else:
        excluded = {"warmup": 0, "knife_round": 0, "restart": 0}
    if not fe_rows and any(warmup_rows.values()):
        # no round boundaries: report the warmup without pretending to count rounds
        excluded["warmup"] = max(excluded["warmup"], 1)
    _shift_first_round(deaths, fires, hurts, live_start, excluded)
    return deaths, fires, hurts, excluded, live_start, fe_rows


def _filter_gun_events(players, deaths, fires, hurts):
    """Phase 4: gun-only fires and the hurts cleanup."""
    teams = dict(zip(players["sid"], players["team"], strict=True))
    # boolean-mask filtering an ALREADY-empty DataFrame drops all its columns in pandas,
    # so every filter is guarded
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
    return fires, hurts


def _flash_left(p, td, flookup, name_to_sid, tickrate) -> pd.Series:
    """Seconds of blindness LEFT on every row of ``td``. flash_duration holds the whole
    length until the fade is over, so the rest is counted from its player_blind event; a
    value without its event is unknown -> NaN."""
    fd = pd.to_numeric(td["flash_duration"], errors="coerce")
    left = fd.where(fd <= 0).astype(float)
    rows = td.loc[fd > 0, ["tick", "steamid"]].dropna()
    if not len(rows):
        return left
    try:
        ev = dict(p.parse_events(["player_blind"])).get("player_blind")
    except Exception:  # noqa: BLE001 - old parser / no such event
        ev = None
    need = {"tick", "user_steamid", "blind_duration"}
    if ev is None or not len(ev) or not need <= set(ev.columns):
        # flashed shots cannot be told from the fade: unknown, not "0% flashed"
        return pd.Series(float("nan"), index=td.index)
    start = pd.DataFrame(
        {
            "at": pd.to_numeric(ev["tick"], errors="coerce"),
            "steamid": fix_ids(
                ev["user_steamid"],
                flookup,
                ev["user_name"] if "user_name" in ev.columns else None,
                name_to_sid,
            ).astype(object),
            "dur": pd.to_numeric(ev["blind_duration"], errors="coerce"),
        }
    ).dropna()
    start["at"] = start["at"].astype("int64")
    rows = rows.astype({"tick": "int64", "steamid": object})
    rows["fd"] = fd
    m = pd.merge_asof(
        rows.rename_axis("row").reset_index().sort_values("tick"),
        start.sort_values(["at", "dur"]),
        left_on="tick",
        right_on="at",
        by="steamid",
    )
    val = (m["dur"] - (m["tick"] - m["at"]) / tickrate).clip(lower=0.0)
    left.loc[m["row"]] = val.where((m["dur"] - m["fd"]).abs() < 0.05).to_numpy()
    return left


def _parse_tick_table(p, fires, flookup, name_to_sid, header, say):
    """Phase 5: positions / buttons / punch / shooter state at every fire tick and tick-1,
    with speed. Returns (td, ticks_list, tickrate)."""
    # tick-1 for the exact one-tick position delta
    fticks = {int(t) for t in fires["tick"]}
    ticks_list = sorted(fticks | {t - 1 for t in fticks if t > 0})
    say("ticks", n=len(fires))
    td = _parse_ticks_tiered(
        p,
        (
            POS + BTN_NAMED + ["buttons"] + PUNCH_PROPS + DUCK_PROPS + ALIVE_PROPS,
            POS + PUNCH_PROPS + DUCK_PROPS + ALIVE_PROPS,
            POS + PUNCH_PROPS + ALIVE_PROPS,
            POS + ALIVE_PROPS,
            POS,
        ),
        ticks_list,
    )
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
    # a separate call: an unknown prop must not take the POS/buttons chain down; on
    # failure the flags come out NA, not False
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
    if "flash_duration" in td.columns:
        td["flash_left"] = _flash_left(p, td, flookup, name_to_sid, tickrate)
    td = _add_speed(td, tickrate)
    return td, ticks_list, tickrate


def _parse_mouse(p, fires, flookup, name_to_sid):
    """Phase 6: usercmd mouse deltas around the first-shot windows
    (None -> flick_move comes out NA everywhere, never 0)."""
    # a separate call too: these rows must never merge into `td` (they would change the
    # delta-position speed)
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
    return mouse


def _parse_totals(p, deaths, fires, hurts, flookup, name_to_sid):
    """Phase 7: the game's own kills_total / deaths_total at the last
    event tick, for the data_checks cross-check (None on any failure)."""
    # The game updates both live and resets them at begin_new_match, so the sample
    # compares to our live-only tables. Any failure -> None, the check is skipped.
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
    return totals


def _round_mark_events(p):
    """The round-end / bomb events in one parse (tiered: an old parser
    without the combined events still gets round_officially_ended)."""
    try:
        return dict(p.parse_events(list(_ROUND_MARK_EVENTS)))
    except Exception:  # noqa: BLE001 - old parser / no such event
        try:
            return dict(p.parse_events(["round_officially_ended"]))
        except Exception:  # noqa: BLE001
            return {}


def _official_end_ticks(round_events, live_start) -> list[int]:
    """Ticks of the live round_officially_ended events (the win panel is
    part of the round, so it ENDS it - not the next freeze)."""
    _rod = round_events.get("round_officially_ended", pd.DataFrame())
    if not len(_rod) or "tick" not in _rod.columns:
        return []
    _omask = pd.Series(True, index=_rod.index)
    if "is_warmup_period" in _rod.columns:
        _omask &= ~_rod["is_warmup_period"].fillna(False).astype(bool)
    if live_start is not None:
        _omask &= (
            pd.to_numeric(_rod["tick"], errors="coerce") >= live_start
        ).fillna(False)
    return sorted(
        int(x)
        for x in pd.to_numeric(_rod.loc[_omask, "tick"], errors="coerce").dropna()
    )


def _named_event_windows(deaths, fires, hurts):
    """(tick, round+1) of every event - the events' own round numbers."""
    _win_parts = []
    for _df in (deaths, fires, hurts):
        if len(_df) and "tick" in _df.columns:
            _win_parts.append(
                pd.DataFrame({"tick": _df["tick"], "round": _round_col(_df) + 1})
            )
    return (
        pd.concat(_win_parts, ignore_index=True)
        if _win_parts
        else pd.DataFrame(columns=["tick", "round"])
    )


def _synthesize_round_bounds(win_ev, tickrate) -> list[int]:
    """No round_freeze_end: boundaries BETWEEN the rounds the events name (inside a round
    they would split it in two)."""
    _named_ev = win_ev[win_ev["round"] >= 1]
    _bounds: list[int] = []
    _prev_last: int | None = None
    for _rn, _g in _named_ev.groupby("round"):
        _lo, _hi = int(_g["tick"].min()), int(_g["tick"].max())
        if _prev_last is None:
            _bounds.append(_lo - int(3.0 * tickrate))
        else:
            _bounds.append(min((_prev_last + _lo) // 2, _lo - 1))
        _prev_last = _hi
    return sorted(_bounds)


def _build_rounds(p, deaths, fires, hurts, fe_rows, live_start, tickrate):
    """Phase 8: the round windows and how rounds ended."""
    _roe = _round_mark_events(p)
    fe_live = sorted(
        t for t, w in fe_rows if not w and (live_start is None or t >= live_start)
    )
    off_ticks = _official_end_ticks(_roe, live_start)
    _win_ev = _named_event_windows(deaths, fires, hurts)
    if not fe_live:
        fe_live = _synthesize_round_bounds(_win_ev, tickrate)
    marks = [
        int(x)
        for name in ("round_end", "bomb_defused", "bomb_exploded")
        if "tick" in (df := _roe.get(name, pd.DataFrame())).columns
        for x in pd.to_numeric(df["tick"], errors="coerce").dropna()
    ]
    round_windows = build_round_windows(
        fe_live, off_ticks, _win_ev, tickrate, marks=marks
    )
    round_info = _round_info_from_events(_roe, round_windows, live_start)
    return round_windows, round_info

# events read in ONE pass next to the round windows: how a round ended
_ROUND_MARK_EVENTS = (
    "round_officially_ended",
    "round_end",
    "bomb_planted",
    "bomb_begindefuse",
    "bomb_defused",
    "bomb_exploded",
)


def _round_info_from_events(ev: dict, windows: dict, live_start) -> dict:
    """Demo.round_info from the parsed round_end + bomb events (live only)."""

    def _live(name) -> pd.DataFrame:
        df = ev.get(name)
        if df is None or not len(df) or "tick" not in df.columns:
            return pd.DataFrame(columns=["tick"])
        d = df.copy()
        d["tick"] = pd.to_numeric(d["tick"], errors="coerce")
        m = d["tick"].notna()
        if "is_warmup_period" in d.columns:
            m &= ~d["is_warmup_period"].fillna(False).astype(bool)
        if live_start is not None:
            m &= d["tick"] >= live_start
        return d[m]

    bombs: list[tuple[int, str, str]] = []
    for name, kind in (
        ("bomb_planted", "plant"),
        ("bomb_begindefuse", "defuse_start"),
        ("bomb_defused", "defuse"),
        ("bomb_exploded", "boom"),
    ):
        d = _live(name)
        for i, tk in enumerate(d["tick"]):
            sid = ""
            if "user_steamid" in d.columns:
                v = d["user_steamid"].iloc[i]
                sid = "" if pd.isna(v) else str(int(v))
            bombs.append((int(tk), kind, sid))
    return build_round_info(
        windows,
        _live("round_end"),
        bombs,
        bomb_known=any(b[1] == "plant" for b in bombs),
    )
