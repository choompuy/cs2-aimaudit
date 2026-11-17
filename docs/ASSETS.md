# Assets: where the art comes from and why it is not in this repository

CS2-AimAudit draws on three kinds of third-party art. None of the Valve
artwork is committed here - it lives on the user's machine in the data
folder, downloaded by a script that pins exactly what it takes. The repo
ships only the code: the fetchers, the loaders and the tests.

| Art | Used by | Written by | Provenance |
| --- | --- | --- | --- |
| Radar images | the Map page, the 2D replay | `tools/fetch_radars.py` | Valve's radar art, published as PNG in the open [awpy-data](https://github.com/pnxenopoulos/awpy-data) release |
| Weapon / status icons | tables, the 2D replay, the kill feed | `tools/fetch_icons.py` | Valve's HUD art (flat white silhouettes, kill-feed style), published in the open [cs2-react-hud](https://github.com/lexogrine/cs2-react-hud) project; or decompiled by the user from their own CS2 install |
| The two eye icons | the 2D replay player list | committed in `ui/roundrep.js` | [Lucide](https://lucide.dev) `eye` / `eye-off`, ISC License (see THIRD_PARTY_LICENSES.md) |

Counter-Strike, Counter-Strike 2 and all related artwork are trademarks
and/or copyrighted works of Valve Corporation. The mirror in
cs2-react-hud distributes the art under its own terms; this repository
claims no rights to it and only points a downloader at it, exactly like
the radar flow does. The HUD project's *code* is MIT - that license does
not cover Valve's artwork, and this repository does not rely on it.

## The flow, one rule for both fetchers

`start.bat` runs the fetchers with `--if-missing` on every start:

* everything is already there -> does nothing, instantly;
* the matching `AIMAUDIT_NO_RADARS` / `AIMAUDIT_NO_ICONS` is set -> the
  download is opted out for good;
* no internet / the host is blocked -> a note, exit code 0, and a
  `.fetch_failed` marker (in the same folder) makes the next automatic
  attempt wait a day. The launcher never blocks on this.

The app itself never touches the network. A missing, empty or broken
asset folder is a normal state: the Map page falls back to a plain grid
in game units, and every weapon / status slot falls back to the localized
text name, exactly what the app showed before the icons existed.

## Radar images

See the README section "Radar images for the Map page". In short:
`python tools\fetch_radars.py` (or the launcher) downloads the PNGs from
a pinned awpy-data release into `data\radars` and writes
`map_data.json`. `AIMAUDIT_RADAR_DIR` points somewhere else.

## Weapon / status icons

```
data\icons\<key>.svg        one silhouette per icon
data\icons\manifest.json    {"<key>": {"file", "w", "h", "sha256", "source"}}
```

The icons are the flat white silhouettes the CS2 kill feed itself shows.
Two families, one folder:

* **weapons** - the file names in the HUD repo equal the demo weapon
  names (`ak47`, `awp`, `knife_karambit`, ...); the app additionally maps
  a few aliases (`usp` -> `usp_silencer`, `p2000` -> `hkp2000`, ...) in
  `ui/icons.py`.
* **status icons** - the HUD repo names them differently than the app
  (`icon_skull_default`, `icon_defuse_default`, ...); `IMAGE_FILE` in
  `tools/fetch_icons.py` holds the mapping. The `c4` key exists in both
  families; the badge set wins.

### Security: sanitize, pin, verify

Every file, from either source, passes `sanitize_svg()` (in
`ui/icons.py`, used by both the fetcher and the loader) before anything
else: `<script>` blocks, `on*` event attributes, `<foreignObject>`,
external `href` / `xlink:href` / `<use>` (local `#fragment` references
survive) and DOCTYPEs are stripped, and a missing `xmlns` / `viewBox` is
added. The sanitizer is idempotent - a clean file passes through
byte-for-byte.

The **SHA-256 pins in `tools/fetch_icons.py` are hashes of the sanitized
content**, not of the raw download. Two upstream files would differ after
sanitizing anyway (`flashbang.svg` has no `xmlns`; `suicide.svg` carries
a DOCTYPE with an external DTD), and pinning the sanitized bytes keeps
the loader's re-sanitization stable: what was downloaded is exactly what
the loader later verifies.

The mirror flow re-checks every file: a download whose sanitized content
does not hash to its pin is **skipped, not saved**. The loader
(`ui.icons`) verifies the hash again on every load - a file that fails
(any tampering, any corruption) is simply "no icon": the UI shows text,
never a broken image, and never raises.

Icons are never inlined as raw SVG anywhere. They enter the DOM as base64
`data:image/svg+xml` URIs behind `mask-image` (CSS) or get drawn through
an alpha-mask `source-in` composite (canvas) - the silhouette is a mask,
the colour always comes from the surrounding UI (part of the art carries
no `fill="white"` and would render black otherwise).

### The `game` source

`--source game` decompiles the icons straight out of the user's own CS2
installation instead of the mirror - useful for weapons newer than the
pinned mirror commit:

1. find the game folder: `--cs2-dir` / `AIMAUDIT_CS2_DIR`, else every
   default Steam root's `steamapps/libraryfolders.vdf` is parsed for
   library folders and `steamapps/common/Counter-Strike Global
   Offensive` is checked;
2. [Source2Viewer-CLI](https://github.com/ValveResourceFormat/Source2Viewer-CLI)
   must be on `PATH` or at `AIMAUDIT_S2V`;
3. the `.vsvg_c` paths inside `pak01_dir.vpk` are **discovered** through
   `--vpk_list --vpk_extensions vsvg_c`, never assumed - the file names
   inside the VPK are not guaranteed to match the HUD repo's;
4. each icon is decompiled with `--vpk_filepath <path> --vpk_decompile
   --output <dir>` (output files, not stdout - the CLI's stdout mixes
   resource types), sanitized, and written with `"source": "game"` in
   the manifest.

`--source auto` (what the launcher effectively gets from
`--if-missing`) uses the mirror; `game` is an explicit opt-in. The
decompile working directory `.tmp_s2v` is created inside the icons
folder and removed afterwards - the app never writes to the system temp.

**Caveat, stated plainly:** this flow follows the Source2Viewer-CLI
documentation and was tested against the mirror set, but it could not be
verified against a real, current CS2 installation while writing this
feature. If a path cannot be found the key is skipped silently and the
mirror / the text fallback still apply.

### Environment variables

| Variable | Meaning |
| --- | --- |
| `AIMAUDIT_NO_ICONS` | `1` skips the download for good (the text fallback everywhere) |
| `AIMAUDIT_ICON_DIR` | read the icons from another folder (tests use this) |
| `AIMAUDIT_CS2_DIR` | the CS2 install folder for `--source game` |
| `AIMAUDIT_S2V` | the path of the Source2Viewer-CLI binary |
| `AIMAUDIT_NO_RADARS` | the same opt-out for the radar images |
| `AIMAUDIT_RADAR_DIR` | the radars from another folder |

The "Delete the icons" button in the app's library settings clears the
folder (and the in-memory cache) the same way the radars can be cleared;
the next `start.bat` re-downloads both.
