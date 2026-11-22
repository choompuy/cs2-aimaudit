#!/usr/bin/env python3
"""
Downloads the weapon / status icons (flat white silhouettes, CS2 kill-feed
style) and their manifest.

The icons are Valve's artwork (the same HUD art Counter-Strike itself
shows), published in the open HUD project github.com/lexogrine/cs2-react-hud
(code there is MIT; the art belongs to Valve and is NOT licensed by this
repository). They are kept on YOUR machine in the data folder next to
start.bat, exactly like the radar images, and are never committed here.
The app itself never touches the network: only this script does.

    python tools\\fetch_icons.py                # download (or re-download) now
    python tools\\fetch_icons.py --if-missing   # what start.bat runs on every start
    python tools\\fetch_icons.py --dest DIR     # another folder (AIMAUDIT_ICON_DIR=DIR)
    python tools\\fetch_icons.py --source game --cs2-dir "C:\\...\\Counter-Strike Global Offensive"
                                                 # decompile straight from an installed
                                                 # CS2 (needs Source2Viewer-CLI on PATH,
                                                 # or AIMAUDIT_S2V=<path to the binary>)

--if-missing is made for the launcher and never blocks it:
  * the icons are already there  -> does nothing, instantly;
  * AIMAUDIT_NO_ICONS=1 is set   -> does nothing (opt out of the download);
  * no internet / GitHub blocked -> says so, exits 0, and does not retry for
    a day (the UI falls back to weapon names as text, like before).

Every file is verified against a pinned SHA-256 before it is saved (the
hash of the SANITIZED content - scripts, event handlers, external
references and DOCTYPEs are stripped first, see ui/icons.py); a file that
does not match is skipped, not saved. The "game" source decompiles the
icons straight out of pak01_dir.vpk for weapons newer than the pinned
mirror commit; the paths inside the VPK are DISCOVERED through
--vpk_list, not assumed (see docs/ASSETS.md: that flow could not be
verified against a real CS2 install).

Result: DIR/<key>.svg and DIR/manifest.json
    {"<key>": {"file": "<key>.svg", "w": 678, "h": 222,
               "sha256": "<pin>", "source": "mirror" | "game"}}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# the data folder / the sanitizer live with the app (see store/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402
from tools import fetchutil  # noqa: E402
from ui.icons import sanitize_svg, svg_size  # noqa: E402

# the seams tests monkeypatch: module-level names, shared code in fetchutil
_get = fetchutil.get
_write = fetchutil.write

REPO = "lexogrine/cs2-react-hud"
COMMIT = "7874750c97fcecd8f72eb3fad382917e035ec651"
URL = "https://raw.githubusercontent.com/{repo}/{commit}/src/assets/{folder}/{name}.svg"
SAFE_KEY = re.compile(r"^[a-z0-9_]+$")  # keys double as file names
TIMEOUT_AUTO, TIMEOUT_FULL = 12.0, 60.0

# --------------------------------------------------------------- the pins
# key (the icon name used by the app) -> SHA-256 of the SANITIZED file
# content. Computed against the pinned commit above; a mirror file whose
# sanitized content does not hash to this value is skipped. Weapon keys
# double as file names in the HUD repo.
WEAPON_SHA = {
    "ak47": "78cd874d200f711e808a132afc3a887f9073d4b81ef774deecab9c107580f5fa",
    "aug": "fc56fa6900aeef91d10f11ba28e4cae91c65a9b1b5564468857096f7d9a340ff",
    "awp": "698739c287bf91a08cd26460c02b9fdf0b0aea6af789d3124b86e3c3c2b0695a",
    "bayonet": "ac612b196e021d3b34c0d4b39c507dcd06c2ef7a1d32deaa270ed165f243ba61",
    "bizon": "9a003e08ab658389831fb5c62b796ad3633e247d87e11581e279bce88fb8dbdf",
    "c4": "892a54b93585ddb513906d8cefc7deb61e716a8886ed6369c0ad51bede7b2459",
    "cz75a": "db370d75a2611e4c67001469fe7138a1e5e858317ae61e7674598b9eeaea7698",
    "deagle": "8d5e01804c07201b3891e959290a9095b057c347c79c0f6076fee63102519099",
    "decoy": "bf0444a4dbdeaad046f9b96d08c3e6637403144e5c9643c142e11a4d75d76683",
    "elite": "c2280b5b5f769e401809093e36de0da631ebf5bc5087d1d5047aecb4a79db0d9",
    "famas": "5fa96965e9838234ad237cc65d4110680344d8c23ca15f604060990460c265fb",
    "fiveseven": "eb9f7a0e33075a0744c71ca4a26aae0e874e39d0a1b81faac48b7a6d52a67c78",
    "flashbang": "0accacac2e0f44696098f2310b88fc0dad8af6a5c5a149ae5e11faae84c9ca12",  # SANITIZED (differs from upstream)
    "g3sg1": "2891217d0964e505026a8815c81626cabe4d68c769a264db5176e39068a88220",
    "galilar": "f0f5196e9dc53e73624ddc216963ad47604f30714642d90ab2e1c334a683b542",
    "glock": "cab94b79aea6ad9dee90dd89ff4df6524b81c10a6eac279017dbff8d93282f97",
    "hegrenade": "8cba245267a3ca87b00082400ad901b1b639e3f0bfe999ee33979f61a5730f94",
    "hkp2000": "70d7ffb8073efa7344310d788be6091be492f70a19a649f6505ba9b269658b64",
    "incgrenade": "ac70d872f1ce7d2c130b2e97325c1a6ab901a1fe5752110ab7723e40329b0ce3",
    "inferno": "567ce5309af3a6ced0132fc3cfed1171240e495da57e244061279e641c6e9788",
    "knife": "09c0262edde9ba07679e7d2a539803259672cfe28d0e0890ea37d8131cf27080",
    "knife_bayonet": "ac612b196e021d3b34c0d4b39c507dcd06c2ef7a1d32deaa270ed165f243ba61",
    "knife_butterfly": "5c213780f31a0fd8b21fdc62a1a6eb9ee493a57dfb4f0e3fc80a1052a17fa1e6",
    "knife_canis": "ecf4edfde061d13e73d993a64f95890966d1d31cfc4fedfb625ecf6913f6c5b5",
    "knife_cord": "3a2c71714711db7cf81a62323c0aa342b630e133e9014f7389f1878dcb1f3a6f",
    "knife_css": "94beee8ff650fbff5e5683a6c7a49d9a916a66ab22fd101c4e2b7a676128380c",
    "knife_falchion": "d434d4d0386b5e3b1a5cbd9c3fdb4d9f2773b770416d51e981844ad606b71ca8",
    "knife_flip": "73ab354c46e6a238ed6bccf7bf7f6c083ca79518d732e331bf5a770302feae1e",
    "knife_gut": "f4bd521c64dd1ff872334750f2657f4f2bf6e30046207356cd96d0a251124c0e",
    "knife_gypsy_jackknife": "da2aabe78bf8f4fd4677d82923151b78b75421494f37f55fe0f6570ef9187e67",
    "knife_karambit": "2364858088de7e026d9ca5eb514d3088d21720703cdc79ec5867ef3b179438b6",
    "knife_m9_bayonet": "b2c4afaff8e7dbdb15aff57588fb451607e4c55961814340bb9884240d445c68",
    "knife_outdoor": "1af4ece39242a41666cc75b79e4e6ac50daf4c5a85fcc7e359d4e7e87e031f88",
    "knife_push": "a65d80b5bd15cc41179a8aff57dda20394ba1c640f44257faac547b35a51c778",
    "knife_skeleton": "359850ff7f0a832706d16e7558a17115047fa72920c6e0e306c885c4779907e7",
    "knife_stiletto": "e6fa0d5f24bd9b378c14d84a1dcb160dfa418decaabaf895cf2b41f957bbcd46",
    "knife_survival_bowie": "7eefe0da3128fa453b7fe24965915e61f01acba0e716ccb70d2b59771caebd32",
    "knife_t": "a62b68922f6953016cb6a031190d00ddefa5e475a5beca7a2b9c3f0db69705ce",
    "knife_tactical": "2a9213c422b2c7ef2bad07c0b4439d029d522fff63063fc8f26e698c886be61e",
    "knife_ursus": "51e6c4361eac86750549f425964188af8e69266fc2ae1a8ae4d735c3050e3495",
    "knife_widowmaker": "48ee10e975471794b9d2d9a93a19271d27c9007b465250bb6358ad7f354d3a9f",
    "m249": "4b7e306c9405a8b0cec0ef01c3c4c26ea13ea941e9622ec79a6c82c48c6628a2",
    "m4a1": "22651e9eb7ea0a12ce3aa6674a53e104d5ac125681237ba3e031e6f7601ba010",
    "m4a1_silencer": "62bc42b4a3178af2a262a552216907832cf232b8b2a02956384e359089ee7212",
    "m4a1_silencer_off": "62bc42b4a3178af2a262a552216907832cf232b8b2a02956384e359089ee7212",
    "mac10": "19348fcf41835362392a010c6569fa3210e9fd8314f84038b0b8082afeb10d41",
    "mag7": "4568cf416df04678b3e46e27d6b843911c13e47978c6f42ad8da7291d612d9f2",
    "molotov": "567ce5309af3a6ced0132fc3cfed1171240e495da57e244061279e641c6e9788",
    "mp5sd": "ceb90eb49686f78c09f2c5646f14f29a1bc8ec25152a371f5d3e5e1202f88411",
    "mp7": "7a3801520bb12955b19d59604d81741a8713f0f75fb607b3cc1d571dd5e80d0a",
    "mp9": "49fca731c188c4f54e49e30678777b76011178bb7a51cdc92d2db80e9c8428fa",
    "negev": "7867e1235f4411bff53e087f0fc8d9b477933ca1e6b3d06e85ac6610103e407d",
    "nova": "251f2701f3fe3b86aa0c35cf6a00f7b2d53835d9e74409d639e9e56c99c3119f",
    "p250": "6d96d9a498acc8f48a1e83bfb5ebbc0cbda2b02d7b8a63218052a2e989b0d1e8",
    "p90": "ddc39e8fec553508f497acc7ea3e47ff66126d1a0ea6f6203078d18ec3b62f86",
    "revolver": "67caa4cda599592544ee2fd45c578eea60da3395a7b500ed526521de8680a0fa",
    "sawedoff": "66d13c083d4eb5992209f1b76debd3a5f757682bcf05d600fc681521cc345b77",
    "scar20": "d2c3bce67b5318ec73e16680497e919c916eadd3b020284d867e5a4622d75b83",
    "sg556": "db9086246c4d523d03f3995191ec40d27c01db049eb97e851938b8c59cb2f9d1",
    "smokegrenade": "af8f91683fc6fac0a2b9ed1291f9f26cd763ffdad1d29dee251c32c86be43b54",
    "ssg08": "ade6929f52b99e971db7b5abd817ce37d1898a93cf678bead24bf4cb87dafba3",
    "taser": "d6d8fbe71ce180052db97f10807241b3dfa2d4ade8fa1aef846af731fd7878c3",
    "tec9": "a26fdc0c6c12ca9b7460dc3fb697d5119525e03d7e733d8df859c65c39182adf",
    "ump45": "3b30884c6b059d306f86275eeeb6ee031ce882c30242dbd1dd9a45556ab25e69",
    "usp_silencer": "ad985d464c16a37fe395ae026b81564b3998f4d5b9fda7c3bbf6cb12014a307f",
    "usp_silencer_off": "ad985d464c16a37fe395ae026b81564b3998f4d5b9fda7c3bbf6cb12014a307f",
    "world": "d382a2b1724ceddf5d91478f6d3c0711e7a3691ba1a489cadac74b372d43a580",
    "xm1014": "54f8edfbdd48f31e15cad6222d611251e9171e3804556d13a891aa3a53bddee0",
}

# status icons: the HUD repo names the files differently than the app
# (app key -> file name in the HUD repo, without .svg), plus the same
# sanitized SHA-256 per key as above.
IMAGE_FILE = {
    "headshot": "headshot",
    "skull": "icon_skull_default",
    "defuse": "icon_defuse_default",
    "bomb": "icon_bomb_default",
    "bomb_explode": "icon_bomb_explosion_default",
    "c4": "icon_c4_default",
    "timer": "icon_timer_default",
    "hourglass": "icon_hourglass_default",
    "wallbang": "wallbang",
    "noscope": "noscope",
    "smoke_kill": "smoke_kill",
    "flashed_kill": "flashed_kill",
    "suicide": "suicide",
    "blind": "icon_blind",
    "burning": "icon_burning",
    "health": "icon_health_default",
    "armor_full": "icon_armor_full_default",
    "armor_half": "icon_armor_half_default",
    "armor_half_helmet": "icon_armor_half_helmet_default",
    "armor_helmet": "icon_armor_helmet_default",
    "armor_none": "icon_armor_none_default",
}

IMAGE_SHA = {
    "headshot": "0ce669fdfc510b4463b9cc0a2759671be4c6e2d835676495713a30fa839f3557",
    "skull": "6261e9647342e42ffc11309586500a372be74489a10400ddc34894ad5d885ac6",
    "defuse": "69cd98fbce7204536b01677e0dfe5a5e0af24e1c57a488627d780f596d25bec7",
    "bomb": "f8464c743a2890cfecfb8f74b0749f8e91da760c303a61de66850770515e7b83",
    "bomb_explode": "439b300125898c80dca5168151d3fb12b2ef7b00b08dbbf951358c771b973f1f",
    "c4": "f963031f72986ab666cd79932399d81d365b511a4d2c7cee6d59cdf7bc6a2056",
    "timer": "bf3e2206184bc1b4fc5288817962b574ae172f495b886c2d0d1dad169a810a03",
    "hourglass": "a4af6bc34da34a429779415617c7424dc2fe4312c3ca6fad286f9eb63558b599",
    "wallbang": "4cc7c84aae80072f40fb5ac26c7eff5a4999e0008e982b69bf4c382962eba60d",
    "noscope": "44862d353644fc902970ecccf15bee653c4f75a284bb956a8a9e821bb53a7b9c",
    "smoke_kill": "156eebf34e2ed0337969d829276b7e4f5f987a89faf9dad8e66bef806416ee28",
    "flashed_kill": "5a1233c62f605337ced75ec0f3a35f8d03341d3cfe14362f0b3d3f9fd33f4445",
    "suicide": "6b467e1343eb36a0f527c3c547946a93cb5fd65ab31d47d98501e77a6603cccc",  # SANITIZED (differs from upstream)
    "blind": "47fc1dd6603ff63f2c5d2b767cc922442d7aec156f46c1c152abf668be98f62a",
    "burning": "177713d26938209f17c95b0063bf4afd79d05803269a63f4b29de5776502b258",
    "health": "84539f6f4e397ec91a4c6a00d6de2fbbff93371b91e70c71a8d736b57d00747a",
    "armor_full": "c17ed7e66ee8bafe131e24d420d68f3ff556dc454ef8cbc25d6fbb214c546293",
    "armor_half": "c71b0f1f741b978af994a3989d7f43c4839a0644f52a5f426e9ab013181ca866",
    "armor_half_helmet": "718e2750a5cae5123c581bf0afadb1a4df083012453a7960c9546a7593d96fd3",
    "armor_helmet": "d86f6237d1326612e4895cdfb17824ab9a5ac8eb8b684fcaed33c9091dff9acf",
    "armor_none": "b315b0f32dee2c4a817ebe2969be07b96dd2c40ea9b68607b4bd61d07f268af8",
}


def present(dest: str) -> bool:
    """The icons are installed: a manifest that covers every pinned key."""
    try:
        with open(os.path.join(dest, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(m, dict):
        return False
    wanted = set(WEAPON_SHA) | set(IMAGE_SHA)
    return wanted <= set(m)


def _read_manifest(dest: str) -> dict:
    try:
        with open(os.path.join(dest, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return {}
    return m if isinstance(m, dict) else {}


# ------------------------------------------------------------ game source
CS2_DIR_NAME = "Counter-Strike Global Offensive"


def _steam_roots() -> list[str]:
    """Default Steam install folders (checked only when no --cs2-dir/env)."""
    env = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    return [os.path.join(env, "Steam"), os.path.join(pf, "Steam")]


def find_cs2_dir(explicit: str | None = None) -> str | None:
    """
    The CS2 game folder (the one with game/csgo/pak01_dir.vpk), or None.
    `explicit` (--cs2-dir / AIMAUDIT_CS2_DIR) wins; otherwise every default
    Steam root's steamapps/libraryfolders.vdf is read and its library
    folders are scanned for steamapps/common/<CS2_DIR_NAME>.
    """
    cands = [explicit] if explicit else []
    cands += [e for e in (os.environ.get("AIMAUDIT_CS2_DIR"),) if e]
    if not cands:
        for root in _steam_roots():
            if not os.path.isdir(root):
                continue
            cands += _libraries(os.path.join(root, "steamapps", "libraryfolders.vdf"))
            cands.append(root)
    for c in cands:
        if _is_cs2_dir(os.path.abspath(os.path.expanduser(c))):
            return os.path.abspath(os.path.expanduser(c))
    return None


def _libraries(vdf: str) -> list[str]:
    """Library folders listed in a Steam libraryfolders.vdf (pure regex)."""
    try:
        with open(vdf, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    return [os.path.normpath(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]


def _is_cs2_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "game", "csgo", "pak01_dir.vpk")) or (
        os.path.isdir(path) and os.path.isfile(os.path.join(path, "pak01_dir.vpk"))
    )


def _s2v_binary() -> str | None:
    """Source2Viewer-CLI (ValveResourceFormat, MIT): PATH or AIMAUDIT_S2V."""
    env = os.environ.get("AIMAUDIT_S2V")
    if env and os.path.isfile(env):
        return env
    return shutil.which("Source2Viewer-CLI") or shutil.which("Source2Viewer-CLI.exe")


def _run_s2v(binary: str, args: list[str], timeout: float) -> str:
    res = subprocess.run(
        [binary, *args], capture_output=True, text=True, timeout=timeout
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Source2Viewer-CLI exited with {res.returncode}: {res.stderr.strip()[:200]}"
        )
    return res.stdout


def _vpk_path(cs2_dir: str) -> str:
    p = os.path.join(cs2_dir, "game", "csgo", "pak01_dir.vpk")
    return p if os.path.isfile(p) else os.path.join(cs2_dir, "pak01_dir.vpk")


def _list_vsvg(binary: str, vpk: str) -> list[str]:
    """Every .vsvg_c path inside the VPK (one --vpk_list run)."""
    out = _run_s2v(binary, ["--input", vpk, "--vpk_list", "--vpk_extensions", "vsvg_c"], 600)
    return [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".vsvg_c")]


_HUD_HINTS = ("hud", "panorama", "icons", "killfeed")


def _pick_path(paths: list[str], key: str) -> str | None:
    """The path that is most likely THIS weapon's HUD icon: prefer paths
    with a HUD-ish folder in them, then the shortest match."""
    matches = [p for p in paths if p.lower().endswith(f"/{key}.vsvg_c") or p.lower() == f"{key}.vsvg_c"]
    if not matches:
        return None
    ranked = sorted(
        matches,
        key=lambda p: (
            not any(h in p.lower() for h in _HUD_HINTS),
            len(p.split("/")),
            len(p),
        ),
    )
    return ranked[0]


def _game_wanted(keys) -> dict[str, str]:
    """app key -> the name to look for inside the VPK: the badge icons are
    named differently than the app spells them (IMAGE_FILE); weapons carry
    their own names."""
    return {k: IMAGE_FILE.get(k, k) for k in keys}


def fetch_game(dest: str, cs2_dir: str, wanted: dict[str, str], timeout: float) -> int:
    """
    Decompile the wanted icons straight out of the installed game.
    `wanted`: {key: filename-inside-the-vpk-without-extension} - the HUD
    names the files like the demos name the weapons, with exceptions the
    caller maps beforehand. Returns the number of icons saved.
    """
    binary = _s2v_binary()
    if not binary:
        raise RuntimeError(
            "Source2Viewer-CLI was not found (set AIMAUDIT_S2V or add it to PATH)"
        )
    vpk = _vpk_path(cs2_dir)
    paths = _list_vsvg(binary, vpk)
    picked = {}
    for key in wanted:
        p = _pick_path(paths, wanted[key])
        if p:
            picked[key] = p
    n = 0
    os.makedirs(dest, exist_ok=True)
    # the decompile output lives INSIDE the icons folder (never the system
    # temp: tests/check_ui.py keeps every module inside the app folder)
    tmp = os.path.join(dest, ".tmp_s2v")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        for key, path in picked.items():
            out_dir = os.path.join(tmp, key)
            os.makedirs(out_dir, exist_ok=True)
            _run_s2v(
                binary,
                [
                    "--input", vpk, "--vpk_filepath", path,
                    "--vpk_decompile", "--output", out_dir,
                ],
                timeout,
            )
            svg = next(
                (f for f in _walk_files(out_dir) if f.endswith(".svg")),
                None,
            )
            if not svg:
                continue
            with open(svg, "rb") as fh:
                text = sanitize_svg(fh.read())
            size = svg_size(text) or (0, 0)
            _write(os.path.join(dest, f"{key}.svg"), text.encode("utf-8"))
            _manifest_set(dest, key, f"{key}.svg", size[0], size[1], "game")
            n += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return n


def _walk_files(root: str):
    for r, _dirs, names in os.walk(root):
        for n in names:
            yield os.path.join(r, n)


# --------------------------------------------------------------- manifest
def _manifest_set(dest: str, key: str, file: str, w: int, h: int, source: str) -> None:
    m = _read_manifest(dest)
    import hashlib

    with open(os.path.join(dest, file), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    m[key] = {"file": file, "w": int(w), "h": int(h), "sha256": digest, "source": source}
    _write(os.path.join(dest, "manifest.json"), json.dumps(m, indent=1).encode("utf-8"))


def download_mirror(dest: str, timeout: float) -> tuple[int, int]:
    """
    Fetch the pinned set from the mirror. Returns (saved, skipped): a file
    whose sanitized content does not hash to its pin is skipped, a network
    error raises (the caller decides how loud to be). The manifest is
    rewritten only with the keys that are really on disk afterwards.
    """
    os.makedirs(dest, exist_ok=True)
    manifest = _read_manifest(dest)
    wanted: dict[str, tuple[str, str]] = {}
    wanted.update({k: (k, h) for k, h in WEAPON_SHA.items()})
    wanted.update({k: (f, IMAGE_SHA[k]) for k, f in IMAGE_FILE.items()})
    saved = skipped = 0
    import hashlib

    for key, (name, pin) in wanted.items():
        target = os.path.join(dest, f"{key}.svg")
        if os.path.isfile(target):
            try:
                with open(target, "rb") as fh:
                    if hashlib.sha256(fh.read()).hexdigest() == pin:
                        continue  # already there, verified
            except OSError:
                pass
        raw = _get(
            URL.format(
                repo=REPO,
                commit=COMMIT,
                # c4 exists in both folders: the badge set wins for its key
                folder="images" if key in IMAGE_FILE else "weapons",
                name=name,
            ),
            timeout,
        )
        text = sanitize_svg(raw)
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != pin:
            skipped += 1
            print(f"  SHA-256 mismatch for {name}.svg - skipped")
            continue
        size = svg_size(text) or (0, 0)
        _write(target, text.encode("utf-8"))
        manifest[key] = {
            "file": f"{key}.svg",
            "w": int(size[0]),
            "h": int(size[1]),
            "sha256": pin,
            "source": "mirror",
        }
        saved += 1
    # drop entries whose file is gone, keep the rest (game-sourced too)
    manifest = {
        k: v
        for k, v in manifest.items()
        if os.path.isfile(os.path.join(dest, str(v.get("file") or "")))
    }
    _write(os.path.join(dest, "manifest.json"), json.dumps(manifest, indent=1).encode("utf-8"))
    return saved, skipped


def _gate(dest: str) -> bool:
    """Launcher mode pre-checks: True when the download should proceed."""
    if os.environ.get("AIMAUDIT_NO_ICONS"):
        return False
    if present(dest):
        return False
    if fetchutil.recently_failed(dest):
        print("  Icons: the last download failed, not retrying yet.")
        print("  Retry any time with:  python tools\\fetch_icons.py")
        return False
    print("  Downloading the weapon icons for the UI (about 200 KB, once)...")
    return True


def _acquire(args, dest: str, auto: bool) -> int:
    """Download from the chosen source. On success the previous failure
    mark is cleared; launcher mode turns any failure into a friendly
    message + a mark (exit 0), manual runs report exit 1."""
    try:
        if args.source == "game":
            cs2 = find_cs2_dir(args.cs2_dir)
            if not cs2:
                raise RuntimeError("no Counter-Strike 2 installation found (use --cs2-dir)")
            n = fetch_game(
                dest, cs2, _game_wanted(set(WEAPON_SHA) | set(IMAGE_SHA)), timeout=TIMEOUT_FULL
            )
            print(f"  Icons from the game: {n} in {dest}")
        else:
            saved, skipped = download_mirror(dest, timeout=TIMEOUT_AUTO if auto else TIMEOUT_FULL)
            if args.source == "auto" and (skipped or saved < len(WEAPON_SHA) + len(IMAGE_SHA)):
                cs2 = find_cs2_dir(args.cs2_dir)
                if cs2 and _s2v_binary():
                    missing = (set(WEAPON_SHA) | set(IMAGE_SHA)) - set(_read_manifest(dest))
                    fetch_game(dest, cs2, _game_wanted(missing), timeout=TIMEOUT_FULL)
            n = len(_read_manifest(dest))
            print(f"  Icons ready: {n} in {dest} ({saved} downloaded, {skipped} skipped)")
    except Exception as e:  # noqa: BLE001 - one friendly message for any failure
        if not auto:
            print(f"download failed: {e}", file=sys.stderr)
            return 1
        fetchutil.mark_failed(dest, e)
        print(f"  Could not download the icons ({e}).")
        print("  The UI shows weapon names as text instead. Retry later with:")
        print("      python tools\\fetch_icons.py")
        return 0
    fetchutil.clear_failed(dest)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest", default=None, help="folder for the icons (default: data\\icons)")
    ap.add_argument(
        "--source",
        choices=("mirror", "game", "auto"),
        default="mirror",
        help="mirror: the pinned HUD commit (default); game: decompile from an "
        "installed CS2; auto: mirror first, game for what it could not deliver",
    )
    ap.add_argument("--cs2-dir", default=None, help="the CS2 folder (default: $AIMAUDIT_CS2_DIR or autodetect)")
    ap.add_argument(
        "--if-missing",
        action="store_true",
        help="launcher mode: do nothing when the icons are there, never fail",
    )
    args = ap.parse_args(argv)
    dest = args.dest or os.environ.get("AIMAUDIT_ICON_DIR") or store.icons_dir()
    auto = args.if_missing

    if auto and not _gate(dest):
        return 0
    code = _acquire(args, dest, auto)
    if code:
        return code
    if os.path.abspath(dest) != os.path.abspath(store.icons_dir()) and not os.environ.get("AIMAUDIT_ICON_DIR"):
        print(f"  set AIMAUDIT_ICON_DIR={dest} before starting the app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
