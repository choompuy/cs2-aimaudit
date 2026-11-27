"""Static checks: i18n, launcher, theme, colours, imports, layering, module sizes."""

import ast
import re
import tomllib
from pathlib import Path

import pytest

import core
import i18n
import state
import theme
from theme.loader import config_theme_dict, contrast, font_faces

ROOT = Path(__file__).resolve().parents[1]
EN, RU = i18n.STR["en"], i18n.STR["ru"]
CONFIG = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

# the app's own modules: {dotted name: path}
MODULES = {
    p.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__"): p
    for pattern in ("*.py", "core/*.py", "ui/*.py", "theme/*.py", "views/*.py", "sources/*.py", "store/*.py")
    for p in ROOT.glob(pattern)
}
SOURCE = {name: p.read_text(encoding="utf-8") for name, p in MODULES.items()}


def package(name: str) -> str:
    return name.split(".")[0]


def joined(*packages: str) -> str:
    return "\n".join(s for name, s in SOURCE.items() if package(name) in packages)


UI_PACKAGES = ("app", "context", "filters_ui", "shell", "ui", "views")
UI_CODE = joined(*UI_PACKAGES)
APP_CODE = UI_CODE + joined("core", "sources", "store", "mapview", "nativedialog")

USED_KEYS = set(re.findall(r'(?<![A-Za-z_0-9])t\("([a-z_0-9]+)"\)', UI_CODE))
USED_KEYS |= {f"k_{b}" for b in core.BUTTONS}


# -------------------------------------------------------------------- i18n
def test_used_keys_exist():
    assert not USED_KEYS - set(EN)


# strings that are the same in both languages
SAME_IN_RU = {
    "col_hs", "first_none", "k_BACK", "k_FORWARD", "k_LEFT", "k_RIGHT", "kind_faceit",
    "none_val", "m_k2", "m_k3", "m_k4", "rr_pt_hp", "rr_pt_money", "rr_u_s", "tl_ax_speed", "tl_label", "weapon_none",
}


def test_ru_is_translated():
    assert not set(RU) - set(EN), "keys unknown to en"
    # weapon names may be left out: i18n falls back to English
    assert not {k for k in set(EN) - set(RU) if not k.startswith("weapon_")}, "missing in ru"
    assert not {k for k in RU if RU[k] == EN[k]} - SAME_IN_RU, "still English in ru"


def test_no_dead_keys():
    literals = set(re.findall(r"""["']([a-z_0-9]{3,})["']""", APP_CODE))
    prefixes = set(re.findall(r"""\bt\(f["']([a-z_0-9]+_)""", APP_CODE))
    prefixes |= set(re.findall(r"""\bt\(["']([a-z_0-9]+_)["']\s*\+""", APP_CODE))
    prefixes |= {"col_", "weapon_"}  # ui/schema.py, ui/icons.py
    assert not {
        k for k in EN
        if k not in literals and k not in USED_KEYS and not k.startswith(tuple(prefixes))
    }


def placeholders(text: str) -> list[str]:
    return sorted(re.findall(r"\{([a-z_0-9]+)[:}]", text))


def test_placeholders_match():
    assert not {k for k in RU if placeholders(RU[k]) != placeholders(EN[k])}


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_formatting(lang):
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


def test_navigation_labels():
    for key in [f"nav_{p}" for p in state.NAV_PAGES] + [f"tab_{p}" for p in state.MATCH_TABS]:
        assert key in EN and key in RU
    assert not set(state.NAV_PAGES) & set(state.MATCH_TABS)


def test_core_attrs_exist():
    assert not set(re.findall(r"\bcore\.([A-Za-z_]\w*)", UI_CODE)) - set(dir(core)) - {"py"}


# ---------------------------------------------------------------- launcher
BAT = (ROOT / "start.bat").read_bytes()


def test_no_telemetry():
    assert CONFIG["browser"]["gatherUsageStats"] is False


def test_start_bat():
    # Streamlit asks for an email on the very first launch and waits for
    # Enter: an empty credentials.toml skips the prompt
    assert b"credentials.toml" in BAT and b"email = " in BAT
    assert b"\n" not in BAT.replace(b"\r\n", b""), "cmd.exe may mis-parse LF endings"
    assert b"fetch_radars.py --if-missing" in BAT and b"AIMAUDIT_NO_RADARS" in BAT
    assert b"py -3" in BAT and b":no_python" in BAT
    assert not (ROOT / "start.sh").exists(), "the project is Windows-only"


def test_start_bat_python_pin():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    major, minor = re.search(r'python-version:\s*"(\d+)\.(\d+)"', ci).groups()
    assert f"== ({major}, {minor})".encode() in BAT and f"py -{major}.{minor}".encode() in BAT


@pytest.mark.parametrize("var", ["TEMP", "TMP", "USERPROFILE", "PIP_CACHE_DIR", "PYTHONPYCACHEPREFIX"])
def test_start_bat_is_portable(var):
    """Deleting the app folder is a complete uninstall."""
    assert f'set "{var}='.encode() in BAT and b"AIMAUDIT_DATA_DIR" in BAT


OUTSIDE = re.compile(
    r"gettempdir|mkdtemp|TemporaryDirectory|NamedTemporaryFile|"
    r"expanduser\(\s*[\"']~|Path\.home|LOCALAPPDATA|APPDATA|USERPROFILE"
)


def test_only_store_picks_folders():
    sources = {n: s for n, s in SOURCE.items() if package(n) != "store"}
    sources |= {p.name: p.read_text(encoding="utf-8") for p in ROOT.glob("tools/*.py")}
    assert not [
        f"{name}: {line.strip()}"
        for name, text in sources.items()
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and OUTSIDE.search(line)
    ]


# ------------------------------------------------------------------- theme
def test_config_theme_matches_tokens():
    live = CONFIG["theme"]
    expected = config_theme_dict()
    assert {k: live.get(k) for k in expected} == expected, "run python tools/sync_theme.py"
    faces = font_faces()
    assert live.get("fontFaces", []) == [
        {k: f[k] for k in ("family", "url", "weight", "style") if f.get(k) is not None} for f in faces
    ]
    assert not faces or CONFIG["server"]["enableStaticServing"] is True
    assert all((ROOT / f["file"]).is_file() for f in faces if f.get("file"))


@pytest.mark.parametrize("name", [n for n in SOURCE if package(n) in (*UI_PACKAGES, "i18n")])
def test_no_colour_literals(name):
    assert not re.findall(r"#[0-9a-fA-F]{6,8}\b|\brgba?\(", SOURCE[name]), "use theme.tok()"


@pytest.mark.parametrize("fg, bg", [
    ("text-primary", "bg-app"), ("text-primary", "surface-3"), ("text-secondary", "bg-app"),
    ("text-secondary", "surface-2"), ("text-inverse", "accent"),
])
@pytest.mark.parametrize("theme_name", theme.available_themes())
def test_aa_contrast(theme_name, fg, bg):
    tokens = theme.tokens(theme_name)
    assert contrast(tokens[fg], tokens[bg]) >= 4.5


# ------------------------------------------------------------ architecture
IMPORTS: dict[str, set[str]] = {}
for _name, _text in SOURCE.items():
    IMPORTS[_name] = set()
    for _node in ast.walk(ast.parse(_text)):
        if isinstance(_node, ast.Import):
            IMPORTS[_name] |= {a.name for a in _node.names}
        elif isinstance(_node, ast.ImportFrom) and _node.module and _node.level == 0:
            IMPORTS[_name].add(_node.module)


def local_imports(name: str) -> set[str]:
    out = set()
    for target in IMPORTS[name]:
        while target and target not in MODULES:
            target = target.rpartition(".")[0]
        out.add(target)
    return out - {"", name}


def test_no_import_cycles():
    done: set[str] = set()

    def visit(name: str, path: list[str]):
        assert name not in path, " -> ".join([*path, name])
        if name not in done:
            for target in sorted(local_imports(name)):
                visit(target, [*path, name])
            done.add(name)

    for name in sorted(MODULES):
        visit(name, [])


@pytest.mark.parametrize("leaf", ["core", "ui", "theme", "sources", "store"])
def test_layering(leaf):
    banned = {"app", "views", "shell", "filters_ui", "context"}
    if leaf in ("core", "sources", "store"):
        banned.add("streamlit")
    assert not {
        (name, target)
        for name in MODULES if package(name) == leaf
        for target in IMPORTS[name] if package(target) in banned
    }


def test_module_size():
    assert not {n: len(s.splitlines()) for n, s in SOURCE.items() if len(s.splitlines()) > 1500}
