"""
Value formatters shared by the pages and the filter bar.

They read the translator (and the sid->name map for duel_label) through
module globals set once per run via configure().
"""

import pandas as pd

# set per run by configure()
t = None
name_of: dict = {}


def configure(translator, names: dict) -> None:
    """Inject the run's translator and player-name map."""
    global t, name_of
    t = translator
    name_of = names


def fmt_result(v: str) -> str:
    return t("res_kill") if v == "kill" else t("res_death")


def fmt_first(v: str) -> str:
    return {
        "me": t("first_me"),
        "opponent": t("first_opponent"),
        "simultaneous": t("first_simultaneous"),
        "none": t("first_none"),
    }.get(v, str(v))


def duel_label(r) -> str:
    return t(
        "tl_label",
        round=int(r["round"]),
        result=fmt_result(r["result"]),
        weapon=str(r["weapon"]),
        opp=name_of.get(r["opponent"], r["opponent"]),
    )


def pct(v) -> str:
    return f"{v:.0f}%" if pd.notna(v) else t("none_val")


def num(v, nd: int = 1) -> str:
    return f"{v:.{nd}f}" if pd.notna(v) else t("none_val")
