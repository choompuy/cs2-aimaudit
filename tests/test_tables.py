"""The table payload model and the tables.toml schema."""

import ui.schema as schema
from ui.table import Col, table_payload


def test_table_payload():
    _rows = {
        "name": ["a", "b", "c"],
        "acc": [50.0, 100.0, float("nan")],
        "n": [10, 40, 20],
        "err": [1.5, 0.5, 2.5],
        "res": ["kill", "death", "kill"],
    }
    _cols = [
        Col("name", "Name", kind="text"),
        Col("acc", "Acc", fmt="{:.1f}%", bar="pct", best="high"),
        Col("n", "N", fmt="{:.0f}", bar="rel"),
        Col("err", "Err", fmt="{:.1f}°", best="low"),
        Col(
            "res", "Res", kind="badge", tones={"kill": "positive", "death": "negative"}
        ),
    ]
    _pl = table_payload(_rows, _cols, selected=1, stripe=["info", "bogus", None])
    _cell = lambda r, c: _pl["rows"][r]["c"][c]  # noqa: E731
    assert _cell(0, 1)[2] == 0.5 and _cell(1, 1)[2] == 1.0, "table: pct bar fraction"
    assert _cell(0, 2)[2] == 0.25 and _cell(1, 2)[2] == 1.0, "table: rel bar is a share of the column max"
    assert _cell(2, 1)[0] is None and _cell(2, 1)[1] == "—" and _cell(2, 1)[2] is None, "table: empty number -> raw None, dash, no bar"
    assert [c["f"] for c in _pl["cols"]] == ["asc", "desc", "desc", "asc", "asc"], "table: first click sorts best value first"
    assert _cell(0, 4)[3] == "positive" and _cell(1, 4)[3] == "negative", "table: badge tone by raw value"
    assert _pl["rows"][0]["s"] == "info" and _pl["rows"][1]["s"] is None, "table: unknown stripe tone dropped"
    assert _pl["sel"] == 1 and _cell(0, 3)[1] == "1.5°", "table: selected row and display format"


def test_table_grade():
    def _scores(vals, **kw):
        pl = table_payload({"v": vals}, [Col("v", "V", **kw)])
        return [r["c"][0][4] for r in pl["rows"]]
    assert _scores([10, 20, 30], grade="auto") == [0.0, 0.5, 1.0], "grade auto: best value is 1, worst 0"
    assert _scores([10, 20, 30], grade="auto", best="low") == [1.0, 0.5, 0.0], "grade auto: best=low flips the scale"
    assert _scores([5, 5, 9], grade="auto") == [None, None, None], "grade auto: < 3 distinct values -> no colour"
    assert (
        _scores([1.0], grade=(0.5, 1.5)) == [0.5]
        and _scores([0.5], grade=(1.5, 0.5)) == [1.0]
    ), "grade (bad, good): order sets the direction"
    assert (
        _scores([0.7, 0.85, 1.0, 1.4, 9.0], grade=(0.7, 1.0, 1.4))
        == [0.0, 0.3, 0.6, 1.0, 1.0]
    ), "grade (bad, ok, good): ok is the neutral colour"
    assert (
        _scores([None, 3.0], grade=(0, 6)) == [None, 0.5]
        and _scores([1.0, 2.0]) == [None, None]
    ), "grade: none / empty cell -> no colour"


def test_table_schema():
    errs = schema.validate()
    assert not errs, "tables.toml schema valid"
    def _spec(**m):
        return {"metric": {"m": m}, "table": {"t": {"columns": ["m"]}}}
    assert schema.validate(_spec(label="col_kills")) == [], "schema: minimal spec valid"
    # negative examples: config mistakes must fail validation, not render
    assert schema.validate(_spec(fmt="{:.2")) != [], "schema: broken fmt rejected"
    assert schema.validate(_spec(label="col_nope")) != [], "schema: unknown i18n key rejected"
    assert schema.validate(_spec(grade=[0.0, 5.0, 1.0])) != [], "schema: grade ok outside bad..good rejected"
    assert schema.validate(_spec(grade=[1.0, 1.0])) != [], "schema: grade bad == good rejected"
    assert schema.validate(_spec(tone="magenta")) != [], "schema: unknown tone rejected"
    assert schema.validate(_spec(kind="text", bar="pct")) != [], "schema: bar on a text column rejected"
    assert (
        schema.validate(
            {"metric": {"m": {}}, "table": {"t": {"columns": ["m"], "basic": ["x"]}}}
        )
        != []
    ), "schema: basic outside columns rejected"
    assert (
        schema.validate(
            {
                "metric": {"m": {"sort_by": "nope"}},
                "table": {"t": {"columns": ["m"], "extra": []}},
            }
        )
        != []
    ), "schema: dangling sort_by rejected"
    # the migrated grids really build through the schema
    for tid, n_basic, n_all in (
        ("overview", 9, 20),
        ("weapons", 10, 10),
        ("duels", 9, 18),
        ("matches", 9, 9),
    ):
        cols = schema.build_cols(tid, t=lambda k, **kw: k)
        cols_all = schema.build_cols(tid, all_columns=True, t=lambda k, **kw: k)
        assert (
            len(cols) == n_basic
            and len(cols_all) == n_all
            and all(type(c) is Col for c in cols + cols_all)
        ), f"schema: {tid} builds ({n_basic} basic / {n_all} all)"
