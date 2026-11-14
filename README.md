# CS2-AimAudit 🎯

**Audit your aim — local CS2 demo analysis. Shooting, duels, movement and key presses, with nothing ever leaving your computer.**

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.13-blue)
![UI](https://img.shields.io/badge/UI-Streamlit-FF4B4B)
![Privacy](https://img.shields.io/badge/privacy-100%25%20local-green)

English · [Русский](README_RU.md)

CS2-AimAudit turns a Counter-Strike 2 match demo (`.dem`) into a coach-style after-match report. It does not train your aim — it shows what actually happened in the match: where you shot from, how fast you were moving, who fired first, and which keys you were holding.

## Features

- **Overview** — every player in one table: K/D, accuracy, HS%, shots on the move, "risky shots", shoots-first rate, aim error, duel duration. K/D (as kills / deaths and as a ratio), opening duels, trades and assists follow the **Side** and **Rounds** filters. Tick a row's checkbox to analyze that player; the **All columns** toggle adds the detail metrics (incl. first-shot accuracy and opening-duel winrate).
- **Shooting** — hit zones, speed at each shot, a speed-vs-distance scatter with the acceptable-speed curve, per-weapon stats.
- **Duels** — every duel with distance, who fired first, aim error at the first shot, and flags: no-scope, through smoke, wallbang, blinded. Below: your duels by map zone and how every round started (opening duels, both teams).
- **Impact** — per player: opening duels won/lost, trade kills, traded deaths, 2k–5k rounds, assists and flash assists.
- **Matches** — your recent Steam games with a download button that puts the demo straight into the library (see [Importing matches](#importing-matches)).
- **Map** — where the selected player killed and died, on the radar: your position (circle), the opponent's (open diamond) and a line between them; blue = kill, red = death; works with every filter, and two-level maps (Nuke, Vertigo) get an Upper / Lower switch. Needs the radar images once, see [Radar images](#radar-images-for-the-map-page).
- **Timeline** — speed (with the moving threshold), yaw turn, pitch and held keys as coloured bars with a legend, around any duel, with the first shot and the death marked, cut at the moment of death. Switch the view to **round replay** — the whole round on the radar, like the spectator mini-map: every player of both teams, HP in the circle, a view-direction arrow and the weapon in hands; lines are shots, stars are hits, crosses are deaths and the side feed lists every kill of the round, so both where you were shooting and who traded you are visible. The panel on the left lists the players (the weapon in hands, the HP; hideable with a button), the right column holds a card per duel of yours in the round: duels that have not happened yet are semi-transparent, past ones are solid, the selected one gets an outline. Clicking a card locks the playhead to that duel's segment and shows the keys both duelists pressed; release it with another click, the ✕ button or by picking another duel. The timeline carries kill marks (blue for CT kills, yellow for T kills), and below it sits the match's round chips, coloured by the winning side (the winner is inferred from a full side elimination - CS2 demos expose no round_end/winner event); a click replays that round. The wheel zooms the radar, an LMB drag pans it, a double click resets the view. Play / 0.5x / 0.25x; two-level maps (Nuke, Vertigo) get the upper and lower level side by side (input recorded after death is a demo-parser artifact, not real play).
- **Risk model** — a shot is _risky_ when your speed exceeded what is acceptable at that distance to the nearest living enemy: any speed point-blank, near-stationary at long range. Both distances are configurable.
- **Data report** — built-in anomaly checks (impossible speeds, unresolved SteamIDs, missing positions…) plus a JSON export to attach to bug reports.
- Warmup, team damage and non-gun events are excluded automatically.
- UI in English and Russian.

## Quick start

CS2-AimAudit is **Windows-only** (Windows 10/11): the launcher and the tests target Windows, Linux and macOS are not supported.

Double-click `start.bat`. It prints what it is doing and pauses on any error:

1. checks for Python 3.13 (it also finds it through the `py` launcher),
2. creates `.venv` and installs the packages (the first run takes 1–3 min),
3. downloads the radar images for the Map page, once (about 3 MB, see [below](#radar-images-for-the-map-page)),
4. starts the app and opens a browser tab.

Or manually, in a terminal in the project folder:

```bat
git clone https://github.com/choompuy/cs2-aimaudit.git
cd cs2-aimaudit
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python tools\fetch_radars.py
streamlit run app.py
```

Requires Windows and Python 3.13 (the exact version CI tests, matching the pinned dependencies in `requirements.txt`) and a CS2 match demo (`.dem`).

### Where to get a demo

- **Premier / Competitive:** CS2 → Play → _Your Matches_ → download the demo. Demos are kept only for a limited time, so grab them soon after the match.
- **FACEIT / ESEA:** the demo link on the match page. Compressed files (`.dem.bz2`, `.dem.gz`, `.zip`, `.dem.zst`) are unpacked by the app automatically.
- Or import your recent Valve Matchmaking games straight from Steam — see [Importing matches](#importing-matches).

Then pick the file in the app.

## Importing matches

The **Matches** tab lists your recent **Steam / Valve Matchmaking** games and downloads the demo you pick into the same library. Nothing happens in the background: a network request is only made when you press **Refresh**, **Download** or **Install Steam helper** — opening the page only reads the local cache. No account, key or password is needed: the helper talks to the Steam client you are already logged into.

Valve only exposes recent matches through the running Steam client, so the app uses [boiler-writter](https://github.com/akiver/boiler-writter) — a small MIT-licensed console tool. Press **Install Steam helper**: a pinned release is downloaded from GitHub, checked against a SHA-256 hash pinned in the code, and unpacked into `data\tools`. Your Steam password is never asked, stored or sent; for a couple of seconds your Steam status may show CS2 running — that is how the match list is fetched. The Game Coordinator returns roughly your last 8 matches, each with a direct demo link (`replay*.valve.net`, valid for about a month).

- Demos you downloaded yourself in CS2 (Watch → Your Matches) are found in the `replays` folder and shown with the state *on disk*.
- For an older match, paste its **share code** (`CSGO-…`) in the settings and press **Add by code**.
- Set your **SteamID64** to get win/loss on the cards (the helper usually detects the logged-in account on its own).

The list is ordered by date, so downloading a demo does not move its card. The **filter** above the cards shows all matches, only the ones already in the library, or only the not-downloaded ones. Every downloaded card has **Delete** (removes that demo and its caches, with a confirm step), and **Delete all demos** clears the whole library in one confirmed click.

### What is stored, and how to remove it

Everything lives inside the app's `data` folder: `accounts.json` (folders and SteamID64, no secrets), `matches.json` (the cached match lists), `tools\` (the Steam helper), plus the downloaded demos in `demos\`. Deleting `accounts.json` and `matches.json` resets the feature completely; delete `data\tools` to remove the helper. To block every network feature, set `AIMAUDIT_OFFLINE=1`.

## Demo library and where files are stored

There is one way to open a demo: add a file (drag it into the upload box; `.dem`, `.bz2`, `.gz`, `.zst` and `.zip` work) or pick one of your saved demos from the list. Every demo you add is saved to the library, so next time you just pick it, and the last one you used opens by itself. A demo that is already in the library (same file, any name) is not saved twice.

Everything the app stores lives in one folder next to `start.bat`:

```
data/
  demos/        your demo library (plain files; you can also copy .dem files in by hand)
  cache/
    parsed/     parsed demos, so opening one again is instant
    unpacked/   demos unpacked from .bz2 / .gz / .zst / .zip
  radars/       radar images (tools/fetch_radars.py)
  tools/        the Steam helper (boiler-writter, installed from the Matches tab)
  accounts.json match-import accounts (nickname, folders - no secrets)
  matches.json  the cached match lists
  library.json  the list of demos        state.json  last demo and player
```

Open *Library and storage* in the app to see the folder, the sizes, delete a demo or clear the cache. You can also delete `data/cache/` by hand at any time: it is rebuilt when needed; only `demos/` holds something that cannot be rebuilt. Move the folder with the `AIMAUDIT_DATA_DIR` environment variable. `data/` is git-ignored.

## Radar images for the Map page

The Map page draws points on the real radar. The radar images are Valve's artwork and are not part of this repository, so **`start.bat` downloads them for you the first time** (about 3 MB from the public [awpy-data](https://github.com/pnxenopoulos/awpy-data) release into `data\radars` next to `start.bat`). After that nothing is downloaded again. Without internet the app still works: points are drawn on a plain grid in game units, and `start.bat` tries again the next day.

- Skip the download for good: set the environment variable `AIMAUDIT_NO_RADARS=1` (for example `setx AIMAUDIT_NO_RADARS 1`).
- Download or update by hand: `python tools\fetch_radars.py`.
- The app finds the radars in `data\radars`, in an existing awpy cache (`~\.awpy`) or in the folder set by `AIMAUDIT_RADAR_DIR`.

## Privacy

All parsing and analysis happen inside the app on your machine, and the app itself makes no requests on its own. The only network activity is explicit and listed here:

- the one-time radar download of `start.bat` / `tools\fetch_radars.py` (switch it off with `AIMAUDIT_NO_RADARS=1`);
- the **Matches** tab, and only on a button press: `replay*.valve.net` (Valve demo files) and `github.com` (only while installing the Steam helper, after a SHA-256 check). Set `AIMAUDIT_OFFLINE=1` to disable all of it.

No telemetry is collected and your demos are not uploaded anywhere; the match import needs no accounts, keys or passwords. The bundled `.streamlit/config.toml` also disables Streamlit's own anonymous usage statistics (`gatherUsageStats = false`) — if you copy or replace that file, keep the setting.

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

```bat
python tests/test_core.py        # synthetic match, no .dem file needed
python tests/test_sources.py     # match import (Steam), no network
python tests/check_ui.py         # i18n / schema / layering guardrails
python tests/smoke_app.py --fast # AppTest end-to-end (fake parser)
```

- `core/` — parsing and all metrics (demoparser2), no UI code.
- `sources/` — match import from Steam: the provider, the HTTP
  transport, the protobuf reader and the inbox (also no UI code).
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
