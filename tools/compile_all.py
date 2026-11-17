"""
Byte-compile every app source - what CI runs instead of an explicit file
list (the list once missed nativedialog.py, and every new module needs a
list edit nobody remembers).

Walks the repo from its root and compiles each .py outside tests/ (they
are executed by the test steps anyway), .venv/, data/ and node_modules/
(not ours). Prints the file count; exit 1 on any compile error, like
`python -m compileall`.
"""

from __future__ import annotations

import compileall
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# never compiled here: tests run as themselves, the rest is not app code
SKIP_DIRS = {"tests", ".venv", "data", "node_modules", "__pycache__", ".git"}

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def app_sources() -> list[str]:
    """Every .py of the application, top-level modules first for a stable
    error order."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return out


def main() -> int:
    files = app_sources()
    ok = True
    for f in files:
        ok = compileall.compile_file(f, quiet=1) and ok
    print(f"{len(files)} files compiled" + ("" if ok else " - ERRORS"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
