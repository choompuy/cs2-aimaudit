"""
Static consistency checks for the CS2-AimAudit UI: i18n keys used vs defined,
core attributes used vs defined, .format() placeholder parity between langs.
Run from the repo root: python tests/check_ui.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core  # noqa: E402
import i18n  # noqa: E402

FAIL = 0


def bad(name, cond, extra=""):
    global FAIL
    if not cond:
        FAIL += 1
        print(f"  FAIL {name} {extra}")
    else:
        print(f"  ok  {name}")


app = (ROOT / "app.py").read_text(encoding="utf-8")

# ---- 1. all t("key") literals in app.py exist in both languages
used = set(re.findall(r'(?<![A-Za-z_0-9])t\("([a-z_0-9]+)"\)', app))
# dynamic keys built in app.py
used |= {f"k_{b}" for b in core.BUTTONS}
used |= {
    f"hg_{k}"
    for k in [
        "head",
        "neck",
        "chest",
        "stomach",
        "left_arm",
        "right_arm",
        "left_leg",
        "right_leg",
        "other",
    ]
}
used |= {
    f"chk_{cid}"
    for cid in [
        "na_ids",
        "unknown_shooters",
        "missing_pos",
        "no_speed",
        "no_dist",
        "fast_speeds",
        "round0",
        "duel_dist",
        "angles",
        "hitgroups",
        "sides",
    ]
}
for lang in ("en", "ru"):
    missing = sorted(used - set(i18n.STR[lang]))
    bad(f"all t() keys exist in {lang}", not missing, missing)

# ---- 2. en/ru key sets identical
diff_en = sorted(set(i18n.STR["en"]) - set(i18n.STR["ru"]))
diff_ru = sorted(set(i18n.STR["ru"]) - set(i18n.STR["en"]))
bad(
    "en/ru key sets identical",
    not diff_en and not diff_ru,
    f"en-only={diff_en} ru-only={diff_ru}",
)

# ---- 3. .format() placeholders match between en and ru
ph = lambda s: sorted(re.findall(r"\{([a-z_0-9]+)[:}]", s))
for k in i18n.STR["en"]:
    if k in i18n.STR["ru"]:
        bad(
            f"placeholders match: {k}",
            ph(i18n.STR["en"][k]) == ph(i18n.STR["ru"][k]),
            f"en={ph(i18n.STR['en'][k])} ru={ph(i18n.STR['ru'][k])}",
        )

# ---- 4. every core.X referenced by app.py exists
refs = set(re.findall(r"core\.([A-Za-z_][A-Za-z_0-9]*)", app))
refs.discard("py")  # 'core.py' mentioned in the docstring
have = set(dir(core))
unknown = sorted(refs - have)
bad("core attrs exist", not unknown, unknown)

# ---- 5. keys actually formatted with the right kwargs (smoke: t() runs)
for lang in ("en", "ru"):
    t = i18n.make_t(lang)
    t("tl_label", round=1, result="r", weapon="w", opp="o")
    t("tl_summary", res="r", opp="o", weapon="w", first="f", shots=1, dur=1.0)
    t("step_ticks", n=5)
    t("tip_risky", pct=10.0)
    t("tip_err", err=1.0)
    t("tip_lost_first", n=1, m=2)
    t("chk_na_ids", n=1, pct=1.0)
    t("chk_angles", med=1.0, n=1)
    t("chk_hitgroups", n=1, vals="x")
    t("tl_no_buttons", keys="k")
    t("tl_buttons", keys="k")
    t("tl_first", speed="1", err="1")
    t("err_path_missing", path="x")
    t("chk_sides", n=1, pct=1.0)
bad("t() smoke formatting", True)

# ---- 6. columns produced by core vs consumed by app
import pandas as pd  # noqa: E402

demo = core.load_demo  # symbol check only
for fn, cols in {
    "shots_table": ["tick", "sid", "weapon", "round", "speed", "dist", "hit", "side"],
    "player_summary": [
        "player",
        "sid",
        "team",
        "sides",
        "kills",
        "deaths",
        "shots",
        "hits",
        "accuracy",
        "hs_hits",
        "hs_kills",
        "moving",
        "risky",
        "first_shot",
        "aim_err",
        "duration",
    ],
    "duels_for": [
        "round",
        "tick",
        "result",
        "opponent",
        "weapon",
        "my_side",
        "opp_side",
        "headshot",
        "distance",
        "first",
        "my_shots",
        "opp_shots",
        "duration_s",
        "my_speed",
        "my_err",
        "my_first_tick",
        "noscope",
        "thrusmoke",
        "attackerblind",
        "penetrated",
    ],
    "weapon_stats": [
        "weapon",
        "shots",
        "median_speed",
        "median_dist",
        "moving",
        "risky",
        "hits",
        "hs",
        "accuracy",
    ],
}.items():
    src = (ROOT / "core.py").read_text(encoding="utf-8")
    # crude: every column name must appear somewhere in core.py source
    missing = [c for c in cols if c not in src]
    bad(f"{fn} columns present in core source", not missing, missing)

# ---- 7. privacy: Streamlit usage statistics must stay disabled
# The README promises "no telemetry"; without this setting Streamlit phones
# home with anonymous usage stats.
import configparser  # noqa: E402

cfg = configparser.ConfigParser()
cfg.read(ROOT / ".streamlit" / "config.toml")
bad(
    "[browser] gatherUsageStats = false",
    cfg.has_option("browser", "gatherUsageStats")
    and cfg.getboolean("browser", "gatherUsageStats") is False,
    "Streamlit telemetry is NOT disabled in .streamlit/config.toml",
)

print(f"\n{'FAILURES: ' + str(FAIL) if FAIL else 'ALL OK'}")
sys.exit(1 if FAIL else 0)
