"""
Native file / folder dialogs for the demo library ("Choose demo file...").

The app runs on the user's own machine, so a real Windows dialog can hand it
the REAL path of a demo - something a browser upload never does. That is what
lets the library link a demo instead of copying it.

The dialog (tkinter, part of the standard Python installer) runs in a CHILD
process: Streamlit executes the script on a worker thread, and Tk wants its
own main thread. The child prints the chosen paths as JSON.

Plain Python, no Streamlit, no app knowledge: callers get a list of paths
(empty when the dialog was cancelled) or None when no dialog is available.
"""

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

_FOLDER = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
path = filedialog.askdirectory(title=sys.argv[1], mustexist=True, initialdir=sys.argv[2] or None)
print(json.dumps([path] if path else []))
"""


def _initial_dir() -> str:
    """A folder the dialog can actually open in. start.bat's portable mode
    points USERPROFILE into data\\home, where ~/Desktop does not exist -
    Windows then answers 'Location is not available' before the dialog
    even shows. The real Desktop comes from the registry; the fallbacks
    are folders that always exist."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            desktop = winreg.QueryValueEx(k, "Desktop")[0]
        desktop = os.path.expandvars(str(desktop))
        if desktop and os.path.isdir(desktop):
            return desktop
    except Exception:  # noqa: BLE001 - non-Windows / locked registry: fall through
        pass
    for c in (os.path.expanduser("~"), os.getcwd()):
        if os.path.isdir(c):
            return c
    return os.getcwd()


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


def pick_folder(title: str = "Choose a folder with demos", timeout: float = 600) -> list[str] | None:
    """Ask for a folder: [path], [] on cancel, None if unavailable."""
    return _run(_FOLDER, [title, _initial_dir()], timeout)
