"""
Headless smoke test of the real Streamlit UI (app.py) on the synthetic demos.

For every fake demo from test_core.py the app is started with the demo path
preset, then a few filters / languages / players are switched. Any uncaught
exception in app.py fails the test. Run from the repo root:

    python tests/smoke_app.py            # all demos (a few minutes)
    python tests/smoke_app.py aim.dem   # only some demos
"""

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.chdir(ROOT)

with contextlib.redirect_stdout(io.StringIO()):
    import test_core as tc  # patches core.DemoParser with the fake parser
import core  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

core.STATE_PATH = os.path.join(tempfile.mkdtemp(), "state.json")  # keep ~ clean
TMP = tempfile.mkdtemp()
failures: list[str] = []
runs = 0


def exceptions(at) -> list[str]:
    return [str(e.value)[:200] for e in at.exception]


def step(label, at, change=None):
    global runs
    if change is not None:
        change()
    at.run()
    runs += 1
    for e in exceptions(at):
        failures.append(f"{label}: {e}")


ONLY = sys.argv[1:]  # optional: run only these demos, e.g. `smoke_app.py aim.dem`
for key in list(tc.FakeParser.demos):
    if ONLY and key not in ONLY:
        continue
    path = os.path.join(TMP, key)
    with open(path, "wb") as f:
        f.write(b"x")
    tc.FakeParser.demos[path] = tc.FakeParser.demos[key]
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.session_state["demo_path"] = path
    step(f"{key} start", at)
    if exceptions(at) or at.error:  # e.g. warmup_only.dem shows a clean error
        continue
    players = list(core.load_demo(path).players["sid"])
    weapons = at.multiselect(key="flt_weapons").options
    for lang in ("ru", "en"):
        step(f"{key} lang={lang}", at, lambda: at.selectbox(key="lang").set_value(lang))
    for sid in players:
        step(f"{key} player", at, lambda: at.session_state.__setitem__("me", sid))
        for w in weapons[:3]:
            step(f"{key} weapon={w}", at, lambda: at.multiselect(key="flt_weapons").set_value([w]))
        step(f"{key} no weapons", at, lambda: at.multiselect(key="flt_weapons").set_value([]))
        for res in (["kill"], ["death"], []):
            step(f"{key} result={res}", at, lambda: at.multiselect(key="flt_res").set_value(res))
        if any(x.key == "flt_side" for x in at.selectbox):
            for sd in ("T", "CT", "all"):
                step(f"{key} side={sd}", at, lambda: at.selectbox(key="flt_side").set_value(sd))
    step(f"{key} thr=0", at, lambda: at.slider(key="thr").set_value(0))
    step(f"{key} thr=250", at, lambda: at.slider(key="thr").set_value(250))

print(f"{runs} UI runs on {len(tc.FakeParser.demos)} demos, {len(failures)} failures")
for f in dict.fromkeys(failures):
    print("  FAIL", f)
sys.exit(1 if failures else 0)
