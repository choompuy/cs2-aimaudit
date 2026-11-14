"""
The CS2 replays folder (Watch -> Your Matches) as a match source.

The folder is found through the Steam registry key plus libraryfolders.vdf,
or a manual override. Every .dem with a .dem.info sidecar becomes a record
with the state "on disk"; the sidecar's demo url pins the real match id.

Pure filesystem access: no network, no credentials.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from sources.model import DemoState, MatchRecord

_DEMO_EXTS = (".dem", ".bz2", ".gz", ".zst", ".zip")
_URL_MATCH = re.compile(r"replay\d+\.valve\.net/730/(\d+)_\d+\.dem\.bz2")


def _demo_files(folder: str) -> list[str]:
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for n in names:
        if n.startswith("."):
            continue
        if n.lower().endswith(_DEMO_EXTS):
            out.append(os.path.join(folder, n))
    return sorted(out)


# ------------------------------------------------------------ CS2 replays
def _steam_root() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            return str(value)
    except OSError:
        return None


def _library_folders(vdf_path: str) -> list[str]:
    """The "path" entries of steamapps/libraryfolders.vdf."""
    try:
        with open(vdf_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    libs = [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s+"([^"]+)"', text)]
    return libs


def cs2_replays_dirs(override: str = "") -> list[str]:
    """Every Counter-Strike 2 replays folder on this machine."""
    if override:
        return [override] if os.path.isdir(override) else []
    root = _steam_root()
    if not root:
        return []
    libs = [root] + [
        p for p in _library_folders(os.path.join(root, "steamapps", "libraryfolders.vdf"))
        if p and os.path.isdir(p)
    ]
    out = []
    for lib in libs:
        cand = os.path.join(
            lib, "steamapps", "common", "Counter-Strike Global Offensive",
            "game", "csgo", "replays",
        )
        if os.path.isdir(cand) and cand not in out:
            out.append(cand)
    return out


def _read_info(path: str) -> dict:
    """The .dem.info sidecar written by CS2; unknown shape -> {}."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _match_id_from_info(info: dict) -> str | None:
    for scope in (info.get("match") or {}, info):
        for key in ("matchId", "match_id", "matchid"):
            v = scope.get(key)
            if v not in (None, "", 0):
                return str(v)
    return None


def _demo_url_from_info(info: dict) -> str | None:
    for scope in (info.get("demo") or {}, info):
        for key in ("url", "demo_url", "demoUrl"):
            v = scope.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
    return None


def scan_replays(folder: str) -> list[MatchRecord]:
    """Demos in a CS2 replays folder, as records with the state 'on disk'."""
    out: dict[str, MatchRecord] = {}
    if not folder or not os.path.isdir(folder):
        return []
    for path in _demo_files(folder):
        if not path.lower().endswith(".dem"):
            continue  # game-downloaded replays are plain .dem
        stem = os.path.basename(path)[: -len(".dem")]
        info = _read_info(path + ".info")
        mid = _match_id_from_info(info)
        url = _demo_url_from_info(info)
        if not mid and url:
            m = _URL_MATCH.search(url)
            if m:
                mid = m.group(1)
        match_id = mid or f"replays:{stem}"
        try:
            mtime = os.path.getmtime(path)
            played = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            played = None
        rec = MatchRecord(
            provider="steam",
            match_id=match_id,
            played_at=played,
            map_name=None,
            mode=None,
            score=None,
            result=None,
            demo=DemoState.MANUAL,
            demo_ref=path,
            page_url=None,
            extra={"file": os.path.basename(path)},
        )
        out[match_id] = rec
    return sorted(out.values(), key=lambda r: r.played_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def replays_records(replays_dir: str = "") -> list[MatchRecord]:
    """Replays from every CS2 install found (or the override folder)."""
    seen: dict[str, MatchRecord] = {}
    for folder in cs2_replays_dirs(replays_dir):
        for rec in scan_replays(folder):
            seen[rec.match_id] = rec
    return sorted(seen.values(), key=lambda r: r.played_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
