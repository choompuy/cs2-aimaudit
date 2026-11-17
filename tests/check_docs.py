"""
Docs numbers machine-checked (F6): the module table in
docs/ARCHITECTURE.md carries per-file line counts, and this script keeps
them honest - every count must be within +-15% of the file's real size,
so the table cannot silently rot while the code moves.

Run from the repo root: python tests/check_docs.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ARCHITECTURE.md"

# | `file.py` (+ `other.toml`)?  |  NNN (+ MMM)?  | description |
# The count cell class must include "+": without it combined "NNN + MMM"
# rows never matched and were silently skipped (the i18n row's count
# drifted 656 -> 1306 with the check green).
ROW = re.compile(
    r"^\|\s*`([^`]+)`(?:\s*\+\s*`([^`]+)`)?\s*\|\s*([0-9 +\u2014\u2013-]+)\|"
)
NUM = re.compile(r"\d+")
TOL = 0.15

fail = 0
rows = 0
for line in DOC.read_text(encoding="utf-8").splitlines():
    m = ROW.match(line)
    if not m:
        continue
    files = [f for f in m.group(1, 2) if f]
    # "1 180" is one number with a thin space separator, not two numbers
    cell = re.sub(r"(?<=\d) (?=\d{3}\b)", "", m.group(3))
    counts = [int(x) for x in NUM.findall(cell)]
    if len(counts) != len(files):
        # "—" rows (theme/, and the like) have no numbers at all
        if not counts:
            continue
        print(f"  FAIL  {files[0]}: {len(counts)} numbers for {len(files)} files")
        fail += 1
        rows += 1
        continue
    rows += 1
    for name, doc_n in zip(files, counts):
        p = ROOT / name
        if not p.is_file():
            print(f"  FAIL  {name}: listed in the table but missing")
            fail += 1
            continue
        real_n = len(p.read_text(encoding="utf-8").splitlines())
        if abs(doc_n - real_n) > TOL * real_n:
            print(
                f"  FAIL  {name}: table says {doc_n}, file has {real_n} "
                f"({abs(doc_n - real_n) / real_n:.0%} > {TOL:.0%})"
            )
            fail += 1
        else:
            print(f"  ok    {name}: {doc_n} vs {real_n}")

print(f"\n{rows} module-table rows checked, {fail} failed")
if __name__ == "__main__":
    sys.exit(1 if fail else 0)
