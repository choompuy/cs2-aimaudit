# Architecture

One page about how CS2-AimAudit is put together: the module map, the
one-way dependency rule, the data-driven table schema, and the recipes for
the four most common changes. `tests/check_ui.py` enforces most of what is
written here, so the doc and the guardrails stay honest.

## Module map

| Module                            |     Lines | Responsibility                                                                                                                               |
| --------------------------------- | --------: | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.py`                          |        88 | Entry point only: page config, theme injection, `Ctx` assembly, page dispatcher.                                                             |
| `context.py`                      |       172 | Cached `core` calls (`get_demo`, `get_shots`, `get_duels`, `get_timeline`, `get_replay`) and the `Ctx` dataclass every page renders from.    |
| `shell.py`                        |       475 | App shell: sidebar, demo library, file input, loading, context row, navigation.                                                              |
| `filters_ui.py`                   |       528 | URL state (`_URL_KEYS`) and link migration, presets, callbacks, the filter panel.                                                            |
| `views/overview.py`               |        77 | Overview page (one grid for both teams).                                                                                                     |
| `views/shooting.py`               |       433 | Shooting page: metric tiles, Top-3 issues, charts, the weapons grid.                                                                         |
| `views/duels.py`                  |       197 | Duels page: the duels grid, the by-zone table, the openings table.                                                                           |
| `views/timeline.py`               |       568 | Timeline page (`@st.fragment` replay + duel strip navigation).                                                                               |
| `views/map.py`                    |       104 | Map page (`@st.fragment`, radar plot).                                                                                                       |
| `views/extras.py`                 |        80 | Guide page and the debug expander rendered under every page.                                                                                 |
| `core/model.py`                   |       601 | Constants, the `Demo` container, pure helpers (weapons, angles, `wilson_ci`, `rank_in_lobby`).                                               |
| `core/filters.py`                 |       203 | `Filters`, round halves/pistol rounds, `apply_filters`.                                                                                      |
| `core/loader.py`                  |       897 | Demo unpacking, path resolution, `state.json`, `load_demo` and data preparation.                                                             |
| `core/metrics.py`                 |       964 | `shots_table`, risk model, `build_duels`, `openings_table`, `impact_stats`, `player_summary`, `weapon_stats`.                                |
| `core/replay.py`                  |       357 | `timeline_data`, `replay_data`, radar transforms, `kill_points`.                                                                             |
| `core/checks.py`                  |       353 | `data_checks`, `debug_report`.                                                                                                               |
| `core/__init__.py`                |       134 | Facade: re-exports the public API so `import core` / `core.X` keep working.                                                                  |
| `ui/table.py`                     |       432 | The universal grid renderer: `Col`, `table_payload`, `data_table` (custom component).                                                        |
| `ui/schema.py` + `ui/tables.toml` | 317 + 273 | The table schema: loader, `build_cols`, `table`, `validate`; the TOML is the single source of truth for grids.                               |
| `ui/charts.py`                    |        86 | `style_figure` and shared chart builders.                                                                                                    |
| `ui/format.py`                    |        51 | `fmt_result`, `fmt_first`, `pct`, `num`, `duel_label`.                                                                                       |
| `ui/strip.py`                     |       352 | The duel navigation strip (Timeline).                                                                                                        |
| `theme/`                          |         — | Design tokens (`tokens.toml`), `overrides.css`, the loader; `tools/sync_theme.py` regenerates `.streamlit/config.toml` from the same tokens. |
| `i18n.py`                         |       853 | English/Russian strings; every UI text goes through `t("key")` literals.                                                                     |

Support: `tests/test_core.py` (synthetic-match unit tests), `tests/smoke_app.py`
(AppTest end-to-end scenarios), `tests/check_ui.py` (static guardrails),
`tools/` (`dump_payloads.py` golden grid payloads, `api_snapshot.py` core API
snapshot, `fetch_radars.py`, `inspect_rounds.py`, `sync_theme.py`).

## Dependency direction

```
ui/  theme/  i18n.py          leaf libraries: no Streamlit runs, no app knowledge
        ↑
      core/                    parsing + metrics; NO Streamlit calls
        ↑
   context.py                 cached core calls + Ctx
        ↑
filters_ui.py  shell.py  views/   Streamlit pages and panels
        ↑
      app.py                  entry point, ~90 lines, no logic
```

`tests/check_ui.py` enforces:

1. **No import cycles** anywhere in the graph (DFS over `core`, `ui`,
   `theme`, `views` and the root modules).
2. **Layering**: `core`, `ui` and `theme` never import `app`, `views`,
   `shell`, `filters_ui` or `context`.
3. **Module size**: every app-code module fails the check past 1500 lines
   (warning past 1300). `tests/` are out of scope — splitting them is a
   separate task.
4. **Schema validity**: `schema.validate()` (see below) plus i18n-key and
   token cross-checks.

Rules of the road, in one breath: no logic in `app.py`, no Streamlit in
`core`, colors only through `theme` tokens (never hex/rgb literals),
every UI string through `t("key")` literals, no new widget or
`session_state` keys without checking `tests/smoke_app.py` expectations.

## The table schema (`ui/tables.toml`)

Grids are described by data, not by hand-built `Col(...)` lists in the
pages. Three layers:

- `[metric.<id>]` — one column definition: `kind` (`num | text | badge`),
  `fmt` (a `str.format` spec, units inside: `"{:.1f}%"`, `"{:.0f} u"`),
  `best` (`high | low | none` — what sorts first), `grade`, `bar`, `tone`,
  `sort_by` (another data column holding the raw sort value), `sticky`,
  `label` (an i18n key, default `col_<id>`), `help`, and `tones` for badges
  (raw value → tone token).
- `[table.<id>]` — the column order, the `basic` subset for the
  Basic/All-columns switch, the `stripe` rule (row tint by a data field),
  `max_height`, `selectable`, and `extra` (data columns rows carry but
  never display: sort keys, stripe sources).
- `[table.<id>.override.<col>]` — per-table tweaks on top of the metric
  (e.g. `overview` grades `kdr` with `[0.7, 1.0, 1.4]`, `weapons` turns
  the `hs` flag column into a percentage).

Value colouring (`grade`): `none` keeps the plain text colour; `auto`
grades by the value's position inside the column; `[bad, good]` slides
from red below `bad` to green above `good`; `[bad, ok, good]` adds a
neutral plateau (`ok`) between them — below `ok` the value slides toward
red, above toward green. Bars (`bar`) are cell backgrounds: `pct` for
0..100 values, `rel` for a share of the column maximum; `tone` names the
colour token. All colours resolve through `theme` tokens, never literals.

`ui/schema.py` exposes `build_cols(table_id, all_columns=...)`,
`table(table_id, rows, key=...)` (the one-liner pages call),
`columns(table_id)` (data column names to prepare) and `validate()`.
`validate()` checks that i18n keys exist in both locales, `fmt` actually
formats a test number, `grade`/`best`/`bar`/`tone` values are legal
(`ok` between `bad` and `good`, `bad != good`), `basic ⊆ columns`,
`sort_by`/`stripe`/`extra` references resolve, and colour tokens exist in
`theme/`. It runs as part of `tests/check_ui.py`, so a broken edit to the
TOML fails CI, not the browser.

Four small tables remain plain `st.dataframe` calls on purpose (they show
data the grids do not): the by-zone and openings tables in
`views/duels.py` and the two Top-3 evidence popovers in
`views/shooting.py`.

## How to add a metric (a `tables.toml` row)

1. Compute it: extend the producing function in `core/metrics.py`
   (`player_summary` for overview-style rows, `weapon_stats` for weapons,
   `build_duels` for duels) and add the raw column to the DataFrame.
2. Describe it: add a `[metric.<id>]` block with `fmt`, `best`, `grade`,
   `bar`, `tone` as needed, plus `col_<id>` (and optional
   `col_<id>_help`) in **both** locales in `i18n.py`.
3. Place it: add the id to the `columns` list (and `basic` if it belongs
   in the default view) of the target `[table.<id>]`.
4. Feed it: the page builds the rows dict — add the numpy column under
   the same key (see `views/overview.py` `ov_cells`).

`python tests/check_ui.py` now validates the schema, i18n keys and
tokens; `python tools/dump_payloads.py tools/_payload_check` regenerates
a JSON dump of the built grids if you want a before/after proof.

## How to add a column or a table

A **column** is the recipe above. A **table**:

1. Add `[table.<id>]` with `columns` (order), optional `basic`, `stripe`,
   `extra`, `selectable`, `max_height`; reuse `[metric.*]` blocks or add
   new ones, tweak per-table with `[table.<id>.override.<col>]`.
2. In the page, prepare the rows (one key per data column, including the
   `extra` ones) and call
   `schema.table("<id>", rows, key="<unique_grid_key>")`.
3. Keep widget keys unique — grid keys live in `session_state`.

## How to add a page

1. Create `views/<name>.py` with a single `render(ctx)` function (unpack
   what you need from `ctx` into locals first). `@st.fragment` helpers
   stay top-level functions of that module — see `views/timeline.py`.
2. Register the page id in `PAGES` in `shell.nav` (`shell.py`) and add
   the `tab_<id>` label to both locales in `i18n.py`.
3. Dispatch it in `app.py` (one `if page == "<id>":` line).
4. Add a smoke scenario in `tests/smoke_app.py` if the page carries
   logic worth locking.

## How to add a filter

1. Pick the `session_state` key (`flt_<name>`) and, if the filter should
   survive shared links, add it to `_URL_KEYS` in `filters_ui.py` (URL
   short name → session key).
2. Render the widget in the filter panel (`filters_ui.render`) and keep
   the widget `key` equal to the session key so presets and URL restore
   work.
3. Apply it where the data is cut: `core/filters.py` (`Filters` +
   `apply_filters`) for shot/duel-level filters, or the page-level
   re-slicing in `context.py` / the view when only one view is affected
   (follow the existing rounds/side filters).
4. Add expectations to `tests/test_core.py` (semantics) and a scenario in
   `tests/smoke_app.py` (panel interaction).

## Tests and tools

```bash
python tests/test_core.py        # 417 checks, synthetic match, no .dem needed
python tests/check_ui.py         # guardrails: i18n, schema, layering, sizes
python tests/smoke_app.py --fast # 248 AppTest checks against a fake parser
python tests/smoke_app.py --all  # full matrix, longer
```

`tools/dump_payloads.py` writes the golden `table_payload` JSONs for the
three grids (Basic/All × en/ru); the dumps are git-ignored, regenerate
them when you need a migration proof. `tools/api_snapshot.py` snapshots
the `core` facade API the same way.
