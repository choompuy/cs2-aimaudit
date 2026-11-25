"""
Compressed-demo handling: .bz2 / .gz / .zip (stdlib) and .zst (the
optional zstandard package) archives are stream-decompressed into the
data folder's cache/unpacked and cached there (never the system temp
dir). A truncated or corrupt archive raises ValueError - the only
exception shape the UI catches - and a half-written .part never
survives a failure.
"""

from __future__ import annotations

import bz2
import gzip
import os
import shutil
import zipfile

import store

__all__ = ["decompressed_demo"]


COMPRESSED_EXTS = {".bz2", ".gz", ".zst", ".zip"}


def _zstandard_module():
    try:
        import zstandard  # optional dependency, only needed for .zst
    except ImportError as e:
        raise ValueError(
            "The .zst demo needs the 'zstandard' package: pip install zstandard"
        ) from e
    return zstandard


def _unpack(ext: str, p: str, tmp: str) -> None:
    """Stream-decompress archive `p` into file `tmp` (may raise on corrupt input)."""
    try:
        if ext == ".bz2":
            with bz2.open(p, "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif ext == ".gz":
            with gzip.open(p, "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif ext == ".zst":
            zstd = _zstandard_module()
            with open(p, "rb") as src, open(tmp, "wb") as dst:
                with zstd.ZstdDecompressor().stream_reader(src) as rdr:
                    shutil.copyfileobj(rdr, dst)
        else:  # .zip
            with zipfile.ZipFile(p) as z:
                members = sorted(n for n in z.namelist() if n.lower().endswith(".dem"))
                if not members:
                    raise ValueError(
                        f"No .dem file inside the archive: {os.path.basename(p)}"
                    )
                with z.open(members[0]) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except ValueError:
        # one of our own error messages - drop the partial .part
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    except Exception as e:  # noqa: BLE001 - broken archive, any library error
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ValueError(
            f"Cannot decompress {os.path.basename(p)}: {type(e).__name__}: {e}"
        ) from e


def decompressed_demo(path: str, tag: str | None = None) -> str:
    # 68 lines, under the 80 cap: the per-format branches share the
    # cache/verify/teardown sequence and read best as one unit.
    """
    Path to a plain .dem for `path`, decompressing transparently.

    Demos are often delivered compressed: Valve serves .dem.bz2, FACEIT
    .dem.gz, tournaments .zip. Supported: .bz2 / .gz / .zip (stdlib) and
    .zst (needs the `zstandard` package). Inside a .zip the first *.dem
    member (alphabetically) is used. The decompressed copy is cached in
    cache/unpacked of the data folder (never the system temp dir) under a
    name derived from the source name + size + mtime, so re-opening the same
    archive does not decompress again. `tag` (the library id) is put into
    that name so deleting a demo from the library also removes its unpacked
    copy. Anything else is returned unchanged.

    A truncated or corrupt archive raises ValueError (whatever the
    decompressor throws - EOFError, BadZipFile, ZstdError, OSError...):
    the UI only catches ValueError, and a raw traceback is not a friendly
    message. A half-written .part is never left behind either - it can be
    as large as the unpacked demo.
    """

    p = os.path.realpath(path)
    ext = os.path.splitext(p)[1].lower()
    if ext not in COMPRESSED_EXTS:
        return p
    stem = os.path.basename(p)
    stem = stem[: -len(ext)] if stem.lower().endswith(ext) else stem
    try:
        st = os.stat(p)
    except OSError as e:
        raise ValueError(f"Cannot read demo file: {p}") from e
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem)[:80]
    if tag and f"__{tag}" not in safe:
        safe = f"{safe}__{tag}"
    target = os.path.join(
        _unpack_dir(),
        f"cs2_aimaudit_{safe}_{st.st_size}_{int(st.st_mtime)}.dem",
    )
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target  # cached from an earlier run
    tmp = target + ".part"

    def _drop_part() -> None:
        try:
            os.remove(tmp)
        except OSError:
            pass

    try:
        _unpack(ext, p, tmp)
    except ValueError:
        _drop_part()
        raise
    except (
        Exception
    ) as e:  # noqa: BLE001 - truncated/corrupt archive, bad zip, zstd error...
        _drop_part()
        raise ValueError(
            f"Cannot unpack {os.path.basename(p)}: {type(e).__name__}: {e}"
        ) from e
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ValueError(f"Decompressed demo is empty: {os.path.basename(p)}")
    os.replace(tmp, target)  # atomic: no half-written cache on crash
    return target


def _unpack_dir() -> str:
    """cache/unpacked in the data folder: the app never writes to the system temp dir."""
    return store.unpacked_dir()
