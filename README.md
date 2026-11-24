# CS2-AimAudit 🎯

**Audit your aim — local CS2 demo analysis. Shooting, duels, movement and key presses, with nothing ever leaving your computer.**

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.13-blue)
![UI](https://img.shields.io/badge/UI-Streamlit-FF4B4B)
![Privacy](https://img.shields.io/badge/privacy-100%25%20local-green)

English · [Русский](README_RU.md)

CS2-AimAudit turns a Counter-Strike 2 match demo (`.dem`) into a coach-style after-match report. It does not train your aim — it shows what actually happened in the match: where you shot from, how fast you were moving, who fired first, and which keys you were holding.

## Features

- **Sidebar navigation** — Dashboard, **Matches**, Settings and Guide live in the left rail. A match is opened FROM the Matches list (there is no Match item): click a row, and the analysis pages become the match's tabs.
- **Matches** — every match of every source in one dense table: map, date, score (coloured by result, with the W/L/D letter), pool (Matchmaking Competitive / Premier / FACEIT / Manual upload), your K/D, accuracy and the action. Click a row to open the match when the demo is in the library — or to download / import it when it is not (a finished download is analyzed right away). Tick the checkbox of several rows and press **Download selected ({n})** to fetch and analyze them all in one go; the newest of them opens at the end. Filters (period, map, pool, result) in one row, **Refresh** and **Add match** (pick demo files) next to it.
- **Match view** — the open match's header (back to the list, the result, the map / mode / source / date chips) with the tabs Overview · Shooting · Duels · Map · 2D replay and the analysis filters underneath.
- **Settings** — the analysis sliders, accounts (SteamID64, the replays folder, the share-code import, the Steam helper), Library and storage (sizes, delete a demo, clear the cache) and privacy.
- **Overview** — every player in one table: K/D, accuracy, HS%, shots on the move, "risky shots", shoots-first rate, aim error, duel duration, and the best / most-died-to weapon as kill-feed silhouettes (the localized name in the tooltip and as the text fallback). K/D (as kills / deaths and as a ratio), opening duels, trades and assists follow the **Side** and **Rounds** filters. Tick a row's checkbox to analyze that player; the **All columns** toggle adds the detail metrics (incl. first-shot accuracy and opening-duel winrate).
- **Shooting** — hit zones, speed at each shot, a speed-vs-distance scatter with the acceptable-speed curve, per-weapon stats.
- **Duels** — every duel with distance, who fired first, aim error at the first shot, and flags: no-scope, through smoke, wallbang, blinded. Below: your duels by map zone and how every round started (opening duels, both teams).
- **Impact** — per player: opening duels won/lost, trade kills, traded deaths, 2k–5k rounds, assists and flash assists.
- **Map** — where the selected player killed and died, on the radar: your position (circle), the opponent's (open diamond) and a line between them; blue = kill, red = death; works with every filter, and two-level maps (Nuke, Vertigo) get an Upper / Lower switch. Needs the radar images once, see [Radar images](#radar-images-for-the-map-page).
- **Duels → click a duel** — a window with the chart: speed (with the moving threshold), yaw turn, pitch and held keys as coloured bars with a legend, around the duel, with the first shot and the death marked, cut at the moment of death (input recorded after death is a demo-parser artifact, not real play). The strip walks all duels (← / → hotkeys, Shift+← / → between rounds) without closing the window; a button shows the duel in the 2D replay.
- **2D replay** — the whole round on the radar, like the spectator mini-map, for everybody: nobody is selected or highlighted. A marker per player (blue = CT, orange = T) with a view cone and an HP ring around it, the name above and the weapon silhouette under it (the plain weapon name without the icon set; the Names button hides the names); lines are shots, stars are hits and skulls in the team colour are deaths (the living stack in the table order, the dead lie under them in death order). The radar art is toned down so only the teams are bright, and fitted between the side panels; pointing at a card or a marker highlights the same player in both. The **banner above the map says who won the round and how** (bomb exploded / defused, everybody eliminated, time ran out); the bomb plant, defuse and explosion are marked on the timeline and every round chip shows how its round ended. The result comes from the demo's `round_end` when it has one and is otherwise read from the bomb events and the kills (marked as such). The panel on the left lists the players split into CT and T, sorted by nickname inside a team, as spectator-style cards without photos (fixed-size slots, so nothing shifts when an icon appears; the card shows three, two or one line depending on the height of the screen, and under 900px the panels stack under the map): the nickname, the match's kills / deaths (live, the round's own kills are added as they happen), the money, the weapon silhouette, the armor and the HP number, the grenades with the bomb (T) or the defuse kit (CT) at the end of that line, the card's background filled in the team colour up to the HP, the live speed (a dead player's card is dimmed and gets the skull), and an **eye** that hides the player from the map (the eye on top hides or shows everyone; the choice survives round switches). The right column holds a card for EVERY duel of the round, of every player: duels that have not happened yet are semi-transparent, past ones are solid, the selected one gets an outline. Clicking a card locks the playhead to that duel's segment and opens the **duel panel**: for both duelists the live speed, how fast the crosshair turns and the angle between the crosshair and the enemy's head, a chart of those over the whole duel with every shot, the numbers the duels table measured at the first shot, and the keys they pressed; click the chart to jump. The **speed button opens a menu from 0.1x to 4x** (on hover or click); with the timeline focused the arrows step one sample, Shift one second, `[` / `]` change the speed, Space plays. A round is bounded by its own freeze end and the round's official end, so the replay and the duel list always read the same window. The wheel zooms the radar, an LMB drag pans it, a double click resets the view; two-level maps (Nuke, Vertigo) show both levels, side by side or one under the other, whichever is bigger.
- **Risk model** — a shot is _risky_ when your speed exceeded what is acceptable at that distance to the nearest living enemy: any speed point-blank, near-stationary at long range. Both distances are configurable.
- **Data report** — built-in anomaly checks (impossible speeds, unresolved SteamIDs, missing positions…) plus a JSON export to attach to bug reports.
- Warmup, team damage and non-gun events are excluded automatically.
- UI in English and Russian.

## Quick start

CS2-AimAudit is **Windows-only** (Windows 10/11): the launcher and the tests target Windows, Linux and macOS are not supported.

Double-click `start.bat`. It prints what it is doing and pauses on any error:

1. checks for Python 3.13 (it also finds it through the `py` launcher),
2. creates `.venv` and installs the packages (the first run takes 1–3 min),
3. downloads the radar images for the Map page and the weapon / status icons, once (about 3 MB together, see [below](#radar-images-for-the-map-page) and [Weapon and status icons](#weapon-and-status-icons)),
4. starts the app and opens a browser tab.

Or manually, in a terminal in the project folder:

```bat
git clone https://github.com/choompuy/cs2-aimaudit.git
cd cs2-aimaudit
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python tools\fetch_radars.py
python tools\fetch_icons.py
streamlit run app.py
```

Requires Windows and Python 3.13 (the exact version CI tests, matching the pinned dependencies in `requirements.txt`) and a CS2 match demo (`.dem`).

### Where to get a demo

- **Premier / Competitive:** CS2 → Play → _Your Matches_ → download the demo. Demos are kept only for a limited time, so grab them soon after the match.
- **FACEIT / ESEA:** the demo link on the match page. Compressed files (`.dem.bz2`, `.dem.gz`, `.zip`, `.dem.zst`) are unpacked by the app automatically.
- Or import your recent Valve Matchmaking games straight from Steam — see [Importing matches](#importing-matches).

Then pick the file in the app.

## Importing matches

The **Matches** page lists your recent **Steam / Valve Matchmaking** games and your own demo files in one table, and downloads the demo you pick into the same library. Nothing happens in the background: a network request is only made when you press **Refresh** or a row's download action — opening the page only reads the local caches. No account, key or password is needed: the helper talks to the Steam client you are already logged into.

Valve only exposes recent matches through the running Steam client, so the app uses [boiler-writter](https://github.com/akiver/boiler-writter) — a small MIT-licensed console tool. Press **Install Steam helper**: a pinned release is downloaded from GitHub, checked against a SHA-256 hash pinned in the code, and unpacked into `data\tools`. Your Steam password is never asked, stored or sent; for a couple of seconds your Steam status may show CS2 running — that is how the match list is fetched. The Game Coordinator returns roughly your last 8 matches, each with a direct demo link (`replay*.valve.net`, valid for about a month).

- Demos you downloaded yourself in CS2 (Watch → Your Matches) are found in the `replays` folder and offered with an *Import and open* action.
- For an older match, paste its **share code** (`CSGO-…`) in *Settings → Accounts* and press **Add by code**.
- Set your **SteamID64** in *Settings → Accounts* to get win/loss and your own numbers (the helper usually detects the logged-in account on its own).

The table is ordered by date (newest first). The filters above it narrow it by period, map, pool (Competitive / Premier / FACEIT / Manual upload) and result; opening a match — or finishing a download — computes its summary on the way in, for free. A demo without a known "you" (no SteamID64 set, nobody picked yet) asks once when you open it: the **Remember in Settings** checkbox saves the picked SteamID globally (every future match, also the ones you download, knows who you are at once), without it the pick applies to that demo only. The **⋮** menu next to the player row in the match view says "this is not me": pick another player there and the summary and the whole analysis recompute — with the same remember choice. A per-match pick always beats the global SteamID. **Delete all demos** clears the whole library in one confirmed click, single demos are deleted in *Settings → Library and storage*.

Where the summary numbers live: `data/cache/matchstats/` — one small JSON per demo (deleted together with the demo or with *Clear cache*). The table itself never parses demos.

### What is stored, and how to remove it

Everything lives inside the app's `data` folder: `accounts.json` (folders and SteamID64, no secrets), `matches.json` (the cached match lists), `tools\` (the Steam helper), plus the downloaded demos in `demos\`. Deleting `accounts.json` and `matches.json` resets the feature completely; delete `data\tools` to remove the helper. To block every network feature, set `AIMAUDIT_OFFLINE=1`.

## Demo library and where files are stored

There is one way into a match: the **Matches** list. **Add match** opens the file picker and puts the picked demos into the library (and opens them one by one); a Steam row's **Download** fetches the demo and analyzes it right away. The URL keeps the screen (`?m=<demo>&tab=duels`), so a refresh or a copied link reopens the exact view.

- **Add match** adds the demo **by its path**: nothing is copied, the file stays where it is, no duplicate. If it is moved, renamed or its drive is not connected, the app says so and offers **Locate the file…** or **Remove from list**. Removing a linked demo only removes the list entry and its cache, never your file.
- Compressed demos (`.bz2`, `.gz`, `.zst`, `.zip`) are unpacked into the cache automatically.

**Everything the app stores lives in one folder next to `start.bat`, and `start.bat` keeps Python, pip and Streamlit from writing anywhere else:**

```
data/
  demos/        demos downloaded by the app (copies; you can also drop .dem files in by hand)
  cache/
    parsed/     parsed demos, so opening one again is instant
    matchstats/ per-match summaries (K/D, HS%, ...) for the Matches table
    unpacked/   demos unpacked from .bz2 / .gz / .zst / .zip
    pip/        pip's download cache
    pycache/    compiled .pyc files
    tmp/        the temp folder (TEMP / TMP point here)
  home/         Streamlit's own ~/.streamlit (USERPROFILE points here)
  radars/       radar images (downloaded by start.bat)
  icons/        weapon / status icons (downloaded by start.bat)
  tools/        the Steam helper (boiler-writter, installed from the Matches tab)
  accounts.json match-import accounts (nickname, folders - no secrets)
  matches.json  the cached match lists
  library.json  the list of demos (copies and links)      state.json  last demo and player
.venv/          the Python environment (next to data/)
```

**To uninstall, delete the folder.** `start.bat` points `TEMP`, `USERPROFILE`, pip's cache and Python's `.pyc` folder into `data/`, so nothing is left in `%TEMP%`, your user profile or `%LOCALAPPDATA%`. What stays on the machine: the Python installation itself, your browser's own data for `localhost`, and the demos you linked (they are your files, in your folders).

Open *Library and storage* in the app to see the folder, the sizes, remove or delete a demo, or clear the cache. You can also delete `data/cache/` by hand at any time: it is rebuilt when needed. Only `data/demos` (downloaded copies) holds something that cannot be rebuilt. Move the folder with the `AIMAUDIT_DATA_DIR` environment variable. If the app folder is read-only (for example under Program Files), data goes to `%LOCALAPPDATA%\CS2-AimAudit` and the app tells you; put the app in a writable folder to keep everything in one place. `data/` is git-ignored.

## Radar images for the Map page

The Map page draws points on the real radar. The radar images are Valve's artwork and are not part of this repository, so **`start.bat` downloads them for you the first time** (about 3 MB from the public [awpy-data](https://github.com/pnxenopoulos/awpy-data) release into `data\radars` next to `start.bat`). After that nothing is downloaded again. Without internet the app still works: points are drawn on a plain grid in game units, and `start.bat` tries again the next day.

- Skip the download for good: set the environment variable `AIMAUDIT_NO_RADARS=1` (for example `setx AIMAUDIT_NO_RADARS 1`).
- Download or update by hand: `python tools\fetch_radars.py`.
- The app finds the radars in `data\radars`, in an existing awpy cache (`~\.awpy`) or in the folder set by `AIMAUDIT_RADAR_DIR`.

## Weapon and status icons

The tables and the 2D replay show weapons and events the way the CS2 kill feed does: flat white silhouettes, tinted by the UI. The icons are Valve's artwork and are not part of this repository, so **`start.bat` downloads them for you the first time** (about 1 MB from the public [cs2-react-hud](https://github.com/lexogrine/cs2-react-hud) project into `data\icons` next to `start.bat`, verified against SHA-256 hashes pinned in the code). After that nothing is downloaded again. Without the icons the app still works: every slot shows the localized weapon / event name as text, and `start.bat` tries again the next day. See [docs/ASSETS.md](docs/ASSETS.md) for the provenance and the security model (the SVGs are sanitized and hash-verified before use).

- Skip the download for good: set the environment variable `AIMAUDIT_NO_ICONS=1` (for example `setx AIMAUDIT_NO_ICONS 1`).
- Download or update by hand: `python tools\fetch_icons.py`.
- Newer weapons than the pinned set: `python tools\fetch_icons.py --source game` decompiles the icons straight out of your installed CS2 (needs [Source2Viewer-CLI](https://github.com/ValveResourceFormat/Source2Viewer-CLI) on PATH or at `AIMAUDIT_S2V`; the folder can be set with `--cs2-dir` / `AIMAUDIT_CS2_DIR`).
- The app reads the icons from `data\icons` or from the folder set by `AIMAUDIT_ICON_DIR`; *Settings → Library and storage* shows their size and can delete them.

## Privacy

All parsing and analysis happen inside the app on your machine, and the app itself makes no requests on its own. The only network activity is explicit and listed here:

- the one-time radar and icon downloads of `start.bat` / `tools\fetch_radars.py` / `tools\fetch_icons.py` (switch them off with `AIMAUDIT_NO_RADARS=1` / `AIMAUDIT_NO_ICONS=1`);
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
- **Duel chart** — look for: shooting while moving, big last-moment aim corrections, crouch/walk timing.

The in-app **Guide** tab explains the risk model and the limitations: sensitivity, FPS and ping are not stored in demos (raw mouse deltas are, since CS2, but they scale with sensitivity); aim error is approximate; duels are detected by a time window, not by line of sight.

## Something looks wrong?

Open the **Data report / debug** expander at the bottom of the page. The built-in checks flag data anomalies, and the downloadable JSON report contains the raw parser fields needed to find the cause — please attach it when you [open an issue](../../issues).

## Development

```bat
python tests/test_core.py        # synthetic match, no .dem file needed
python tests/test_sources.py     # match import (Steam), no network
python tests/test_icons.py       # icon sanitizer / aliases / fallbacks
python tests/check_ui.py         # i18n / schema / layering guardrails
python tests/smoke_app.py --fast # AppTest end-to-end (fake parser)
cd tests/js && npm ci && cd ../.. && python tests/check_roundrep_js.py
```

The JS line needs Node once (`npm ci` in `tests/js`); without it
`check_roundrep_js.py` skips. CI runs both.

- `core/` — parsing and all metrics (demoparser2), no UI code.
- `sources/` — match import from Steam: the provider, the HTTP
  transport, the protobuf reader and the inbox (also no UI code).
- `app.py` — Streamlit entry point (dispatcher); the pages live in `views/`,
  the shell in `shell.py`, the filter panel in `filters_ui.py`.
- `ui/tables.toml` — the table schema: formats, sorting, grading and bars
  for every grid, described as data.
- `i18n.py` + `i18n/en.toml` / `i18n/ru.toml` — UI strings, one flat file
  per language; missing keys fall back to English.
- `docs/ARCHITECTURE.md` — module map, dependency rules and how to add a
  metric, column, page or filter.
- `docs/DEMOPARSER_NOTES.md` — notes on the demoparser2 API.

Pull requests are welcome. Bug reports with the debug JSON attached are especially valuable.

## Name and trademarks

CS2-AimAudit is an independent open-source project. It is not affiliated with, endorsed by, or connected to Valve Corporation, Counter-Strike 2, or the Aimlabs (Aim Lab) aim trainer. The radar images and the weapon / status icons are Valve's artwork and are never committed to this repository (see [docs/ASSETS.md](docs/ASSETS.md) and NOTICE).

## License

[MIT](LICENSE)
