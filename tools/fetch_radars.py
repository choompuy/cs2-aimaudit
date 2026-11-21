#!/usr/bin/env python3
"""
Downloads the radar images and their calibration for the Map page.

The images are Valve's radar art converted to PNG and published in the open
awpy-data release (github.com/pnxenopoulos/awpy-data). They are kept on YOUR
machine in the data folder next to start.bat and are not part of this
repository. The app itself never touches the network: only this script does.

    python tools\\fetch_radars.py               # download (or re-download) now
    python tools\\fetch_radars.py --if-missing  # what start.bat runs on every start
    python tools\\fetch_radars.py --dest DIR    # another folder (AIMAUDIT_RADAR_DIR=DIR)
    python tools\\fetch_radars.py --tag 2000942 # another awpy-data release

--if-missing is made for the launcher and never blocks it:
  * the radars are already there  -> does nothing, instantly;
  * AIMAUDIT_NO_RADARS=1 is set   -> does nothing (opt out of the download);
  * no internet / GitHub blocked  -> says so, exits 0, and does not retry for
    a day (the Map page still works on a plain grid in game units).

Result: DIR/map_data.json and DIR/<map>.png (+ <map>_lower.png for two-level
maps), flat in one folder.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile

# the data folder lives next to the launcher (see store/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402
from tools import fetchutil  # noqa: E402
from tools.fetchutil import FAILED_MARK  # noqa: F401 - tests read this

# the seams tests monkeypatch: module-level names, shared code in fetchutil
_get = fetchutil.get
_write = fetchutil.write

REPO = "pnxenopoulos/awpy-data"
DEFAULT_TAG = "2000942"
URL = "https://github.com/{repo}/releases/download/{tag}/{name}"
SAFE_PNG = re.compile(r"^radars/[a-z0-9_]+\.png$")


def present(dest: str) -> bool:
    """The radars are installed: the calibration and at least one image."""
    try:
        names = os.listdir(dest)
    except OSError:
        return False
    return "map_data.json" in names and any(n.endswith(".png") for n in names)


def download_radar_set(dest: str, tag: str, timeout: float) -> int:
    """Download into `dest`; returns the number of images. Raises on any failure."""
    meta = _get(URL.format(repo=REPO, tag=tag, name="map_data.json"), timeout)
    json.loads(meta)  # fail early on an error page
    archive = _get(URL.format(repo=REPO, tag=tag, name="images.zip"), timeout)
    os.makedirs(dest, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for name in zf.namelist():
            if SAFE_PNG.match(name):  # only plain radars/<map>.png members
                _write(os.path.join(dest, os.path.basename(name)), zf.read(name))
                n += 1
    if n == 0:
        raise ValueError("the archive holds no radar images")
    # the calibration goes last: its presence means the set is complete
    _write(os.path.join(dest, "map_data.json"), meta)
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest", default=None, help="folder for the radars (default: data\\radars)")
    ap.add_argument("--tag", default=DEFAULT_TAG)
    ap.add_argument(
        "--if-missing",
        action="store_true",
        help="launcher mode: do nothing when the radars are there, never fail",
    )
    args = ap.parse_args(argv)
    dest = args.dest or store.radars_dir()
    auto = args.if_missing

    if auto:
        if os.environ.get("AIMAUDIT_NO_RADARS"):
            return 0
        if present(dest):
            return 0
        if fetchutil.recently_failed(dest):
            print("  Radar images: the last download failed, not retrying yet.")
            print("  Retry any time with:  python tools\\fetch_radars.py")
            return 0
        print("  Downloading the radar images for the Map page (about 3 MB, once)...")

    try:
        n = download_radar_set(dest, args.tag, timeout=20 if auto else 60)
    except Exception as e:  # noqa: BLE001 - one friendly message for any failure
        if not auto:
            print(f"download failed: {e}", file=sys.stderr)
            return 1
        fetchutil.mark_failed(dest, e)
        print(f"  Could not download the radars ({e}).")
        print("  The Map page still works on a plain grid. Retry later with:")
        print("      python tools\\fetch_radars.py")
        return 0
    fetchutil.clear_failed(dest)
    print(f"  Radar images ready: {n} maps in {dest}")
    if os.path.abspath(dest) != os.path.abspath(store.radars_dir()):
        print(f"  set AIMAUDIT_RADAR_DIR={dest} before starting the app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
