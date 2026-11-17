"""
Golden snapshot check: the safety net for refactor commits.

Regenerates the two "before" snapshots into a temp folder and compares
them byte-for-byte with the committed goldens:

  tests/goldens/payloads/*.json     table_payload() JSON of the three grids
                                    (Basic / All columns x EN / RU) -
                                    produced by tools/dump_payloads.py
  tests/goldens/api_snapshot.txt    every top-level name `core` defines,
                                    with its consumers - produced by
                                    tools/api_snapshot.py

Any refactor commit can prove "nothing changed" by running:

    python tools/check_goldens.py

Fix commits that intentionally change behaviour must regenerate the
goldens (and say so in the commit message):

    python tools/check_goldens.py --update

Both producers are deterministic (verified: two runs produce identical
files), so a diff is always a real behaviour change. Exit code 0 = no
difference, 1 = difference (with the file list), 2 = producer failure.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import store  # noqa: E402  (the one module that knows locations)

GOLDENS = ROOT / "tests" / "goldens"
# scratch lives under data/cache/tmp (start.bat points TEMP there as well);
# tempfile is store's business, not a tool's
SCRATCH = Path(store.data_dir()) / "cache" / "tmp" / "goldens-check"

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        raise SystemExit(2)


def _snapshot_into(tmp: Path) -> None:
    _run([sys.executable, "tools/dump_payloads.py", str(tmp / "payloads")])
    _run([sys.executable, "tools/api_snapshot.py", "--out", str(tmp / "api_snapshot.txt")])


def _compare(fresh_dir: Path) -> list[Path]:
    """Files that differ or exist on only one side (goldens vs fresh)."""
    problems: list[Path] = []
    fresh = sorted(p.relative_to(fresh_dir) for p in fresh_dir.rglob("*") if p.is_file())
    for rel in fresh:
        golden = GOLDENS / rel
        if not golden.exists():
            problems.append(rel)
        elif golden.read_bytes() != (fresh_dir / rel).read_bytes():
            problems.append(rel)
    for golden in sorted(GOLDENS.rglob("*")):
        if golden.is_file() and not (fresh_dir / golden.relative_to(GOLDENS)).exists():
            problems.append(golden.relative_to(GOLDENS))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--update", action="store_true",
                    help="regenerate the committed goldens instead of comparing")
    args = ap.parse_args()

    if args.update:
        _snapshot_into(GOLDENS)
        n = len(list(GOLDENS.rglob("*.json"))) + len(list(GOLDENS.rglob("*.txt")))
        print(f"{n} golden files updated -> {GOLDENS.relative_to(ROOT)}")
        return 0

    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    try:
        _snapshot_into(SCRATCH)
        problems = _compare(SCRATCH)
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)

    if problems:
        print(f"GOLDENS DIFFER ({len(problems)} file(s)):")
        for p in problems:
            print(f"  {p}")
        print("A refactor commit must not change these; a fix commit that")
        print("changes them on purpose runs: python tools/check_goldens.py --update")
        return 1
    print(f"goldens identical ({len(list(GOLDENS.rglob('*')))} files "
          f"compared with a fresh run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
