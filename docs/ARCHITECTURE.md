# Architecture

One page about how CS2-AimAudit is put together: the module map, the
one-way dependency rule, the data-driven table schema, and the recipes for
the four most common changes. `tests/check_ui.py` enforces most of what is
written here, so the doc and the guardrails stay honest.

## Module map

| Module                            |     Lines | Responsibility                                                                                                                               |
| --------------------------------- | --------: | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.py`                          |       127 | Entry point only: page config, theme injection, `Ctx` assembly, the two-level page dispatcher (`_match` is the only pipeline that loads a demo). |
| `context.py`                      |       184 | Cached `core` calls (`get_demo`, `get_shots`, `get_duels`, `get_timeline`, `get_round_replay`) and the `Ctx` dataclass every page renders from. |
| `shell.py`                        |       727 | App shell: the sidebar rail, navigation (`go_page`/`go_match`/`go_tab`, URL state), demo loading, the match header, the context row, the library panel. |
| `filters_ui.py`                   |       548 | URL state (`_URL_KEYS`) and link migration, presets, callbacks, the filter panel (rendered inside the match header).                         |
| `views/matches.py`                |       580 | The Matches page: the unified table (filters, Refresh, Add match, row actions, the download flow).                                        |
| `views/_shared.py`                |        30 | Helpers shared by the pages (`steam_cfg`, `show_error`); started as the Matches/Settings duplicates.                                       |
| `views/dashboard.py`              |        27 | The Dashboard stub (cross-match aggregates land in a future `core/aggregate.py`).                                                            |
| `views/settings.py`               |       214 | The Settings page: language note, analysis sliders, accounts + Steam helper, Library and storage, privacy.                                   |
| `views/overview.py`               |        77 | Overview page (one grid for both teams).                                                                                                     |
| `views/shooting.py`               |       578 | Shooting page: metric tiles, Top-3 issues, charts, the weapons grid.                                                                         |
| `views/duels.py`                  |       198 | Duels page: the duels grid, the by-zone table, the openings table.                                                                           |
| `views/replay.py`                 |       518 | The 2D replay page (`@st.fragment`) and the duel window (`st.dialog`: verdict + chart); feeds the round's icons into `round_payload`.             |
| `views/map.py`                    |       104 | Map page (`@st.fragment`, radar plot).                                                                                                       |
| `views/extras.py`                 |       116 | Guide page (incl. the icon legend) and the debug expander rendered under every match tab.                                                    |
| `core/matchlist.py`               |       201 | The unified match list: provider records + library entries + summaries merged into `MatchRow`s (no Streamlit, no parsing).                   |
| `core/matchstats.py`              |       163 | The per-match summary cache behind the Matches table (compute from a parsed demo, read/write the JSON cache, resolve "me").                 |
| `core/model.py`                   |       788 | Constants, the `Demo` container, pure helpers (weapons, angles, `wilson_ci`, `rank_in_lobby`).                                               |
| `core/filters.py`                 |       203 | `Filters`, round halves/pistol rounds, `apply_filters`.                                                                                      |
| `core/loader.py`                  |       1 180 | Demo unpacking, path resolution, `state.json`, `load_demo` and data preparation.                                                             |
| `core/metrics.py`                 |      1114 | `shots_table`, risk model, `build_duels`, `openings_table`, `impact_stats`, `player_summary` (incl. best / died-to weapons), `weapon_stats`.   |
| `core/replay.py`                  |       720 | `timeline_data`, `round_replay_data`, `round_duels` (incl. kill-modifier flags), `duel_telemetry`, radar transforms.            |
| `core/checks.py`                  |       353 | `data_checks`, `debug_report`.                                                                                                               |
| `core/__init__.py`                |       300 | Facade: re-exports the public API so `import core` / `core.X` keep working.                                                                  |
| `ui/table.py`                     |       478 | The universal grid renderer: `Col`, `table_payload` (num / text / badge / icon cells), `data_table` (custom component).                       |
| `ui/component.py`                 |        38 | Custom-component glue: `declare` + `event_callback`, shared by table / strip / roundrep.                                                |
| `ui/schema.py` + `ui/tables.toml` | 338 + 354 | The table schema: loader, `build_cols`, `table`, `validate`; the TOML is the single source of truth for grids (incl. `[table.matches]`).     |
| `ui/charts.py`                    |        86 | `style_figure` and shared chart builders.                                                                                                    |
| `ui/format.py`                    |        63 | `fmt_result`, `fmt_first`, `pct`, `num`, `duel_label`, `map_name`.                                                                           |
| `ui/strip.py`                     |       352 | The duel navigation strip (Timeline).                                                                                                        |
| `ui/icons.py`                     |       360 | The icon set loader: SVG sanitizer, SHA-256 verification, weapon-name aliases, `weapon_info` / `weapon_cells` / `payload` for grids and the replay. |
| `ui/roundrep.py`                  |       540 | 2D replay component: `round_payload` (pure JSON model, carries the round's `icons` / `wlab`) + `round_replay`; front end in `roundrep.js` / `.css`. |
| `theme/`                          |         — | Design tokens (`tokens.toml`), `overrides.css` (incl. the sidebar rail), the loader; `tools/sync_theme.py` regenerates `.streamlit/config.toml` from the same tokens. |
| `sources/model.py`                |       162 | The match-import model: `MatchRecord`, `DemoState`, the Steam config and the localized `ProviderError` family.                               |
| `sources/http.py`                 |       214 | Shared urllib transport: timeouts, 429/Retry-After and 5xx retries, streaming downloads to `.part`, https-only, `AIMAUDIT_OFFLINE`.           |
| `sources/steam_gc.py`             |       266 | Dependency-free protobuf wire reader for `CMsgGCCStrike15_v2_MatchList` (field numbers from SteamDatabase/Protobufs).                        |
| `sources/steam.py`                |       380 | boiler-writter runner + hash-pinned install, the share-code codec, Valve demo downloads, optional Web API share-code chaining.               |
| `sources/inbox.py`                |       162 | The CS2 replays folder (Watch -> Your Matches) as a match source (registry + libraryfolders.vdf).                                            |
| `sources/__init__.py`             |       169 | Facade: `list_matches`, `fetch_demo`, `import_local` - the only calls the Matches page makes into the package - plus the `matches.json` cache glue (`read_cache` / `write_cache` / `cache_time`). |
| `theme/`                          |         — | Design tokens (`tokens.toml`), `overrides.css`, the loader; `tools/sync_theme.py` regenerates `.streamlit/config.toml` from the same tokens. |
| `i18n.py` + `i18n.toml`           |     24 + 656 | `i18n.toml` is the single source of truth for the strings (one table per language); every UI text goes through `t("key")` literals.          |
| `store.py`                        |       836 | The ONE place that knows where files live: the `data/` folder next to `start.bat`, the demo library (copies + linked demos), caches (parsed / unpacked / matchstats / icons), support notes (`data/logs/notes.log`), parse keys. |
| `state.py`                        |       170 | The `session_state` registry: key-name constants, typed get_*/set_* for the keys shared by 2+ modules, and the navigation mutators (`go_page`/`go_match`/`go_tab`/`leave_match`, re-exported by `shell`). |
| `nativedialog.py`                 |        95 | Native Windows file dialogs (tkinter in a child process): gives the real path a browser upload never reveals.                       |

Support: `tests/test_core.py` (synthetic-match unit tests),
`tests/test_sources.py` (match import: transport, providers, protobuf,
share codes, inbox, privacy), `tests/test_icons.py` (icon sanitizer,
aliases, manifest and fallbacks), `tests/smoke_app.py` (AppTest
end-to-end scenarios), `tests/check_ui.py` (static guardrails),
`tests/check_docs.py` (the module table's line counts stay within ±15%
of the real files),
`tests/check_roundrep_js.py` + `tests/js` (front-end unit tests),
`tools/` (`dump_payloads.py` golden grid payloads, `api_snapshot.py`
core API snapshot, `fetch_radars.py`, `fetch_icons.py`,
`inspect_rounds.py`, `sync_theme.py`).

## Navigation and state

Navigation is two-level and lives in `session_state`
(never only in widget state - Streamlit drops the state of widgets a run
does not render). The shared keys are a REGISTRY (`state.py`): key-name
constants and typed get_*/set_* accessors; a module never touches
another module's key by a string literal. `lang` and `me` are widget-OWNED
keys (the sidebar selectbox / the player selectbox), so the registry only
reads them:

- **Sidebar level**: `page` ∈ `dashboard | matches | settings | guide`,
  switched ONLY through `state.go_page` (re-exported as `shell.go_page`;
  the sidebar buttons - clicking
  Matches lands on the LIST). A fresh session starts on the Matches list.
- **Match view**: inside `page == "matches"` - `match` (the library demo id
  of the open match, or absent = the list) and `match_tab` ∈ `overview |
  shooting | duels | map | replay`. `state.go_match(demo_id, tab)` (the
  `shell.go_match` re-export) is the
  ONE way to open a match (a list row, a download, the old
  links); `state.leave_match` is the `← Матчи` button; `state.go_tab`
  switches tabs inside the open match.
- **Compatibility**: `shell.migrate_nav` turns old single-level page ids
  (and the retired `timeline`) into `matches` + `match_tab` + the last
  opened demo, and re-assigns `match_tab` on the pages where its widget
  does not render. `?p=&m=&tab=` (next to the old `?f=` pack of
  `filters_ui`) carries the same state in the URL; bad values fall back
  safely (`parse_nav_params` is pure and unit-checked).
- Only `app.py::_match` loads a demo; every other page opens on an empty
  library and keeps the filter/slider widget states alive
  (`filters_ui.preserve_filters`, `shell.preserve_analysis_settings`).

The Matches table never parses demos: its number cells come from
`core/matchstats.py`'s JSON cache (`data/cache/matchstats/`, keyed like
the parsed cache), computed at match open (the demo is parsed there
anyway) or right after a row's demo download (the match opens and
computes it on the way in). Who "me" is: the
accounts' SteamID64 when she played, else the `me_sid` remembered in the
library entry (the match view asks once).

## Where files may live

Nothing may be written outside the app folder: `start.bat` points `TEMP`,
`USERPROFILE`, pip's cache and Python's `.pyc` folder into `data/`, and
`store.py` is the only module that decides locations. Everything else asks
`store` (`demos_dir`, `unpacked_dir`, `parsed_dir`, `radars_dir`,
`icons_dir`, `logs_dir`, `state_path`) and never calls `tempfile.gettempdir`,
`mkdtemp`, `Path.home` or reads
`APPDATA` / `LOCALAPPDATA` / `USERPROFILE` itself (`tests/check_ui.py`
enforces it). Demos are added **by path** (a linked entry, nothing copied);
only a browser upload is copied into `data/demos`. Removing a linked demo
never touches the user's file.

## Dependency direction

```
ui/  theme/  i18n.py          leaf libraries: no Streamlit runs, no app knowledge
        ↑
      core/                    parsing + metrics; NO Streamlit calls
        ↑                    (matchlist may import sources.model + store,
                             matchstats may import store: both stay leaf-wards)
        ↑
   context.py                 cached core calls + Ctx
        ↑
filters_ui.py  shell.py  views/   Streamlit pages and panels
        ↑
      app.py                  entry point, ~130 lines, no logic

sources/                    match import (Steam): a leaf like core/ -
                            no Streamlit at all, no views/shell knowledge; may
                            import store.py for paths (store itself is a
                            stdlib-only leaf under everything).
```

`tests/check_ui.py` enforces:

1. **No import cycles** anywhere in the graph (DFS over `core`, `ui`,
   `theme`, `views`, `sources` and the root modules).
2. **Layering**: `core`, `ui`, `theme` and `sources` never import `app`,
   `views`, `shell`, `filters_ui` or `context`; `sources/` additionally
   never imports Streamlit.
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

- `[metric.<id>]` — one column definition: `kind` (`num | text | badge |
  icon`), `fmt` (a `str.format` spec, units inside: `"{:.1f}%"`,
  `"{:.0f} u"`), `best` (`high | low | none` — what sorts first), `grade`,
  `bar`, `tone`, `sort_by` (another data column holding the raw sort
  value), `sticky`, `label` (an i18n key, default `col_<id>`), `help`,
  `title_by` (for `icon` columns: the data column holding the display
  name — the tooltip AND the no-icon text fallback) and `tones` for
  badges (raw value → tone token).
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

## Weapon and status icons (`ui/icons.py`)

The tables (an `icon` cell shows the weapon silhouette) and the 2D replay
(kill feed, duel cards, player list, the canvas name plates) draw flat
white silhouettes in the CS2 kill-feed style. The art is Valve's and is
NEVER committed: `tools/fetch_icons.py` downloads it once into
`data/icons` (pinned SHA-256 per file, sanitized content; `--source game`
can instead decompile newer icons straight out of an installed CS2 via
Source2Viewer-CLI — see docs/ASSETS.md for the whole provenance story,
including what could not be verified against a real install).

`ui/icons.py` is the one loader everything asks:

- `sanitize_svg()` runs when the fetcher saves AND again when the loader
  reads — scripts, `on*` handlers, `foreignObject`, external references
  and DOCTYPEs never survive; a hash mismatch means "no icon", never an
  exception.
- `weapon_key()` / `weapon_info()` / `weapon_cells()` map demo weapon
  names to icon keys (with aliases: `usp` → `usp_silencer`, `p2000` →
  `hkp2000`, `knife_*` → `knife`) plus the localized label.
- `payload(keys)` / `badge_payload(keys)` build the `{key: [data-uri, w,
  h]}` dicts for the grid and replay payloads — only the keys asked for,
  only the ones that exist, so payloads stay small.

Rendering rule: an icon is NEVER inlined as raw SVG. It enters the DOM as
a base64 data URI behind CSS `mask-image` (colour = `background-color`)
or is tinted on canvas through an alpha-mask `source-in` composite — part
of the art carries no white `fill` and would render black otherwise.
Without the icon set every call answers "none" and the UI falls back to
the localized weapon / event name as text; `AIMAUDIT_NO_ICONS=1` opts out
of the download, `AIMAUDIT_ICON_DIR` points tests at a fixture folder.

## How to add a metric (a `tables.toml` row)

1. Compute it: extend the producing function in `core/metrics.py`
   (`player_summary` for overview-style rows, `weapon_stats` for weapons,
   `build_duels` for duels) and add the raw column to the DataFrame.
2. Describe it: add a `[metric.<id>]` block with `fmt`, `best`, `grade`,
   `bar`, `tone` as needed, plus `col_<id>` (and optional
   `col_<id>_help`) in **both** locales in `i18n.toml`.
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

## How to add a navigation item (a sidebar page)

1. Create `views/<name>.py` with a single `render(ctx)` function. It must
   open on an empty library (no demo needed) - only `app.py::_match`
   loads demos.
2. Add the id to `NAV_PAGES` in `shell.py` and an icon to `NAV_ICONS`;
   the sidebar button (`nav_<id>`, `nav_<id>__on` when active) comes
   from that one loop, and `theme/overrides.css` needs the new
   `.st-key-nav_<id>__on button` selector for the active highlight.
3. Add the `nav_<id>` label to BOTH locales in `i18n.toml`
   (`tests/check_ui.py` fails without it).
4. Dispatch it in `app.py` (one `elif page == "<id>":` line; pages
   without the analysis filters stay in the `filters_ui.preserve_filters()`
   branch) and add a smoke scenario.

## How to add a match tab

1. Create `views/<name>.py` with a single `render(ctx)` function (unpack
   what you need from `ctx` into locals first). `@st.fragment` helpers
   stay top-level functions of that module.
2. Add the id to `MATCH_TABS` in `shell.py` (the segmented control of
   the match header picks it up) and the `tab_<id>` label to both
   locales in `i18n.py`.
3. Dispatch it in `app.py::_match` (one `if tab == "<id>":` line).
4. Old links keep working: `shell.migrate_nav` maps a legacy top-level
   page id onto the same-named match tab. Add a smoke scenario.

## How to add a match source (a provider)

The import pipeline is `sources/`: a leaf package with no Streamlit and no
app knowledge (enforced by `tests/check_ui.py`). A new provider is four
small pieces plus one dispatch line:

1. **Model**: reuse `sources/model.py` - build `MatchRecord`s (win/loss from
   the player's own perspective, `demo` = a `DemoState` the UI can act on).
   Failures raise the localized `ProviderError` subclasses, never raw
   exceptions.
2. **Client**: a small class taking `(cfg, *, transport)` - the transport is
   always injected, so tests fake `get_json` / `post_json` / `download` and
   never touch the network. Use `sources/http.py` for anything HTTP: it
   owns the offline gate, https enforcement, retries and `.part` handling.
   Two deliberate stubs live here until a second provider lands:
   `HttpTransport.post_json` (a POST twin of `get_json` for a future
   FACEIT/Web API) and `sources/steam.py::next_share_code` (CS match-history
   pagination). Both are test-covered, neither is called by the app.
3. **Dispatch**: extend `sources/__init__.py::list_matches` / `fetch_demo`
   with the provider id; downloads end in `store.add_demo(...)` so hashing,
   dedup and the library stay in one place, then `_bind` writes the `match`
   field onto the entry.
4. **UI**: `views/matches.py` renders whatever lands in `matches.json`
   (`sources.read_cache`); usually only the connections-row status and the
   row action are new (account fields belong to `views/settings.py`). All
   texts go into `i18n.py` in both locales, badges ride the `aa-badge-*`
   classes from `theme/overrides.css`. `core/matchlist.py` picks the
   provider up automatically through `sources.read_cache`.
5. **Tests**: fixtures in the documented response shapes, a fake transport
   and (for helpers) a fake runner - see `tests/test_sources.py`; add the
   refresh/error path to the smoke scenario in `tests/smoke_app.py`.

Rules that are easy to break: the network runs only on explicit button
presses (opening the page reads the cache only); secrets live in
`data/secrets.json` or environment variables, never in accounts, caches,
logs or URLs; anything downloaded from the network is hash- or
shape-verified before it runs (`boiler-writter.exe` is the precedent).

## Where cross-match aggregates will live (future tabs)

Dashboard / Match / duel-map tabs will need aggregates over many demos.
They will live in a separate module *on top of* `core/` (say
`core/aggregate.py`), fed by the per-match summaries of
`core/matchstats.py` (already cached under `data/cache/matchstats/`, keyed
like the parsed cache: demo id + code version), so an aggregate never
parses demos either. The library entries already carry `match` (provider,
match id, map, score) and `analyzed_at`; `MatchRecord.extra` and the
versioned `matches.json` are the extension points - no schema change
needed to start. `views/dashboard.py` is the stub that will host it.

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
python tests/test_core.py        # 444 checks, synthetic match, no .dem needed
python tests/test_sources.py     # 135 checks, match import, no network
python tests/test_icons.py       # sanitizer / aliases / manifest / fallbacks
python tests/check_ui.py         # guardrails: i18n, schema, layering, sizes
python tests/check_roundrep_js.py # front-end unit tests (node, tests/js)
python tests/smoke_app.py --fast # 285 AppTest checks against a fake parser
python tests/smoke_app.py --all  # full matrix, longer
```

`tools/check_goldens.py` regenerates the golden `table_payload` JSONs of
the three grids (Basic/All × en/ru) and the `core` facade snapshot
(`tools/api_snapshot.py` format) into a scratch folder and byte-compares
them with the committed `tests/goldens/`; `--update` rewrites the goldens
for a deliberate behaviour change. `tools/dump_payloads.py` still dumps
one ad-hoc payload set to a folder of your choice.
