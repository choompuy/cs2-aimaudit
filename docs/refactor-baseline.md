# Refactor baseline and working log

Working document of the fix + refactor pass described by the task prompt.
"Before" numbers live here; the final report fills in the "after" column.

Base commit: **`5b3cdad`** (`feat: add CS2 weapon icons and favorite-weapon
metrics`). The prompt was written against `227ddcc`; the repository moved one
commit forward since, and that commit **already fixed both F1 items**:

- `nativedialog.py::_initial_dir` no longer touches `~` (registry + `os.getcwd()`
  only), so the `no module writes outside the app folder` check passes;
- `tests/check_ui.py` expects 9/9 matches columns, matching the redesigned
  `[table.matches]`.

Everything else in the prompt still applies. Numbers below are re-measured on
`5b3cdad` with the exact commands from the prompt (ruff/radon/vulture pins from
the repo's own venv), so "your numbers must match, else record the difference"
is satisfied by the delta column.

## How measured

```
python tests/test_core.py && python tests/test_sources.py && python tests/test_icons.py \
  && python tests/check_ui.py && python tests/smoke_app.py --fast
ruff check --select F,B,SIM,C901,PLR0911,PLR0912,PLR0913,PLR0915,S110 --line-length 100 --output-format concise .
radon cc -s -n C --exclude "tests/*,.venv/*" .
vulture --min-confidence 60 --exclude tests,.venv .
```

App code = what CI byte-compiles (app.py, the core/sources/ui/views/theme
packages, i18n/mapview/store/context/filters_ui/shell/nativedialog, tools/) —
tests excluded. Lines are physical lines.

## Baseline (before), `5b3cdad`

| Metric | `227ddcc` (prompt) | `5b3cdad` (base here) | Delta and why |
|---|---|---|---|
| App Python lines (no tests) | 14 842 | 16 233 | +1 291: the icons commit adds `tools/fetch_icons.py` (545), `ui/icons.py` (362), grows `ui/roundrep.py`, `ui/table.py`, `ui/schema.py`, views |
| Functions ≥ 70 lines | 27 | 30 | new long functions in `tools/fetch_icons.py`, `ui/icons.py` |
| Functions ≥ 120 lines | 14 | 14 | unchanged |
| Functions at radon rank D or worse | 18 | 22 | +`_guess_round_winners` (D 21), `inspect_demo` (D 24), `fetch_icons.main` (D 21); `table_payload` 38→46, `validate` 55→63, `round_payload` 80→98 grew |
| ruff findings (prompt selection) | — | 331 | incl. F821 ×4, B023 ×6 (app code), B905 ×28, S110 ×2 (app code), SIM105 ×16, PLR0913 ×24 |
| vulture findings (≥60% conf.) | — | 27 | matches F3: `get_replay`, `_live_freeze_ends`, `age_days`, `account_id_to_steamid64`, `post_json`, `next_share_code`, `add_folder`, `pick_folder`, … |
| `tests/test_core.py` lines | 4 334 | 4 334 | unchanged |
| `tests/smoke_app.py` lines | 2 529 | 2 529 (+ helper later) | unchanged at base |
| Test checks passing | 549 / 98 / — / 406 | 549 / 98 / 74 / 406 (+27 JS) | all green on base; JS green via `npm install` but NOT run in CI (F2) |
| `session_state` string keys | 48 | 39 literal (+ dynamic) | counted by AST scan of subscript literals; the prompt's 48 includes dynamic keys |
| Widget keys | 81 | 77 literal | same counting caveat |
| Largest module | i18n.py | i18n.py 1 413 (warn at 1 300) | unchanged |

Test counts per suite on base: `test_core` 549, `test_sources` 98, `test_icons`
74, `check_ui` all ok, `smoke_app --fast` 406, JS (`check_roundrep_js`) 27.

### Functions at radon rank D or worse (base)

```
F 128  core/loader.py:453        load_demo
F  98  ui/roundrep.py:86         round_payload
F  96  filters_ui.py:95          render
F  89  views/shooting.py:27      render
F  63  ui/schema.py:201          validate
F  50  core/metrics.py:308       build_duels
F  46  ui/table.py:98            table_payload
F  44  core/checks.py:28         data_checks
F  43  core/matchlist.py:117     build_rows
E  38  core/metrics.py:792       impact_stats
E  33  views/duels.py:16         render
E  32  sources/steam.py:265      SteamProvider._record
D  30  core/metrics.py:918       player_summary
D  29  core/replay.py:391        round_replay_data
D  28  views/replay.py:57        _duel_figure
D  28  views/replay.py:298       _duel_dialog
D  24  sources/steam_gc.py:166   _parse_roundstats
D  24  tools/inspect_rounds.py:70 inspect_demo
D  22  store.py:278              _sync
D  21  shell.py:538              open_demo
D  21  core/loader.py:699        _guess_round_winners
D  21  tools/fetch_icons.py:472  main
```

### Functions ≥ 70 lines (base, 30)

`load_demo` 472, `filters_ui.render` 454, `views/shooting.render` 412,
`round_payload` 305, `core/checks.data_checks` 203, `_duel_figure` 203,
`views/duels.render` 189, `build_duels` 182, `player_summary` 146,
`round_replay_data` 138, `ui/schema.validate` 138, `build_figure` 133,
`open_demo` 132, `impact_stats` 124, `debug_report` 112, `shots_table` 110,
`_replay_fragment` 110, `build_rows` 102, `duel_telemetry` 102,
`table_payload` 99, `_duel_dialog` 98, `context_bar` 96, `library_panel` 87,
`_map_fragment` 87, `views/matches.render` 85, `round_duels` 81,
`views/overview.render` 75, `apply_filters` 72, `matchstats.compute` 72,
`fetch_icons.main` 70.

## Safety net (stage 0)

- Goldens committed: `tests/goldens/payloads/*.json` (10 table_payload dumps,
  Basic/All × EN/RU) and `tests/goldens/api_snapshot.txt` (182 `core` names).
- `python tools/check_goldens.py` regenerates both into a temp dir and
  compares byte-for-byte; `--update` rewrites the goldens (fix commits that
  change behaviour on purpose say so in the message). Exit 1 on any diff.
- Determinism verified: two consecutive runs produce identical files.
- `tests/smoke_app.py::pick(at, kind, key, value)` selects in
  `format_func` widgets by raw option OR displayed label (AppTest's
  `select_index` double-formats and is unusable there). Regression:
  `scenario_pick()` — raw and label, EN and RU, selectbox and
  segmented_control, unknown value raises. 17 checks.

## Confirmed but not yet fixed (found while building the safety net)

- Widget state loss across page switches (reproduced with the new `pick()`,
  `scripts/trap_audit.py`): `mflt_*` match filters reset to `all` after
  leaving and returning to the Matches list (URL param `fkind` drops too);
  `ov_full` / `du_full` ("All columns" toggles) reset after switching to
  another match tab. The Settings sliders (`thr`) DO survive. These are
  stage-3 fix candidates with regression tests.
- The owner-observations section of the prompt is empty — nothing to
  reproduce beyond the systematic audit.

> Note: an earlier revision of this file claimed a `branches: ain]` typo
> in `.github/workflows/ci.yml`. That was a rendering artifact of an
> inspection command (a literal `[m` was eaten from the output), not a
> real defect — the committed trigger is `branches: [main]`, verified
> byte-level. Only the compileall list and the skipped JS tests were
> real CI holes.

## Stage 2 — F3 dead code, F4 annotations, F5 exceptions

**F3 removed** (0 usages verified by search before each deletion):
`context.get_replay` → `core.replay_data` chain (both dead; tests only ever
used `round_replay_data`), `core/replay.py::_live_freeze_ends`,
`sources/model.py::age_days`, `sources/steam_gc.py::account_id_to_steamid64`,
`store.add_folder` (its 3 checks replaced by two `add_link` calls that keep
the storage-report test intact), `nativedialog.pick_folder` + the `_FOLDER`
child script, `ui/icons.py::icon_uri` (not in the module's documented API,
0 users). **Kept as deliberate stubs**, now noted in `docs/ARCHITECTURE.md`:
`HttpTransport.post_json` (POST twin for a future FACEIT/Web API) and
`sources/steam.py::next_share_code` (match-history pagination).

**F3 i18n**: 13 keys deleted from both languages (6 from the prompt:
`change_demo`, `change_cancel`, `ci_line`, `lib_label`, `lib_help`,
`lib_none_found`; 7 more found by the new check: `flt_n`, `flt_n_all`,
`rank_line`, `rank_part`, `small_sample`, `tagline`, `mt_helper_missing` —
orphaned by earlier redesigns, verified 0 references + git history).
`check_ui` now has check 2b: every defined key must appear as an exact
literal in app code (incl. `core/`, `sources/`, `store.py`) or live under a
dynamic prefix the code builds (`t(f"nav_..")`, `t('chk_' + ..)`, plus the
cited `col_` / `weapon_` sites); keys only tests use are dead by definition.

**F4**: `if TYPE_CHECKING: from core.filters import Filters` added in
`core/metrics.py` and `core/replay.py` — ruff F821 is now 0.

**F5** (2×S110 restructured + 8 narrowed + logging added, the rest kept
broad with a reason each):

- S110 gone: `nativedialog._initial_dir` (registry helper returns None on
  `(ImportError, OSError)` — no `pass` left) and `sources/http.py`'s
  HTTPError body read (`(OSError, ValueError, http.client.HTTPException)`
  → `body = b""`).
- Narrowed: `app._app_version` `(OSError, subprocess.SubprocessError)`,
  `core/checks` + `core/loader._parse_version`
  `importlib.metadata.PackageNotFoundError`, `filters_ui._url_unpack`
  `ValueError` (base64/Unicode/JSON errors are all ValueError),
  `store.write_parsed` `(OSError, TypeError, pickle.PicklingError)`,
  `store.write_matchstats` `(OSError, TypeError, ValueError)`.
- New `store.log_note(tag, msg)` → `data/logs/notes.log` (trimmed to 200
  lines, best-effort, store-only writes): corrupt-cache drops and failed
  cache writes are no longer silent. 5 new checks in `test_core`.
- Kept broad (documented): the demoparser2 tiered-parse loops re-raise
  `last_err`; per-prop fallbacks are deliberate feature detection
  ("the buttons lesson"); `shell.open_demo`'s guard SHOWS `st.error`;
  `store.read_parsed` keeps the catch-all because `pickle.load` of foreign
  data can raise anything (now logged); `ui/schema.py`/`ui/icons.py`
  bare-Streamlit fallbacks cannot enumerate (version-dependent).

**Bug found by vulture (100 %)**: `next_share_code` accepted `auth_code`
but never sent it — Valve's `GetNextMatchSharingCode` REQUIRES the
`steamidkey` parameter. Fixed (URL + test URLs); the stub would have been
a guaranteed 4xx against the real API.

**Verification**: vulture ≥60 %: 27 → 20, **≥80 %: 0**; ruff F821/S110: 0;
test counts 551 / 98 / 74 / 423 (+2 net: −3 `add_folder`, +5 `log_note`);
payload goldens byte-identical; `api_snapshot` regenerated on purpose
(`replay_data`, `_live_freeze_ends` gone, `TYPE_CHECKING` import visible).

## Stage 3 — diagnostics: state loss (fix commits)

The random walk (`tests/walk_app.py`, 3 seeds × 60 actions in CI) and the
Streamlit-trap audit found ONE real defect family: **widget state lost on
page switch**. Every page-local toggle / segmented control / select that
did not render on other pages lost its value when the user navigated away
(Streamlit drops the state of widgets a run does not render):

- `ov_full`, `du_full`, `sh_all` (the Basic/Full column switches), the
  Map page's `map_show` / `map_level` / `map_lines`, and the Matches list
  filters `mflt_period` / `mflt_result` / `mflt_mode` / `mflt_src` all
  reset after a round-trip through another page;
- fixed with `persist_state="session"` (Streamlit ≥ 1.46): widget keys
  unchanged, smoke scenarios keep working, values now survive the trip;
- the Map page's manual `st.session_state.setdefault("map_show", ...)`
  workaround became redundant and was removed.

`walk_app.py` asserts after every action: no `at.exception`, no `st.error`
output, no "value not among the options" mismatches. `smoke_app.py`
gained the state-loss regression scenarios (a toggle survives a page
round-trip; `ov_full` kept across a demo switch). CI runs the walk
(3 seeds × 60) on every push, `--full` (5 × 250) on schedule.

**Verification**: 551 / 98 / 74 / 471 (smoke +48 checks); the
`overview_all_ru` golden regenerated ON PURPOSE (with `ov_full` now
persisting, the full-columns table renders where it previously reset).

## Stage 4 — R1: one copy of each duplicated block (refactor)

Behaviour-preserving; payload goldens byte-identical, `api_snapshot`
regenerated only for the two new private names (`_parse_ticks_tiered`,
`_events_in_window`).

- `views/_shared.py` (new): `steam_cfg` + `show_error`, previously
  identical in `views/matches.py` and `views/settings.py`; the trivial
  pass-throughs `_accounts()` → `store.read_accounts()` and
  `_provider_cache()` → `sources.read_cache()` are inlined.
- `core/loader.py::_parse_ticks_tiered` (new): the "try prop-set tiers,
  keep the last error" loop that `core/replay.py` (player telemetry,
  round replay) and `core/loader.py` (`load_demo`) each had inline.
- `ui/component.py` (new): `declare` + `event_callback`, the component
  registration and the `on_*_change` closure that `ui/roundrep.py`,
  `ui/strip.py`, `ui/table.py` each duplicated.
- Same-name helpers disambiguated (each name now means ONE thing):
  `replay._num(row,name)` → `_to_float`, `replay._num(r,name,nd)` →
  `_rnd`, `matchstats._num(col,cast)` → `_cell` (all three were nested
  locals with different semantics; `ui/table._num` keeps the name),
  `ui/table._score` → `_grade` (colour quality; `matchlist._score`
  keeps the name), `sources/http._remove` → `_unlink`, `store._remove`
  → `_unlink_freed` (it returns bytes freed), `tools/fetch_radars`
  `download` → `download_radar_set`.
- `core/replay.py::_events_in_window` (new): the fires/deaths/hurts
  window guard the three round-event blocks repeated.
- `views/shooting.py::_events_popover` (new): the two Top-3 evidence
  popovers (err / lost-first) collapsed into one parameterised helper.

Test adjustments (names follow the rename, expectations untouched):
`fetch_radars.download` → `download_radar_set` in `test_core.py`.

## Stage 4b — R3: the session_state registry (refactor)

`state.py` (new, 161 lines, a leaf like `store.py`): key-name constants,
typed get_*/set_* for the keys shared by 2+ modules, and the navigation
mutators `go_page`/`go_match`/`go_tab`/`leave_match` (moved from shell,
re-exported there so `shell.go_*` callers and tests keep working).

- Shared keys routed through the registry: `page`, `match`, `match_tab`,
  `lang`, `me`, `du_open`, `tl_duel`, `demo_id`, `demo_loaded`,
  `_pick_err`. No app module touches another module's key by a string
  literal any more (ui/icons.py and ui/schema.py read `lang` through the
  registry too, via a lazy import that keeps their bare-mode fallback).
- `lang` and `me` are WIDGET-OWNED keys (the sidebar / player
  selectboxes): the registry only reads them - a manual setter would
  trip Streamlit's widget-state guard.
- Dead state found while enumerating keys: `demo_change` was WRITE-ONLY
  (nothing read it, at `5b3cdad` either) - the two writes are gone.
- Local `state = core.load_state()` / `ctx.state` dicts inside
  `shell.open_demo` / `shell.context_bar` renamed to `saved` (they
  shadowed the new module and would have mis-fired at runtime).
- `docs/ARCHITECTURE.md`: the module map and the "Navigation and state"
  section now describe the registry; `check_ui` 12c gained a `state.py`
  needle so the doc cannot rot.

**Verification**: 551 / 98 / 74 / 471 checks green; walk 3 × 60 clean;
payload goldens byte-identical; `api_snapshot` unchanged (state.py is
not part of the core facade); vulture ≥80 %: 0.

## Stage 5 — R2: split the monster functions (refactor, one per commit)

### R2-1 `core/loader.py::load_demo` (was 472 lines, CC 128)

Split into nine phase functions plus the live-match helpers; the public
interface and `PARSE_VERSION` unchanged, `load_demo` is now a 55-line
orchestrator:

- `_parse_players` (players + id lookups + header), `_parse_events_frames`
  (tiered event parse + steamid fixing), `_drop_warmup` +
  `_exclude_preliminary` (live-match start / knife-round fallback, now
  with `_round_bounds` / `_count_pre_live` / `_keep_from_tick` /
  `_knife_fallback` / `_shift_first_round` and module-level
  `_between` / `_has_gun_fire` / `_knife_only` / `_has_knife_death`),
  `_filter_gun_events`, `_parse_tick_table`, `_parse_mouse`,
  `_parse_totals`, `_build_rounds` (with `_round_mark_events` /
  `_official_end_ticks` / `_named_event_windows` /
  `_synthesize_round_bounds`).

Complexity: F(125) → nothing above C(18); every extracted function ≤60
lines except `_parse_events_frames` (75) and the pre-existing
`decompressed_demo` (68), both under the 80-line cap with the reason in
a comment. Goldens: payloads byte-identical; `api_snapshot` regenerated
for the new private names only.

### R2-2 `filters_ui.py::render` (was 454 lines, CC 96)

Data + small renderers: `_filter_data` (what the demo offers),
`_preset_options`, `_drop_stale_selections` + `_migrate_filter_state`
(old links / clamping), `_round_kwargs` + `_resolve_filters`,
`_active_chips`, `_keep_hidden_filter_keys`, `_card_controls_row1/row2`
(the declarative control groups), `_render_open_card` /
`_render_summary_row`, `_sync_filter_url`; the chip/preset/reset
callbacks are factories closing over the run's n_rounds/halves.
`render` is a 58-line orchestrator; widget keys, order and the URL pack
unchanged. Max complexity C(17), nothing over 60 lines.

### R2-3 `views/shooting.py::render` (was 408 lines, CC 89)

Sections, each fed from a `_Stats` bundle computed once: `_duel_stats` +
`_discipline_stats` + `_stats` (the numbers, incl. the thresholds),
`_movement_tips` + `_discipline_tips` + `_build_tips` (the coaching
engine), `_top3_card`, `_metric_tiles` + `_all_metrics_tiles`, `_charts`
+ `_risk_chart`, `_weapons_grid`. `render` is 12 lines. Widget keys and
draw order unchanged (`sh_all` keeps persist_state="session"). Max
complexity C(17); `_top3_card` (72) and `_all_metrics_tiles` (79) stay
under the 80-line cap with the reason commented.

### R2-4 `ui/roundrep.py::round_payload` (was 305 lines, CC 98)

Payload assembly by sections: `_weapon_changes` / `_key_bits` /
`_player_frames` (the per-player rows), `_duel_rows` (the duel rows with
telemetry), `_round_bar` (the round bar + how the round ended),
`_bomb_rows`, `_register_badges` (the status icons), `_radar_payload` /
`_grid_bounds`, `_event_rows`, `_sel_and_at`, and the `_weapon_registry`
factory (the shared weapon-name closure). `round_payload` keeps the
interface docstring and the `_badge`/`_name`/`_pi` closures and is now
C(17). The JS front-end golden test stays green (27 checks) and the
payload goldens are byte-identical.

### R2-5..R2-16 the rest of the D/E list and the 70+ functions

Every function that ranked D or worse at the base is split (see the
commit list): `ui/schema.validate` (F 63), `core/metrics.build_duels`
(F 50), `ui/table.table_payload` (F 46), `core/checks.data_checks`
(F 44), `core/matchlist.build_rows` (F 43), `core/metrics.impact_stats`
(E 38), `views/duels.render` (E 33), `sources/steam._record` (E 32),
`core/metrics.player_summary` (D 30), `views/replay._duel_figure` /
`_duel_dialog` (D 28), `tools/inspect_rounds.inspect_demo` (D 24),
`sources/steam_gc._parse_roundstats` (D 24), `shell.open_demo` (D 23),
`store._sync` (D 22), `tools/fetch_icons.main` (D 21); then the long
ones: `core/replay.round_replay_data` (120), `duel_telemetry` (102),
`round_duels` (81), `mapview.build_figure` (133), `core/metrics.shots_table`
(110), `core/checks.debug_report` (112), `player_summary` (83),
`ui/roundrep.round_payload` (118), `views/replay._replay_fragment` (110),
`views/matches.render` (86), `shell.library_panel` (87), `shell.context_bar`
(96), `views/map._map_fragment` (89).

After R2: radon rank D+ = **0** in app code (max C(19), the pre-existing
`core/metrics.shots_table` and `core/checks._scoreboard_check`);
functions >= 70 lines = **8**, each with a reason:

- `core/filters.apply_filters` (72) - the documented filter chain, one
  stage per line group; splitting it would hide the order.
- `core/loader.decompressed_demo` (70) - the tiered decompress loop
  (bz2/gz/zst/zip), one tier per block.
- `core/loader._parse_events_frames` (80) - the documented prop-tier
  fallback parse; the tiers are one unit and stay under the 80-line cap.
- `core/matchstats.compute` (72) - the summary-metric chain, one metric
  per line group.
- `shell.open_demo` (71) - the orchestrator after R2-11 (the status
  plate, missing-file card and load failures are helpers already).
- `views/overview.render` (75) - the overview page body (one table call
  plus its columns).
- `views/shooting._top3_card` (76) / `_all_metrics_tiles` (79) - the
  documented tile builders (one tile per line, under the 80-line cap,
  reason commented in R2-3).

## Stage status

- [x] 0. Baseline, goldens, `pick()` helper (this document)
- [x] 1. F2: CI holes (compileall list → `tools/compile_all.py` walk; JS
      tests run in CI via `npm ci` + committed `tests/js/package-lock.json`;
      `tools/check_goldens.py` added to CI)
- [x] 2. F3 dead code, F4 F821 annotations, F5 swallowed exceptions
      (this section)
- [x] 3. Diagnostics: state-loss fixes, `tests/walk_app.py` in CI
      (this section)
- [x] 4. R1 duplicates + R3 state registry (the two sections above)
- [x] 5. R2 split monster functions (R2-1..R2-16; D+ = 0, >=70-line
      functions = 8 with reasons)
- [ ] 6. R5 test split, R4 i18n as data
- [ ] 7. F6 doc numbers checked by test, F7 B023/B905
- [ ] 8. Final report: before/after table

## Acceptance targets (from the prompt)

- All suites green locally and in CI, check count never drops; JS included.
- Goldens byte-identical except in commits that change behaviour on purpose.
- ≥70-line functions: ≤ 8 (each with a reason); rank D+: 0 outside a
  reported list.
- ruff F821 / B023 / S110: 0 in app code; no duplicate helper names from R1.
- App code −10 % lines (~1 600) without functionality loss; no module > 800
  lines without a documented reason.
- Docs numbers machine-checked (±15 %); no new dependencies.
