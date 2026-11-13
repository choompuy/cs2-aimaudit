#!/usr/bin/env python3
"""One-off diagnostics for round/halves inspection (NOT part of CI).

Shows how a demo encodes rounds, so the knife-round handling in part B
is based on data, not guesses:

  python tools/inspect_rounds.py faceit1.dem faceit2.dem premier.dem

For every demo it prints:
  * the header (map name, demo version, ...);
  * which of the wanted game events exist in this demoparser2 version
    (round_start, round_end, round_freeze_end, begin_new_match);
  * a unified timeline of those events with tick /
    total_rounds_played / is_warmup_period (whichever columns exist),
    plus the total_rounds_played trajectory to spot counter resets;
  * for the first 3 round segments: which weapons appear in
    weapon_fire and player_death (with counts).

Accepts plain .dem and the compressed forms the app supports
(.dem.bz2 / .dem.gz / .dem.zst / .zip) via core.decompressed_demo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Player names and event fields can contain any Unicode: never let a
# legacy Windows codepage (cp1252) crash the diagnostics dump.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from demoparser2 import DemoParser  # noqa: E402

from core import decompressed_demo  # noqa: E402

WANTED = ("round_start", "round_end", "round_freeze_end", "begin_new_match")
EXTRA = ("weapon_fire", "player_death")
KEY_FIELDS = ("tick", "total_rounds_played", "is_warmup_period")
# round_end fields worth seeing when present (winner/reason = round result)
NICE_EXTRA = ("winner", "reason", "message", "objective", "restart", "maxrounds")
FIRST_N_SEGMENTS = 3


def _trajectory(df) -> str:
    """Compact total_rounds_played sequence, e.g. 0,0,1,2,3 - resets are visible."""
    if df is None or "total_rounds_played" not in df.columns:
        return "-"
    seq = [int(v) for v in df["total_rounds_played"].fillna(-1)]
    return ",".join(map(str, seq)) if seq else "-"


def _weapons(df, seg_lo: int, seg_hi: int) -> str:
    """Value counts of the weapon column for rows within a tick range."""
    if df is None or len(df) == 0 or "tick" not in df.columns:
        return "(no rows)"
    part = df[(df["tick"] >= seg_lo) & (df["tick"] < seg_hi)]
    if len(part) == 0:
        return "(no rows)"
    if "weapon" not in part.columns:
        return f"{len(part)} rows (no weapon column)"
    counts = part["weapon"].fillna("(none)").value_counts()
    return ", ".join(f"{w} x{n}" for w, n in counts.items())


def inspect_demo(path: str, dfs: dict) -> None:
    print(f"\n{'=' * 72}\n=== {Path(path).name}\n{'=' * 72}")

    # --- unified timeline of the wanted events
    rows = []
    for name in WANTED:
        df = dfs.get(name)
        if df is None or "tick" not in df.columns:
            continue
        for _, r in df.iterrows():
            rows.append(
                (
                    int(r["tick"]),
                    name,
                    r.get("total_rounds_played"),
                    r.get("is_warmup_period"),
                    r.get("winner"),
                )
            )
    rows.sort(key=lambda x: x[0])
    print(f"--- timeline ({len(rows)} events) ---")
    print("  tick              event              trp  warmup  winner")
    for tick, name, trp, warm, win in rows[:80]:
        w = win if win is not None else ""
        print(f"  {tick:<17}  {name:<17}  {str(trp):>3}  {str(warm):>6}  {w}")
    if len(rows) > 80:
        print(f"  ... {len(rows) - 80} more")

    for name in WANTED:
        df = dfs.get(name)
        if df is None:
            continue
        cols = [c for c in KEY_FIELDS + NICE_EXTRA if c in df.columns]
        print(f"\n--- {name} ({len(df)} rows) ---")
        if cols:
            print(df[cols].head(40).to_string(index=True))
        else:
            print(f"  (columns: {', '.join(map(str, df.columns))})")
        print(f"  total_rounds_played trajectory: {_trajectory(df)}")

    # --- first round segments: weapons in weapon_fire / player_death
    seg_events, seg_name = dfs.get("round_freeze_end"), "round_freeze_end"
    if seg_events is None or len(seg_events) == 0:
        seg_events, seg_name = dfs.get("round_start"), "round_start"
    if seg_events is None or "tick" not in getattr(seg_events, "columns", []):
        print("\n--- weapons per round: no round boundary events, cannot segment ---")
        return

    bounds = sorted(int(t) for t in seg_events["tick"])
    bounds.append(None)  # sentinel: the last segment runs to the end
    fires, deaths = dfs.get("weapon_fire"), dfs.get("player_death")
    trp_events = dfs.get("round_end")

    print(f"\n--- first {FIRST_N_SEGMENTS} segments (boundaries: {seg_name}) ---")
    for i in range(min(FIRST_N_SEGMENTS, len(bounds) - 1)):
        lo, hi = bounds[i], bounds[i + 1]
        hi_disp = str(hi) if hi is not None else "end-of-demo"
        hi_inf = hi if hi is not None else 2**62
        trp = ""
        if trp_events is not None and "tick" in trp_events.columns:
            before = trp_events[trp_events["tick"] < hi_inf]["total_rounds_played"]
            if len(before):
                trp = f"  trp_by_round_end={int(before.iloc[-1])}"
        print(f"\n  segment {i + 1}: ticks {lo}..{hi_disp}{trp}")
        print(f"    weapon_fire: {_weapons(fires, lo, hi_inf)}")
        print(f"    player_death: {_weapons(deaths, lo, hi_inf)}")


def run(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    failures = 0
    for path in argv:
        try:
            if not os.path.isfile(path):
                raise ValueError(f"file not found: {path}")
            plain = decompressed_demo(path)
            parser = DemoParser(plain)
            print(f"\n### {Path(path).name}: header: {parser.parse_header()}")
            available = set(parser.list_game_events())
            wanted = [e for e in WANTED + EXTRA if e in available]
            missing = [e for e in WANTED if e not in available]
            if missing:
                print(
                    f"### wanted events absent in this demo/demoparser2: {', '.join(missing)}"
                )
            if not wanted:
                dfs = {}
            else:
                # total_rounds_played / is_warmup_period are server vars
                # merged in via other= (same pattern as core.py), not
                # native fields. Fall back without other= in case this
                # demoparser2 build refuses it for some event - the
                # columns then stay absent.
                try:
                    dfs = dict(
                        parser.parse_events(
                            wanted, other=["total_rounds_played", "is_warmup_period"]
                        )
                    )
                except Exception:
                    dfs = dict(parser.parse_events(wanted))
            inspect_demo(path, dfs)
        except Exception as e:  # noqa: BLE001 - diagnostics must not die mid-batch
            failures += 1
            print(f"\nERROR on {path}: {type(e).__name__}: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
