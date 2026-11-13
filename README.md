# CS2-AimAudit 🎯

**Audit your aim — local CS2 demo analysis. Shooting, duels, movement and key presses, with nothing ever leaving your computer.**

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![UI](https://img.shields.io/badge/UI-Streamlit-FF4B4B)
![Privacy](https://img.shields.io/badge/privacy-100%25%20local-green)

English · [Русский](README_RU.md)

CS2-AimAudit turns a Counter-Strike 2 match demo (`.dem`) into a coach-style after-match report. It does not train your aim — it shows what actually happened in the match: where you shot from, how fast you were moving, who fired first, and which keys you were holding.

## Features

- **Overview** — every player in one table: K/D, accuracy, HS%, shots on the move, "risky shots", shoots-first rate, aim error, duel duration. K/D (as kills / deaths and as a ratio), opening duels, trades and assists follow the **Side** and **Rounds** filters. Tick a row's checkbox to analyze that player; the **All columns** toggle adds the detail metrics (incl. first-shot accuracy and opening-duel winrate).
- **Shooting** — hit zones, speed at each shot, a speed-vs-distance scatter with the acceptable-speed curve, per-weapon stats.
- **Duels** — every duel with distance, who fired first, aim error at the first shot, and flags: no-scope, through smoke, wallbang, blinded. Below: your duels by map zone and how every round started (opening duels, both teams).
- **Impact** — per player: opening duels won/lost, trade kills, traded deaths, 2k–5k rounds, assists and flash assists.
- **Map** — where the selected player killed and died, on the radar: your position (circle), the opponent's (open diamond) and a line between them; blue = kill, red = death; works with every filter, and two-level maps (Nuke, Vertigo) get an Upper / Lower switch. Needs the radar images once, see [Radar images](#radar-images-for-the-map-page).
- **Timeline** — speed (with the moving threshold), yaw turn, pitch and held keys as coloured bars with a legend, around any duel, with the first shot and the death marked, cut at the moment of death. Switch the view to **round replay** — the whole round on the radar, like the spectator mini-map: every player of both teams, HP in the circle, a view-direction arrow and the weapon in hands; lines are shots, stars are hits, crosses are deaths and the side feed lists every kill of the round, so both where you were shooting and who traded you are visible. The panel on the left lists the players (the weapon in hands, the HP; hideable with a button), the right column holds a card per duel of yours in the round: duels that have not happened yet are semi-transparent, past ones are solid, the selected one gets an outline. Clicking a card locks the playhead to that duel's segment and shows the keys both duelists pressed; release it with another click, the ✕ button or by picking another duel. The timeline carries kill marks (blue for CT kills, yellow for T kills), and below it sits the match's round chips, coloured by the winning side (the winner is inferred from a full side elimination - CS2 demos expose no round_end/winner event); a click replays that round. The wheel zooms the radar, an LMB drag pans it, a double click resets the view. Play / 0.5x / 0.25x; two-level maps (Nuke, Vertigo) get the upper and lower level side by side (input recorded after death is a demo-parser artifact, not real play).
- **Risk model** — a shot is _risky_ when your speed exceeded what is acceptable at that distance to the nearest living enemy: any speed point-blank, near-stationary at long range. Both distances are configurable.
- **Data report** — built-in anomaly checks (impossible speeds, unresolved SteamIDs, missing positions…) plus a JSON export to attach to bug reports.
- Warmup, team damage and non-gun events are excluded automatically.
- UI in English and Russian.

## Quick start

Easiest:

- **Windows:** double-click `start.bat` — it prints what it is doing (`[1/3] creating venv…`, `[2/3] installing packages, first run takes 1–3 min…`, `[3/3] starting`) and pauses on any error.
- **macOS / Linux:** `./start.sh` in a terminal.

Or manually:

```bash
git clone https://github.com/choompuy/cs2-aimaudit.git
cd cs2-aimaudit
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Requires Python 3.11+ (tested on 3.11–3.14) and a CS2 match demo (`.dem`).

### Where to get a demo

- **Premier / Competitive:** CS2 → Play → _Your Matches_ → download the demo. Demos are kept only for a limited time, so grab them soon after the match.
- **FACEIT / ESEA:** the demo link on the match page. Compressed files (`.dem.bz2`, `.dem.gz`, `.zip`, `.dem.zst`) are unpacked by the app automatically.

Then pick the file in the app.

## Demo library and where files are stored

There is one way to open a demo: add a file (drag it into the upload box; `.dem`, `.bz2`, `.gz`, `.zst` and `.zip` work) or pick one of your saved demos from the list. Every demo you add is saved to the library, so next time you just pick it, and the last one you used opens by itself. A demo that is already in the library (same file, any name) is not saved twice.

Everything the app stores lives in one folder next to `start.bat` / `start.sh`:

```
data/
  demos/        your demo library (plain files; you can also copy .dem files in by hand)
  cache/
    parsed/     parsed demos, so opening one again is instant
    unpacked/   demos unpacked from .bz2 / .gz / .zst / .zip
  radars/       radar images (tools/fetch_radars.py)
  library.json  the list of demos        state.json  last demo and player
```

Open *Library and storage* in the app to see the folder, the sizes, delete a demo or clear the cache. You can also delete `data/cache/` by hand at any time: it is rebuilt when needed; only `demos/` holds something that cannot be rebuilt. Move the folder with the `AIMAUDIT_DATA_DIR` environment variable. `data/` is git-ignored.

## Radar images for the Map page

The Map page draws points on the real radar. The radar images are Valve's artwork and are not part of this repository, so fetch them once, on your own machine:

```bash
python tools/fetch_radars.py
```

This downloads the images and their calibration from the public [awpy-data](https://github.com/pnxenopoulos/awpy-data) release into the `data/radars` folder next to `start.bat` (about 3 MB). The app finds them there, or in an existing awpy cache (`~/.awpy`), or in the folder set by `AIMAUDIT_RADAR_DIR`. Without radar images the Map page still works: points are drawn on a plain grid in game units.

## Privacy

All parsing and analysis happen inside the app on your machine. CS2-AimAudit makes no network requests (the only exception is `tools/fetch_radars.py`, which you run yourself once to get radar images), collects no telemetry and does not upload your demos anywhere. The bundled `.streamlit/config.toml` also disables Streamlit's own anonymous usage statistics (`gatherUsageStats = false`) — if you copy or replace that file, keep the setting.

## Reading the numbers

- **Shots on the move** — fired while your speed was above the threshold. Stop first (counter-strafe), then shoot.
- **Aim error** — the angle between your crosshair (recoil punch included) and the enemy head at your first shot of a duel. A few degrees or less means the crosshair was already on the head.
- **Shoots first** — how often you fired first in duels where someone fired.
- **First-shot accuracy** — hit rate on the first shot of an engagement (no shot of yours in the previous ~0.35 s). Reflects crosshair placement; the median mouse movement before those shots (sensitivity-relative) shows how much you swing before committing.
- **Opening duel winrate** — how often you win the round's first enemy kill when you are in it (as the killer or the victim). The entry player's number; a low one means you should peek behind utility or let a teammate take the first contact.
- **Trades** — a kill is a trade kill when it avenges a teammate's death within 5 s (same round); "traded deaths" is the share of your own deaths avenged that way — a teammate stat, not a solo verdict.
- **Duel time** — from the first shot of the duel to the kill/death. Long duels with many shots point to spray/aim problems.
- **Timeline** — look for: shooting while moving, big last-moment aim corrections, crouch/walk timing.

The in-app **Guide** tab explains the risk model and the limitations: sensitivity, FPS and ping are not stored in demos (raw mouse deltas are, since CS2, but they scale with sensitivity); aim error is approximate; duels are detected by a time window, not by line of sight.

## Something looks wrong?

Open the **Data report / debug** expander at the bottom of the page. The built-in checks flag data anomalies, and the downloadable JSON report contains the raw parser fields needed to find the cause — please attach it when you [open an issue](../../issues).

## Development

```bash
python tests/test_core.py        # synthetic match, no .dem file needed
python tests/check_ui.py         # i18n / schema / layering guardrails
python tests/smoke_app.py --fast # AppTest end-to-end (fake parser)
```

- `core/` — parsing and all metrics (demoparser2), no UI code.
- `app.py` — Streamlit entry point (dispatcher); the pages live in `views/`,
  the shell in `shell.py`, the filter panel in `filters_ui.py`.
- `ui/tables.toml` — the table schema: formats, sorting, grading and bars
  for every grid, described as data.
- `i18n.py` — English/Russian strings.
- `docs/ARCHITECTURE.md` — module map, dependency rules and how to add a
  metric, column, page or filter.
- `docs/DEMOPARSER_NOTES.md` — notes on the demoparser2 API.

Pull requests are welcome. Bug reports with the debug JSON attached are especially valuable.

## Name and trademarks

CS2-AimAudit is an independent open-source project. It is not affiliated with, endorsed by, or connected to Valve Corporation, Counter-Strike 2, or the Aimlabs (Aim Lab) aim trainer.

## License

[MIT](LICENSE)
