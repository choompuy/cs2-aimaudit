"""Native file dialogs: they give the REAL path of a demo, which a browser upload never
does, so the library can link it.

tkinter runs in a CHILD process (Streamlit runs the script on a worker thread, Tk wants
a main thread). Returns the paths, [] when cancelled, None when no dialog is available."""

from __future__ import annotations

import json
import os
import subprocess
import sys

_CREATE_NO_WINDOW = 0x08000000  # Windows: no console flash for the child

_FILES = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
paths = filedialog.askopenfilenames(
    title=sys.argv[1],
    filetypes=[(sys.argv[2], "*.dem *.bz2 *.gz *.zst *.zip"), (sys.argv[3], "*.*")],
    initialdir=sys.argv[4] or None,
)
print(json.dumps(list(paths)))
"""


def _desktop_from_registry() -> str | None:
    """The real Desktop path from the registry, or None when unresolvable."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            desktop = winreg.QueryValueEx(k, "Desktop")[0]
    except (ImportError, OSError):
        # ImportError: not Windows; OSError: the registry key is locked/absent
        return None
    desktop = os.path.expandvars(str(desktop))
    if desktop and os.path.isdir(desktop):
        return desktop
    return None


def _initial_dir() -> str:
    """A folder the dialog can open in: portable mode points the profile into data\\home,
    where ~/Desktop does not exist, so the real one comes from the registry."""
    return _desktop_from_registry() or os.getcwd()


def _run(code: str, args: list[str], timeout: float) -> list[str] | None:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        out = subprocess.run(
            [sys.executable, "-c", code, *args],
            capture_output=True,
            timeout=timeout,
            env=env,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None  # tkinter missing or a headless session
    try:
        paths = json.loads(out.stdout.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError, UnicodeDecodeError):
        return None
    return [str(p) for p in paths] if isinstance(paths, list) else None


def pick_files(
    title: str = "Choose demo files",
    demo_label: str = "CS2 demos",
    all_label: str = "All files",
    timeout: float = 600,
) -> list[str] | None:
    """Ask for one or more demo files: their paths, [] on cancel, None if unavailable."""
    return _run(_FILES, [title, demo_label, all_label, _initial_dir()], timeout)
