"""
UI translations - i18n.toml is the single source of truth (the same way
ui/tables.toml is for the grid tables and theme/tokens.toml for the
palette): one table per language, loaded once at import. English is the
primary language; add a new one by adding its table to i18n.toml and its
label to LANGS. Missing keys fall back to English.
"""

import tomllib
from pathlib import Path

LANGS = {"en": "English", "ru": "Русский"}

STR: dict[str, dict[str, str]] = tomllib.loads(
    (Path(__file__).resolve().parent / "i18n.toml").read_text(encoding="utf-8")
)


def make_t(lang: str):
    def t(key: str, **kw) -> str:
        s = STR.get(lang, {}).get(key) or STR["en"].get(key, key)
        return s.format(**kw) if kw else s

    return t
