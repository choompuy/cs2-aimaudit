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

## Stage status

- [x] 0. Baseline, goldens, `pick()` helper (this document)
- [x] 1. F2: CI holes (compileall list → `tools/compile_all.py` walk; JS
      tests run in CI via `npm ci` + committed `tests/js/package-lock.json`;
      `tools/check_goldens.py` added to CI)
- [x] 2. F3 dead code, F4 F821 annotations, F5 swallowed exceptions
      (this section)
- [ ] 3. Diagnostics: random walk, Streamlit-trap audit, fix commits
- [ ] 4. R1 duplicates, R3 state registry
- [ ] 5. R2 split monster functions (by complexity, one per commit)
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
