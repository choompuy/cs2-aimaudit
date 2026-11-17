"""
UI translations - the i18n/ folder is the single source of truth (the
same way ui/tables.toml is for the grid tables and theme/tokens.toml
for the palette): one file per language, i18n/<lang>.toml, loaded once
at import. English (i18n/en.toml) is the primary language - keys
missing from a translation fall back to it. Add a language by adding
its file to i18n/ and its label to LANGS below.
"""

import tomllib
from pathlib import Path

LANGS = {"en": "English", "ru": "Русский"}

_DIR = Path(__file__).resolve().parent / "i18n"

STR: dict[str, dict[str, str]] = {
    path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
    for path in sorted(_DIR.glob("*.toml"))
}

if "en" not in STR:
    raise RuntimeError("i18n/en.toml (the primary language) is missing")
for _lang, _table in STR.items():
    _not_str = [k for k, v in _table.items() if not isinstance(v, str)]
    if _not_str:
        raise RuntimeError(
            f"i18n/{_lang}.toml: keys {_not_str[:5]} are not plain strings;"
            " a language file is one flat table (no [section] headers)"
        )
_mismatch = set(STR) ^ set(LANGS)
if _mismatch:
    raise RuntimeError(
        f"i18n/*.toml and LANGS disagree on {sorted(_mismatch)}: every"
        " language file needs its label in LANGS, every LANGS entry"
        " needs its i18n/<lang>.toml file"
    )


def make_t(lang: str):
    def t(key: str, **kw) -> str:
        s = STR.get(lang, {}).get(key) or STR["en"].get(key, key)
        return s.format(**kw) if kw else s

    return t
