"""Global shot/duel filters and the round/half/pistol structure."""

from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd

from core.model import weapon_class


@dataclass
class Filters:
    """Global filter applied to shots/duels/hurts before any metric."""

    weapons: set[str] | None = None  # exact weapon names (norm_weapon form)
    classes: set[str] | None = None  # rifle, sniper, smg, pistol, ...
    rounds: tuple[int, int] | None = None  # inclusive 1-based round range
    rounds_set: set[int] | None = None  # exact rounds (pistol rounds preset)
    side: str | None = None  # "T" / "CT" (the player's side in that round)
    results: set[str] | None = None  # {"kill", "death"} (duels_for frames)
    dist: tuple[float, float] | None = None  # inclusive distance range

    def empty(self) -> bool:
        return not any(
            (
                self.weapons,
                self.classes,
                self.rounds,
                self.rounds_set,
                self.side,
                self.results,
                self.dist,
            )
        )


# MR12 (current CS2): halves of 12 rounds, overtime starts at round 25.
ROUNDS_PER_HALF = 12


def halves_from_sides(sides: dict, n_rounds: int) -> list[tuple[int, int]]:
    """
    Inclusive (lo, hi) round ranges of the halves (including every overtime
    half), derived from the per-round sides: a half boundary is a round in
    which (almost) every player with known sides swapped T<->CT. Returns []
    when sides are missing or no full swap is found - the caller then falls
    back to the MR12 assumption in round_bounds/pistol_rounds.

    A pair of rounds is only compared when both are known AND consecutive:
    with a gap (rounds without any events) the swap position is unknowable,
    and guessing it would misplace the halves.
    """
    if not sides:
        return []
    rounds = sorted(r for r in sides if isinstance(r, int) and r >= 1)
    if not rounds:
        return []
    bounds = []
    for prev, cur in zip(rounds, rounds[1:]):
        if cur != prev + 1:
            continue
        known = swapped = 0
        for sid, s_prev in sides[prev].items():
            s_cur = sides[cur].get(sid)
            if s_prev in ("T", "CT") and s_cur in ("T", "CT"):
                known += 1
                if s_prev != s_cur:
                    swapped += 1
        if known >= 2 and swapped >= 0.75 * known:
            bounds.append(cur)
    if not bounds:
        return []
    n = max(int(n_rounds), 1)
    out = []
    lo = rounds[0]
    for b in bounds + [n + 1]:
        hi = min(b - 1, n)
        if lo <= hi:
            out.append((lo, hi))
        lo = b
    return out


def round_bounds(n_rounds: int, preset: str, halves=None) -> tuple[int, int]:
    """
    Inclusive 1-based round range of a preset for a match of `n_rounds`
    rounds ('all', 'first', 'second', 'ot'). `halves` are the data-driven
    (lo, hi) ranges from halves_from_sides; when given they define the
    halves (MR15, overtime lengths and all), otherwise MR12 is assumed:
    first half 1-12, second 13-24, overtime 25+. A preset beyond the match
    gives an empty range (lo > hi) on purpose - the UI simply does not
    offer it.
    """

    n = max(int(n_rounds), 1)
    if halves:
        if preset == "first":
            lo, hi = halves[0]
        elif preset == "second" and len(halves) >= 2:
            lo, hi = halves[1]
        elif preset == "ot" and len(halves) >= 3:
            lo, hi = halves[2][0], n
        else:  # 'all' / anything unknown
            lo, hi = 1, n
        hi = max(lo, min(hi, n))
        return (lo, hi)
    if preset == "first":
        lo, hi = 1, ROUNDS_PER_HALF
    elif preset == "second":
        lo, hi = ROUNDS_PER_HALF + 1, 2 * ROUNDS_PER_HALF
    elif preset == "ot":
        lo, hi = 2 * ROUNDS_PER_HALF + 1, max(n, 2 * ROUNDS_PER_HALF + 1)
    else:  # 'all' / anything unknown
        lo, hi = 1, n
    hi = max(lo, min(hi, n))
    return (lo, hi)


def pistol_rounds(n_rounds: int, halves=None) -> set[int]:
    """
    First round of each half incl. overtime halves. With `halves` from
    halves_from_sides that is data-driven (MR15 -> {1, 16}); the MR12
    fallback gives {1, 13, 25, ...}.
    """
    if halves:
        return {lo for lo, hi in halves if lo <= max(int(n_rounds), 1)}
    return {
        r for r in range(1, max(int(n_rounds), 1) + 1) if (r - 1) % ROUNDS_PER_HALF == 0
    }


def apply_filters(
    shots: pd.DataFrame,
    duels: pd.DataFrame,
    hurts: pd.DataFrame | None = None,
    f: Filters | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """
    Boolean-index shots / duels / hurts by the global filter (copies back).

    Every selection is optional: None/empty means 'no constraint'. Works on
    the raw frames (shots_table / build_duels / demo.hurts) and on
    duels_for() frames - 'my_side' and 'result' columns are used when
    present; raw duels keep a row for the side filter when EITHER role
    matches (each player's kills/deaths then keep the correct rows).
    """

    if f is None or f.empty():
        return (
            shots.copy(),
            duels.copy() if duels is not None else pd.DataFrame(),
            hurts.copy() if hurts is not None else None,
        )

    def _weapon_mask(df: pd.DataFrame) -> pd.Series | None:
        if "weapon" not in df.columns or not len(df):
            return None
        # weapons and classes combine as a UNION: picking "rifles" plus a
        # specific AWP shows both, not their intersection.
        parts: list[pd.Series] = []
        if f.weapons:
            parts.append(df["weapon"].isin(f.weapons))
        if f.classes:
            parts.append(df["weapon"].map(weapon_class).isin(f.classes))
        if not parts:
            return None
        m = parts[0]
        for p in parts[1:]:
            m = m | p
        return m

    def _apply(df: pd.DataFrame | None) -> pd.DataFrame | None:
        if df is None or not len(df):
            return df.copy() if df is not None else None
        out = df
        wm = _weapon_mask(out)
        if wm is not None:
            out = out[wm]
        if f.rounds is not None and "round" in out.columns and len(out):
            lo, hi = f.rounds
            out = out[(out["round"] >= lo) & (out["round"] <= hi)]
        if f.rounds_set and "round" in out.columns and len(out):
            out = out[out["round"].isin(f.rounds_set)]
        if f.side is not None and len(out):
            if "my_side" in out.columns:
                out = out[out["my_side"] == f.side]
            elif {"killer_side", "victim_side"}.issubset(out.columns):
                out = out[
                    (out["killer_side"] == f.side) | (out["victim_side"] == f.side)
                ]
            elif "side" in out.columns:
                out = out[out["side"] == f.side]
        if f.results is not None and "result" in out.columns and len(out):
            out = out[out["result"].isin(f.results)]
        if f.dist is not None and len(out):
            dcol = next((c for c in ("dist", "distance") if c in out.columns), None)
            if dcol is not None:
                d = pd.to_numeric(out[dcol], errors="coerce")
                lo, hi = f.dist
                out = out[(d >= lo) & (d <= hi)]
        return out.copy()

    return _apply(shots), _apply(duels), _apply(hurts)
