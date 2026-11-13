"""
Snapshot tool for the table_payload() JSON of the three grids.

Runs the real app.py through streamlit AppTest on the synthetic demo from
tests/smoke_app.py (make_match) and dumps the exact table_payload() JSON
that the three grids (overview / weapons / duels) hand to the renderer -
Basic and All-columns modes, in English and Russian.

Usage, from the repo root:
    python tools/dump_payloads.py OUT_DIR

Run it before and after a renderer change and diff the two directories:
the payloads must not change a byte.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import core  # noqa: E402
import smoke_app  # noqa: E402  (sets AIMAUDIT_DATA_DIR to a temp dir)
import ui.table as ut  # noqa: E402

# DemoParser lives in core.loader / core.replay
core.DemoParser = core.loader.DemoParser = core.replay.DemoParser = smoke_app.FakeParser

# --- recorder: intercept data_table and keep the payload it is about to send
RECORD: dict[str, dict] = {}
LABEL = [""]
_orig_data_table = ut.data_table


def _recording_data_table(rows, cols, *, key, **kw):
    payload = ut.table_payload(
        rows,
        cols,
        selected=kw.get("selected"),
        stripe=kw.get("stripe"),
        clickable=kw.get("on_click") is not None,
        max_height=kw.get("max_height", 620),
        empty=kw.get("empty", ""),
    )
    if LABEL[0]:
        RECORD[LABEL[0]] = payload
    return _orig_data_table(rows, cols, key=key, **kw)


ut.data_table = _recording_data_table
# ui.schema did "from ui.table import data_table" at import time, so it
# holds its own binding: patch it too (ui.table was imported first, above)
try:
    import ui.schema as _us

    _us.data_table = _recording_data_table
except ImportError:
    pass


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "tools" / "_payloads")
    out.mkdir(parents=True, exist_ok=True)

    demo = smoke_app.make_match([1, 2], weapons=("ak47", "awp"))
    LABEL[0] = "overview_basic_en"
    at = smoke_app.open_demo(demo, "payloads.dem", me=smoke_app.S1)

    def step(label, fn):
        LABEL[0] = label
        fn()
        assert not at.exception, f"{label}: {at.exception[0].value!r}"
        assert label in RECORD, f"{label}: no table payload recorded"
        print(f"  ok  {label} ({len(RECORD[label]['rows'])} rows)")

    def page(p):
        at.session_state["page"] = p
        at.run()

    step("overview_basic_en", lambda: None)  # the first open_demo() run
    step("overview_all_en", lambda: at.toggle(key="ov_full").set_value(True).run())
    step("weapons_en", lambda: page("shooting"))
    step("duels_basic_en", lambda: page("duels"))
    step("duels_all_en", lambda: at.toggle(key="du_full").set_value(True).run())
    step("duels_all_ru", lambda: at.selectbox(key="lang").select("ru").run())
    step("duels_basic_ru", lambda: at.toggle(key="du_full").set_value(False).run())
    step("weapons_ru", lambda: page("shooting"))
    step("overview_all_ru", lambda: page("overview"))
    step("overview_basic_ru", lambda: at.toggle(key="ov_full").set_value(False).run())

    for label, payload in RECORD.items():
        path = out / f"{label}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"\n{len(RECORD)} payloads -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
