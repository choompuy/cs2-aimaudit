# CS2-AimAudit 🎯

**Audit your aim — local CS2 demo analysis. Shooting, duels, movement and key presses, with nothing ever leaving your computer.**

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![UI](https://img.shields.io/badge/UI-Streamlit-FF4B4B)
![Privacy](https://img.shields.io/badge/privacy-100%25%20local-green)

English · [Русский](README_RU.md)

CS2-AimAudit turns a Counter-Strike 2 match demo (`.dem`) into a coach-style after-match report. It does not train your aim — it shows what actually happened in the match: where you shot from, how fast you were moving, who fired first, and which keys you were holding.

## Features

- **Overview** — every player in one table: K/D, accuracy, HS%, shots on the move, "risky shots", shoots-first rate, aim error, duel duration. Tick a row's checkbox to analyze that player; the **All columns** toggle adds the detail metrics (incl. first-shot accuracy and opening-duel winrate).
- **Shooting** — hit zones, speed at each shot, a speed-vs-distance scatter with the acceptable-speed curve, per-weapon stats.
- **Duels** — every duel with distance, who fired first, aim error at the first shot, and flags: no-scope, through smoke, wallbang, blinded. Below: your duels by map zone and how every round started (opening duels, both teams).
- **Impact** — per player: opening duels won/lost, trade kills, traded deaths, 2k–5k rounds, assists and flash assists.
- **Timeline** — speed / yaw / pitch / held keys around any duel, cut at the moment of death (input recorded after death is a demo-parser artifact, not real play).
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

## Privacy

All parsing and analysis happen inside the app on your machine. CS2-AimAudit makes no network requests, collects no telemetry and does not upload your demos anywhere. The bundled `.streamlit/config.toml` also disables Streamlit's own anonymous usage statistics (`gatherUsageStats = false`) — if you copy or replace that file, keep the setting.

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
python tests/test_core.py   # synthetic match, no .dem file needed
python tests/check_ui.py    # i18n / API consistency checks
```

- `core.py` — parsing and all metrics (demoparser2), no UI code.
- `app.py` — Streamlit UI.
- `i18n.py` — English/Russian strings.
- `docs/DEMOPARSER_NOTES.md` — notes on the demoparser2 API.

Pull requests are welcome. Bug reports with the debug JSON attached are especially valuable.

## Name and trademarks

CS2-AimAudit is an independent open-source project. It is not affiliated with, endorsed by, or connected to Valve Corporation, Counter-Strike 2, or the Aimlabs (Aim Lab) aim trainer.

## License

[MIT](LICENSE)
