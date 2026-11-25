"""Compressed demos (.bz2 / .gz / .zip / .zst), unpacked into the data folder's cache. A
broken archive raises ValueError and leaves no .part behind."""

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
    """Path to a plain .dem for `path`, decompressing transparently (the first *.dem of a
    .zip). Cached under source name + size + mtime; `tag` (the library id) lets
    delete_demo find the copy. Anything else is returned unchanged."""

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
