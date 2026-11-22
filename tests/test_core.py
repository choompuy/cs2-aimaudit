"""
Synthetic tests for CS2-AimAudit core.py with a fake demoparser2.

Builds a tiny fake match (4 players, warmup shots, a duel, a teamkill,
a lonely shot) and verifies every core metric without a real .dem file.
Run from the repo root: python tests/test_core.py

Layout: the shared header (env, imports, check(), the fake parser) is
tests/core_sections/_shared.py; every section module runs its checks at
import, in the original order, so the sequence and the totals are the
same as before the split.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core_sections import (  # noqa: E402,F401 - each module runs its sections at import
    s01_metrics_tables,
    s02_first_shots_checks,
    s03_state_filters,
    s04_aim_model,
    s05_live_impact,
    s06_replay_payload,
    s07_store_tools,
)
from core_sections._shared import FAIL, PASS  # noqa: E402

print(f"\n{PASS} passed, {FAIL} failed")
if __name__ == "__main__":
    sys.exit(1 if FAIL else 0)
