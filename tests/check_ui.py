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


# app-level Python sources: app.py and the modules that carry the app
# code (context.py, ui/*.py, shell.py, filters_ui.py, views/*.py).
# t() and core.X scans must cover all of them.
APP_SOURCES = [ROOT / "app.py", ROOT / "i18n.py", ROOT / "context.py"]
APP_SOURCES += sorted((ROOT / "ui").glob("*.py"))
views_dir = ROOT / "views"
if views_dir.is_dir():
    APP_SOURCES += sorted(views_dir.glob("*.py"))
for _extra in ("filters_ui.py", "shell.py"):
    if (ROOT / _extra).is_file():
        APP_SOURCES.append(ROOT / _extra)
app = "\n".join(p.read_text(encoding="utf-8") for p in APP_SOURCES)

# ---- 1. all t("key") literals in the app sources exist in both languages
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

# ---- 4. every core.X referenced by the app sources exists
refs = set(re.findall(r"core\.([A-Za-z_][A-Za-z_0-9]*)", app))
refs.discard("py")  # 'core.py' mentioned in the docstring
have = set(dir(core))
unknown = sorted(refs - have)
bad("core attrs exist", not unknown, unknown)

# ---- 5. keys formatted with the right kwargs (smoke: t() runs)
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
    # core is a package: read every module of it
    src = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((ROOT / "core").glob("*.py"))
    )
    # crude: every column name must appear somewhere in core source
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

# ---- 8. launcher: Windows only, no first-run "Email:" prompt, radars step
# Even with gatherUsageStats=false, Streamlit asks for an email on the very
# first launch and waits for Enter - the launcher window then looks frozen.
# An empty ~/.streamlit/credentials.toml skips the prompt, so the launcher
# must create it before starting the app (and start.bat must stay CRLF,
# see .gitattributes). The project is Windows-only: there is no start.sh.
bat = (ROOT / "start.bat").read_bytes()
bad(
    "start.bat creates credentials.toml",
    b"credentials.toml" in bat and b"email = " in bat,
    "start.bat does not pre-create ~/.streamlit/credentials.toml",
)
bad(
    "start.bat keeps CRLF line endings",
    b"\r\n" in bat and bat.replace(b"\r\n", b"").find(b"\n") == -1,
    "cmd.exe may mis-parse a .bat with mixed/LF endings",
)
bad(
    "start.bat fetches the radars once and honours AIMAUDIT_NO_RADARS",
    b"fetch_radars.py --if-missing" in bat and b"AIMAUDIT_NO_RADARS" in bat,
    "the Map page needs the radar step in the launcher (opt-out: AIMAUDIT_NO_RADARS)",
)
bad(
    "start.bat finds Python through the py launcher as well",
    b"py -3" in bat and b":no_python" in bat,
    "a Python installed without PATH (only the py launcher) would not start",
)
_ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
_pin = re.search(r'python-version:\s*"(\d+)\.(\d+)"', _ci)
bad(
    "start.bat requires the same Python as CI (requirements.txt pins are tested on it)",
    bool(_pin)
    and f"== ({_pin.group(1)}, {_pin.group(2)})".encode() in bat
    and f"py -{_pin.group(1)}.{_pin.group(2)}".encode() in bat,
    "bump the Python pin in ci.yml, README, requirements.txt AND start.bat together",
)
bad(
    "no Linux / macOS launcher: the project is Windows-only",
    not (ROOT / "start.sh").exists(),
    "start.sh is back; CS2-AimAudit supports Windows only",
)

# ---- 9. design system: .streamlit/config.toml [theme] matches the tokens
# tools/sync_theme.py regenerates the section from theme/tokens.toml; if the
# file was hand-edited (or sync was forgotten after a token change) the app
# renders with a stale palette for all native widgets.
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
# context/shell/filters_ui carry app code too; views/ as well
for _extra in ("context.py", "filters_ui.py", "shell.py"):
    if (ROOT / _extra).is_file():
        UI_SOURCES.append(ROOT / _extra)
views_dir = ROOT / "views"
if views_dir.is_dir():
    UI_SOURCES += sorted(views_dir.glob("*.py"))
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
        Col(
            "res", "Res", kind="badge", tones={"kill": "positive", "death": "negative"}
        ),
    ]
    _pl = table_payload(_rows, _cols, selected=1, stripe=["info", "bogus", None])
    _cell = lambda r, c: _pl["rows"][r]["c"][c]  # noqa: E731
    bad(
        "table: pct bar fraction",
        _cell(0, 1)[2] == 0.5 and _cell(1, 1)[2] == 1.0,
        _cell(0, 1),
    )
    bad(
        "table: rel bar is a share of the column max",
        _cell(0, 2)[2] == 0.25 and _cell(1, 2)[2] == 1.0,
        _cell(0, 2),
    )
    bad(
        "table: empty number -> raw None, dash, no bar",
        _cell(2, 1)[0] is None and _cell(2, 1)[1] == "—" and _cell(2, 1)[2] is None,
        _cell(2, 1),
    )
    bad(
        "table: first click sorts best value first",
        [c["f"] for c in _pl["cols"]] == ["asc", "desc", "desc", "asc", "asc"],
        [c["f"] for c in _pl["cols"]],
    )
    bad(
        "table: badge tone by raw value",
        _cell(0, 4)[3] == "positive" and _cell(1, 4)[3] == "negative",
        _cell(0, 4),
    )
    bad(
        "table: unknown stripe tone dropped",
        _pl["rows"][0]["s"] == "info" and _pl["rows"][1]["s"] is None,
        _pl["rows"][:2],
    )
    bad(
        "table: selected row and display format",
        _pl["sel"] == 1 and _cell(0, 3)[1] == "1.5°",
        (_pl["sel"], _cell(0, 3)),
    )
except Exception as exc:  # noqa: BLE001
    bad("table: payload model builds", False, repr(exc))

# ---- 10c. table grade: value colour quality 0 (worst) .. 1 (best)
try:
    from ui.table import Col, table_payload

    def _scores(vals, **kw):
        pl = table_payload({"v": vals}, [Col("v", "V", **kw)])
        return [r["c"][0][4] for r in pl["rows"]]

    bad(
        "grade auto: best value is 1, worst 0",
        _scores([10, 20, 30], grade="auto") == [0.0, 0.5, 1.0],
        _scores([10, 20, 30], grade="auto"),
    )
    bad(
        "grade auto: best=low flips the scale",
        _scores([10, 20, 30], grade="auto", best="low") == [1.0, 0.5, 0.0],
        "",
    )
    bad(
        "grade auto: < 3 distinct values -> no colour",
        _scores([5, 5, 9], grade="auto") == [None, None, None],
        _scores([5, 5, 9], grade="auto"),
    )
    bad(
        "grade (bad, good): order sets the direction",
        _scores([1.0], grade=(0.5, 1.5)) == [0.5]
        and _scores([0.5], grade=(1.5, 0.5)) == [1.0],
        "",
    )
    bad(
        "grade (bad, ok, good): ok is the neutral colour",
        _scores([0.7, 0.85, 1.0, 1.4, 9.0], grade=(0.7, 1.0, 1.4))
        == [0.0, 0.3, 0.6, 1.0, 1.0],
        _scores([0.7, 0.85, 1.0, 1.4, 9.0], grade=(0.7, 1.0, 1.4)),
    )
    bad(
        "grade: none / empty cell -> no colour",
        _scores([None, 3.0], grade=(0, 6)) == [None, 0.5]
        and _scores([1.0, 2.0]) == [None, None],
        "",
    )
except Exception as exc:  # noqa: BLE001
    bad("grade: model builds", False, repr(exc))

# ---- 10d. duel strip (ui/strip.py): the payload model Python hands to
# the JS renderer - grouping by round, round starts for jumps, chip tones,
# selection clamping. Everything the strip does must be decided here.
try:
    from ui.strip import strip_payload

    _s_items = [
        {"round": 3, "result": "kill", "tip": "a"},
        {"round": 3.0, "result": "death", "tip": "b"},
        {"round": 7, "result": "kill", "tip": "c"},
        {"round": None, "result": "kill", "tip": "d"},
        {"round": 7, "result": "kill", "tip": "e"},
    ]
    _s_pl = strip_payload(_s_items, 2, round_label=lambda r, n: f"R{r}x{n}")
    bad(
        "strip: consecutive rounds share one badge (3.0 == 3)",
        [g["r"] for g in _s_pl["groups"]] == [3, 7, None, 7]
        and [it["i"] for it in _s_pl["groups"][0]["items"]] == [0, 1],
        [g["r"] for g in _s_pl["groups"]],
    )
    bad(
        "strip: rstarts = first duel of every group",
        _s_pl["rstarts"] == [0, 2, 3, 4],
        _s_pl["rstarts"],
    )
    bad(
        "strip: chips keep their index and a kill/death tone",
        _s_pl["groups"][0]["items"][1]["w"] == "death"
        and _s_pl["groups"][0]["items"][0]["w"] == "kill"
        and _s_pl["groups"][0]["items"][0]["tip"] == "a",
        _s_pl["groups"][0]["items"][1],
    )
    bad(
        "strip: group tooltip from round_label(r, n)",
        _s_pl["groups"][0]["tip"] == "R3x2" and _s_pl["groups"][2]["tip"] == "RNonex1",
        [_s_pl["groups"][0]["tip"], _s_pl["groups"][2]["tip"]],
    )
    bad(
        "strip: selection clamped into range (empty -> 0)",
        _s_pl["sel"] == 2
        and strip_payload(_s_items, 99, round_label=lambda r, n: "")["sel"] == 0
        and strip_payload([], 5, round_label=lambda r, n: "")["n"] == 0,
        (_s_pl["sel"], strip_payload(_s_items, 99, round_label=lambda r, n: "")["sel"]),
    )
    bad(
        "strip: labels normalized to strings, empty default",
        strip_payload(_s_items, 0, round_label=lambda r, n: "", labels={"prev": None})[
            "lab"
        ]
        == {"prev": ""}
        and strip_payload(_s_items, 0, round_label=lambda r, n: "")["lab"] == {},
        "lab normalization",
    )
except Exception as exc:  # noqa: BLE001
    bad("strip: payload model builds", False, repr(exc))

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

# ---- 12. table schema (ui/tables.toml + ui/schema.py): the grids are
# described by data. validate() checks every rule the file must hold:
# i18n keys exist in both locales, fmt formats a number, best/grade/bar/
# tone values are legal (bad != good, ok between), basic ⊆ columns,
# sort_by/stripe references resolve, colour tokens exist in theme/.
try:
    import ui.schema as schema
    from ui.table import Col

    errs = schema.validate()
    bad("tables.toml schema valid", not errs, errs[:6])

    def _spec(**m):
        return {"metric": {"m": m}, "table": {"t": {"columns": ["m"]}}}

    bad(
        "schema: minimal spec valid",
        schema.validate(_spec(label="col_kills")) == [],
        schema.validate(_spec(label="col_kills")),
    )
    # negative examples: config mistakes must fail validation, not render
    bad("schema: broken fmt rejected", schema.validate(_spec(fmt="{:.2")) != [])
    bad(
        "schema: unknown i18n key rejected",
        schema.validate(_spec(label="col_nope")) != [],
    )
    bad(
        "schema: grade ok outside bad..good rejected",
        schema.validate(_spec(grade=[0.0, 5.0, 1.0])) != [],
    )
    bad(
        "schema: grade bad == good rejected",
        schema.validate(_spec(grade=[1.0, 1.0])) != [],
    )
    bad("schema: unknown tone rejected", schema.validate(_spec(tone="magenta")) != [])
    bad(
        "schema: bar on a text column rejected",
        schema.validate(_spec(kind="text", bar="pct")) != [],
    )
    bad(
        "schema: basic outside columns rejected",
        schema.validate(
            {"metric": {"m": {}}, "table": {"t": {"columns": ["m"], "basic": ["x"]}}}
        )
        != [],
    )
    bad(
        "schema: dangling sort_by rejected",
        schema.validate(
            {
                "metric": {"m": {"sort_by": "nope"}},
                "table": {"t": {"columns": ["m"], "extra": []}},
            }
        )
        != [],
    )
    # the migrated grids really build through the schema
    for tid, n_basic, n_all in (
        ("overview", 9, 18),
        ("weapons", 10, 10),
        ("duels", 9, 18),
    ):
        cols = schema.build_cols(tid, t=lambda k, **kw: k)
        cols_all = schema.build_cols(tid, all_columns=True, t=lambda k, **kw: k)
        bad(
            f"schema: {tid} builds ({n_basic} basic / {n_all} all)",
            len(cols) == n_basic
            and len(cols_all) == n_all
            and all(type(c) is Col for c in cols + cols_all),
            (len(cols), len(cols_all)),
        )
except Exception as exc:  # noqa: BLE001
    bad("schema: loads and validates", False, repr(exc))

# ---- 13. import graph: no cycles between the runtime modules
# (the layering must stay one-way: model <- filters <- loader/metrics <-
# replay <- checks, and core/ui/theme must never import app-level code.)
import ast  # noqa: E402


def _local_modules() -> dict[str, Path]:
    mods: dict[str, Path] = {}
    for pattern, prefix in (
        (ROOT.glob("*.py"), None),
        (ROOT.glob("core/*.py"), "core"),
        (ROOT.glob("ui/*.py"), "ui"),
        (ROOT.glob("theme/*.py"), "theme"),
        (ROOT.glob("views/*.py"), "views"),
    ):
        for path in pattern:
            name = path.stem
            if prefix:
                name = prefix if name == "__init__" else f"{prefix}.{name}"
            mods[name] = path
    return mods


_mods = _local_modules()
_edges: dict[str, set[str]] = {m: set() for m in _mods}
for _name, _path in _mods.items():
    for _node in ast.walk(ast.parse(_path.read_text(encoding="utf-8"))):
        _targets = []
        if isinstance(_node, ast.Import):
            _targets = [a.name for a in _node.names]
        elif isinstance(_node, ast.ImportFrom) and _node.module and _node.level == 0:
            _targets = [_node.module]
        for _t in _targets:
            while _t and _t not in _mods:
                _t = _t.rpartition(".")[0]
            if _t and _t != _name:
                _edges[_name].add(_t)

_color = dict.fromkeys(_mods, 0)  # 0 white, 1 gray, 2 black
_stack: list[str] = []


def _dfs(u: str) -> str | None:
    _color[u] = 1
    _stack.append(u)
    for v in sorted(_edges[u]):
        if _color[v] == 1:
            return " -> ".join(_stack[_stack.index(v) :] + [v])
        if _color[v] == 0:
            found = _dfs(v)
            if found:
                return found
    _stack.pop()
    _color[u] = 2
    return None


_cycle = None
for _m in sorted(_mods):
    if _color[_m] == 0:
        _cycle = _dfs(_m)
        if _cycle:
            break
bad("import graph: no cycles", not _cycle, _cycle)

# ---- 14. layering: core/ui/theme never import app-level code. The
# dependency chain is one-way (ui, theme, i18n <- core <- context <-
# filters_ui, shell, views <- app); a violation here would mean a leaf
# module grew UI/navigation logic again. Beyond app/views/shell this also
# bans filters_ui/context for the same reason.
_APP_LEVEL = {"app", "views", "shell", "filters_ui", "context"}
_layer_bad = sorted(
    f"{_name} -> {_t}"
    for _name in _mods
    if _name.split(".")[0] in {"core", "ui", "theme"}
    for _t in _edges[_name]
    if _t.split(".")[0] in _APP_LEVEL
)
bad(
    "layering: core/ui/theme free of app-level imports",
    not _layer_bad,
    _layer_bad[:4],
)

# ---- 15. module size guardrails: warn past 1300 lines, fail past 1500,
# so no module quietly grows back into a second app.py. Scope is the app
# code (_mods = root modules + core/ + ui/ + theme/ + views/); tests/
# are deliberately out of scope - splitting them is a separate task.


def _warn(name, extra=""):
    print(f"  warn {name} {extra}")


_sizes = {
    _name: len(_path.read_text(encoding="utf-8").splitlines())
    for _name, _path in _mods.items()
}
_over = sorted(f"{_n}: {_s}" for _n, _s in _sizes.items() if _s > 1500)
bad("module size: none over 1500 lines", not _over, _over)
for _n, _s in sorted(_sizes.items()):
    if 1300 < _s <= 1500:
        _warn("module size: over 1300 lines", f"{_n}: {_s}")

print(f"\n{'FAILURES: ' + str(FAIL) if FAIL else 'ALL OK'}")
sys.exit(1 if FAIL else 0)
