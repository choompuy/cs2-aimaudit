"""Global shot/duel filters and the round/half/pistol structure."""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from core.model import weapon_class

__all__ = [
    "Filters", "ROUNDS_PER_HALF", "apply_filters", "halves_from_sides", "pistol_rounds",
    "round_bounds",
]


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
    """Inclusive (lo, hi) round ranges of the halves: a boundary is a round where (almost)
    every player swapped sides. [] when no full swap is found. Only consecutive known
    rounds are compared: across a gap the swap position is unknowable."""
    if not sides:
        return []
    rounds = sorted(r for r in sides if isinstance(r, int) and r >= 1)
    if not rounds:
        return []
    bounds = []
    for prev, cur in zip(rounds, rounds[1:], strict=False):
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
    """Inclusive round range of a preset ('all', 'first', 'second', 'ot') from `halves`,
    else MR12 (1-12, 13-24, 25+). Beyond the match: an empty range (lo > hi)."""

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
    """First round of each half incl. overtime (MR12 without `halves`)."""
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
    """Cut shots / duels / hurts by the filter; None/empty selections do not constrain. Raw
    duels keep a row when EITHER role matches the side; hurts come from hurts_table."""

    if f is None or f.empty():
        return (
            shots.copy(),
            duels.copy() if duels is not None else pd.DataFrame(),
            hurts.copy() if hurts is not None else None,
        )

    def _weapon_mask(df: pd.DataFrame) -> pd.Series | None:
        if "weapon" not in df.columns or not len(df):
            return None
        # weapons and classes combine as a UNION
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
