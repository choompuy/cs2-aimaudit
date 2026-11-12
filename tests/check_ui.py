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
# A cp1252 console (stock Windows) cannot encode the em dash in the output:
# force UTF-8. CI sets PYTHONUTF8=1 as well - this covers local runs.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
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
    t("tip_flashed", n=1, pct=10.0)
    t("tip_slowed", n=1, pct=10.0)
    t("chk_na_ids", n=1, pct=1.0)
    t("chk_angles", med=1.0, n=1)
    t("chk_hitgroups", n=1, vals="x")
    t("tl_no_buttons", keys="k")
    t("tl_buttons", keys="k")
    t("tl_first", speed="1", err="1")
    t("err_path_missing", path="x")
    t("chk_sides", n=1, pct=1.0)
    t("flt_n", shots=1, duels=1)
    t("flt_n_all", shots=1, duels=1)
    t("rank_part", name="x", pos=1, n=2)
    t("rank_line", parts="x")
    t(
        "ci_line",
        acc="a",
        n_shots=1,
        hs="h",
        n_hits=1,
        first="f",
        n_duels=1,
        hw="w",
        n_hw=1,
    )
    t("small_sample", shots=10, duels=5)
bad("t() smoke formatting", True)

# ---- 6. columns produced by core vs consumed by app
import pandas as pd  # noqa: E402

demo = core.load_demo  # symbol check only
for fn, cols in {
    "shots_table": [
        "tick",
        "sid",
        "weapon",
        "round",
        "speed",
        "dist",
        "hit",
        "side",
        "weapon_class",
        "zone",
        "round_sec",
        "flashed",
        "slowed",
    ],
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
        "first_acc",
        "open_rate",
        "aim_err",
        "duration",
    ],
    "duels_for": [
        "round",
        "tick",
        "result",
        "opponent",
        "weapon",
        "weapon_class",
        "my_side",
        "opp_side",
        "headshot",
        "distance",
        "first",
        "my_shots",
        "opp_shots",
        "my_shots_raw",
        "opp_shots_raw",
        "low_conf",
        "duration_s",
        "my_speed",
        "my_err",
        "my_first_tick",
        "noscope",
        "thrusmoke",
        "attackerblind",
        "penetrated",
        "round_sec",
        "my_zone",
        "opp_zone",
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
    "openings_table": [
        "round",
        "tick",
        "killer",
        "victim",
        "killer_side",
        "victim_side",
        "weapon",
        "weapon_class",
        "headshot",
        "distance",
        "killer_zone",
        "victim_zone",
        "round_sec",
    ],
    "impact_stats": [
        "sid",
        "open_attempts",
        "open_wins",
        "open_rate",
        "trade_kills",
        "traded_deaths",
        "traded_pct",
        "k2",
        "k3",
        "k4",
        "k5",
        "assists",
        "flash_assists",
    ],
}.items():
    src = (ROOT / "core.py").read_text(encoding="utf-8")
    # crude: every column name must appear somewhere in core.py source
    missing = [c for c in cols if c not in src]
    bad(f"{fn} columns present in core source", not missing, missing)

# ---- 7. privacy: Streamlit usage statistics must stay disabled
# The README promises "no telemetry"; without this setting Streamlit phones
# home with anonymous usage stats. (Parsed with tomllib: configparser cannot
# handle the repeated [[theme.fontFaces]] tables.)
import tomllib  # noqa: E402

cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
bad(
    "[browser] gatherUsageStats = false",
    cfg.get("browser", {}).get("gatherUsageStats") is False,
    "Streamlit telemetry is NOT disabled in .streamlit/config.toml",
)

# ---- 8. launchers: no first-run "Email:" prompt
# Even with gatherUsageStats=false, Streamlit asks for an email on the very
# first launch and waits for Enter - the launcher window then looks frozen.
# An empty ~/.streamlit/credentials.toml skips the prompt, so both launchers
# must create it before starting the app (and start.bat must stay CRLF,
# see .gitattributes).
bat = (ROOT / "start.bat").read_bytes()
bad(
    "start.bat creates credentials.toml",
    b"credentials.toml" in bat and b"type nul" in bat,
    "start.bat does not pre-create ~/.streamlit/credentials.toml",
)
bad(
    "start.bat keeps CRLF line endings",
    b"\r\n" in bat and bat.replace(b"\r\n", b"").find(b"\n") == -1,
    "cmd.exe may mis-parse a .bat with mixed/LF endings",
)
sh = (ROOT / "start.sh").read_text(encoding="utf-8")
bad(
    "start.sh creates credentials.toml",
    "credentials.toml" in sh and "$HOME/.streamlit" in sh,
    "start.sh does not pre-create ~/.streamlit/credentials.toml",
)

# ---- 9. design system: .streamlit/config.toml [theme] matches the tokens
# tools/sync_theme.py regenerates the section from theme/tokens.toml; if the
# file was hand-edited (or sync was forgotten after a token change) the app
# silently renders with a stale palette for all native widgets.
import theme  # noqa: E402
from theme.loader import config_theme_dict, font_faces  # noqa: E402

expected_theme = config_theme_dict()
live_theme = cfg.get("theme", {})
mismatch = {
    key: (value, live_theme.get(key))
    for key, value in expected_theme.items()
    if live_theme.get(key) != value
}
bad(
    "config.toml [theme] matches theme/tokens.toml",
    not mismatch,
    f"mismatch {mismatch} — run python tools/sync_theme.py",
)
expected_faces = [
    {
        k: face[k]
        for k in ("family", "url", "weight", "style")
        if face.get(k) is not None
    }
    for face in font_faces()
]
bad(
    "[[theme.fontFaces]] match theme/tokens.toml",
    live_theme.get("fontFaces", []) == expected_faces,
    f"expected {expected_faces}, got {live_theme.get('fontFaces')}",
)
bad(
    "[server] enableStaticServing for local fonts",
    (not expected_faces) or cfg.get("server", {}).get("enableStaticServing") is True,
    "local [[fontface]] files are unreachable without server.enableStaticServing",
)
bad(
    "declared font files exist in static/fonts/",
    all((ROOT / face["file"]).is_file() for face in font_faces() if face.get("file")),
    "a [[fontface]] points at a missing file",
)

# ---- 10. design system: no hex/rgb colours outside theme/
# Every colour must come from theme/tokens.toml (directly or via
# .streamlit/config.toml, which check 9 pins to the tokens). Hex/rgb literals
# in UI sources are how palettes drift apart.
UI_SOURCES = [ROOT / "app.py", ROOT / "i18n.py"]
ui_dir = ROOT / "ui"
if ui_dir.is_dir():
    UI_SOURCES += sorted(ui_dir.glob("*.py"))
for src in UI_SOURCES:
    text = src.read_text(encoding="utf-8")
    hexes = sorted(set(re.findall(r"#[0-9a-fA-F]{6,8}\b", text)))
    rgbs = sorted(set(re.findall(r"\brgba?\(", text)))
    bad(
        f"no colour literals in {src.name}",
        not hexes and not rgbs,
        f"hex {hexes} rgb {rgbs} — use theme.tok() instead",
    )

# ---- 10b. universal table (ui/table.py): the cell model Python hands to the
# JS renderer - bars, "best first" sort direction, empty cells, badge tones.
try:
    from ui.table import Col, table_payload

    _rows = {
        "name": ["a", "b", "c"],
        "acc": [50.0, 100.0, float("nan")],
        "n": [10, 40, 20],
        "err": [1.5, 0.5, 2.5],
        "res": ["kill", "death", "kill"],
    }
    _cols = [
        Col("name", "Name", kind="text"),
        Col("acc", "Acc", fmt="{:.1f}%", bar="pct", best="high"),
        Col("n", "N", fmt="{:.0f}", bar="rel"),
        Col("err", "Err", fmt="{:.1f}°", best="low"),
        Col("res", "Res", kind="badge", tones={"kill": "positive", "death": "negative"}),
    ]
    _pl = table_payload(_rows, _cols, selected=1, stripe=["info", "bogus", None])
    _cell = lambda r, c: _pl["rows"][r]["c"][c]  # noqa: E731
    bad("table: pct bar fraction", _cell(0, 1)[2] == 0.5 and _cell(1, 1)[2] == 1.0, _cell(0, 1))
    bad("table: rel bar is a share of the column max", _cell(0, 2)[2] == 0.25 and _cell(1, 2)[2] == 1.0, _cell(0, 2))
    bad("table: empty number -> raw None, dash, no bar", _cell(2, 1)[0] is None and _cell(2, 1)[1] == "—" and _cell(2, 1)[2] is None, _cell(2, 1))
    bad(
        "table: first click sorts best value first",
        [c["f"] for c in _pl["cols"]] == ["asc", "desc", "desc", "asc", "asc"],
        [c["f"] for c in _pl["cols"]],
    )
    bad("table: badge tone by raw value", _cell(0, 4)[3] == "positive" and _cell(1, 4)[3] == "negative", _cell(0, 4))
    bad("table: unknown stripe tone dropped", _pl["rows"][0]["s"] == "info" and _pl["rows"][1]["s"] is None, _pl["rows"][:2])
    bad("table: selected row and display format", _pl["sel"] == 1 and _cell(0, 3)[1] == "1.5°", (_pl["sel"], _cell(0, 3)))
except Exception as exc:  # noqa: BLE001
    bad("table: payload model builds", False, repr(exc))

# ---- 11. design system: WCAG AA contrast for body text, every theme
# text-primary / text-secondary must pass AA (>= 4.5:1) on every surface
# they are used on, and text-inverse on the accent chips, in every theme.
from theme.loader import contrast  # noqa: E402

for theme_name in theme.available_themes():
    tk = theme.tokens(theme_name)
    pairs = [
        ("text-primary", "bg-app"),
        ("text-primary", "surface-3"),
        ("text-secondary", "bg-app"),
        ("text-secondary", "surface-2"),
        ("text-inverse", "accent"),
    ]
    for fg_name, bg_name in pairs:
        ratio = contrast(tk[fg_name], tk[bg_name])
        bad(
            f"AA contrast [{theme_name}] {fg_name} on {bg_name}",
            ratio >= 4.5,
            f"{ratio:.2f}:1",
        )

print(f"\n{'FAILURES: ' + str(FAIL) if FAIL else 'ALL OK'}")
sys.exit(1 if FAIL else 0)
