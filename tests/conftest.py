"""Every file the app stores goes to a throw-away data folder, the demo parser
is fake. Both are set before any test module is imported."""

import os
import tempfile

# realpath: on Windows CI the temp dir has an 8.3 short name
os.environ["AIMAUDIT_DATA_DIR"] = os.path.realpath(tempfile.mkdtemp(prefix="cs2aa_data_"))

import pytest  # noqa: E402

import core  # noqa: E402
from fakes import FakeParser, make_match  # noqa: E402

core.loader.DemoParser = core.replay.DemoParser = FakeParser


def pytest_addoption(parser):
    parser.addoption("--full", action="store_true", help="the whole widget matrix and the deep random walk")
    parser.addoption("--demo", help="also run the app on a real .dem file")


@pytest.fixture(autouse=True)
def data_dir(tmp_path_factory, monkeypatch):
    """Every test starts with an empty data folder."""
    monkeypatch.setenv("AIMAUDIT_DATA_DIR", os.path.realpath(tmp_path_factory.mktemp("data")))


@pytest.fixture
def demo():
    FakeParser.demos["match.dem"] = make_match()
    return core.load_demo("match.dem")


@pytest.fixture
def shots(demo):
    return core.add_risk(core.shots_table(demo), 40)


@pytest.fixture
def duels(demo):
    return core.build_duels(demo, 3.0)
