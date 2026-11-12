#!/usr/bin/env python3
"""
One-time download of the radar images and their calibration for the Map page.

This is the ONLY place in the project that touches the network, and only when
you run it yourself - the app never downloads anything. The files come from
the public awpy-data release (github.com/pnxenopoulos/awpy-data), they are
Valve's radar art converted to PNG, kept on YOUR machine and not part of this
repository.

    python tools/fetch_radars.py                 # -> <app folder>/data/radars
    python tools/fetch_radars.py --dest DIR      # then set AIMAUDIT_RADAR_DIR=DIR
    python tools/fetch_radars.py --tag 2000942   # another awpy-data release

Result: DIR/map_data.json and DIR/<map>.png (+ <map>_lower.png for two-level
maps), flat in one folder. If awpy is installed and has already downloaded its data
(~/.awpy), the app finds that too and you do not need this script.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

# the data folder lives next to the launcher (see store.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402

REPO = "pnxenopoulos/awpy-data"
DEFAULT_TAG = "2000942"
URL = "https://github.com/{repo}/releases/download/{tag}/{name}"
SAFE_PNG = re.compile(r"^radars/[a-z0-9_]+\.png$")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "cs2-aimaudit"})
    with urllib.request.urlopen(
        req, timeout=60
    ) as resp:  # noqa: S310 - fixed https host
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest", default=store.radars_dir())
    ap.add_argument("--tag", default=DEFAULT_TAG)
    args = ap.parse_args()

    try:
        print(f"downloading map_data.json ({REPO} {args.tag}) ...")
        meta = _get(URL.format(repo=REPO, tag=args.tag, name="map_data.json"))
        json.loads(meta)  # fail early on an error page
        print("downloading images.zip (about 3 MB) ...")
        archive = _get(URL.format(repo=REPO, tag=args.tag, name="images.zip"))
    except Exception as e:  # noqa: BLE001 - one friendly message for any failure
        print(f"download failed: {e}", file=sys.stderr)
        return 1

    os.makedirs(args.dest, exist_ok=True)
    with open(os.path.join(args.dest, "map_data.json"), "wb") as fh:
        fh.write(meta)
    n = 0
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for name in zf.namelist():
            if not SAFE_PNG.match(name):  # only plain radars/<map>.png members
                continue
            # flat: data/radars/de_mirage.png next to map_data.json
            with open(os.path.join(args.dest, os.path.basename(name)), "wb") as fh:
                fh.write(zf.read(name))
            n += 1
    print(f"done: {n} radar images in {args.dest}")
    if os.path.abspath(args.dest) != os.path.abspath(store.radars_dir()):
        print(f"set AIMAUDIT_RADAR_DIR={args.dest} before starting the app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
