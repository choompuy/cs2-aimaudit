# Architecture

One page about how CS2-AimAudit is put together: the module map, the
one-way dependency rule, the data-driven table schema, and the recipes for
the four most common changes. `tests/test_static.py` enforces most of what is
written here, so the doc and the guardrails stay honest.

## Module map

| Module | Responsibility |
| --- | --- |
| `app.py` | Entry point only: page config, theme injection, the `st.Page` bodies (`_PAGE_FNS`) handed to `shell.navigation`, and `_match` - the only pipeline that loads a demo. |
| `context.py` | Cached `core` calls (`get_demo`, `get_shots`, `get_duels`, `get_timeline`, `get_round_replay`) and the `Ctx` dataclass every page renders from. |
| `shell.py` | App shell: `st.navigation` + the `st.page_link` rail + the language box (`navigation`), the URL state (`?m=&tab=`), `go_page`/`go_match`, demo loading, the match header (`map · score`, the demo info popover), the player row (incl. the identity menu), the ask card / remember logic, the library panel. |
| `filters_ui.py` | URL state (`_URL_KEYS`), presets, callbacks, the filter bar, the Duels page's kill/death control (`result_control`). |
| `views/matches.py` | The Matches page: the unified table (filters, Refresh, Add match, row actions, the single / multi download + on-the-spot analysis flow). |
| `views/_shared.py` | Helpers shared by the pages (`steam_cfg`, `show_error`). |
| `views/dashboard.py` | The Dashboard stub (cross-match aggregates land in a future `core/aggregate.py`). |
| `views/settings.py` | The Settings page: language note, analysis sliders (`persist_state="session"`), accounts + Steam helper, Library and storage, privacy. |
| `views/overview.py` | Overview page (one grid for both teams). |
| `views/shooting.py` | Shooting page: metric tiles, Top-3 issues (`st.badge` severities), charts, the weapons grid. |
| `views/duels.py` | Duels page: the duels grid, the by-zone table, the openings table. |
| `views/replay.py` | The 2D replay page (`@st.fragment`) and the duel window (`st.dialog`: verdict + chart). |
| `views/map.py` | Map page (`@st.fragment`, radar plot). |
| `views/extras.py` | Guide page and the debug expander rendered under every match tab. |
| `core/matchlist.py` | The unified match list: provider records + library entries + summaries merged into `MatchRow`s (no Streamlit, no parsing). |
| `core/matchstats.py` | The per-match summary cache behind the Matches table (compute from a parsed demo, read/write/drop the JSON cache, resolve "me"). |
| `core/model.py` | Constants, the `Demo` container, pure helpers (weapons, angles, `wilson_ci`, `rank_in_lobby`). |
| `core/filters.py` | `Filters`, round halves/pistol rounds, `apply_filters`. |
| `core/ids.py` | SteamID resolution (`build_id_lookup` / `fix_ids`) and frame normalization (punch, alive, fire-tick speed). |
| `core/unpack.py` | Compressed demos: `.bz2` / `.gz` / `.zip` / `.zst` stream-decompression into `cache/unpacked`, `.part` safety, ValueError contract. |
| `core/phases.py` | The parse phases `load_demo` walks through (players, events, warmup / knife / restart exclusion, tick table, mouse, totals, round windows). |
| `core/loader.py` | `load_demo` orchestration + `PARSE_VERSION` (hash of the core package), `state.json`, per-round sides, the parsed-demo cache. |
| `core/shots.py` | `shots_table` (one row per gun shot: speed, dist, state, hit), `hurts_table` (the hits with their round, side and the dist of their shot, for the filters) + `add_stop_quality` / `add_risk`. |
| `core/duels.py` | `build_duels`, `duels_for`: the shots both duelists fired, aimed at each other, with the first-shot numbers. |
| `core/openings.py` | Round structure: `round_outcomes` / `round_winners`, `openings_table`, the shared `_enemy_kill_rows` / `_round_side_keep` / `_top_weapon`. |
| `core/impact.py` | `impact_stats`: openings, trades, multi-kills, assists from the enemy-kill rows. |
| `core/summary.py` | `player_summary` (overview row, best / died-to weapons), `weapon_stats`, `hitgroup_counts`, `_hurts_of`. |
| `core/replay.py` | `timeline_data`, `round_replay_data` (incl. the `ECONOMY_PROPS` tick columns), `round_duels` (incl. kill-modifier flags), `duel_telemetry`, radar transforms. |
| `core/checks.py` | `data_checks`, `debug_report`. |
| `core/__init__.py` | Facade: star-imports the `__all__` of every submodule. |
| `ui/table.py` + `.js` / `.css` | The universal grid renderer: `Col`, `table_payload` (num / text / badge / icon cells + the checkbox column), `data_table` (custom component). |
| `ui/component.py` | Custom-component glue shared by table / strip / roundrep: declares `ui/X.js` + `ui/X.css` with `ui/common.js` in front, wires the event callbacks, renders the payload. |
| `ui/schema.py` + `ui/tables.toml` | The table schema: loader, `build_cols`, `table` (selected/on_click + the checkbox column passthrough), `validate`; the TOML is the single source of truth for grids (incl. `[table.matches]`). |
| `ui/charts.py` | `style_figure` and shared chart builders. |
| `ui/format.py` | `fmt_result`, `fmt_first`, `pct`, `num`, `duel_label`, `map_name`. |
| `ui/strip.py` + `.js` / `.css` | The duel navigation strip (Timeline). |
| `ui/icons.py` | The icon set loader: SVG sanitizer, SHA-256 verification, weapon-name aliases, `weapon_cells` for grids, `bundle` / `weapon_labels` for the replay. |
| `ui/roundrep.py` | 2D replay component: `round_payload` (pure JSON model, carries the whole `icons` / `wlab` set, economy runs, `thr`) + `round_replay`; front end in `roundrep.js` / `.css`. |
| `theme/` | Design tokens (`tokens.toml`), `overrides.css` (tabular digits only), the loader; `tools/sync_theme.py` regenerates `.streamlit/config.toml` from the same tokens. |
| `sources/model.py` | The match-import model: `MatchRecord`, `DemoState`, the Steam config and the localized `ProviderError` family. |
| `sources/http.py` | Shared urllib transport: timeouts, 429/Retry-After and 5xx retries, streaming downloads to `.part`, https-only, `AIMAUDIT_OFFLINE`. |
| `sources/steam_gc.py` | Dependency-free protobuf wire reader for `CMsgGCCStrike15_v2_MatchList` (field numbers from SteamDatabase/Protobufs). |
| `sources/steam.py` | boiler-writter runner + hash-pinned install, the share-code codec, Valve demo downloads. |
| `sources/inbox.py` | The CS2 replays folder (Watch -> Your Matches) as a match source (registry + libraryfolders.vdf). |
| `sources/__init__.py` | Facade: `list_matches`, `fetch_demo`, `import_local` - the only calls the Matches page makes into the package - plus the `matches.json` cache glue (`read_cache` / `write_cache` / `cache_time`). |
| `i18n.py` | Thin loader: `STR`/`LANGS`/`make_t` built from the `i18n/<lang>.toml` files; fails fast on a missing `en.toml`, a non-flat file or a file/LANGS mismatch. |
| `i18n/en.toml` + `i18n/ru.toml` | The UI strings, one flat table per language; every UI text goes through `t("key")` literals, missing keys fall back to `en.toml`. |
| `store/` (package) | The ONE place that knows where files live; the facade re-exports everything so `store.X` is unchanged. |
| `store/paths.py` | The `data/` folder next to `start.bat`: AIMAUDIT_DATA_DIR, the read-only fallback, every subfolder, file names / demo ids, `log_note`. |
| `store/library.py` | The demo library: copies AND linked demos, add / relink / delete, the disk sync that adopts dropped files. |
| `store/caches.py` | Parsed / matchstats caches (parse keys, LRU pruning, drop_matchstats), the storage report and the clear actions. |
| `store/jsonio.py` | `read_json` / `write_json`: atomic writes, a broken file reads as empty. |
| `store/importdata.py` | accounts.json / matches.json: atomic writes, a corrupt file counts as empty. |
| `state.py` | The `session_state` registry: `Key` objects for the keys shared by 2+ modules, and the match-level mutators (`go_tab`/`leave_match`). |
| `nativedialog.py` | Native Windows file dialogs (tkinter in a child process): gives the real path a browser upload never reveals. |

Support: `tests/` (pytest: unit tests on synthetic matches, `test_app.py`
and `test_walk.py` drive the real app under AppTest, `test_static.py` holds
the static guardrails, `test_roundrep_js.py` + `tests/js` the front end),
`tools/` (`fetch_radars.py`, `fetch_icons.py`, `inspect_rounds.py`,
`sync_theme.py`).

## Navigation and state

Navigation is native: `shell.navigation()` registers the four pages
through `st.navigation` (position `"hidden"` - the rail is the app's own
`st.page_link` list in the sidebar, so the language box sits underneath)
and the page itself lives in the URL path (`/dashboard`, `/matches`, ...).
`app.py` owns the page bodies (`_PAGE_FNS`); `shell` never imports `views`,
so the import graph stays one-way. The shared keys are a REGISTRY
(`state.py`): `Key` objects with get / set / pop; a module
never touches another module's key by a string literal. `lang` and `me`
are widget-OWNED keys (the sidebar selectbox / the player selectbox), so
the registry only reads them:

- **Page level**: `page` mirrors the `st.Page` that is running -
  `navigation()` records it before anything else runs. `shell.go_page` (the dashboard /
  settings buttons) is the programmatic switch; the rail itself is
  `st.page_link`, whose click clears the query params - so the Matches
  item always lands on the LIST. A fresh session starts on the Matches
  list (`default=True`).
- **Match view**: inside the `matches` page - `match` (the library demo
  id of the open match, or absent = the list) and `match_tab` ∈ `overview
  | shooting | duels | map | replay`. `shell.go_match(demo_id, tab)` is
  the ONE way to open a match (a list row, a download): it sets the
  registry keys and the shareable `?m=`. `?m=` is the truth about the open match - `apply_match_url`
  re-reads it on every Matches run, so a rail click (params cleared)
  lands on the list and a reload reopens the match. `state.leave_match`
  is the `← Матчи` button; `state.go_tab` switches tabs inside the open
  match (`?tab=` is applied once per session, then the segmented control
  owns the tab and `sync_nav_url` mirrors it into the URL).
- **Shared links** are plain query params only: `?m=&tab=` (the open match),
  the analysis filters (`filters_ui._URL_KEYS`) and the list filters
  (`?fp&fmap&fkind&fres`). Unknown values fall back to the defaults.
- Only `app.py::_match` loads a demo. Widget values that must survive a
  page switch (which drops unmounted widget state) carry
  `persist_state="session"`: the analysis sliders, every `flt_*` filter
  control, `match_tab`, the `mflt_*` list filters and the tab toggles.

The Matches table never parses demos: its number cells come from
`core/matchstats.py`'s JSON cache (`data/cache/matchstats/`, keyed like
the parsed cache), computed at match open (the demo is parsed there
anyway) or right after a demo download - a single row's download opens
the match, a multi-download (the checkbox column + "Download selected")
analyzes every finished entry on the spot and opens the newest one. Who
"me" is: the `me_sid` remembered in the library entry first (the
explicit per-match pick - the ask card without the "remember" checkbox,
or the "this is not me" identity menu of the player row), else the accounts'
SteamID64 when she played; the "remember" variant of either picker
writes the SteamID into `accounts.json`, so future matches (downloads
included) resolve on their own.

## Where files may live

Nothing may be written outside the app folder: `start.bat` points `TEMP`,
`USERPROFILE`, pip's cache and Python's `.pyc` folder into `data/`, and
The `store` package is the only module that decides locations. Everything
else asks it (`demos_dir`, `unpacked_dir`, `parsed_dir`, `radars_dir`,
`icons_dir`, `logs_dir`, `state_path`) and never calls `tempfile.gettempdir`,
`mkdtemp`, `Path.home` or reads
`APPDATA` / `LOCALAPPDATA` / `USERPROFILE` itself (`tests/test_static.py`
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
                            import store for paths (the store package is a
                            stdlib-only leaf under everything).
```

`tests/test_static.py` enforces:

1. **No import cycles** anywhere in the graph (DFS over `core`, `ui`,
   `theme`, `views`, `sources` and the root modules).
2. **Layering**: `core`, `ui`, `theme` and `sources` never import `app`,
   `views`, `shell`, `filters_ui` or `context`; `core`, `sources`
   and `store` never import Streamlit.
3. **Module size**: every app-code module fails the check past 1500 lines.
4. **i18n, colours, launcher**; the table schema is in `tests/test_tables.py`.

Rules of the road, in one breath: no logic in `app.py`, no Streamlit in
`core`, colors only through `theme` tokens (never hex/rgb literals),
every UI string through `t("key")` literals, no new widget or
`session_state` keys without checking `tests/test_app.py` expectations.

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
`theme/`. It runs in `tests/test_tables.py`, so a broken edit to the
TOML fails CI, not the browser.

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
- `weapon_id()` / `weapon_key()` / `weapon_cells()` map demo weapon
  names to icon keys (with aliases: `usp` → `usp_silencer`, `p2000` →
  `hkp2000`, `knife_*` → `knife`) plus the localized label.
- `payload(keys)` builds the `{key: [data-uri, w, h]}` dicts of the grids;
  `bundle()` / `weapon_labels()` are the whole set for the 2D replay.

Rendering rule: an icon is NEVER inlined as raw SVG. It enters the DOM as
a base64 data URI behind CSS `mask-image` (colour = `background-color`)
or is tinted on canvas through an alpha-mask `source-in` composite — part
of the art carries no white `fill` and would render black otherwise.
Without the icon set every call answers "none" and the UI falls back to
the localized weapon / event name as text; `AIMAUDIT_NO_ICONS=1` opts out
of the download, `AIMAUDIT_ICON_DIR` points tests at a fixture folder.

## How to add a metric (a `tables.toml` row)

1. Compute it: extend the producing function in the metric module it
   belongs to (`core/summary.py::player_summary` for overview-style rows,
   `weapon_stats` for weapons, `core/duels.py::build_duels` for duels,
   `core/shots.py::shots_table` for shot-level cells) and add the raw
   column to the DataFrame.
2. Describe it: add a `[metric.<id>]` block with `fmt`, `best`, `grade`,
   `bar`, `tone` as needed, plus `col_<id>` (and optional
   `col_<id>_help`) in **both** language files (`i18n/en.toml`,
   `i18n/ru.toml`).
3. Place it: add the id to the `columns` list (and `basic` if it belongs
   in the default view) of the target `[table.<id>]`.
4. Feed it: the page builds the rows dict — add the numpy column under
   the same key (see `views/overview.py` `ov_cells`).

`pytest` validates the schema, i18n keys and tokens.

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
2. Add the id to `NAV_PAGES` in `state.py` and an icon to `NAV_ICONS` in `shell.py`;
   `shell.navigation` derives the `st.Page` (url path, title) and the
   `st.page_link` rail entry from that one loop - no CSS, no per-item
   markup (Streamlit marks the active item itself).
3. Add the `nav_<id>` label to BOTH language files (`i18n/en.toml`,
   `i18n/ru.toml`) — `tests/test_static.py` fails without it.
4. Provide the body in `app.py` (`_PAGE_FNS["<id>"]`; pages that never
   show the analysis filters need nothing else - the controls carry
   `persist_state="session"`) and add a smoke scenario.

## How to add a match tab

1. Create `views/<name>.py` with a single `render(ctx)` function (unpack
   what you need from `ctx` into locals first). `@st.fragment` helpers
   stay top-level functions of that module.
2. Add the id to `MATCH_TABS` in `state.py` (the segmented control of
   the match header picks it up) and the `tab_<id>` label to both
   locales in `i18n.py`.
3. Dispatch it in `app.py::_match` (one `if tab == "<id>":` line).
4. Add a smoke scenario.

## How to add a match source (a provider)

The import pipeline is `sources/`: a leaf package with no Streamlit and no
app knowledge (enforced by `tests/test_static.py`). A new provider is four
small pieces plus one dispatch line:

1. **Model**: reuse `sources/model.py` - build `MatchRecord`s (win/loss from
   the player's own perspective, `demo` = a `DemoState` the UI can act on).
   Failures raise the localized `ProviderError` subclasses, never raw
   exceptions.
2. **Client**: a small class taking `(cfg, *, transport)` - the transport is
   always injected, so tests fake `get_json` / `download` and
   never touch the network. Use `sources/http.py` for anything HTTP: it
   owns the offline gate, https enforcement, retries and `.part` handling.
3. **Dispatch**: extend `sources/__init__.py::list_matches` / `fetch_demo`
   with the provider id; downloads end in `store.add_demo(...)` so hashing,
   dedup and the library stay in one place, then `_bind` writes the `match`
   field onto the entry.
4. **UI**: `views/matches.py` renders whatever lands in `matches.json`
   (`sources.read_cache`); usually only the connections-row status and the
   row action are new (account fields belong to `views/settings.py`). All
   texts go into `i18n.py` in both locales; the few badges outside the
   grid ride `st.badge` with Streamlit's semantic palette. `core/matchlist.py` picks the
   provider up automatically through `sources.read_cache`.
5. **Tests**: fixtures in the documented response shapes, a fake transport
   and (for helpers) a fake runner - see `tests/test_sources.py`; add the
   refresh/error path to the smoke scenario in `tests/test_app.py`.

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
2. Render the widget in the filter bar (`filters_ui._controls`)
   and keep the widget `key` equal to the session key - plus
   `persist_state="session"`, so the value survives every page and tab
   switch (presets and URL restore rely on both).
3. Apply it where the data is cut: `core/filters.py` (`Filters` +
   `apply_filters`) for shot/duel-level filters, or the page-level
   re-slicing in `context.py` / the view when only one view is affected
   (follow the existing rounds/side filters).
4. Add expectations to `tests/test_filters.py` (semantics) and a scenario in
   `tests/test_app.py` (panel interaction).

## Tests and tools

```bash
pip install -r requirements-dev.txt
pytest                       # no .dem file, no network
pytest --full                # + every page and locale, the deep random walk
pytest --demo path/to.dem    # + the app on a real demo
```
