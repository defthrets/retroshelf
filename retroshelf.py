#!/usr/bin/env python3
"""RetroShelf — single-file retro game launcher.

Serves a local web GUI that auto-scans a games folder (recursively, any
structure), figures out which system each game belongs to, and launches it
through the matching emulator. Emulators can be auto-downloaded per system
from their official releases, or the UI links to the download page.

Folders (Settings tab):
    library_root    — your games, scanned recursively (e.g. M:\oldgames)
    emulators_root  — emulators live in <emulators_root>\<system>\
    art_root        — covers in <art_root>\<system>\, screenshots in
                      <art_root>\<system>\screens\, named like the rom file.
                      Art can also sit next to the rom (or in art/ covers/
                      screens/ screenshots/ subfolders beside it).

Run:  python retroshelf.py          (opens browser)
      python retroshelf.py --no-browser
"""

import concurrent.futures
import difflib
import functools
import json
import mimetypes
import os
import re
import shlex
import shutil
import zlib
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 7830
if getattr(sys, "frozen", False):          # running as a PyInstaller exe
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "retroshelf.json"

DEFAULT_ARGS = '"{emu}" "{rom}"'

# Each system: display name, exe names to look for, launch template, where the
# emulator comes from ("dl" = auto-download spec, "emu_url" = manual page).
SYSTEMS = [
    {"id": "nes", "name": "Nintendo NES",
     "exes": ["mesen.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Mesen", "emu_site": "mesen.ca",
     "emu_url": "https://github.com/SourMesen/Mesen2/releases",
     "dl": {"repo": "SourMesen/Mesen2", "asset": r"Windows\.zip$"},
     "note": "Windows build needs the .NET 8 Desktop Runtime installed."},
    {"id": "snes", "name": "Super Nintendo",
     "exes": ["snes9x-x64.exe", "snes9x.exe", "bsnes.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Snes9x", "emu_site": "snes9x.com",
     "emu_url": "https://github.com/snes9xgit/snes9x/releases",
     "dl": {"repo": "snes9xgit/snes9x", "asset": r"win32-x64\.zip$"}},
    {"id": "n64", "name": "Nintendo 64",
     "exes": ["simple64-gui.exe", "project64.exe"], "args": DEFAULT_ARGS,
     "emu_name": "simple64", "emu_site": "simple64.github.io",
     "emu_url": "https://github.com/simple64/simple64/releases",
     "dl": {"repo": "simple64/simple64", "asset": r"win64.*\.zip$"},
     "note": "simple64 is the maintained GUI bundle of the mupen64plus core."},
    {"id": "gb", "name": "Game Boy / Color",
     "exes": ["visualboyadvance-m.exe", "mgba.exe"], "args": DEFAULT_ARGS,
     "emu_name": "VisualBoyAdvance-M", "emu_site": "vba-m.com",
     "emu_url": "https://github.com/visualboyadvance-m/visualboyadvance-m/releases",
     "dl": {"repo": "visualboyadvance-m/visualboyadvance-m",
            "asset": r"^visualboyadvance-m-Win-x86_64\.zip$"}},
    {"id": "gba", "name": "Game Boy Advance",
     "exes": ["visualboyadvance-m.exe", "mgba.exe"], "args": DEFAULT_ARGS,
     "emu_name": "VisualBoyAdvance-M", "emu_site": "vba-m.com",
     "emu_url": "https://github.com/visualboyadvance-m/visualboyadvance-m/releases",
     "dl": {"repo": "visualboyadvance-m/visualboyadvance-m",
            "asset": r"^visualboyadvance-m-Win-x86_64\.zip$"}},
    {"id": "nds", "name": "Nintendo DS",
     "exes": ["melonds.exe"], "args": DEFAULT_ARGS,
     "emu_name": "melonDS", "emu_site": "melonds.kuribo64.net",
     "emu_url": "https://github.com/melonDS-emu/melonDS/releases",
     "dl": {"repo": "melonDS-emu/melonDS", "asset": r"windows-x86_64\.zip$"}},
    {"id": "gamecube", "name": "GameCube",
     "exes": ["dolphin.exe"], "args": '"{emu}" -e "{rom}"',
     "emu_name": "Dolphin", "emu_site": "dolphin-emu.org",
     "emu_url": "https://dolphin-emu.org/download/", "dl": None,
     "note": "Dolphin ships as a 7z archive — unzip it manually."},
    {"id": "wii", "name": "Nintendo Wii",
     "exes": ["dolphin.exe"], "args": '"{emu}" -e "{rom}"',
     "emu_name": "Dolphin", "emu_site": "dolphin-emu.org",
     "emu_url": "https://dolphin-emu.org/download/", "dl": None,
     "note": "Dolphin ships as a 7z archive — unzip it manually."},
    {"id": "genesis", "name": "Sega Mega Drive",
     "exes": ["ares.exe", "blastem.exe", "fusion.exe"], "args": DEFAULT_ARGS,
     "emu_name": "ares", "emu_site": "ares-emulator.github.io",
     "emu_url": "https://github.com/ares-emulator/ares/releases",
     "dl": {"repo": "ares-emulator/ares", "asset": r"^ares-windows-x64\.zip$"}},
    {"id": "dreamcast", "name": "Sega Dreamcast",
     "exes": ["flycast.exe", "redream.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Flycast", "emu_site": "flycast.dev",
     "emu_url": "https://github.com/flyinghead/flycast/releases",
     "dl": {"repo": "flyinghead/flycast", "asset": r"win64.*\.zip$"}},
    {"id": "ps1", "name": "PlayStation",
     "exes": ["duckstation-qt-x64-releaseltcg.exe", "duckstation-qt.exe",
              "duckstation.exe"], "args": DEFAULT_ARGS,
     "emu_name": "DuckStation", "emu_site": "duckstation.org",
     "emu_url": "https://github.com/stenzek/duckstation/releases",
     "dl": {"repo": "stenzek/duckstation",
            "asset": r"^duckstation-windows-x64-release\.zip$"},
     "note": "Zip/7z/rar game archives are unpacked automatically on first launch."},
    {"id": "ps2", "name": "PlayStation 2",
     "exes": ["pcsx2-qt.exe", "pcsx2-qtx64-avx2.exe", "pcsx2.exe"],
     "args": DEFAULT_ARGS,
     "emu_name": "PCSX2", "emu_site": "pcsx2.net",
     "emu_url": "https://pcsx2.net/downloads", "dl": None,
     "note": "PCSX2 ships as a 7z/installer — install manually. Needs a PS2 BIOS."},
    {"id": "psp", "name": "PlayStation Portable",
     "exes": ["ppssppwindows64.exe", "ppssppwindows.exe"], "args": DEFAULT_ARGS,
     "emu_name": "PPSSPP", "emu_site": "ppsspp.org",
     "emu_url": "https://github.com/hrydgard/ppsspp/releases",
     "dl": {"repo": "hrydgard/ppsspp", "asset": r"Windows-x64\.zip$"}},
    {"id": "arcade", "name": "Arcade (MAME)",
     "exes": ["mame.exe"], "args": '"{emu}" {romname} -rompath "{romdir}"',
     "emu_name": "MAME", "emu_site": "mamedev.org",
     "emu_url": "https://www.mamedev.org/release.html", "dl": None,
     "note": "MAME ships as a self-extracting exe — run it into the folder manually."},
    {"id": "atari2600", "name": "Atari 2600",
     "exes": ["stella.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Stella", "emu_site": "stella-emu.github.io",
     "emu_url": "https://github.com/stella-emu/stella/releases",
     "dl": {"repo": "stella-emu/stella", "asset": r"windows\.zip$"}},
    {"id": "c64", "name": "Commodore 64",
     "exes": ["x64sc.exe", "x64.exe"], "args": DEFAULT_ARGS,
     "emu_name": "VICE", "emu_site": "vice-emu.sourceforge.io",
     "emu_url": "https://vice-emu.sourceforge.io/index.html#download",
     "dl": {"url": "https://sourceforge.net/projects/vice-emu/files/releases/"
                   "binaries/windows/GTK3VICE-3.9-win64.zip/download"}},
    {"id": "3do", "name": "Panasonic 3DO",
     "exes": ["retroarch.exe"],
     "args": '"{emu}" -L "{emudir}\\cores\\opera_libretro.dll" "{rom}" -f',
     "emu_name": "RetroArch + Opera core", "emu_site": "retroarch.com",
     "emu_url": "https://www.retroarch.com/?page=platforms",
     "dl": {"parts": [
         {"resolve": "retroarch"},
         {"url": "https://buildbot.libretro.com/nightly/windows/x86_64/latest/"
                 "opera_libretro.dll.zip", "sub": "cores"}]},
     "note": "Needs a 3DO BIOS (panafz10.bin is the usual one). Games in "
             "zip/7z/rar are unpacked automatically on first launch."},
    {"id": "amiga", "name": "Commodore Amiga",
     "exes": ["winuae64.exe", "winuae.exe"], "args": '"{emu}" -0 "{rom}" -G',
     "emu_name": "WinUAE", "emu_site": "winuae.net",
     "emu_url": "https://www.winuae.net/download/",
     "dl": {"url": "https://download.abime.net/winuae/releases/WinUAE6010_x64.zip"},
     "note": "Auto-boots WHDLoad games: the archive is extracted and booted "
             "through WHDLoad automatically (needs 7-Zip installed). "
             "You must supply Kickstart ROMs: drop the .rom files into "
             "emulators\\amiga\\kickstarts\\ (plain dumps; Cloanto-encrypted "
             "ROMs are not supported). Include Kickstart 3.1 A1200 + 1.3 "
             "for best coverage. Launch args are managed automatically."},
]

# Emulator commands to look for on Linux/macOS, in preference order. Anything
# on PATH is used as-is, so a homelab box just needs the packages installed.
POSIX_BINS = {
    "nes": ["mesen", "fceux", "nestopia"],
    "snes": ["snes9x-gtk", "snes9x", "bsnes", "zsnes"],
    "n64": ["simple64-gui", "mupen64plus-gui", "mupen64plus"],
    "gb": ["mgba-qt", "mgba", "visualboyadvance-m", "sameboy"],
    "gba": ["mgba-qt", "mgba", "visualboyadvance-m"],
    "nds": ["melonDS", "melonds", "desmume"],
    "gamecube": ["dolphin-emu", "dolphin-emu-nogui"],
    "wii": ["dolphin-emu", "dolphin-emu-nogui"],
    "genesis": ["blastem", "ares", "mednafen"],
    "dreamcast": ["flycast", "reicast"],
    "ps1": ["duckstation-qt", "duckstation-nogui", "duckstation", "mednafen"],
    "ps2": ["pcsx2-qt", "PCSX2", "pcsx2"],
    "psp": ["PPSSPPSDL", "PPSSPPQt", "ppsspp"],
    "arcade": ["mame", "mame64"],
    "atari2600": ["stella", "mednafen"],
    "c64": ["x64sc", "x64", "vice"],
    "amiga": ["fs-uae", "amiberry"],
    "3do": ["retroarch"],
}
# Debian dropped most emulator packages, but RetroArch runs nearly everything
# through libretro cores, so that is the fallback when nothing standalone is
# installed. Cores are fetched from the libretro buildbot on request.
LIBRETRO_CORES = {
    "nes": "mesen", "snes": "snes9x", "n64": "mupen64plus_next",
    "gb": "gambatte", "gba": "mgba", "nds": "melonds",
    "genesis": "genesis_plus_gx", "dreamcast": "flycast",
    "ps1": "swanstation", "psp": "ppsspp", "arcade": "fbneo",
    "atari2600": "stella2014", "c64": "vice_x64", "amiga": "puae",
    "3do": "opera",
}
CORE_BASE = "https://buildbot.libretro.com/nightly/linux/x86_64/latest/"


def core_dirs():
    home = Path.home()
    return [home / ".config/retroarch/cores",
            home / ".var/app/org.libretro.RetroArch/config/retroarch/cores",
            Path("/usr/lib/x86_64-linux-gnu/libretro"),
            Path("/usr/lib/libretro"),
            Path("/usr/local/lib/libretro")]


def find_core(sysid):
    """Path to the installed libretro core for a system, if there is one."""
    name = LIBRETRO_CORES.get(sysid)
    if not name:
        return None
    fname = name + "_libretro.so"
    for d in core_dirs():
        p = d / fname
        if p.is_file():
            return p
    return None


def install_core(sysid):
    """Fetch a libretro core from the buildbot into RetroArch's core folder."""
    name = LIBRETRO_CORES.get(sysid)
    if not name:
        raise RuntimeError("no libretro core is defined for " + sysid)
    dest = core_dirs()[0]
    dest.mkdir(parents=True, exist_ok=True)
    url = CORE_BASE + name + "_libretro.so.zip"
    tmp = dest / (name + ".zip")
    req = urllib.request.Request(url, headers={"User-Agent": "RetroShelf"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    with zipfile.ZipFile(tmp) as z:
        for m in z.infolist():
            if m.filename.endswith(".so"):
                z.extract(m, dest)
    tmp.unlink(missing_ok=True)
    return find_core(sysid)

SYSTEMS_BY_ID = {s["id"]: s for s in SYSTEMS}
ART_EXTS = [".png", ".jpg", ".jpeg", ".webp"]

# --- how files are matched to systems -------------------------------------
UNIQUE_EXTS = {
    ".nes": "nes", ".fds": "nes", ".sfc": "snes", ".smc": "snes",
    ".z64": "n64", ".n64": "n64", ".v64": "n64",
    ".gb": "gb", ".gbc": "gb", ".gba": "gba", ".nds": "nds",
    ".gcm": "gamecube", ".rvz": "gamecube", ".ciso": "gamecube",
    ".wbfs": "wii", ".md": "genesis", ".gen": "genesis", ".smd": "genesis",
    ".gdi": "dreamcast", ".cdi": "dreamcast",
    ".pbp": "ps1", ".m3u": "ps1", ".cso": "psp", ".a26": "atari2600",
    ".d64": "c64", ".t64": "c64", ".prg": "c64", ".tap": "c64", ".crt": "c64",
    ".adf": "amiga", ".lha": "amiga", ".ipf": "amiga",
}
AMBIG_EXTS = {
    ".zip": ["arcade", "nes", "snes", "n64", "gb", "gba", "genesis", "atari2600",
             "ps1", "amiga", "c64", "3do"],
    ".7z": ["arcade", "ps1", "amiga", "3do"],
    ".iso": ["ps2", "psp", "ps1", "gamecube", "wii", "3do"],
    ".bin": ["genesis", "atari2600", "ps1", "c64", "3do"],
    ".chd": ["ps1", "ps2", "dreamcast", "psp", "3do"],
    ".cue": ["ps1", "dreamcast", "3do"],
    ".img": ["ps1"],
    ".ecm": ["ps1"],
    ".mds": ["ps1"],
    ".rar": ["amiga", "ps1", "3do"],
}
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}
# systems whose games are disc images; archives are unpacked before launching
DISC_SYSTEMS = {"ps1", "ps2", "psp", "dreamcast", "gamecube", "wii", "3do"}
DISC_PLAYABLE = [".cue", ".chd", ".m3u", ".pbp", ".iso", ".img", ".bin", ".gdi",
                 ".cdi", ".cso", ".mds"]
# Folder-name hints (checked deepest folder first; more specific systems first)
HINTS = {
    "gba": ["gba", "game boy advance", "gameboy advance"],
    "gb": ["gb", "gbc", "game boy", "gameboy"],
    "n64": ["n64", "nintendo 64", "nintendo64"],
    "nes": ["nes", "famicom"],
    "snes": ["snes", "super nintendo", "super famicom"],
    "nds": ["nds", "nintendo ds"],
    "gamecube": ["gamecube", "ngc"],
    "wii": ["wii"],
    "genesis": ["genesis", "mega drive", "megadrive"],
    "dreamcast": ["dreamcast"],
    "ps2": ["ps2", "playstation 2"],
    "ps1": ["ps1", "psx", "psone", "playstation"],
    "psp": ["psp"],
    "arcade": ["arcade", "mame", "neogeo", "fba"],
    "atari2600": ["atari", "2600"],
    "c64": ["c64", "commodore 64", "commodore64"],
    "amiga": ["amiga", "whdload"],
    "3do": ["3do", "panasonic 3do", "tresdo"],
}
SKIP_DIRS = {"art", "covers", "screens", "screenshots", "emulators", "boxart",
             "boxarts", "named_boxarts", "named_snaps", "named_titles",
             "media", "thumbnails"}
# folders that hold firmware/BIOS dumps rather than games
SKIP_WORDS = ("firmware", "bios", "kickstart")


def skip_dir(name):
    n = name.lower()
    return n in SKIP_DIRS or any(w in n for w in SKIP_WORDS)

_lock = threading.Lock()


def exts_for(sysid):
    e = [x for x, s in UNIQUE_EXTS.items() if s == sysid]
    e += [x for x, c in AMBIG_EXTS.items() if sysid in c]
    return sorted(set(e))


def default_config():
    """Defaults follow the platform: C:\\RetroShelf on Windows, and on
    Linux/macOS /srv/retroshelf if it exists (a typical server layout),
    otherwise ~/RetroShelf."""
    if os.name == "nt":
        root = Path("C:/RetroShelf")
        games = root / "games"
    elif Path("/srv/retroshelf").is_dir() or Path("/srv/games").is_dir():
        root = Path("/srv/retroshelf")
        games = Path("/srv/games")
    else:
        root = Path.home() / "RetroShelf"
        games = root / "games"
    return {
        "library_root": str(games),
        "emulators_root": str(root / "emulators"),
        "art_root": str(root / "art"),
        "covers_dir": "",
        "overrides": {}, "stats": {},
    }


_cfg_cache = {"mtime": None, "data": None}


def load_config():
    """Parsed config, reused until the file changes on disk. It is read on
    every art request, and re-parsing hundreds of KB each time is slow."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _cfg_cache["data"] is not None and _cfg_cache["mtime"] == mtime:
        return _cfg_cache["data"]
    if mtime is not None:
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = default_config()
    else:
        cfg = default_config()
    for key, val in default_config().items():
        cfg.setdefault(key, val)
    _cfg_cache.update(mtime=mtime, data=cfg)
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        _cfg_cache.update(mtime=CONFIG_PATH.stat().st_mtime_ns, data=cfg)
    except OSError:
        _cfg_cache.update(mtime=None, data=cfg)


def _find_emulator_posix(sysdef, cfg):
    """On Linux/macOS an emulator is normally a package on PATH; also accept a
    binary or AppImage dropped into the emulators folder."""
    sysid = sysdef["id"]
    emu_dir = Path(cfg["emulators_root"]) / sysid
    names = POSIX_BINS.get(sysid, [])
    if emu_dir.is_dir():
        for p in sorted(emu_dir.rglob("*")):
            if not p.is_file() or not os.access(p, os.X_OK):
                continue
            low = p.name.lower()
            if low in [n.lower() for n in names] or low.endswith(".appimage"):
                return p
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    # nothing standalone: RetroArch can run it if the core is installed
    ra = shutil.which("retroarch")
    if ra and find_core(sysid):
        return Path(ra)
    return None


_emu_cache = {}


def find_emulator(sysdef, cfg):
    """Return Path to the emulator exe, or None. Cached - scanning emulator
    folders (RetroArch alone has thousands of files) on every request is slow."""
    override = cfg["overrides"].get(sysdef["id"], {}).get("emu_path", "")
    key = (sysdef["id"], cfg["emulators_root"], override)
    hit = _emu_cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    found = _find_emulator_uncached(sysdef, cfg, override)
    _emu_cache[key] = (time.time(), found)
    return found


def _find_emulator_uncached(sysdef, cfg, override):
    if override:
        p = Path(override)
        return p if p.is_file() else None
    if os.name != "nt":
        return _find_emulator_posix(sysdef, cfg)
    emu_dir = Path(cfg["emulators_root"]) / sysdef["id"]
    if not emu_dir.is_dir():
        return None
    wanted = [n.lower() for n in sysdef["exes"]]
    exes = list(emu_dir.rglob("*.exe"))
    for p in exes:
        if p.name.lower() in wanted:
            return p
    if len(exes) == 1:  # a lone exe in the folder is almost certainly it
        return exes[0]
    return None


# --- scanning ---------------------------------------------------------------

TAG_RE = re.compile(r"[\(\[][^\)\]]*[\)\]]")

# region and language markers as they appear in rom filenames
REGION_TAGS = [
    ("world", "World"), ("usa", "USA"), ("(u)", "USA"), ("[u]", "USA"),
    ("(us", "USA"), ("europe", "Europe"), ("(e)", "Europe"), ("[e]", "Europe"),
    ("(eu", "Europe"), ("japan", "Japan"), ("(j)", "Japan"), ("[j]", "Japan"),
    ("korea", "Korea"), ("china", "China"), ("taiwan", "Taiwan"),
    ("germany", "Germany"), ("france", "France"), ("italy", "Italy"),
    ("spain", "Spain"), ("netherlands", "Netherlands"), ("sweden", "Sweden"),
    ("norway", "Norway"), ("denmark", "Denmark"), ("finland", "Finland"),
    ("australia", "Australia"), ("canada", "Canada"), ("brazil", "Brazil"),
    ("russia", "Russia"), ("poland", "Poland"), ("asia", "Asia"),
]
LANG_CODES = {"En": "English", "Fr": "French", "De": "German", "Es": "Spanish",
              "It": "Italian", "Ja": "Japanese", "Nl": "Dutch", "Pt": "Portuguese",
              "Sv": "Swedish", "No": "Norwegian", "Da": "Danish", "Fi": "Finnish",
              "Zh": "Chinese", "Ko": "Korean", "Ru": "Russian", "Pl": "Polish",
              "Cs": "Czech", "Hu": "Hungarian", "El": "Greek", "Tr": "Turkish"}
LANG_RE = re.compile(r"[\(\[]((?:[A-Z][a-z])(?:\s*,\s*[A-Z][a-z])*)[\)\]]")


def parse_region(name):
    low = name.lower()
    for tag, label in REGION_TAGS:
        if tag in low:
            return label
    return ""


def parse_langs(name):
    out = []
    for m in LANG_RE.finditer(name):
        for code in (c.strip() for c in m.group(1).split(",")):
            if code in LANG_CODES and code not in out:
                out.append(code)
    return out


def clean_name(stem):
    n = TAG_RE.sub("", stem)
    n = re.sub(r"_v\d[\w.]*.*$", "", n)          # Amiga _v1.2_AGA_0044 suffixes
    n = re.sub(r"_(s)(?=\s|$|_)", r"'\1", n)     # Bug_s -> Bug's
    n = n.replace("_", " ")
    n = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", n)  # AlienBreed2 -> Alien Breed 2
    n = re.sub(r"\s+", " ", n).strip(" -.")
    return n or stem


def _tok_match(tok, part):
    i = part.find(tok)
    while i != -1:
        pre_ok = i == 0 or not part[i - 1].isalnum()
        j = i + len(tok)
        post_ok = (j >= len(part) or not part[j].isalnum()
                   or (tok[-1].isdigit() and part[j].isalpha()))
        if pre_ok and post_ok:
            return True
        i = part.find(tok, i + 1)
    return False


def path_hints(parts):
    """System ids hinted by folder names, deepest folder first."""
    out = []
    for part in reversed([p.lower() for p in parts]):
        for sysid, toks in HINTS.items():
            if sysid not in out and any(_tok_match(t, part) for t in toks):
                out.append(sysid)
    return out


_zip_cache = {}


def zip_peek(path):
    """Classify a .zip by what's inside it."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    if key in _zip_cache:
        return _zip_cache[key]
    res = None
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist()[:40]:
                e = Path(n).suffix.lower()
                if e in UNIQUE_EXTS:
                    res = UNIQUE_EXTS[e]
                    break
    except (OSError, zipfile.BadZipFile):
        res = None
    _zip_cache[key] = res
    return res


def classify(parts, ext, path):
    if ext in UNIQUE_EXTS:
        return UNIQUE_EXTS[ext]
    cands = AMBIG_EXTS.get(ext)
    if not cands:
        return None
    hints = path_hints(parts)
    for h in hints:
        if h in cands:
            return h
    # an archive can hold anything, so trust the folder the user filed it under
    if ext in ARCHIVE_EXTS and hints:
        return hints[0]
    if ext == ".zip":
        peeked = zip_peek(path)
        if peeked:
            return peeked
    return cands[0]


def scan_all(cfg):
    """Walk library_root recursively, classify every game file."""
    root = Path(cfg["library_root"])
    games = {s["id"]: [] for s in SYSTEMS}
    if not root.is_dir():
        return games

    listing_cache = {}

    def listdir(d):
        d = str(d)
        if d not in listing_cache:
            try:
                listing_cache[d] = {n.lower() for n in os.listdir(d)}
            except OSError:
                listing_cache[d] = set()
        return listing_cache[d]

    def has_art(sysid, dirpath, stem, kind):
        if kind == "screen":
            dirs = [dirpath / "screens", dirpath / "screenshots",
                    Path(cfg["art_root"]) / sysid / "screens"]
        else:
            dirs = [dirpath, dirpath / "art", dirpath / "covers",
                    Path(cfg["art_root"]) / sysid]
        sl = stem.lower()
        for d in dirs:
            names = listdir(d)
            for ext in ART_EXTS:
                if sl + ext in names:
                    return True
        return False

    def walk(top):
        """os.walk, but yielding DirEntry objects - their stat data is already
        cached by the OS, so file sizes cost nothing extra."""
        stack = [top]
        while stack:
            here = stack.pop()
            try:
                with os.scandir(here) as it:
                    entries = list(it)
            except OSError:
                continue
            files = []
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not skip_dir(e.name):
                            stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        files.append(e)
                except OSError:
                    continue
            yield here, files

    for dirpath, entries in walk(str(root)):
        filenames = [e.name for e in entries]
        sizes = {}
        for e in entries:
            try:
                sizes[e.name] = e.stat().st_size
            except OSError:
                sizes[e.name] = 0
        dp = Path(dirpath)
        try:
            rel_parts = dp.relative_to(root).parts
        except ValueError:
            rel_parts = ()
        cue_stems = {Path(f).stem.lower() for f in filenames
                     if Path(f).suffix.lower() in (".cue", ".gdi", ".m3u")}
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            stem = Path(fname).stem
            if ext in (".bin", ".img", ".iso") and stem.lower() in cue_stems:
                continue
            p = dp / fname
            sysid = classify(rel_parts, ext, p)
            if not sysid:
                continue
            games[sysid].append({
                "name": clean_name(stem),
                "file": str(p),
                "size": sizes.get(fname, 0),
                "region": parse_region(fname),
                "langs": parse_langs(fname),
                "art": has_art(sysid, dp, stem, "cover"),
                "shot": has_art(sysid, dp, stem, "screen"),
            })
    for lst in games.values():
        lst.sort(key=lambda g: g["name"].lower())
    return games


_scan_cache = {"key": None, "time": 0.0, "data": None, "version": 0}
SCAN_TTL = 300


def get_games(cfg, force=False):
    key = (cfg["library_root"], cfg["art_root"])
    now = time.time()
    if (not force and _scan_cache["key"] == key
            and now - _scan_cache["time"] < SCAN_TTL):
        return _scan_cache["data"]
    data = scan_all(cfg)
    _scan_cache.update(key=key, time=now, data=data)
    return data


_scan_busy = threading.Event()


def request_rescan(cfg):
    """Rescan in the background so the UI never waits on a slow drive."""
    if _scan_busy.is_set():
        return
    def work():
        _scan_busy.set()
        try:
            get_games(cfg, force=True)
            _scan_cache["version"] += 1
            # note: the art cache is deliberately kept - art does not move
            # because the game list changed, and rebuilding it is expensive
        finally:
            _scan_busy.clear()
    threading.Thread(target=work, daemon=True).start()


def library_signature(root):
    """Cheap fingerprint of the library: every folder's mtime and entry count.
    Adding or removing a file bumps its folder's mtime, so this catches new
    games without walking every file."""
    sig = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not skip_dir(d)]
            try:
                sig.append((dirpath, int(os.stat(dirpath).st_mtime),
                            len(filenames)))
            except OSError:
                continue
    except OSError:
        return None
    return hash(tuple(sig))


def _watch_worker():
    """Notice new games dropped into the library and refresh the scan."""
    last = None
    while True:
        time.sleep(20)
        try:
            cfg = load_config()
            sig = library_signature(cfg["library_root"])
            if sig is None:
                continue
            if last is not None and sig != last:
                _scan_cache["time"] = 0            # force a rescan next request
                games = get_games(cfg, force=True)
                _scan_cache["version"] += 1
                # newly added games usually have no art yet - go and get it
                missing = any(not g["art"] or not g["shot"]
                              for sysid, lst in games.items()
                              if sysid in LIBRETRO_SYS for g in lst)
                if missing and SHOTS.get("status") not in ("indexing", "fetching"):
                    time.sleep(10)      # let a bulk copy finish first
                    if library_signature(cfg["library_root"]) == sig:
                        start_shots(cfg)
            last = sig
        except Exception:
            continue


_art_cache = {}


def find_art(sysdef, rom_path, cfg, kind="cover"):
    key = (sysdef["id"], str(rom_path), kind)
    hit = _art_cache.get(key, False)
    if hit is not False:
        return hit
    p = _find_art_uncached(sysdef, rom_path, cfg, kind)
    if len(_art_cache) > 20000:
        _art_cache.clear()
    _art_cache[key] = p
    return p


def _find_art_uncached(sysdef, rom_path, cfg, kind="cover"):
    stem = rom_path.stem
    # the central art folder is checked first: it holds almost everything and
    # lives on a local disk, while the game folders can be on a slow drive
    if kind == "screen":
        dirs = [Path(cfg["art_root"]) / sysdef["id"] / "screens",
                rom_path.parent / "screens", rom_path.parent / "screenshots"]
    else:
        dirs = [Path(cfg["art_root"]) / sysdef["id"], rom_path.parent,
                rom_path.parent / "art", rom_path.parent / "covers"]
    for d in dirs:
        for ext in ART_EXTS:
            c = d / (stem + ext)
            if c.is_file():
                return c
    return None


# --- emulator auto-download -------------------------------------------------

DOWNLOADS = {}
_dl_lock = threading.Lock()
DL_ACTIVE = ("resolving", "downloading", "extracting")


def _set_dl(sysid, **kw):
    with _dl_lock:
        DOWNLOADS.setdefault(sysid, {}).update(kw)


def _latest_retroarch():
    req = urllib.request.Request("https://buildbot.libretro.com/stable/",
                                 headers={"User-Agent": "RetroShelf"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    vers = set(re.findall(r"/stable/(\d+(?:\.\d+)+)/", html))
    if not vers:
        raise RuntimeError("could not find a RetroArch release")
    best = max(vers, key=lambda v: tuple(int(x) for x in v.split(".")))
    return ("https://buildbot.libretro.com/stable/" + best
            + "/windows/x86_64/RetroArch.7z")


def _resolve_dl_url(spec):
    if spec.get("resolve") == "retroarch":
        return _latest_retroarch()
    if "url" in spec:
        return spec["url"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{spec['repo']}/releases/latest",
        headers={"User-Agent": "RetroShelf", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.load(r)
    pat = re.compile(spec["asset"], re.I)
    for a in rel.get("assets", []):
        if pat.search(a["name"]):
            return a["browser_download_url"]
    raise RuntimeError("no matching release asset found")


def _fetch_and_extract(sysid, spec, dest):
    url = _resolve_dl_url(spec)
    dest.mkdir(parents=True, exist_ok=True)
    suffix = ".7z" if url.lower().endswith(".7z") else ".zip"
    tmp = dest / ("_retroshelf_download" + suffix)
    req = urllib.request.Request(url, headers={"User-Agent": "RetroShelf"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        _set_dl(sysid, status="downloading")
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                _set_dl(sysid, pct=int(got * 100 / total))
    _set_dl(sysid, status="extracting", pct=100)
    if suffix == ".zip":
        droot = dest.resolve()
        with zipfile.ZipFile(tmp) as z:
            for m in z.infolist():
                tgt = (dest / m.filename).resolve()
                if tgt == droot or droot in tgt.parents:
                    z.extract(m, dest)
    else:
        extract_archive(tmp, dest)
    tmp.unlink(missing_ok=True)


def _download_worker(sysdef, emulators_root):
    sysid = sysdef["id"]
    if os.name != "nt":
        # on Linux the download is the libretro core for RetroArch
        try:
            _set_dl(sysid, status="downloading", pct=0, msg="")
            if not shutil.which("retroarch"):
                raise RuntimeError("retroarch is not installed")
            core = install_core(sysid)
            _emu_cache.clear()
            if not core:
                raise RuntimeError("core did not install")
            _set_dl(sysid, status="done", pct=100, msg=str(core))
        except Exception as e:
            _set_dl(sysid, status="error", msg=str(e))
        return
    try:
        _set_dl(sysid, status="resolving", pct=0, msg="")
        dest = Path(emulators_root) / sysid
        spec = sysdef["dl"]
        parts = spec.get("parts") or [spec]
        for part in parts:
            target = dest
            sub = part.get("sub")
            if sub:
                # place extras (e.g. cores) beside the emulator executable
                base = dest
                wanted = [n.lower() for n in sysdef["exes"]]
                for p in dest.rglob("*.exe"):
                    if p.name.lower() in wanted:
                        base = p.parent
                        break
                target = base / sub
            _fetch_and_extract(sysid, part, target)
        _emu_cache.clear()
        _set_dl(sysid, status="done", msg="installed")
    except Exception as e:  # report any failure to the UI
        _set_dl(sysid, status="error", msg=str(e))


def start_download(sysid, cfg):
    sysdef = SYSTEMS_BY_ID.get(sysid)
    if not sysdef:
        return False, "unknown system"
    if os.name != "nt":
        if sysid not in LIBRETRO_CORES:
            return False, "no libretro core is available for this system"
    elif not sysdef.get("dl"):
        return False, "no auto-download for this system"
    if DOWNLOADS.get(sysid, {}).get("status") in DL_ACTIVE:
        return True, "already downloading"
    threading.Thread(target=_download_worker,
                     args=(sysdef, cfg["emulators_root"]), daemon=True).start()
    return True, "download started"


# --- cover matching ---------------------------------------------------------

COVERS = {}
_cover_lock = threading.Lock()

_norm_re = re.compile(r"[^a-z0-9]+")


def _set_cov(**kw):
    with _cover_lock:
        COVERS.update(kw)


_ROMAN = [("viii", "8"), ("vii", "7"), ("iii", "3"), ("vi", "6"),
          ("iv", "4"), ("ix", "9"), ("ii", "2")]


@functools.lru_cache(maxsize=40000)
def norm_title(s):
    """Normalise a title for matching: drop tags/punctuation, split camelcase."""
    s = TAG_RE.sub("", s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", s)
    s = s.lower()
    s = re.sub(r",\s*(the|a|an)\b", "", s)      # "Zelda, The" -> "Zelda"
    s = _norm_re.sub(" ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    for rom, num in _ROMAN:   # Alien Breed II <-> Alien Breed 2
        s = re.sub(r"\b" + rom + r"\b", num, s)
    return s


def _cover_worker(covers_dir, cfg):
    try:
        _set_cov(status="indexing", done=0, total=0, matched=0, copied=0, msg="")
        root = Path(covers_dir)
        if not root.is_dir():
            _set_cov(status="error", msg="covers folder not found: " + str(root))
            return
        # index cover images, bucketed by system hint in the folder path
        index = {}
        for dirpath, _dirs, filenames in os.walk(root):
            try:
                rel = Path(dirpath).relative_to(root).parts
            except ValueError:
                rel = ()
            hints = path_hints(rel)
            bucket = hints[0] if hints else "*"
            d = index.setdefault(bucket, {})
            for f in filenames:
                if Path(f).suffix.lower() not in ART_EXTS:
                    continue
                key = norm_title(Path(f).stem)
                if key and key not in d:
                    d[key] = Path(dirpath) / f
        prepared = {}
        for bk, d in index.items():
            nospace, byletter = {}, {}
            for k, v in d.items():
                nospace.setdefault(k.replace(" ", ""), v)
                byletter.setdefault(k[:1], []).append(k)
            prepared[bk] = (d, nospace, byletter)

        games = get_games(cfg)
        todo = [(sysid, g) for sysid, lst in games.items() for g in lst]
        _set_cov(status="matching", total=len(todo))
        matched = copied = 0
        for i, (sysid, g) in enumerate(todo):
            if i % 50 == 0:
                _set_cov(done=i, matched=matched, copied=copied)
            key = norm_title(g["name"])
            if not key:
                continue
            src = None
            for bk in (sysid, "*"):
                if bk not in prepared:
                    continue
                exact, nospace, byletter = prepared[bk]
                src = exact.get(key) or nospace.get(key.replace(" ", ""))
                if not src:
                    close = difflib.get_close_matches(
                        key, byletter.get(key[:1], []), n=1, cutoff=0.87)
                    if close:
                        src = exact[close[0]]
                if src:
                    break
            if not src:
                continue
            matched += 1
            dest_dir = Path(cfg["art_root"]) / sysid
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / (Path(g["file"]).stem + src.suffix.lower())
            if not target.exists():
                shutil.copy2(src, target)
                copied += 1
        _scan_cache["time"] = 0          # art changed — force fresh scan
        _set_cov(status="done", done=len(todo), matched=matched, copied=copied)
    except Exception as e:
        _set_cov(status="error", msg=str(e))


def start_cover_match(cfg, covers_dir):
    if COVERS.get("status") in ("indexing", "matching"):
        return True, "already matching"
    if not covers_dir:
        return False, "no covers folder set"
    threading.Thread(target=_cover_worker, args=(covers_dir, cfg),
                     daemon=True).start()
    return True, "matching started"


# --- online art fetcher (libretro thumbnails) --------------------------------

LIBRETRO_BASE = "https://thumbnails.libretro.com/"
LIBRETRO_SYS = {
    "nes": "Nintendo - Nintendo Entertainment System",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "n64": "Nintendo - Nintendo 64",
    "gb": "Nintendo - Game Boy",
    "gba": "Nintendo - Game Boy Advance",
    "nds": "Nintendo - Nintendo DS",
    "gamecube": "Nintendo - GameCube",
    "wii": "Nintendo - Wii",
    "genesis": "Sega - Mega Drive - Genesis",
    "dreamcast": "Sega - Dreamcast",
    "ps1": "Sony - PlayStation",
    "ps2": "Sony - PlayStation 2",
    "psp": "Sony - PlayStation Portable",
    "arcade": "MAME",
    "atari2600": "Atari - 2600",
    "c64": "Commodore - 64",
    "amiga": "Commodore - Amiga",
    "3do": "The 3DO Company - 3DO",
}

SHOTS = {}
_shots_lock = threading.Lock()


def _set_shots(**kw):
    with _shots_lock:
        SHOTS.update(kw)


def _libretro_index(sysdir, kind):
    """Fetch the thumbnail directory listing, keyed by normalised title."""
    url = LIBRETRO_BASE + urllib.parse.quote(sysdir) + "/" + kind + "/"
    req = urllib.request.Request(url, headers={"User-Agent": "RetroShelf"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    exact, nospace, byletter, coll, abbrev = {}, {}, {}, {}, {}
    for href in re.findall(r'href="([^"]+\.png)"', html):
        fname = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        key = norm_title(fname[:-4])
        if not key:
            continue
        if key not in exact:
            exact[key] = fname
            nospace.setdefault(key.replace(" ", ""), fname)
            ck = collapse_initials(key)
            coll.setdefault(ck, fname)
            if ck != key:            # the title is an initialism, e.g. O.D.T.
                abbrev.setdefault(ck, fname)
            byletter.setdefault(key[:1], []).append(key)
    toksets = [(k, frozenset(collapse_initials(k).split())) for k in exact]
    return exact, nospace, byletter, coll, abbrev, toksets


def loose_key(key, keys):
    """Last-resort title match for subtitle or publisher-prefix differences,
    e.g. "SnowJob" -> "SnowJob Starring Tracy Scoggins", or
    "Over Drivin'" -> "Road & Track Presents - OverDrivin'".
    Short titles are skipped so things like "D" cannot match everything."""
    if len(key) < 6:
        return None
    # prefix match, but only on a whole-word boundary ("snowjob starring ...")
    cands = [k for k in keys
             if k.startswith(key + " ") or (len(k) >= 6 and key.startswith(k + " "))]
    ks = key.replace(" ", "")
    if not cands and len(ks) >= 7:
        cands = [k for k in keys if len(k) >= 7 and ks in k.replace(" ", "")]
    return min(cands, key=len) if cands else None


def subset_match(key, toksets):
    """Match titles that differ by word order or by extra words, e.g. a rom
    called "Madden 2000" against "Madden NFL 2000", or "N64 1080 Snowboarding"
    against "1080 Snowboarding". One side's words must all appear in the
    other's, and the overlap has to be substantial enough to be meaningful."""
    ours = frozenset(collapse_initials(key).split())
    if not ours:
        return None
    best, best_gap = None, None
    for k, theirs in toksets:
        if not (ours <= theirs or theirs <= ours):
            continue
        shared = ours & theirs
        smaller = ours if len(ours) <= len(theirs) else theirs
        # two words in common, or one distinctive long word
        if not (len(shared) >= 2
                or (len(smaller) == 1 and len(next(iter(smaller))) >= 6)):
            continue
        gap = abs(len(theirs) - len(ours))
        if gap > 3:                      # too much extra text to trust
            continue
        if best is None or gap < best_gap or (gap == best_gap and len(k) < len(best)):
            best, best_gap = k, gap
    return best


def _libretro_match(key, index):
    exact, nospace, byletter, coll, abbrev, toksets = index
    ck = collapse_initials(key)
    hit = (exact.get(key) or nospace.get(key.replace(" ", "")) or coll.get(ck))
    if not hit:
        # "O.D.T." on their side vs "ODT Escape Or Die Trying" on ours
        first = ck.split(" ")[0]
        if len(first) >= 3:
            hit = abbrev.get(first)
    if not hit:
        close = difflib.get_close_matches(key, byletter.get(key[:1], []),
                                          n=1, cutoff=0.87)
        if close:
            hit = exact[close[0]]
    if not hit:
        alt = loose_key(key, exact.keys())
        if alt:
            hit = exact[alt]
    if not hit:
        alt = subset_match(key, toksets)
        if alt:
            hit = exact[alt]
    return hit


def _shots_worker(cfg):
    try:
        _set_shots(status="indexing", done=0, total=0, found=0, msg="", sys="")
        games = get_games(cfg)
        jobs = []      # (sysid, kind, destsub, [games])
        for sysid, lst in games.items():
            if sysid not in LIBRETRO_SYS or not lst:
                continue
            mshots = [g for g in lst if not g["shot"]]
            mcovers = [g for g in lst if not g["art"]]
            if mshots:
                jobs.append((sysid, "Named_Snaps", "screens", mshots))
            if mcovers:
                jobs.append((sysid, "Named_Boxarts", "", mcovers))
        total = sum(len(j[3]) for j in jobs)
        _set_shots(status="fetching", total=total)
        done = found = 0
        for sysid, kind, destsub, lst in jobs:
            sysdir = LIBRETRO_SYS[sysid]
            _set_shots(sys=SYSTEMS_BY_ID[sysid]["name"], done=done, found=found)
            try:
                index = _libretro_index(sysdir, kind)
            except Exception:
                done += len(lst)
                continue
            dest = Path(cfg["art_root"]) / sysid
            if destsub:
                dest = dest / destsub
            dest.mkdir(parents=True, exist_ok=True)
            base = LIBRETRO_BASE + urllib.parse.quote(sysdir) + "/" + kind + "/"

            def task(g):
                hit = _libretro_match(norm_title(g["name"]), index)
                if not hit:
                    return False
                target = dest / (Path(g["file"]).stem + ".png")
                if target.exists():
                    return True
                try:
                    req = urllib.request.Request(
                        base + urllib.parse.quote(hit),
                        headers={"User-Agent": "RetroShelf"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        data = r.read()
                    if data[:4] == b"\x89PNG":
                        target.write_bytes(data)
                        return True
                except Exception:
                    pass
                return False

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                for ok in ex.map(task, lst):
                    done += 1
                    if ok:
                        found += 1
                    if done % 10 == 0:
                        _set_shots(done=done, found=found)

        # second pass: fill remaining gaps from the LaunchBox image CDN
        _set_shots(status="fetching", sys="LaunchBox images")
        games = get_games(cfg, force=True)
        for sysid, lst in games.items():
            if sysid not in LB_PLATFORM:
                continue
            gaps = [(g, kind) for g in lst
                    for kind in ("box", "shot")
                    if not g["art" if kind == "box" else "shot"]]
            if not gaps:
                continue
            _set_shots(sys=SYSTEMS_BY_ID[sysid]["name"] + " (LaunchBox)")

            def lb_task(item):
                g, kind = item
                rec = meta_lookup(sysid, g["name"])
                if not rec or not rec.get(kind):
                    return False
                dest = Path(cfg["art_root"]) / sysid
                if kind == "shot":
                    dest = dest / "screens"
                dest.mkdir(parents=True, exist_ok=True)
                target = dest / (Path(g["file"]).stem
                                 + Path(rec[kind]).suffix.lower())
                if target.exists():
                    return False
                try:
                    req = urllib.request.Request(
                        LB_IMG_BASE + urllib.parse.quote(rec[kind]),
                        headers={"User-Agent": "RetroShelf"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        data = r.read()
                    if data[:4] == b"\x89PNG" or data[:2] == b"\xff\xd8":
                        target.write_bytes(data)
                        return True
                except Exception:
                    pass
                return False

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                for ok in ex.map(lb_task, gaps):
                    done += 1
                    if ok:
                        found += 1
                    if done % 10 == 0:
                        _set_shots(done=done, found=found)

        _scan_cache["time"] = 0
        _art_cache.clear()
        _set_shots(status="done", done=done, found=found, sys="")
    except Exception as e:
        _set_shots(status="error", msg=str(e))


def start_shots(cfg):
    if SHOTS.get("status") in ("indexing", "fetching"):
        return True, "already fetching"
    threading.Thread(target=_shots_worker, args=(cfg,), daemon=True).start()
    return True, "fetching started"


# --- game descriptions (LaunchBox Games Database dump) -----------------------

LB_META_URL = "https://gamesdb.launchbox-app.com/Metadata.zip"
LB_IMG_BASE = "https://images.launchbox-app.com/"
LB_PLATFORM = {
    "nes": "Nintendo Entertainment System",
    "snes": "Super Nintendo Entertainment System",
    "n64": "Nintendo 64",
    "gb": "Nintendo Game Boy",
    "gba": "Nintendo Game Boy Advance",
    "nds": "Nintendo DS",
    "gamecube": "Nintendo GameCube",
    "wii": "Nintendo Wii",
    "genesis": "Sega Genesis",
    "dreamcast": "Sega Dreamcast",
    "ps1": "Sony Playstation",
    "ps2": "Sony Playstation 2",
    "psp": "Sony PSP",
    "arcade": "Arcade",
    "atari2600": "Atari 2600",
    "c64": "Commodore 64",
    "amiga": "Commodore Amiga",
    "3do": "3DO Interactive Multiplayer",
}
LB_EXTRA = {"gb": ["Nintendo Game Boy Color"],
            "amiga": ["Commodore Amiga CD32"]}

META = {}
_meta_lock = threading.Lock()
_meta_index = {}       # sysid -> {normkey: dict}


def _meta_dir():
    d = APP_DIR / "metadata"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_meta(**kw):
    with _meta_lock:
        META.update(kw)


def _meta_worker():
    try:
        _set_meta(status="downloading", pct=0, games=0, msg="")
        zpath = _meta_dir() / "_Metadata.zip"
        req = urllib.request.Request(LB_META_URL, headers={"User-Agent": "RetroShelf"})
        with urllib.request.urlopen(req, timeout=120) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    _set_meta(pct=int(got * 100 / total))
        _set_meta(status="parsing", pct=100)

        wanted = {}
        for sysid, plat in LB_PLATFORM.items():
            wanted[plat.lower()] = sysid
            for alt in LB_EXTRA.get(sysid, []):
                wanted[alt.lower()] = sysid
        out = {s: {} for s in LB_PLATFORM}
        import xml.etree.ElementTree as ET
        kept = 0
        by_id = {}          # database id -> (sysid, key)
        images = {}         # database id -> {"box": file, "shot": file}
        IMG_WANT = {"Box - Front": "box", "Screenshot - Gameplay": "shot"}
        with zipfile.ZipFile(zpath) as z, z.open("Metadata.xml") as f:
            for _ev, el in ET.iterparse(f, events=("end",)):
                if el.tag == "GameImage":
                    kind = IMG_WANT.get(el.findtext("Type") or "")
                    if kind:
                        dbid = el.findtext("DatabaseID") or ""
                        fname = el.findtext("FileName") or ""
                        if dbid and fname:
                            slot = images.setdefault(dbid, {})
                            region = (el.findtext("Region") or "").lower()
                            # first one wins, but a US image beats a non-US one
                            if kind not in slot or ("united states" in region
                                                    and not slot.get(kind + "_us")):
                                slot[kind] = fname
                                if "united states" in region:
                                    slot[kind + "_us"] = True
                    el.clear()
                    continue
                if el.tag != "Game":
                    continue
                sysid = wanted.get((el.findtext("Platform") or "").lower())
                if sysid:
                    name = el.findtext("Name") or ""
                    key = norm_title(name)
                    if key and key not in out[sysid]:
                        date = el.findtext("ReleaseDate") or ""
                        rec = {
                            "ov": (el.findtext("Overview") or "").strip(),
                            "dev": el.findtext("Developer") or "",
                            "pub": el.findtext("Publisher") or "",
                            "gen": el.findtext("Genres") or "",
                            "yr": el.findtext("ReleaseYear") or (date[:4] if date else ""),
                            "pl": el.findtext("MaxPlayers") or "",
                            "rt": el.findtext("CommunityRating") or "",
                            "esrb": el.findtext("ESRB") or "",
                        }
                        dbid = el.findtext("DatabaseID") or ""
                        if any(rec.values()) or dbid:
                            out[sysid][key] = rec
                            if dbid:
                                by_id[dbid] = (sysid, key)
                            kept += 1
                            if kept % 2000 == 0:
                                _set_meta(games=kept)
                el.clear()
        # attach image filenames to their game records
        for dbid, (sysid, key) in by_id.items():
            imgs = images.get(dbid)
            if not imgs:
                continue
            rec = out[sysid].get(key)
            if rec is None:
                continue
            if imgs.get("box"):
                rec["box"] = imgs["box"]
            if imgs.get("shot"):
                rec["shot"] = imgs["shot"]
        for sysid, d in out.items():
            if d:
                (_meta_dir() / (sysid + ".json")).write_text(
                    json.dumps(d, separators=(",", ":")), encoding="utf-8")
        with _meta_lock:
            _meta_index.clear()
        zpath.unlink(missing_ok=True)
        _set_meta(status="done", games=kept)
    except Exception as e:
        _set_meta(status="error", msg=str(e))


def start_meta():
    if META.get("status") in ("downloading", "parsing"):
        return True, "already running"
    threading.Thread(target=_meta_worker, daemon=True).start()
    return True, "metadata download started"


_INITIALS_RE = re.compile(r"\b(?:[a-z0-9] )+[a-z0-9]\b")


def collapse_initials(s):
    """"O.D.T." normalises to "o d t" while a rom named "ODT" gives "odt".
    Joining runs of single characters makes the two forms meet."""
    return _INITIALS_RE.sub(lambda m: m.group(0).replace(" ", ""), s)


def _tokkey(s):
    return " ".join(sorted(s.split()))


def meta_lookup(sysid, name):
    with _meta_lock:
        entry = _meta_index.get(sysid)
    if entry is None:
        p = _meta_dir() / (sysid + ".json")
        try:
            idx = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except (OSError, json.JSONDecodeError):
            idx = {}
        # secondary indexes: no-space and token-order-insensitive
        nospace, toks = {}, {}
        for k in idx:
            nospace.setdefault(k.replace(" ", ""), k)
            toks.setdefault(_tokkey(k), k)
            toks.setdefault(collapse_initials(k), k)
        toksets = [(k, frozenset(collapse_initials(k).split())) for k in idx]
        entry = (idx, nospace, toks, list(idx.keys()), toksets)
        with _meta_lock:
            _meta_index[sysid] = entry
    idx, nospace, toks, keys, toksets = entry
    if not idx:
        return None
    key = norm_title(name)
    if key in idx:
        return idx[key]
    for alt in (nospace.get(key.replace(" ", "")), toks.get(_tokkey(key)),
                toks.get(collapse_initials(key))):
        if alt:
            return idx[alt]
    close = difflib.get_close_matches(key, keys, n=1, cutoff=0.9)
    if close:
        return idx[close[0]]
    alt = loose_key(key, keys) or subset_match(key, toksets)
    return idx[alt] if alt else None


def meta_have():
    return sorted(p.stem for p in _meta_dir().glob("*.json"))


# --- import community ratings into the star field ---------------------------

RATINGS = {}
_rate_lock = threading.Lock()


def _set_rate(**kw):
    with _rate_lock:
        RATINGS.update(kw)


def _ratings_worker(cfg, overwrite):
    try:
        _set_rate(status="working", done=0, total=0, set=0, msg="")
        games = get_games(cfg)
        todo = [(s, g) for s, lst in games.items() if s in LB_PLATFORM
                for g in lst]
        _set_rate(total=len(todo))
        n = 0
        for i, (sysid, g) in enumerate(todo):
            if i % 50 == 0:
                _set_rate(done=i, set=n)
            st = cfg["stats"].get(g["file"], {})
            # never clobber a rating the user set by hand
            if st.get("rating") and st.get("rsrc") != "db" and not overwrite:
                continue
            rec = meta_lookup(sysid, g["name"])
            if not rec or not rec.get("rt"):
                continue
            try:
                val = max(1, min(5, int(round(float(rec["rt"])))))
            except (TypeError, ValueError):
                continue
            if st.get("rating") == val and st.get("rsrc") == "db":
                continue
            st = cfg["stats"].setdefault(g["file"], {})
            st["rating"] = val
            st["rsrc"] = "db"
            n += 1
        with _lock:
            cur = load_config()
            cur["stats"].update(cfg["stats"])
            save_config(cur)
        _set_rate(status="done", done=len(todo), set=n)
    except Exception as e:
        _set_rate(status="error", msg=str(e))


def start_ratings(cfg, overwrite=False):
    if RATINGS.get("status") == "working":
        return True, "already running"
    if not meta_have():
        return False, "download the game database first"
    threading.Thread(target=_ratings_worker, args=(cfg, overwrite),
                     daemon=True).start()
    return True, "importing ratings"


# --- Amiga WHDLoad launching -------------------------------------------------
# The user's Amiga games are WHDLoad installs packed as .rar/.lha. WinUAE can't
# boot those directly, so on launch we extract the archive, build a minimal
# bootable volume with the (freely distributed) WHDLoad binary, and generate a
# per-game WinUAE config. Kickstart ROMs must be supplied by the user in
# emulators\amiga\kickstarts\ — identified by CRC32 and copied where WHDLoad
# expects them.

WHDLOAD_URL = "https://whdload.de/whdload/WHDLoad_usr.lha"
SKICK_URL = "https://aminet.net/util/boot/skick346.lha"   # kickXXXX.RTB tables

KICK_CRCS = {  # well-known Kickstart image checksums -> WHDLoad image name
    0x11F9E62F: "kick33180.A500",    # Kickstart 1.2
    0xC4F0F55F: "kick34005.A500",    # Kickstart 1.3
    0xC3BDB240: "kick37175.A500",    # Kickstart 2.04
    0x6C9B07D2: "kick39106.A1200",   # Kickstart 3.0 A1200
    0x1483A091: "kick40068.A1200",   # Kickstart 3.1 A1200
    0xD6BAE334: "kick40068.A4000",   # Kickstart 3.1 A4000
    0xE40A5DFB: "kick40063.A600",    # Kickstart 3.1 A600
}
AGA_KICKS = ("kick40068.A1200", "kick39106.A1200")

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_7z():
    for c in (r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if Path(c).is_file():
            return c
    return shutil.which("7z")


def check_archive(path):
    """Spot part-downloaded files early: they are usually pre-allocated at full
    size and still full of zeros, which 7-Zip only reports as "Is not archive".
    Extensions are not trusted - plenty of .zip files are really 7z or rar, and
    7-Zip opens them by content."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as e:
        raise RuntimeError("cannot read " + path.name + ": " + str(e))
    if not head.strip(b"\x00"):
        raise RuntimeError(path.name + " has no data yet - it looks like it is "
                           "still downloading. Try again once it finishes.")


def extract_archive(archive, dest):
    exe = find_7z()
    if not exe:
        raise RuntimeError("7-Zip not found - install it from 7-zip.org")
    check_archive(Path(archive))
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "x", "-y", "-o" + str(dest), str(archive)],
                       capture_output=True, creationflags=_NOWIN)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace")
        if "Is not archive" in err or "Cannot open the file" in err:
            raise RuntimeError(Path(archive).name + " could not be opened - the "
                               "download is probably incomplete or corrupt.")
        if "There is not enough space" in err or "disk full" in err.lower():
            raise RuntimeError("not enough disk space to unpack "
                               + Path(archive).name)
        # keep the tail of 7-Zip's message, it names the actual problem
        tail = [l for l in err.splitlines() if l.strip()][-1:] or ["unknown error"]
        raise RuntimeError("could not unpack " + Path(archive).name
                           + ": " + tail[0][:120])


def _amiga_dirs(cfg):
    base = Path(cfg["emulators_root"]) / "amiga"
    return base / "whdboot", base / "kickstarts", base / "cache"


def _unpack_kickstart_zips(ksdir):
    """Allow dropping downloaded .zip files straight into the kickstarts
    folder: recognised roms inside are extracted next to them."""
    for z in sorted(ksdir.glob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                for m in zf.infolist():
                    if m.is_dir() or m.file_size > 4 * 1024 * 1024:
                        continue
                    name = KICK_CRCS.get(m.CRC & 0xFFFFFFFF)
                    if name and not (ksdir / (name + ".rom")).exists():
                        (ksdir / (name + ".rom")).write_bytes(zf.read(m))
        except (OSError, zipfile.BadZipFile):
            continue


def _sync_kickstarts(cfg):
    """Identify roms in the kickstarts folder, stage them for WHDLoad.
    Returns (found: name->path, best: Path|None)."""
    whdboot, ksdir, _cache = _amiga_dirs(cfg)
    devs = whdboot / "Devs" / "Kickstarts"
    devs.mkdir(parents=True, exist_ok=True)
    found, best = {}, None
    if not ksdir.is_dir():
        return found, best
    _unpack_kickstart_zips(ksdir)
    for p in sorted(ksdir.iterdir()):
        if (not p.is_file() or p.suffix.lower() == ".zip"
                or p.stat().st_size > 4 * 1024 * 1024):
            continue
        data = p.read_bytes()
        if data[:11] == b"AMIROMTYPE1":
            continue                      # Cloanto-encrypted, needs rom.key
        name = KICK_CRCS.get(zlib.crc32(data) & 0xFFFFFFFF)
        if name:
            found[name] = p
            tgt = devs / name
            if not tgt.exists():
                shutil.copy2(p, tgt)
        elif best is None and len(data) in (262144, 524288):
            best = p                      # unknown dump, still usable to boot
    for pref in ("kick40068.A1200", "kick39106.A1200", "kick40068.A4000",
                 "kick40063.A600", "kick37175.A500", "kick34005.A500",
                 "kick33180.A500"):
        if pref in found:
            best = found[pref]
            break
    return found, best


def _ensure_whdload_bin(whdboot):
    target = whdboot / "C" / "WHDLoad"
    if target.is_file():
        return
    (whdboot / "C").mkdir(parents=True, exist_ok=True)
    lha = whdboot / "_WHDLoad_usr.lha"
    tmp = whdboot / "_whdload_tmp"
    req = urllib.request.Request(WHDLOAD_URL, headers={"User-Agent": "RetroShelf"})
    with urllib.request.urlopen(req, timeout=60) as r:
        lha.write_bytes(r.read())
    extract_archive(lha, tmp)
    src = None
    for p in tmp.rglob("WHDLoad"):
        if p.is_file():
            src = p
            break
    if not src:
        raise RuntimeError("WHDLoad binary not found in downloaded archive")
    shutil.copy2(src, target)
    shutil.rmtree(tmp, ignore_errors=True)
    lha.unlink(missing_ok=True)


def _ensure_rtbs(whdboot):
    """WHDLoad needs a .RTB relocation table next to each kickstart image.
    They're freely distributed in the Soft-Kick package on Aminet."""
    devs = whdboot / "Devs" / "Kickstarts"
    devs.mkdir(parents=True, exist_ok=True)
    if list(devs.glob("*.RTB")) or list(devs.glob("*.rtb")):
        return
    lha = whdboot / "_skick.lha"
    tmp = whdboot / "_skick_tmp"
    req = urllib.request.Request(SKICK_URL, headers={"User-Agent": "RetroShelf"})
    with urllib.request.urlopen(req, timeout=60) as r:
        lha.write_bytes(r.read())
    extract_archive(lha, tmp)
    for p in tmp.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".rtb":
            tgt = devs / p.name
            if not tgt.exists():
                shutil.copy2(p, tgt)
    shutil.rmtree(tmp, ignore_errors=True)
    lha.unlink(missing_ok=True)


def _find_slave(d):
    if not d.is_dir():
        return None
    slaves = [p for p in d.rglob("*.[sS]lave") if p.is_file()]
    slaves.sort(key=lambda p: len(p.parts))
    return slaves[0] if slaves else None


def _vol_name(s):
    s = re.sub(r"[^A-Za-z0-9_-]", "", s) or "Game"
    return s[:27]


def _amiga_launch(cfg, rom_path, emu):
    whdboot, ksdir, cache = _amiga_dirs(cfg)
    ksdir.mkdir(parents=True, exist_ok=True)
    found, bestrom = _sync_kickstarts(cfg)
    if not bestrom:
        return False, ("no Kickstart ROMs - put the Amiga .rom files in "
                       + str(ksdir) + " (plain dumps, not Cloanto-encrypted) "
                       "and try again")
    aga = any(bestrom == found.get(k) for k in AGA_KICKS)
    conf = {
        "use_gui": "no",
        "kickstart_rom_file": str(bestrom),
        "sound_output": "exact",
        "sound_stereo": "stereo",
        "sound_frequency": "44100",
        "cachesize": "0",
        "cpu_compatible": "true",
    }
    extra = []
    ext = rom_path.suffix.lower()
    if ext in (".adf", ".ipf"):
        rom13 = found.get("kick34005.A500") or bestrom
        conf.update({
            "kickstart_rom_file": str(rom13),
            "cpu_model": "68000", "chipset": "ecs_agnus",
            "chipset_compatible": "A500",
            "chipmem_size": "1", "bogomem_size": "2",
            "floppy0": str(rom_path), "nr_floppies": "1",
        })
    else:
        gamedir = cache / _vol_name(rom_path.stem)
        slave = _find_slave(gamedir)
        if not slave:
            extract_archive(rom_path, gamedir)
            if ext == ".rar":
                for inner in list(gamedir.rglob("*.lha")):
                    extract_archive(inner, gamedir)
                    inner.unlink(missing_ok=True)
            slave = _find_slave(gamedir)
        if not slave:
            return False, "no WHDLoad .slave found inside " + rom_path.name
        _ensure_whdload_bin(whdboot)
        _ensure_rtbs(whdboot)
        gdata = slave.parent
        sdir = whdboot / "S"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "startup-sequence").write_text(
            "FAILAT 999\ncd DH1:\nC:WHDLoad SLAVE=DH1:" + slave.name
            + " PRELOAD\n", newline="\n")
        conf.update({
            "cpu_model": "68020" if aga else "68000",
            "chipset": "aga" if aga else "ecs_agnus",
            "chipset_compatible": "A1200" if aga else "A500",
            "chipmem_size": "4" if aga else "2",
            "fastmem_size": "8" if aga else "4",
            "nr_floppies": "0",
        })
        extra = [
            "filesystem2=rw,DH0:Boot:" + str(whdboot) + ",10",
            "filesystem2=rw,DH1:" + _vol_name(gdata.name) + ":" + str(gdata) + ",0",
        ]
    cache.mkdir(parents=True, exist_ok=True)
    uae = cache / (_vol_name(rom_path.stem) + ".uae")
    uae.write_text("\n".join([f"{k}={v}" for k, v in conf.items()] + extra)
                   + "\n", newline="\n")
    subprocess.Popen([str(emu), "-f", str(uae)], cwd=str(emu.parent))
    return True, "launched"


# --- state / launch ---------------------------------------------------------

def collapse_versions(gl):
    """One library entry per title. The copy shown (and launched by default)
    is disc 1 if it is a multi-disc set, otherwise the largest file."""
    groups, order = {}, []
    for g in gl:
        key = norm_title(g["name"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(g)

    def rank(g):
        name = Path(g["file"]).name
        m = DISCNUM_RE.search(name)
        low = name.lower()
        # disc 1 first, then full releases over demos/betas, then USA/Europe
        # releases, and finally the biggest file
        partial = any(w in low for w in
                      ("demo", "beta", "proto", "sample", "not for resale"))
        return (int(m.group(1)) if m else 0, partial,
                _region_rank(name), -g.get("size", 0))

    out = []
    for key in order:
        items = sorted(groups[key], key=rank)
        best = dict(items[0])
        best["copies"] = len(items)
        # filters look across every copy, so a title with a Japanese release
        # still shows under Japan even when the default copy is the US one
        best["regions"] = sorted({g.get("region") for g in items if g.get("region")})
        best["langs"] = sorted({l for g in items for l in g.get("langs", [])})
        if len(items) > 1:
            # art may sit on any of the copies; play counts belong to the title
            withart = next((g for g in items if g["art"]), None)
            withshot = next((g for g in items if g["shot"]), None)
            best["art"] = bool(withart)
            best["shot"] = bool(withshot)
            best["artref"] = (withart or best)["file"]
            best["shotref"] = (withshot or best)["file"]
            best["plays"] = sum(g.get("plays", 0) for g in items)
            best["last"] = max(g.get("last", 0) for g in items)
            best["fav"] = any(g.get("fav") for g in items)
            if not best.get("rating"):
                best["rating"] = max((g.get("rating", 0) for g in items),
                                     default=0)
        else:
            best["artref"] = best["shotref"] = best["file"]
        out.append(best)
    return out


def build_state(cfg, rescan=False):
    if rescan:
        request_rescan(cfg)
    games = get_games(cfg)
    systems = []
    for sysdef in SYSTEMS:
        emu = find_emulator(sysdef, cfg)
        override = cfg["overrides"].get(sysdef["id"], {})
        gl = []
        for g in games.get(sysdef["id"], []):
            st = cfg["stats"].get(g["file"], {})
            gl.append({**g, "plays": st.get("plays", 0), "last": st.get("last", 0),
                       "fav": st.get("fav", False),
                       "rating": st.get("rating", 0),
                       "rsrc": st.get("rsrc", "")})
        gl = collapse_versions(gl)
        bios_rule = BIOS_RULES.get(sysdef["id"])
        if sysdef["id"] == "amiga":
            nbios = len(list((Path(cfg["emulators_root"]) / "amiga" /
                              "kickstarts").glob("*.rom"))) if emu else 0
            bdir = str(Path(cfg["emulators_root"]) / "amiga" / "kickstarts")
        else:
            nbios = len(bios_files(cfg, sysdef["id"])) if bios_rule else 0
            bdir = str(Path(cfg["emulators_root"]).parent / "bios" / sysdef["id"])
        systems.append({
            "bios_needed": bool(bios_rule),
            "bios_count": nbios,
            "bios_dir": bdir,
            "bios_hint": bios_rule["hint"] if bios_rule else "",
            "id": sysdef["id"],
            "name": sysdef["name"],
            "exts": exts_for(sysdef["id"]),
            "emu_name": sysdef["emu_name"],
            "emu_site": sysdef["emu_site"],
            "emu_url": sysdef["emu_url"],
            "dl": ("auto" if (sysdef["id"] in LIBRETRO_CORES if os.name != "nt"
                              else bool(sysdef.get("dl"))) else "manual"),
            "note": sysdef.get("note", ""),
            "emu_found": emu is not None,
            "emu_path": str(emu) if emu else "",
            "emu_override": override.get("emu_path", ""),
            "args": override.get("args", sysdef["args"]),
            "emu_dir": str(Path(cfg["emulators_root"]) / sysdef["id"]),
            "games": gl,
        })
    with _dl_lock:
        downloads = {k: dict(v) for k, v in DOWNLOADS.items()}
    with _cover_lock:
        covers = dict(COVERS)
    with _shots_lock:
        shots = dict(SHOTS)
    with _meta_lock:
        meta = dict(META)
    with _rate_lock:
        ratings = dict(RATINGS)
    meta["have"] = meta_have()
    return {
        "meta": meta,
        "version": _scan_cache["version"],
        "scanning": _scan_busy.is_set(),
        "cache_size": cache_size(cfg),
        "library_root": cfg["library_root"],
        "emulators_root": cfg["emulators_root"],
        "art_root": cfg["art_root"],
        "covers_dir": cfg.get("covers_dir", ""),
        "downloads": downloads,
        "covers": covers,
        "shots": shots,
        "ratings": ratings,
        "systems": systems,
    }


# --- BIOS handling ----------------------------------------------------------
# Drop BIOS files into <root>\bios\<system>\ and RetroShelf copies them where
# each emulator expects them. Some emulators need a marker file to keep their
# data next to the exe instead of in Documents.
BIOS_RULES = {
    "ps1": {"dirs": ["bios"], "portable": "portable.txt",
            "hint": "PlayStation BIOS, e.g. scph5501.bin / scph1001.bin"},
    "ps2": {"dirs": ["bios"], "portable": "portable.ini",
            "hint": "PS2 BIOS, e.g. SCPH-70012.bin (+ its .MEC/.NVM files)"},
    "dreamcast": {"dirs": ["data"], "portable": None,
                  "hint": "Dreamcast dc_boot.bin and dc_flash.bin"},
    "3do": {"dirs": ["system"], "portable": None,
            "hint": "3DO BIOS, e.g. panafz10.bin"},
    "amiga": {"dirs": [], "portable": None,
              "hint": "Amiga Kickstart roms (kickstarts folder)"},
}


def bios_dir(cfg, sysid):
    d = Path(cfg["emulators_root"]).parent / "bios" / sysid
    d.mkdir(parents=True, exist_ok=True)
    return d


def bios_files(cfg, sysid):
    d = Path(cfg["emulators_root"]).parent / "bios" / sysid
    if not d.is_dir():
        return []
    return [p for p in sorted(d.iterdir())
            if p.is_file() and p.suffix.lower() not in (".txt", ".md")]


def sync_bios(cfg, sysid, emu):
    """Copy dropped BIOS files into the emulator's own folder."""
    rule = BIOS_RULES.get(sysid)
    if not rule or not emu:
        return
    files = bios_files(cfg, sysid)
    if not files:
        return
    base = emu.parent
    if rule["portable"]:
        marker = base / rule["portable"]
        if not marker.exists():
            marker.write_text("", encoding="utf-8")
    for sub in rule["dirs"]:
        target = base / sub
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            dst = target / f.name
            if not dst.exists() or dst.stat().st_size != f.stat().st_size:
                try:
                    shutil.copy2(f, dst)
                except OSError:
                    pass


# --- duplicate finder -------------------------------------------------------
# Nothing is ever deleted outright: chosen files are moved to a quarantine
# folder so a mistake can be undone by dragging them back.
DISC_RE = re.compile(r"\(\s*(?:disc|disk|cd)\s*\d+", re.I)
# English releases first, then untagged files, then everything else
REGION_RANK = [("usa", 0), ("(us)", 0), ("(u)", 0), ("(us,", 0),
               ("world", 1), ("canada", 1),
               ("europe", 2), ("(eu)", 2), ("(e)", 2), ("(eu,", 2),
               ("australia", 2),
               ("japan", 4), ("(j)", 4), ("korea", 5), ("germany", 5),
               ("france", 5), ("italy", 5), ("spain", 5), ("brazil", 5),
               ("china", 5), ("taiwan", 5)]
NO_REGION_RANK = 3      # an untagged file is usually the main copy


def _region_rank(name):
    n = name.lower()
    for tag, rank in REGION_RANK:
        if tag in n:
            return rank
    return NO_REGION_RANK


def find_dupes(cfg):
    games = get_games(cfg)
    out = []
    for sysid, lst in games.items():
        if not lst:
            continue
        groups = {}
        for g in lst:
            groups.setdefault(norm_title(g["name"]), []).append(g)
        for title, items in sorted(groups.items()):
            if len(items) < 2:
                continue
            # multi-disc sets are not duplicates - leave them alone
            if any(DISC_RE.search(Path(g["file"]).name) for g in items):
                continue
            files = []
            for g in items:
                p = Path(g["file"])
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                files.append({"file": str(p), "name": p.name, "size": size,
                              "plays": g.get("plays", 0),
                              "rank": _region_rank(p.name)})
            # suggested keeper: best region, then biggest, then most played
            files.sort(key=lambda f: (f["rank"], -f["size"], -f["plays"]))
            files[0]["keep"] = True
            for f in files[1:]:
                f["keep"] = False
            out.append({"system": sysid,
                        "sysname": SYSTEMS_BY_ID[sysid]["name"],
                        "title": items[0]["name"], "files": files})
    return out


REGION_WORDS = [("usa", "USA"), ("(us)", "USA"), ("world", "World"),
                ("europe", "Europe"), ("(eu)", "Europe"), ("japan", "Japan"),
                ("korea", "Korea"), ("germany", "Germany"), ("france", "France"),
                ("italy", "Italy"), ("spain", "Spain"), ("australia", "Australia"),
                ("canada", "Canada"), ("brazil", "Brazil")]
FLAG_WORDS = [("demo", "Demo"), ("beta", "Beta"), ("proto", "Proto"),
              ("sample", "Sample"), ("aga", "AGA"), ("cd32", "CD32"),
              ("ntsc", "NTSC"), ("pal", "PAL"), ("rev ", "Rev"),
              ("not for resale", "NFR"), ("(u)", "USA"), ("(e)", "Europe"),
              ("(j)", "Japan")]
DISCNUM_RE = re.compile(r"\(\s*(?:disc|disk|cd)\s*(\d+)", re.I)


def version_label(fname):
    """Short human label for a game file: region, disc and variant flags."""
    low = fname.lower()
    bits = []
    for key, label in REGION_WORDS:
        if key in low:
            bits.append(label)
            break
    d = DISCNUM_RE.search(fname)
    if d:
        bits.append("Disc " + d.group(1))
    for key, label in FLAG_WORDS:
        if key in low and label not in bits:
            bits.append(label)
    ver = re.search(r"_v(\d[\w.]*)", fname)
    if ver:
        bits.append("v" + ver.group(1).rstrip("_"))
    if not bits:
        bits.append(Path(fname).suffix.lstrip(".").upper() or "File")
    return " · ".join(bits[:4])


def list_versions(cfg, sysid, name):
    """Every file in this system that is the same title, for the picker."""
    games = get_games(cfg).get(sysid, [])
    key = norm_title(name)
    out = []
    for g in games:
        if norm_title(g["name"]) != key:
            continue
        p = Path(g["file"])
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append({"file": str(p), "name": p.name, "size": size,
                    "label": version_label(p.name),
                    "disc": int(DISCNUM_RE.search(p.name).group(1))
                            if DISCNUM_RE.search(p.name) else 0,
                    "rank": _region_rank(p.name)})
    out.sort(key=lambda v: (v["disc"], v["rank"], -v["size"]))
    return out


def quarantine(cfg, paths):
    root = Path(cfg["library_root"])
    dest_root = Path(cfg["emulators_root"]).parent / "quarantine"
    moved, failed = 0, []
    for s in paths:
        p = Path(s)
        try:
            rel = p.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            failed.append(p.name + " (outside the games folder)")
            continue
        if not p.is_file():
            failed.append(p.name + " (not found)")
            continue
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(target.stem + "_" + str(int(time.time()))
                                      + target.suffix)
        try:
            shutil.move(str(p), str(target))
            moved += 1
        except OSError as e:
            failed.append(p.name + " (" + str(e) + ")")
    if moved:
        _scan_cache["time"] = 0
    return moved, failed


# Gamepad mapping DuckStation's own setup wizard would write for the first
# SDL controller (Xbox layout: A=bottom, B=right, X=left, Y=top).
DUCKSTATION_PAD1 = """[Pad1]
Type = AnalogController
Up = SDL-0/DPadUp
Down = SDL-0/DPadDown
Left = SDL-0/DPadLeft
Right = SDL-0/DPadRight
Cross = SDL-0/A
Circle = SDL-0/B
Square = SDL-0/X
Triangle = SDL-0/Y
Select = SDL-0/Back
Start = SDL-0/Start
L1 = SDL-0/LeftShoulder
R1 = SDL-0/RightShoulder
L2 = SDL-0/+LeftTrigger
R2 = SDL-0/+RightTrigger
L3 = SDL-0/LeftStick
R3 = SDL-0/RightStick
Analog = SDL-0/Guide
LLeft = SDL-0/-LeftX
LRight = SDL-0/+LeftX
LUp = SDL-0/-LeftY
LDown = SDL-0/+LeftY
RLeft = SDL-0/-RightX
RRight = SDL-0/+RightX
RUp = SDL-0/-RightY
RDown = SDL-0/+RightY
LargeMotor = SDL-0/LargeMotor
SmallMotor = SDL-0/SmallMotor
"""


def _ini_section(text, name):
    """Return (start, end) of an ini section body including its header."""
    head = "[" + name + "]"
    start = text.find(head)
    if start == -1:
        return None
    nxt = text.find("\n[", start + len(head))
    return (start, len(text) if nxt == -1 else nxt + 1)


def _set_ini_key(text, section, key, value):
    span = _ini_section(text, section)
    line = key + " = " + value
    if not span:
        return text.rstrip("\n") + "\n\n[" + section + "]\n" + line + "\n"
    body = text[span[0]:span[1]]
    pat = re.compile(r"(?m)^" + re.escape(key) + r"\s*=.*$")
    if pat.search(body):
        body = pat.sub(line, body, count=1)
    else:
        body = body.rstrip("\n") + "\n" + line + "\n"
    return text[:span[0]] + body + text[span[1]:]


def prep_emulator(sysid, emu):
    """First-run tweaks so a freshly downloaded emulator boots games directly
    instead of stopping at its setup wizard - which is also where it would
    have bound the gamepad, so that has to be done here too."""
    if not emu:
        return
    base = emu.parent
    if sysid == "ps1":
        ini = base / "settings.ini"
        try:
            text = ini.read_text(encoding="utf-8", errors="replace") \
                if ini.exists() else "[Main]\n"
            if "SetupWizardIncomplete" not in text:
                text = _set_ini_key(text, "Main", "SetupWizardIncomplete", "false")
            text = _set_ini_key(text, "BIOS", "SearchDirectory", "bios")
            text = _set_ini_key(text, "InputSources", "SDL", "true")
            # bind the first gamepad unless the user has mapped one already
            span = _ini_section(text, "Pad1")
            pad = text[span[0]:span[1]] if span else ""
            if "SDL-" not in pad and "XInput-" not in pad:
                if span:
                    text = text[:span[0]] + DUCKSTATION_PAD1 + "\n" + text[span[1]:]
                else:
                    text = text.rstrip("\n") + "\n\n" + DUCKSTATION_PAD1
            ini.write_text(text, encoding="utf-8")
        except OSError:
            pass


def cache_dir(cfg, sysid):
    d = Path(cfg["emulators_root"]).parent / "cache" / sysid
    d.mkdir(parents=True, exist_ok=True)
    return d


_cache_size = {"time": 0.0, "bytes": 0}


def cache_size(cfg):
    """Size of the unpacked-disc cache, remembered for a minute - walking it
    on every state request would stall the UI."""
    now = time.time()
    if now - _cache_size["time"] < 60:
        return _cache_size["bytes"]
    root = Path(cfg["emulators_root"]).parent / "cache"
    total = 0
    if root.is_dir():
        for _dir, _sub, files in os.walk(root):
            for f in files:
                try:
                    total += os.stat(os.path.join(_dir, f)).st_size
                except OSError:
                    pass
    _cache_size.update(time=now, bytes=total)
    return total


def clear_cache(cfg):
    root = Path(cfg["emulators_root"]).parent / "cache"
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def _pick_disc(d):
    """Best playable disc file inside an extracted folder."""
    files = [p for p in d.rglob("*") if p.is_file()]
    for ext in DISC_PLAYABLE:
        hits = sorted((p for p in files if p.suffix.lower() == ext),
                      key=lambda p: (len(p.parts), p.name.lower()))
        if hits:
            if ext == ".bin" and len(hits) > 1:
                continue          # multi-track bin set needs its cue
            return hits[0]
    return None


def unpack_disc(cfg, sysid, rom_path):
    """Archived disc images are extracted once into the cache folder."""
    dest = cache_dir(cfg, sysid) / _vol_name(rom_path.stem)
    hit = _pick_disc(dest) if dest.is_dir() else None
    if hit:
        return hit
    extract_archive(rom_path, dest)
    for inner in list(dest.rglob("*")):
        if inner.is_file() and inner.suffix.lower() in ARCHIVE_EXTS:
            extract_archive(inner, dest)
            inner.unlink(missing_ok=True)
    hit = _pick_disc(dest)
    if not hit:
        raise RuntimeError("no disc image found inside " + rom_path.name)
    return hit


def launch_game(cfg, system_id, rom):
    sysdef = SYSTEMS_BY_ID.get(system_id)
    if not sysdef:
        return False, "unknown system"
    rom_path = Path(rom)
    root = Path(cfg["library_root"]).resolve()
    try:
        rom_path.resolve().relative_to(root)
    except ValueError:
        return False, "rom is outside the games folder"
    if not rom_path.is_file():
        return False, "rom file not found"
    emu = find_emulator(sysdef, cfg)
    if not emu:
        return False, "no emulator found for " + sysdef["name"]
    sync_bios(cfg, system_id, emu)
    prep_emulator(system_id, emu)
    if system_id == "amiga":
        try:
            ok, msg = _amiga_launch(cfg, rom_path, emu)
        except Exception as e:
            ok, msg = False, str(e)
        if not ok:
            return False, msg
    else:
        play_path = rom_path
        if (system_id in DISC_SYSTEMS
                and rom_path.suffix.lower() in ARCHIVE_EXTS):
            try:
                play_path = unpack_disc(cfg, system_id, rom_path)
            except Exception as e:
                return False, str(e)
        args_tpl = cfg["overrides"].get(system_id, {}).get("args", sysdef["args"])
        if os.name != "nt" and Path(emu).name.startswith("retroarch"):
            core = find_core(system_id)
            if not core:
                return False, (f"no libretro core installed for {sysdef['name']}"
                               " - use the download button, or "
                               f"POST /api/download {{\"id\":\"{system_id}\"}}")
            args_tpl = '"{emu}" -L "' + str(core) + '" "{rom}" -f'
        cmd = (args_tpl
               .replace("{emu}", str(emu))
               .replace("{emudir}", str(emu.parent))
               .replace("{rom}", str(play_path))
               .replace("{romname}", play_path.stem)
               .replace("{romdir}", str(play_path.parent)))
        try:
            # a string command line is Windows-only; POSIX needs an argv list
            popen_cmd = cmd if os.name == "nt" else shlex.split(cmd)
            subprocess.Popen(popen_cmd, cwd=str(emu.parent))
        except OSError as e:
            return False, str(e)
    stat = cfg["stats"].setdefault(str(rom_path), {})
    stat["plays"] = stat.get("plays", 0) + 1
    stat["last"] = int(time.time())
    save_config(cfg)
    return True, "launched"


def create_layout(cfg):
    for sysdef in SYSTEMS:
        (Path(cfg["emulators_root"]) / sysdef["id"]).mkdir(parents=True, exist_ok=True)
        (Path(cfg["art_root"]) / sysdef["id"] / "screens").mkdir(parents=True, exist_ok=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # a page of covers opens a lot of sockets at once; the default backlog of
    # 5 makes Windows refuse the overflow, which shows up as missing images
    request_queue_size = 128
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    # keep-alive: one connection serves many images instead of one each
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _empty(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/state":
            qs = urllib.parse.parse_qs(parsed.query)
            rescan = qs.get("rescan", ["0"])[0] == "1"
            with _lock:
                self._json(build_state(load_config(), rescan=rescan))
        elif parsed.path == "/api/dupes":
            with _lock:
                self._json({"groups": find_dupes(load_config())})
        elif parsed.path == "/api/details":
            qs = urllib.parse.parse_qs(parsed.query)
            rom = qs.get("rom", [""])[0]
            p = Path(rom)
            info = {"folder": str(p.parent), "file": p.name, "size": 0, "mtime": 0}
            try:
                st = p.stat()
                info["size"] = st.st_size
                info["mtime"] = int(st.st_mtime)
            except OSError:
                pass
            sysid = qs.get("system", [""])[0]
            name = qs.get("name", [""])[0]
            if sysid and name:
                info["meta"] = meta_lookup(sysid, name)
                info["versions"] = list_versions(load_config(), sysid, name)
            self._json(info)
        elif parsed.path == "/api/art":
            qs = urllib.parse.parse_qs(parsed.query)
            system_id = qs.get("system", [""])[0]
            rom = qs.get("rom", [""])[0]
            kind = "screen" if qs.get("kind", [""])[0] == "screen" else "cover"
            sysdef = SYSTEMS_BY_ID.get(system_id)
            cfg = load_config()
            art = find_art(sysdef, Path(rom), cfg, kind) if sysdef and rom else None
            if not art:
                self._empty(404)
                return
            data = art.read_bytes()
            ctype = mimetypes.guess_type(str(art))[0] or "image/png"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        else:
            self._empty(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError):
            self._json({"ok": False, "msg": "bad request"}, 400)
            return
        with _lock:
            cfg = load_config()
            if parsed.path == "/api/launch":
                ok, msg = launch_game(cfg, body.get("system", ""), body.get("rom", ""))
                self._json({"ok": ok, "msg": msg})
            elif parsed.path == "/api/download":
                ok, msg = start_download(body.get("id", ""), cfg)
                self._json({"ok": ok, "msg": msg})
            elif parsed.path == "/api/meta":
                rom = body.get("rom", "")
                if rom:
                    st = cfg["stats"].setdefault(rom, {})
                    if "fav" in body:
                        st["fav"] = bool(body["fav"])
                    if "rating" in body:
                        st["rating"] = max(0, min(5, int(body["rating"] or 0)))
                        st["rsrc"] = "user"      # hand-set ratings are protected
                    save_config(cfg)
                self._json({"ok": True, "msg": "saved"})
            elif parsed.path == "/api/fetchshots":
                ok, msg = start_shots(cfg)
                self._json({"ok": ok, "msg": msg})
            elif parsed.path == "/api/importratings":
                ok, msg = start_ratings(cfg, bool(body.get("overwrite")))
                self._json({"ok": ok, "msg": msg})
            elif parsed.path == "/api/fetchmeta":
                ok, msg = start_meta()
                self._json({"ok": ok, "msg": msg})
            elif parsed.path == "/api/matchcovers":
                covers_dir = body.get("dir", "").strip() or cfg.get("covers_dir", "")
                if covers_dir:
                    cfg["covers_dir"] = covers_dir
                    save_config(cfg)
                ok, msg = start_cover_match(cfg, covers_dir)
                self._json({"ok": ok, "msg": msg})
            elif parsed.path == "/api/settings":
                for key in ("library_root", "emulators_root", "art_root"):
                    val = body.get(key, "").strip()
                    if val:
                        cfg[key] = val
                if "covers_dir" in body:
                    cfg["covers_dir"] = body.get("covers_dir", "").strip()
                save_config(cfg)
                self._json({"ok": True, "msg": "saved"})
            elif parsed.path == "/api/system":
                system_id = body.get("id", "")
                if system_id not in SYSTEMS_BY_ID:
                    self._json({"ok": False, "msg": "unknown system"}, 400)
                    return
                ov = cfg["overrides"].setdefault(system_id, {})
                ov["emu_path"] = body.get("emu_path", "").strip()
                args = body.get("args", "").strip()
                if args and args != SYSTEMS_BY_ID[system_id]["args"]:
                    ov["args"] = args
                else:
                    ov.pop("args", None)
                if not ov.get("emu_path") and "args" not in ov:
                    cfg["overrides"].pop(system_id, None)
                save_config(cfg)
                self._json({"ok": True, "msg": "saved"})
            elif parsed.path == "/api/quarantine":
                paths = body.get("files") or []
                moved, failed = quarantine(cfg, paths)
                self._json({"ok": True, "moved": moved, "failed": failed,
                            "msg": f"moved {moved} file(s)"})
            elif parsed.path == "/api/cores":
                # install a libretro core for every system that has games but
                # no working emulator - one call to get a fresh box playable
                games = get_games(cfg)
                todo = [s["id"] for s in SYSTEMS
                        if games.get(s["id"]) and not find_emulator(s, cfg)
                        and s["id"] in LIBRETRO_CORES]
                done, failed = [], []
                for sysid in todo:
                    try:
                        if install_core(sysid):
                            done.append(sysid)
                        else:
                            failed.append(sysid)
                    except Exception as e:
                        failed.append(f"{sysid}: {e}")
                _emu_cache.clear()
                self._json({"ok": not failed, "installed": done,
                            "failed": failed,
                            "msg": f"installed {len(done)} core(s)"})
            elif parsed.path == "/api/scan":
                request_rescan(cfg)
                self._json({"ok": True, "msg": "scanning",
                            "games": sum(len(v) for v in get_games(cfg).values())})
            elif parsed.path == "/api/clearcache":
                try:
                    clear_cache(cfg)
                    self._json({"ok": True, "msg": "cache cleared"})
                except OSError as e:
                    self._json({"ok": False, "msg": str(e)})
            elif parsed.path == "/api/mkdirs":
                try:
                    create_layout(cfg)
                    self._json({"ok": True, "msg": "folders created"})
                except OSError as e:
                    self._json({"ok": False, "msg": str(e)})
            else:
                self._empty(404)


HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>RetroShelf</title>
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAp0lEQVR4nGNgGAWjYKQDRnQBIR6p/7S08N2XZyh2MjEMMGBE9/nbTXE0tVDYbxFKSDAxDDBghPs87x9dLRaeBPE7E8MAAyZcEppzvoDxgDmAXoAFXQDd1zD+9RQe0kzeagShvc+BKcbWAjD9v3rC4AoBRly5gGyfD7VcwAJjXHr1G0VipR87VnHqAfZBWhfsj/hJUwsdV7APsrqAYaS3B0bBKBgFowAA02A1L+RzQp0AAAAASUVORK5CYII=">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=VT323&family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
:root {
  /* gloomy cyberpunk: dead-black panels, sodium orange, magenta + cyan neon */
  --bg: #05040a; --panel: #0d0b13; --panel2: #16121c; --line: #33263a;
  --amber: #ff7a18; --amber2: #ffb266; --dim: #8f4a14; --text: #cdc4d2;
  --muted: #7a7089; --green: #00ff88; --red: #ff2f4a;
  --cyan: #00e0ff; --magenta: #ff1f6f;
  --glow: 255,122,24;        /* orange glow */
  --glow2: 0,224,255;        /* cyan glow */
  --glow3: 255,31,111;       /* magenta glow */
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
html { scrollbar-color: var(--dim) var(--bg); }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: rgba(0,0,0,.3); }
::-webkit-scrollbar-thumb { background: var(--line); border-radius: 5px; }
::-webkit-scrollbar-thumb:hover { background: var(--dim); }
body {
  background:
    radial-gradient(ellipse at 18% 0%, rgba(255,122,24,.11), transparent 55%),
    radial-gradient(ellipse at 85% 6%, rgba(255,31,111,.07), transparent 48%),
    radial-gradient(ellipse at 60% 100%, rgba(0,224,255,.05), transparent 45%),
    radial-gradient(ellipse at 50% 20%, #0e0a12 0%, #08060c 60%, #040307 100%);
  color: var(--text); font-family: VT323, monospace; font-size: 19px;
  overflow: hidden; display: flex; flex-direction: column;
}
a { color: var(--amber2); }
a:hover { text-shadow: 0 0 8px rgba(var(--glow),.6); }
/* ---- CRT tube ---- */
#gridfloor {
  position: fixed; bottom: -6vh; left: -50%; width: 200%; height: 44vh;
  pointer-events: none; z-index: 0; opacity: .22;
  background:
    repeating-linear-gradient(90deg, rgba(var(--glow2),.18) 0 2px, transparent 2px 70px),
    repeating-linear-gradient(0deg, rgba(var(--glow3),.18) 0 2px, transparent 2px 46px);
  transform: perspective(420px) rotateX(62deg);
  animation: gridmove 1.5s linear infinite;
  -webkit-mask-image: linear-gradient(to top, rgba(0,0,0,.8), transparent 85%);
  mask-image: linear-gradient(to top, rgba(0,0,0,.8), transparent 85%);
}
@keyframes gridmove { from { background-position-y: 0, 0; } to { background-position-y: 0, 46px; } }
#crt { position: fixed; inset: 0; pointer-events: none; z-index: 95;
  border-radius: 22px;
  box-shadow: inset 0 0 130px rgba(0,0,0,.6), inset 0 0 24px rgba(0,0,0,.5); }
#crt::before { content: ""; position: absolute; inset: 0; border-radius: 22px;
  background: radial-gradient(ellipse at center, transparent 58%, rgba(0,0,0,.5) 100%); }
#crt::after { content: ""; position: absolute; inset: 0; border-radius: 22px;
  background: radial-gradient(ellipse at 50% 42%, rgba(var(--glow2),.05), transparent 62%);
  animation: hum 5.5s ease-in-out infinite; }
@keyframes hum { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
#stars { position: fixed; inset: 0; pointer-events: none; z-index: 0; }
.starlayer { position: absolute; top: 0; left: 0; background: transparent;
  animation: twinkle 4s infinite alternate ease-in-out; }
@keyframes twinkle { from { opacity: .2; } to { opacity: .8; } }
.pulseline { height: 3px; flex-shrink: 0; background: linear-gradient(90deg,
  transparent, var(--amber) 18%, var(--cyan) 50%, var(--amber) 82%, transparent);
  animation: pulseline 3.4s ease-in-out infinite; }
@keyframes pulseline {
  0%, 100% { opacity: .3; filter: none; }
  50% { opacity: 1; filter: drop-shadow(0 0 8px rgba(var(--glow),.8)); } }
@keyframes pulse { 50% { opacity: .45; } }
@keyframes blink { 50% { opacity: 0; } }
/* ---- boot + loader ---- */
#boot { position: fixed; inset: 0; z-index: 200; background: #040307; padding: 46px; }
#boot pre { font-family: VT323, monospace; font-size: 22px; color: var(--amber);
  text-shadow: 0 0 8px rgba(var(--glow),.6); line-height: 1.6; white-space: pre-wrap; }
#boot.off { animation: crtoff .55s ease-in forwards; }
@keyframes crtoff {
  0% { transform: scaleY(1); filter: brightness(1); opacity: 1; }
  55% { transform: scaleY(.008); filter: brightness(2.6); opacity: 1; background: #ff9ecb; }
  100% { transform: scaleY(.008) scaleX(.01); filter: brightness(3); opacity: 0; background: #ff9ecb; } }
#loader { position: fixed; inset: 0; z-index: 150; display: none;
  background: rgba(4,3,7,.93); text-align: center; }
#loader .inner { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  width: min(480px, 86vw); }
#loader .t1 { font-family: 'Press Start 2P', monospace; font-size: 18px;
  color: var(--amber); text-shadow: 0 0 14px rgba(var(--glow),.7);
  animation: pulse 1s steps(2) infinite; }
#loader .t2 { font-size: 28px; color: var(--text); margin: 22px 0 26px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#loader .barwrap { height: 22px; border: 2px solid var(--amber); padding: 3px; }
#loader .bar { height: 100%; width: 0; background: repeating-linear-gradient(90deg,
  var(--amber) 0 14px, #5c0b32 14px 18px); }
#loader.go .bar { animation: fill 1.6s steps(24) forwards; }
@keyframes fill { to { width: 100%; } }
/* ---- header ---- */
header { flex-shrink: 0; background: rgba(8,6,14,.94); border-bottom: 1px solid var(--line);
  box-shadow: 0 4px 18px rgba(0,0,0,.5); position: relative; z-index: 12; }
.bar { display: flex; align-items: center; gap: 18px; padding: 8px 20px 8px; }
.logo { display: flex; align-items: flex-end; gap: 4px; white-space: nowrap;
  cursor: pointer; user-select: none; overflow: hidden; }
.logo pre { margin: 0;
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 10.5px; line-height: 1.2; font-weight: 800; letter-spacing: 0;
  -webkit-text-stroke: .35px currentColor;
  background: linear-gradient(100deg, #2a1408 0%, var(--amber) 16%,
    #ffe6c2 26%, var(--magenta) 34%, var(--cyan) 42%, var(--amber) 58%,
    #2a1408 100%);
  background-size: 260% 100%;
  -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 5s linear infinite;
  filter: drop-shadow(0 0 5px rgba(var(--glow),.55))
          drop-shadow(0 0 1px rgba(var(--glow),.9)); }
@keyframes shimmer {
  from { background-position-x: 260%; }
  to { background-position-x: -60%; } }
@media (max-width: 1600px) { .logo pre { font-size: 8.5px; } }
@media (max-width: 1300px) { .logo pre { font-size: 7px; } }
@media (max-width: 1050px) { .logo { display: none; } }
.searchwrap { flex: 1; max-width: 380px; position: relative; }
.searchwrap::before { content: ">"; position: absolute; left: 14px; top: 4px;
  color: var(--dim); font-size: 22px; }
#search { width: 100%; height: 38px; background: rgba(0,0,0,.45);
  border: 1px solid var(--line); border-radius: 4px; padding: 0 16px 0 34px;
  font: inherit; font-size: 20px; color: var(--amber2); outline: none; caret-color: var(--amber); }
#search::placeholder { color: var(--muted); }
#search:focus { border-color: var(--amber);
  box-shadow: 0 0 12px rgba(var(--glow),.35), inset 0 0 8px rgba(var(--glow),.08); }
#count { color: var(--muted); font-size: 18px; margin-left: auto; white-space: nowrap; }
#padbadge { display: inline-flex; width: 26px; height: 26px; flex-shrink: 0; }
#padbadge svg { width: 100%; height: 100%; fill: var(--line); transition: fill .2s; }
#padbadge.on svg { fill: var(--green); filter: drop-shadow(0 0 6px rgba(0,255,168,.7)); }
.tabs { display: flex; gap: 6px; padding: 0 20px; }
.tabs button { background: none; border: none; font-family: 'Press Start 2P', monospace;
  font-size: 10px; color: var(--muted); padding: 8px 12px 10px; cursor: pointer;
  border-bottom: 3px solid transparent; letter-spacing: 1px; }
.tabs button.on { color: var(--amber); border-bottom-color: var(--amber);
  text-shadow: 0 0 10px rgba(var(--glow),.7); }
.tabs button:hover:not(.on) { color: var(--text); }
#termline { padding: 4px 20px 5px; color: var(--amber); font-size: 18px;
  background: rgba(0,0,0,.35); border-bottom: 1px solid var(--line);
  white-space: nowrap; overflow: hidden; flex-shrink: 0;
  text-shadow: 0 0 8px rgba(var(--glow),.4); }
/* ---- three column shell ---- */
#shell { flex: 1; display: flex; min-height: 0; position: relative; z-index: 1; }
#sidebar { width: 226px; flex-shrink: 0; border-right: 1px solid var(--line);
  background: rgba(8,6,14,.75); overflow-y: auto; padding: 10px 0 20px; }
.side-h { font-family: 'Press Start 2P', monospace; font-size: 8px; color: var(--dim);
  letter-spacing: 1px; padding: 14px 16px 7px; }
.side-i { display: flex; align-items: center; gap: 9px; padding: 5px 16px;
  cursor: pointer; color: var(--text); white-space: nowrap; }
.side-i:hover { background: rgba(var(--glow),.07); color: var(--amber2); }
.side-i.on { background: rgba(var(--glow),.14); color: var(--amber);
  box-shadow: inset 3px 0 var(--amber); text-shadow: 0 0 8px rgba(var(--glow),.5); }
.side-i .lbl { overflow: hidden; text-overflow: ellipsis; }
.side-i .n { margin-left: auto; color: var(--muted); font-size: 16px; }
.side-i.on .n { color: var(--amber); }
/* ---- centre ---- */
#centre { flex: 1; min-width: 0; display: flex; flex-direction: column; }
#toolbar { flex-shrink: 0; display: flex; align-items: center; gap: 8px;
  padding: 8px 16px; border-bottom: 1px solid var(--line); background: rgba(0,0,0,.25); }
.tbtn { background: rgba(0,0,0,.3); border: 1px solid var(--line); color: var(--text);
  font: inherit; font-size: 17px; padding: 3px 12px; border-radius: 4px; cursor: pointer; }
.tbtn:hover { border-color: var(--dim); color: var(--amber2); }
.tbtn.on { background: var(--amber); color: #08060e; border-color: var(--amber);
  box-shadow: 0 0 12px rgba(var(--glow),.45); }
.tsel { background: rgba(0,0,0,.35); border: 1px solid var(--line); color: var(--text);
  font: inherit; font-size: 17px; padding: 3px 6px; border-radius: 4px;
  cursor: pointer; max-width: 190px; }
.tsel:hover { border-color: var(--dim); color: var(--amber2); }
.tsel.on { border-color: var(--amber); color: var(--amber);
  box-shadow: 0 0 10px rgba(var(--glow),.35); }
.tsel option { background: var(--panel); color: var(--text); }
#toolbar .sp { margin-left: auto; color: var(--muted); font-size: 17px; }
#view { flex: 1; overflow-y: auto; padding: 16px; }
/* grid */
#grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 16px; }
.tile { cursor: pointer; position: relative; }
/* the whole grid fades in once - per-tile animations could leave hundreds of
   tiles stuck invisible if the compositor throttles them */
#grid, #view > .row:first-child { animation: rowin .28s ease-out; }
@keyframes rowin { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.tile .box { aspect-ratio: 3/4; border-radius: 5px; overflow: hidden; background: var(--panel2);
  border: 2px solid var(--line); display: flex; align-items: center; justify-content: center;
  position: relative; transition: border-color .13s, box-shadow .13s, transform .13s; }
.tile .box img { width: 100%; height: 100%; object-fit: cover; display: block; }
.tile .box .ph { font-family: 'Press Start 2P', monospace; font-size: 17px; color: #fff;
  text-shadow: 1px 2px 0 rgba(0,0,0,.45); }
.tile:hover .box { border-color: var(--dim); transform: translateY(-3px); }
.tile.sel .box { border-color: var(--amber); transform: translateY(-3px);
  box-shadow: 0 0 20px rgba(var(--glow),.45); }
.tile .fav { position: absolute; top: 5px; right: 6px; color: var(--amber);
  font-size: 20px; text-shadow: 0 0 6px rgba(0,0,0,.9); }
.tile .copies { position: absolute; bottom: 5px; right: 5px; background: rgba(4,3,7,.85);
  border: 1px solid var(--amber); color: var(--amber); border-radius: 3px;
  font-size: 14px; padding: 0 6px; line-height: 1.5; }
.tile .cap { font-size: 17px; color: var(--text); margin-top: 6px; line-height: 1.15;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; }
.tile.sel .cap { color: var(--amber2); }
.tile .sub { font-size: 15px; color: var(--muted); }
.tile .sub .tstar { letter-spacing: -1px; text-shadow: 0 0 7px currentColor; }
.tile .sub .tstar .temp { color: #2e2838; text-shadow: none; }
/* list */
.row { display: flex; align-items: center; gap: 16px; padding: 9px 12px;
  border-radius: 6px; cursor: pointer; background: var(--panel);
  border: 1px solid var(--line); margin-bottom: 8px;
  transition: box-shadow .13s, border-color .13s, transform .13s; }
.row:hover { border-color: var(--dim); }
.row.sel { border-color: var(--amber); transform: translateX(5px);
  box-shadow: 0 0 18px rgba(var(--glow),.28); }
.row .cover { width: 54px; height: 72px; flex-shrink: 0; border-radius: 4px;
  overflow: hidden; background: var(--panel2); display: flex;
  align-items: center; justify-content: center; }
.row .cover img { width: 100%; height: 100%; object-fit: cover; }
.row .cover .ph { font-family: 'Press Start 2P', monospace; font-size: 12px; color: #fff; }
.row .shot { width: 128px; height: 72px; flex-shrink: 0; border-radius: 4px;
  overflow: hidden; background: var(--panel2); display: flex;
  align-items: center; justify-content: center; }
.row .shot img { width: 100%; height: 100%; object-fit: cover; }
.row .shot .ph2 { font-family: 'Press Start 2P', monospace; font-size: 6px; color: var(--muted); }
.row .info { min-width: 0; flex: 1; }
.row .nm { font-size: 24px; color: var(--text); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
  text-shadow: 1px 0 rgba(255,31,111,.22), -1px 0 rgba(0,224,255,.22); }
.row.sel .nm { color: var(--amber2); }
.row .sub { font-size: 17px; color: var(--muted); display: flex; align-items: center; }
/* ---- details panel ---- */
#details { width: 320px; flex-shrink: 0; border-left: 1px solid var(--line);
  background: rgba(8,6,14,.8); overflow-y: auto; }
#details .empty2 { padding: 60px 22px; text-align: center; color: var(--muted); }
.dcover { width: 100%; aspect-ratio: 3/4; max-height: 320px; object-fit: contain;
  background: #000; border-bottom: 1px solid var(--line); display: block; }
.dcover.ph3 { display: flex; align-items: center; justify-content: center;
  font-family: 'Press Start 2P', monospace; font-size: 26px; color: #fff; }
.dbody { padding: 14px 18px 26px; }
.dtitle { font-size: 27px; color: var(--amber2); line-height: 1.1;
  text-shadow: 0 0 10px rgba(var(--glow),.35); }
.dsys { font-size: 17px; color: var(--muted); margin-top: 4px;
  display: flex; align-items: center; }
.playbig { width: 100%; margin: 14px 0 12px; background: var(--amber); border: none;
  color: #08060e; font-family: 'Press Start 2P', monospace; font-size: 12px;
  padding: 13px 0; border-radius: 5px; cursor: pointer; letter-spacing: 1px;
  box-shadow: 0 0 16px rgba(var(--glow),.4); }
.playbig:hover { background: var(--amber2); box-shadow: 0 0 26px rgba(var(--glow),.75); }
.drow2 { display: flex; gap: 8px; margin-bottom: 14px; }
.drow2 button { flex: 1; background: rgba(0,0,0,.3); border: 1px solid var(--line);
  color: var(--text); font: inherit; font-size: 17px; padding: 5px 0;
  border-radius: 4px; cursor: pointer; }
.drow2 button:hover { border-color: var(--amber); color: var(--amber2); }
.stars { font-size: 24px; color: var(--dim); letter-spacing: 3px; cursor: pointer;
  margin-bottom: 12px; }
.stars b { color: var(--amber); font-weight: 400;
  text-shadow: 0 0 8px rgba(var(--glow),.6); }
.stars .rsrc { font-size: 15px; color: var(--muted); letter-spacing: 0;
  margin-left: 6px; }
.dshot { width: 100%; border-radius: 4px; border: 1px solid var(--line);
  display: block; margin-bottom: 14px; background: #000; }
.dshot-ph { width: 100%; aspect-ratio: 4/3; border-radius: 4px;
  border: 1px dashed var(--line); display: flex; align-items: center;
  justify-content: center; color: var(--muted); font-size: 16px; margin-bottom: 14px; }
.verwrap { border: 1px solid var(--line); border-radius: 5px; padding: 7px 9px;
  margin: 12px 0 4px; background: rgba(0,0,0,.25); }
.verlabel { font-size: 15px; color: var(--dim); letter-spacing: 1px;
  margin-bottom: 5px; }
.ver { display: flex; align-items: center; gap: 8px; padding: 3px 4px;
  cursor: pointer; border-radius: 3px; font-size: 17px; color: var(--text); }
.ver:hover { background: rgba(var(--glow),.08); }
.ver.on { color: var(--amber2); text-shadow: 0 0 8px rgba(var(--glow),.4); }
.ver .vr { color: var(--amber); width: 12px; }
.ver .vl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ver .vs { color: var(--muted); font-size: 16px; }
.overview { font-size: 18px; color: var(--text); line-height: 1.45; margin-bottom: 12px;
  white-space: pre-wrap; }
.overview.clip { display: -webkit-box; -webkit-line-clamp: 7; -webkit-box-orient: vertical;
  overflow: hidden; }
.ovmore { color: var(--amber); font-size: 17px; cursor: pointer; margin-bottom: 12px;
  display: inline-block; }
.ovmore:hover { text-shadow: 0 0 8px rgba(var(--glow),.6); }
.genres { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 12px; }
.gtag { border: 1px solid var(--line); border-radius: 3px; padding: 1px 9px;
  font-size: 16px; color: var(--muted); }
.dgroup { border: 1px solid var(--line); border-radius: 6px; padding: 10px 14px;
  margin-bottom: 10px; background: var(--panel); }
.dgroup h4 { font-size: 22px; color: var(--amber2); font-weight: 400;
  display: flex; align-items: center; gap: 8px; }
.dgroup h4 small { color: var(--muted); font-size: 17px; }
.dfile { display: flex; align-items: center; gap: 10px; padding: 4px 0 4px 4px;
  font-size: 18px; border-top: 1px solid rgba(58,47,20,.45); }
.dfile input { width: 16px; height: 16px; accent-color: var(--red); cursor: pointer; }
.dfile .fn { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; color: var(--text); }
.dfile .sz { color: var(--muted); width: 90px; text-align: right; }
.dfile .kp { color: var(--green); font-size: 16px; width: 74px; }
.dfile.del .fn { color: var(--red); text-decoration: line-through; }
.dbar { position: sticky; top: 0; z-index: 3; background: rgba(8,6,14,.96);
  border: 1px solid var(--line); border-radius: 6px; padding: 10px 14px;
  margin-bottom: 14px; display: flex; gap: 10px; align-items: center;
  flex-wrap: wrap; }
.dbar .grow { flex: 1; color: var(--muted); font-size: 18px; }
button.danger { background: var(--red); border: none; color: #12030a; font: inherit;
  font-size: 19px; padding: 7px 20px; border-radius: 4px; cursor: pointer; }
button.danger:hover { box-shadow: 0 0 16px rgba(255,47,74,.6); }
button.danger:disabled { background: var(--line); color: var(--muted); cursor: default;
  box-shadow: none; }
.meta { width: 100%; border-collapse: collapse; }
.meta td { padding: 5px 0; font-size: 17px; border-bottom: 1px solid rgba(58,47,20,.5);
  vertical-align: top; }
.meta td:first-child { color: var(--muted); width: 40%; }
.meta td:last-child { color: var(--text); word-break: break-all; }
/* ---- other tabs ---- */
#pages { flex: 1; overflow-y: auto; padding: 18px 22px 60px; display: none; }
#pages .inner2 { max-width: 1020px; margin: 0 auto; }
.card { border: 1px solid var(--line); border-radius: 6px; padding: 16px 20px;
  margin: 14px 0; background: var(--panel); }
.card:hover { border-color: var(--dim); }
.card .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.card .head .nm { font-size: 24px; color: var(--amber2); }
.pill { border-radius: 3px; padding: 2px 12px 2px 10px; font-size: 17px;
  border: 1px solid; display: inline-flex; align-items: center; gap: 7px; }
.pill::before { content: ""; width: 8px; height: 8px; border-radius: 50%;
  background: currentColor; flex-shrink: 0;
  box-shadow: 0 0 7px currentColor, 0 0 2px #fff inset; }
.pill.ok { border-color: rgba(0,255,136,.5); color: var(--green);
  background: rgba(0,255,136,.06); text-shadow: 0 0 8px rgba(0,255,136,.4); }
.pill.bad { border-color: rgba(255,47,74,.5); color: var(--red);
  background: rgba(255,47,74,.06); text-shadow: 0 0 8px rgba(255,47,74,.4); }
.pill.bad::before { animation: ledblink 1.8s ease-in-out infinite; }
@keyframes ledblink { 0%,100% { opacity: 1; } 55% { opacity: .25; } }
.pill.dlp { border-color: rgba(var(--glow),.5); color: var(--amber);
  background: rgba(var(--glow),.07);
  text-shadow: 0 0 8px rgba(var(--glow),.5); animation: pulse 1s steps(2) infinite; }
.dim { color: var(--muted); font-size: 18px; margin-top: 8px; line-height: 1.5; word-break: break-all; }
.dim b { color: var(--text); font-weight: 400; }
.led { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 7px; vertical-align: 1px; }
.led.ok { background: var(--green); box-shadow: 0 0 7px var(--green); }
.led.bad { background: var(--red); box-shadow: 0 0 7px var(--red);
  animation: ledblink 1.8s ease-in-out infinite; }
.err { color: var(--red); font-size: 17px; margin-left: 10px; }
.fields { display: flex; gap: 12px; margin-top: 12px; flex-wrap: wrap; align-items: center; }
.flabel { color: var(--dim); font-size: 17px; width: 100%; margin: 8px 0 -8px; }
input.cfg { background: rgba(0,0,0,.45); border: 1px solid var(--line); border-radius: 4px;
  padding: 7px 12px; font: inherit; font-size: 18px; color: var(--amber2);
  flex: 1; min-width: 220px; outline: none; caret-color: var(--amber); }
input.cfg::placeholder { color: var(--muted); }
input.cfg:focus { border-color: var(--amber); box-shadow: 0 0 10px rgba(var(--glow),.3); }
button.txt { background: none; border: 1px solid transparent; color: var(--amber);
  font: inherit; font-size: 19px; padding: 6px 14px; border-radius: 4px; cursor: pointer; }
button.txt:hover { border-color: var(--amber); box-shadow: 0 0 10px rgba(var(--glow),.35); }
button.filled { background: var(--amber); border: none; color: #08060e; font: inherit;
  font-size: 19px; padding: 7px 22px; border-radius: 4px; cursor: pointer; }
button.filled:hover { background: var(--amber2); box-shadow: 0 0 16px rgba(var(--glow),.6); }
button.outlined { background: rgba(0,0,0,.3); border: 1px solid var(--amber);
  color: var(--amber); font: inherit; font-size: 19px; padding: 6px 22px;
  border-radius: 4px; cursor: pointer; }
button.outlined:hover { box-shadow: 0 0 12px rgba(var(--glow),.4); }
.empty { text-align: center; padding: 70px 20px; color: var(--muted); line-height: 2;
  background: rgba(0,0,0,.25); border: 1px dashed var(--line); border-radius: 6px; font-size: 20px; }
.empty .big { font-family: 'Press Start 2P', monospace; font-size: 15px; color: var(--amber);
  margin-bottom: 18px; text-shadow: 0 0 12px rgba(var(--glow),.6);
  animation: pulse 1.4s steps(2) infinite; }
.empty code { background: var(--panel2); border-radius: 3px; padding: 2px 8px;
  color: var(--amber2); font-family: inherit; }
.howto { color: var(--muted); font-size: 19px; line-height: 1.8; }
.howto code { background: rgba(0,0,0,.4); border-radius: 3px; padding: 1px 7px;
  font-size: 18px; color: var(--amber2); font-family: inherit; }
.howto h3 { color: var(--amber); font-size: 20px; font-weight: 400; margin: 14px 0 4px;
  text-shadow: 0 0 8px rgba(var(--glow),.4); }
#snack { position: fixed; bottom: 22px; left: 22px; background: rgba(4,3,7,.95);
  color: var(--amber); border: 1px solid var(--amber); border-radius: 4px;
  padding: 10px 22px; font-size: 20px; display: none; z-index: 160;
  box-shadow: 0 0 18px rgba(var(--glow),.35); max-width: 70vw;
  text-shadow: 0 0 8px rgba(var(--glow),.5); }
#gphint { position: fixed; bottom: 0; left: 0; right: 0; text-align: center;
  padding: 5px 10px; background: rgba(4,3,7,.92); border-top: 1px solid var(--line);
  color: var(--muted); font-size: 16px; z-index: 96; display: none; }
#gphint .kb { border: 1px solid var(--dim); border-radius: 3px; padding: 0 7px;
  color: var(--amber); margin: 0 3px 0 10px; }
.slogo { display: inline-flex; margin-right: 7px; flex-shrink: 0; }
.slogo svg { width: 100%; height: 100%; }
@media (max-width: 1100px) { #details { display: none; } }
@media (max-width: 820px) { #sidebar { display: none; } }
</style>
</head>
<body>
<div id="gridfloor"></div>
<div id="stars"></div>
<header>
  <div class="bar">
    <div class="logo" onclick="setTab('games')"><pre>  _______    _______  ___________  _______     ______    ________  __    __    _______  ___       _______
 /"      \  /"     "|("     _   ")/"      \   /    " \  /"       )/" |  | "\  /"     "||"  |     /"     "|
|:        |(: ______) )__/  \\__/|:        | // ____  \(:   \___/(:  (__)  :)(: ______)||  |    (: ______)
|_____/   ) \/    |      \\_ /   |_____/   )/  /    ) :)\___  \   \/      \/  \/    |  |:  |     \/    |
 //      /  // ___)_     |.  |    //      /(: (____/ //  __/  \\  //  __  \\  // ___)_  \  |___  // ___)
|:  __   \ (:      "|    \:  |   |:  __   \ \        /  /" \   :)(:  (  )  :)(:      "|( \_|:  \(:  (
|__|  \___) \_______)     \__|   |__|  \___) \"_____/  (_______/  \__|  |__/  \_______) \_______)\__/      </pre></div>
    <div class="searchwrap">
      <input id="search" placeholder="search games..." oninput="onSearch()" autocomplete="off">
    </div>
    <span id="count"></span>
    <span id="padbadge" title="No controller — press any button on the pad">
      <svg viewBox="0 0 24 24"><path d="M7 6h10c2.8 0 5 2.6 5 5.8 0 2.9-1.4 5.2-3.2 5.2-1 0-1.8-.6-2.6-1.6L15 14H9l-1.2 1.4C7 16.4 6.2 17 5.2 17 3.4 17 2 14.7 2 11.8 2 8.6 4.2 6 7 6zm-1 3v1.5H4.5V12H6v1.5h1.5V12H9v-1.5H7.5V9zm10.5.5a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2zm-2.3 2.3a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2z"/></svg>
    </span>
  </div>
  <div class="tabs">
    <button id="tab-games" class="on" onclick="setTab('games')">GAMES</button>
    <button id="tab-systems" onclick="setTab('systems')">SYSTEMS</button>
    <button id="tab-dupes" onclick="setTab('dupes')">DUPES</button>
    <button id="tab-settings" onclick="setTab('settings')">SETTINGS</button>
  </div>
  <div class="pulseline"></div>
  <div id="termline">&gt; <span id="termtext"></span></div>
</header>
<div id="shell">
  <aside id="sidebar"></aside>
  <div id="centre">
    <div id="toolbar">
      <button class="tbtn" id="v-grid" onclick="setView('grid')">▦ GRID</button>
      <button class="tbtn" id="v-list" onclick="setView('list')">☰ LIST</button>
      <button class="tbtn" onclick="cycleSort()" id="sortbtn">SORT: NAME</button>
      <select class="tsel" id="fregion" onchange="setFilter('region',this.value)"></select>
      <select class="tsel" id="flang" onchange="setFilter('lang',this.value)"></select>
      <button class="tbtn" id="fclear" onclick="clearFilters()"
        style="display:none">CLEAR</button>
      <button class="tbtn" onclick="snack('RESCANNING...');refresh(true)">⟳ RESCAN</button>
      <span class="sp" id="shown"></span>
    </div>
    <div id="view"></div>
  </div>
  <aside id="details"></aside>
</div>
<div id="pages"><div class="inner2" id="pagebody"></div></div>
<div id="gphint"><span class="kb">◀▲▼▶</span> move <span class="kb">A</span> play
  <span class="kb">X</span> favourite <span class="kb">LB RB</span> tab
  <span class="kb">Y</span> rescan</div>
<div id="snack"></div>
<div id="loader"><div class="inner">
  <div class="pulseline" style="margin-bottom:26px"></div>
  <div class="t1">NOW LOADING</div>
  <div class="t2" id="loadname"></div>
  <div class="barwrap"><div class="bar"></div></div>
  <div class="pulseline" style="margin-top:26px"></div>
</div></div>
<div id="crt"></div>
<div id="boot"><pre id="boottext"></pre></div>
<script>
let state = null;
let tab = 'games';
let sel = 'all';
let view = localStorage.getItem('rs_view') || 'grid';
let sortMode = localStorage.getItem('rs_sort') || 'name';
let fRegion = localStorage.getItem('rs_fregion') || '';
let fLang = localStorage.getItem('rs_flang') || '';
let shown = 300;
let curGame = null;
let curList = [];
let curVersion = null;

const META = {
  nes:['#e60012','NES','cart'], snes:['#7b5aa6','SNES','cart'],
  n64:['#009e60','N64','cart'], gb:['#8b956d','GB','hand'],
  gba:['#5c67c6','GBA','hand'], nds:['#7f8ea3','DS','hand'],
  gamecube:['#6a5fc1','GC','disc'], wii:['#3aa6dd','WII','disc'],
  genesis:['#0060a8','MD','cart'], dreamcast:['#f0762f','DC','disc'],
  ps1:['#4f5bd5','PS1','disc'], ps2:['#2a3b8f','PS2','disc'],
  psp:['#8a8f98','PSP','hand'], arcade:['#d81b60','ARC','arc'],
  atari2600:['#b7410e','2600','cart'], c64:['#a97142','C64','comp'],
  amiga:['#d33f49','AMIGA','comp'], '3do':['#b9312c','3DO','disc']
};
const ICONS = {
  cart: c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M4 3h16v10h-3v5H7v-5H4z"/><rect x="7.5" y="6" width="9" height="3.5" fill="rgba(0,0,0,.45)"/></svg>`,
  disc: c => `<svg viewBox="0 0 24 24" fill="${c}"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.6" fill="rgba(0,0,0,.6)"/></svg>`,
  hand: c => `<svg viewBox="0 0 24 24" fill="${c}"><rect x="6" y="2" width="12" height="20" rx="2"/><rect x="8" y="4.5" width="8" height="7" fill="rgba(0,0,0,.45)"/><circle cx="10" cy="16.5" r="1.7" fill="rgba(0,0,0,.45)"/><circle cx="14.5" cy="17.5" r="1.3" fill="rgba(0,0,0,.45)"/></svg>`,
  arc:  c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M5 2h14v7l-2 3v10H7V12L5 9z"/><rect x="8" y="4.5" width="8" height="4" fill="rgba(0,0,0,.45)"/><circle cx="10" cy="16" r="1.2" fill="rgba(0,0,0,.45)"/><rect x="13" y="15.2" width="4" height="1.6" fill="rgba(0,0,0,.45)"/></svg>`,
  comp: c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M3 8h18v7l1.5 5H1.5L3 15z"/><rect x="4.5" y="16.5" width="15" height="1.8" fill="rgba(0,0,0,.45)"/><rect x="5" y="9.8" width="14" height="3.4" fill="rgba(0,0,0,.3)"/></svg>`
};
/* rating colours run red -> green so a score reads at a glance */
const RATING_COLORS = {1:'#ff3b30', 2:'#ff8c1a', 3:'#ffd426', 4:'#9ede0a', 5:'#00ff88'};
function ratingColor(n){ return RATING_COLORS[n] || 'var(--muted)'; }

function sysColor(id){ return (META[id]||['#9a8a5c'])[0]; }
function sysLabel(id){ return (META[id]||[0,id.toUpperCase()])[1]; }
function sysLogo(id,size){ const m=META[id]; if(!m) return '';
  return `<span class="slogo" style="width:${size}px;height:${size}px">${ICONS[m[2]](m[0])}</span>`; }
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

/* starfield */
(function stars(){
  const host = document.getElementById('stars');
  const mk = (n,size,dur,color) => {
    const d = document.createElement('div'); d.className='starlayer';
    const sh=[];
    for(let i=0;i<n;i++) sh.push((Math.random()*100).toFixed(1)+'vw '+
      (Math.random()*100).toFixed(1)+'vh 0 '+color);
    d.style.boxShadow=sh.join(','); d.style.width=d.style.height=size+'px';
    d.style.animationDuration=dur+'s'; d.style.animationDelay=(-Math.random()*dur)+'s';
    host.appendChild(d);
  };
  mk(50,2,4.6,'rgba(120,110,170,.45)'); mk(24,3,3.2,'rgba(255,31,111,.4)');
  mk(14,2,5.5,'rgba(0,224,255,.35)');
})();

let _termT=null;
function typeTerm(text){
  const el=document.getElementById('termtext'); clearInterval(_termT);
  let i=0; _termT=setInterval(()=>{ i+=2; el.textContent=text.slice(0,i);
    if(i>=text.length) clearInterval(_termT); },14);
}
let _termInit=false;

const BOOT='RETROSHELF BIOS v5.0\nMEMORY TEST: 640K OK\nCRT DRIVER ........ OK\nSCANNING GAME LIBRARY ...\n\nREADY.';
(function boot(){
  const el=document.getElementById('boottext'), box=document.getElementById('boot');
  let i=0; const t=setInterval(()=>{ i+=3; el.textContent=BOOT.slice(0,i);
    if(i>=BOOT.length){ clearInterval(t); el.textContent=BOOT;
      setTimeout(()=>box.classList.add('off'),300); setTimeout(()=>box.remove(),900);} },16);
})();

async function refresh(rescan){
  if(rescan) typeTerm('LOAD "*",8,1  SEARCHING FOR GAMES...');
  state = await (await fetch('/api/state'+(rescan?'?rescan=1':''))).json();
  render();
  if(!_termInit||rescan){ _termInit=true;
    typeTerm('LOAD "*",8,1  SEARCHING... '+allGames().length+' GAMES FOUND. READY.'); }
}

/* watch for games added to the library while the app is open */
let _libVer=null, _libCount=null;
setInterval(async () => {
  try{
    const s = await (await fetch('/api/state')).json();
    const n = s.systems.reduce((a,x)=>a+x.games.length,0);
    if(_libVer===null){ _libVer=s.version; _libCount=n; return; }
    if(s.version!==_libVer || n!==_libCount){
      const added = n - _libCount;
      _libVer=s.version; _libCount=n;
      state=s; render();
      if(added>0){
        snack(added+(added===1?' NEW GAME FOUND':' NEW GAMES FOUND'));
        typeTerm('LOAD "*",8,1  '+added+' NEW GAME'+(added===1?'':'S')+' ADDED. READY.');
      } else if(added<0){ snack(Math.abs(added)+' GAMES REMOVED'); }
    }
  }catch(e){}
}, 15000);

function setTab(t){
  tab=t;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));
  document.getElementById('tab-'+t).classList.add('on');
  document.getElementById('shell').style.display = t==='games'?'flex':'none';
  document.getElementById('pages').style.display = t==='games'?'none':'block';
  render();
  if(!state) return;
  if(t==='games') typeTerm('LOAD "GAMES",8,1: '+allGames().length+' FOUND. READY.');
  else if(t==='systems') typeTerm('SYS 49152: '+state.systems.filter(s=>s.emu_found).length+
    ' OF '+state.systems.length+' EMULATORS READY.');
  else if(t==='dupes') typeTerm('SEARCHING FOR DUPLICATE TITLES...');
  else typeTerm('OPEN 15,8,15,"CONFIG": READY.');
}

function setView(v){ view=v; localStorage.setItem('rs_view',v); shown=300; render(); }
function cycleSort(){
  const modes=['name','rating','recent','plays','system'];
  sortMode=modes[(modes.indexOf(sortMode)+1)%modes.length];
  localStorage.setItem('rs_sort',sortMode); render();
}
function onSearch(){ shown=300; render(); }
const LANG_NAMES={En:'English',Fr:'French',De:'German',Es:'Spanish',It:'Italian',
  Ja:'Japanese',Nl:'Dutch',Pt:'Portuguese',Sv:'Swedish',No:'Norwegian',Da:'Danish',
  Fi:'Finnish',Zh:'Chinese',Ko:'Korean',Ru:'Russian',Pl:'Polish',Cs:'Czech',
  Hu:'Hungarian',El:'Greek',Tr:'Turkish'};
function setFilter(kind,val){
  if(kind==='region') fRegion=val; else fLang=val;
  localStorage.setItem('rs_f'+kind,val);
  shown=300; render();
}
function clearFilters(){
  fRegion=''; fLang='';
  localStorage.removeItem('rs_fregion'); localStorage.removeItem('rs_flang');
  shown=300; render();
}
function buildFilterMenus(pool){
  // options come from what is actually in the library, with counts
  const rc={}, lc={};
  for(const g of pool){
    for(const r of (g.regions&&g.regions.length?g.regions:['?'])) rc[r]=(rc[r]||0)+1;
    for(const l of (g.langs&&g.langs.length?g.langs:[])) lc[l]=(lc[l]||0)+1;
  }
  const rsel=document.getElementById('fregion'), lsel=document.getElementById('flang');
  const regions=Object.keys(rc).filter(r=>r!=='?').sort((a,b)=>rc[b]-rc[a]);
  rsel.innerHTML=`<option value="">REGION: ANY</option>`
    + regions.map(r=>`<option value="${esc(r)}"${fRegion===r?' selected':''}>${esc(r)} (${rc[r]})</option>`).join('')
    + (rc['?']?`<option value="?"${fRegion==='?'?' selected':''}>Untagged (${rc['?']})</option>`:'');
  const langs=Object.keys(lc).sort((a,b)=>lc[b]-lc[a]);
  lsel.style.display=langs.length?'':'none';
  lsel.innerHTML=`<option value="">LANGUAGE: ANY</option>`
    + langs.map(l=>`<option value="${esc(l)}"${fLang===l?' selected':''}>${esc(LANG_NAMES[l]||l)} (${lc[l]})</option>`).join('');
  rsel.classList.toggle('on',!!fRegion);
  lsel.classList.toggle('on',!!fLang);
  document.getElementById('fclear').style.display=(fRegion||fLang)?'':'none';
}

function snack(msg){
  const t=document.getElementById('snack'); t.textContent='> '+msg;
  t.style.display='block'; clearTimeout(t._h);
  t._h=setTimeout(()=>t.style.display='none',3200);
}
function showLoader(name){
  const l=document.getElementById('loader');
  document.getElementById('loadname').textContent=name;
  l.classList.remove('go'); void l.offsetWidth; l.style.display='block'; l.classList.add('go');
}
function hideLoader(){ document.getElementById('loader').style.display='none'; }

function allGames(){
  const out=[];
  for(const s of state.systems) for(const g of s.games)
    out.push({...g, sysId:s.id, sysName:s.name});
  return out;
}
function ago(ts){
  if(!ts) return '';
  const d=Math.floor(Date.now()/1000-ts);
  if(d<3600) return 'just now';
  if(d<86400) return Math.floor(d/3600)+'h ago';
  if(d<86400*30) return Math.floor(d/86400)+'d ago';
  return new Date(ts*1000).toLocaleDateString();
}
function fmtSize(b){
  if(!b) return '-';
  const u=['B','KB','MB','GB']; let i=0; while(b>=1024&&i<3){b/=1024;i++;}
  return b.toFixed(i?1:0)+' '+u[i];
}
function coverUrl(g){ return '/api/art?system='+g.sysId+'&rom='+encodeURIComponent(g.artref||g.file); }
function shotUrl(g){ return '/api/art?system='+g.sysId+'&kind=screen&rom='+encodeURIComponent(g.shotref||g.file); }

function currentGames(){
  let games;
  if(sel==='all') games=allGames();
  else if(sel==='fav') games=allGames().filter(g=>g.fav);
  else if(sel==='recent') games=allGames().filter(g=>g.last).sort((a,b)=>b.last-a.last);
  else { const s=state.systems.find(x=>x.id===sel)||{games:[],name:''};
         games=s.games.map(g=>({...g,sysId:s.id,sysName:s.name})); }
  buildFilterMenus(games);
  if(fRegion) games=games.filter(g=> fRegion==='?'
    ? !(g.regions&&g.regions.length) : (g.regions||[]).includes(fRegion));
  if(fLang) games=games.filter(g=>(g.langs||[]).includes(fLang));
  const q=document.getElementById('search').value.trim().toLowerCase();
  if(q) games=games.filter(g=>g.name.toLowerCase().includes(q));
  if(sel!=='recent'){
    if(sortMode==='name') games.sort((a,b)=>a.name.toLowerCase()<b.name.toLowerCase()?-1:1);
    else if(sortMode==='rating') games.sort((a,b)=>(b.rating||0)-(a.rating||0)
      || (a.name.toLowerCase()<b.name.toLowerCase()?-1:1));
    else if(sortMode==='recent') games.sort((a,b)=>(b.last||0)-(a.last||0));
    else if(sortMode==='plays') games.sort((a,b)=>(b.plays||0)-(a.plays||0));
    else if(sortMode==='system') games.sort((a,b)=>
      (a.sysName+a.name).toLowerCase()<(b.sysName+b.name).toLowerCase()?-1:1);
  }
  return games;
}

function render(){
  if(!state) return;
  if(tab!=='games'){ renderPage(); return; }
  renderSidebar();
  const games=currentGames();
  curList=games;
  document.getElementById('count').textContent =
    games.length+(games.length===1?' game':' games');
  document.getElementById('sortbtn').textContent='SORT: '+sortMode.toUpperCase();
  document.getElementById('v-grid').classList.toggle('on',view==='grid');
  document.getElementById('v-list').classList.toggle('on',view==='list');
  const host=document.getElementById('view');

  if(!games.length){
    document.getElementById('shown').textContent='';
    host.innerHTML=`<div class="empty"><div class="big">INSERT CARTRIDGE</div>
      Nothing here yet. Point RetroShelf at your games folder in
      <b>SETTINGS</b> &mdash; it scans every subfolder automatically.</div>`;
    renderDetails(); return;
  }
  const total=games.length;
  const list=games.slice(0,shown);
  document.getElementById('shown').textContent=
    'showing '+list.length+' of '+total;

  if(view==='grid'){
    host.innerHTML='<div id="grid">'+list.map((g,i)=>{
      const art=g.art?`<img loading="lazy" src="${coverUrl(g)}">`
        :`<span class="ph">${esc(g.name.slice(0,2).toUpperCase())}</span>`;
      const bg=g.art?'':` style="background:linear-gradient(150deg,${sysColor(g.sysId)},#0f0b18)"`;
      return `<div class="tile${curGame&&curGame.file===g.file?' sel':''}" data-i="${i}"
        onclick="pick(${i})" ondblclick="playIdx(${i})">
        <div class="box"${bg}>${art}${g.fav?'<span class="fav">★</span>':''}
          ${g.copies>1?`<span class="copies">${g.copies}</span>`:''}</div>
        <div class="cap">${esc(g.name)}</div>
        <div class="sub">${esc(sysLabel(g.sysId))}${g.rating
          ?` · <span class="tstar" style="color:${ratingColor(g.rating)}">`
            +'★'.repeat(g.rating)+'<span class="temp">'+'★'.repeat(5-g.rating)
            +'</span></span>':''}${g.plays?' · '+g.plays+'▶':''}</div></div>`;
    }).join('')+'</div>'+moreBtn(total);
  } else {
    host.innerHTML=list.map((g,i)=>{
      const art=g.art?`<img loading="lazy" src="${coverUrl(g)}">`
        :`<span class="ph">${esc(g.name.slice(0,2).toUpperCase())}</span>`;
      const bg=g.art?'':` style="background:linear-gradient(150deg,${sysColor(g.sysId)},#0f0b18)"`;
      const shot=g.shot?`<img loading="lazy" src="${shotUrl(g)}">`
        :`<span class="ph2">NO SHOT</span>`;
      const sub=[g.sysName,g.copies>1?g.copies+' versions':null,
        g.plays?g.plays+(g.plays===1?' play':' plays'):null,
        g.last?'played '+ago(g.last):null].filter(Boolean).join(' · ');
      const rstars=g.rating
        ? `<span class="tstar" style="color:${ratingColor(g.rating)};margin-right:8px">`
          +'★'.repeat(g.rating)+'</span>' : '';
      return `<div class="row${curGame&&curGame.file===g.file?' sel':''}" data-i="${i}"
        onclick="pick(${i})" ondblclick="playIdx(${i})">
        <div class="cover"${bg}>${art}</div>
        <div class="shot">${shot}</div>
        <div class="info"><div class="nm">${g.fav?'★ ':''}${esc(g.name)}</div>
          <div class="sub">${rstars}${sysLogo(g.sysId,14)}${esc(sub)}</div></div></div>`;
    }).join('')+moreBtn(total);
  }
  renderDetails();
}
function moreBtn(total){
  return total>shown
    ? `<div style="text-align:center;padding:18px"><button class="outlined"
       onclick="shown+=300;render()">SHOW MORE (${total-shown} LEFT)</button></div>` : '';
}

function renderSidebar(){
  const withGames=state.systems.filter(s=>s.games.length);
  const all=allGames();
  const favs=all.filter(g=>g.fav).length;
  const recents=all.filter(g=>g.last).length;
  let h=`<div class="side-h">LIBRARY</div>`;
  h+=item('all','▦','All Games',all.length);
  h+=item('fav','★','Favourites',favs);
  h+=item('recent','◷','Recently Played',recents);
  h+=`<div class="side-h">PLATFORMS</div>`;
  for(const s of withGames)
    h+=`<div class="side-i${sel===s.id?' on':''}" onclick="pickSys('${s.id}')">
      ${sysLogo(s.id,16)}<span class="lbl">${esc(s.name)}</span>
      <span class="n">${s.games.length}</span></div>`;
  document.getElementById('sidebar').innerHTML=h;
  function item(id,ic,label,n){
    return `<div class="side-i${sel===id?' on':''}" onclick="pickSys('${id}')">
      <span style="width:16px;text-align:center;color:${sel===id?'var(--amber)':'var(--dim)'}">${ic}</span>
      <span class="lbl">${label}</span><span class="n">${n}</span></div>`;
  }
}
function pickSys(id){ sel=id; shown=300; render();
  document.getElementById('view').scrollTop=0; }

function pick(i){
  curGame=curList[i]||null;
  _ovOpen=false;
  curVersion=curGame?curGame.file:null;
  markSel();
  renderDetails();
  loadDetails();
}
function markSel(){
  document.querySelectorAll('.tile,.row').forEach(el=>{
    el.classList.toggle('sel', curGame && curList[+el.dataset.i]
      && curList[+el.dataset.i].file===curGame.file);
  });
}
function playIdx(i){ const g=curList[i]; if(g) launch(g.sysId,g.file,g.name); }
function playCur(){
  if(!curGame) return;
  launch(curGame.sysId, curVersion || curGame.file, curGame.name);
}

let _det={};
let _ovOpen=false;
async function loadDetails(){
  if(!curGame) return;
  const f=curGame.file, g=curGame;
  if(_det[f]) return;
  try{
    _det[f]=await (await fetch('/api/details?rom='+encodeURIComponent(f)
      +'&system='+encodeURIComponent(g.sysId)+'&name='+encodeURIComponent(g.name))).json();
    if(curGame && curGame.file===f) renderDetails();
  }catch(e){}
}
function toggleOv(){ _ovOpen=!_ovOpen; renderDetails(); }

function renderDetails(){
  const el=document.getElementById('details');
  const g=curGame;
  if(!g){ el.innerHTML=`<div class="empty2">Select a game to see its details.</div>`; return; }
  const d=_det[g.file]||{};
  const cover=g.art?`<img class="dcover" src="${coverUrl(g)}">`
    :`<div class="dcover ph3" style="background:linear-gradient(150deg,${sysColor(g.sysId)},#0f0b18)">
       ${esc(g.name.slice(0,2).toUpperCase())}</div>`;
  const shot=g.shot?`<img class="dshot" src="${shotUrl(g)}">`
    :`<div class="dshot-ph">no screenshot yet</div>`;
  const rc=ratingColor(g.rating);
  let stars='';
  for(let i=1;i<=5;i++)
    stars+=(i<=(g.rating||0)
      ? `<b style="color:${rc};text-shadow:0 0 9px ${rc}88" onclick="setRating(${i})">★</b>`
      : `<span onclick="setRating(${i})">☆</span>`);
  const rsrc=g.rating?(g.rsrc==='db'?' <span class="rsrc">from database</span>'
    :' <span class="rsrc">your rating</span>'):'';
  el.innerHTML=`${cover}<div class="dbody">
    <div class="dtitle">${esc(g.name)}</div>
    <div class="dsys">${sysLogo(g.sysId,15)}${esc(g.sysName||'')}</div>
    ${versionHtml(d)}
    <button class="playbig" onclick="playCur()">▶ PLAY</button>
    <div class="drow2">
      <button onclick="toggleFav()">${g.fav?'★ FAVOURITE':'☆ FAVOURITE'}</button>
      <button onclick="openFolder()">📁 FOLDER</button>
    </div>
    <div class="stars">${stars}${rsrc}</div>
    ${shot}
    ${overviewHtml(d)}
    ${genresHtml(d)}
    <table class="meta">
      <tr><td>Platform</td><td>${esc(g.sysName||'')}</td></tr>
      ${(g.regions&&g.regions.length)?`<tr><td>Region</td><td>${esc(g.regions.join(', '))}</td></tr>`:''}
      ${(g.langs&&g.langs.length)?`<tr><td>Languages</td><td>${esc(g.langs.map(l=>LANG_NAMES[l]||l).join(', '))}</td></tr>`:''}
      ${metaRows(d)}
      <tr><td>Play count</td><td>${g.plays||0}</td></tr>
      <tr><td>Last played</td><td>${g.last?esc(ago(g.last)):'never'}</td></tr>
      <tr><td>Rating</td><td>${g.rating
        ?`<span style="color:${rc}">${g.rating} / 5</span>`+(g.rsrc==='db'?' (database)':' (yours)')
        :'not rated'}</td></tr>
      <tr><td>File</td><td>${esc(d.file||'')}</td></tr>
      <tr><td>Size</td><td>${fmtSize(d.size)}</td></tr>
      <tr><td>Folder</td><td>${esc(d.folder||'')}</td></tr>
    </table></div>`;
}

function versionHtml(d){
  const vs=d.versions||[];
  if(vs.length<2) return '';
  const cur=curVersion||(curGame?curGame.file:'');
  return `<div class="verwrap"><div class="verlabel">${vs.length} VERSIONS &mdash; pick one to play</div>`+
    vs.map(v=>`<div class="ver${v.file===cur?' on':''}" onclick="pickVersion(${JSON.stringify(v.file).replace(/"/g,'&quot;')})">
      <span class="vr">${v.file===cur?'●':'○'}</span>
      <span class="vl">${esc(v.label)}</span>
      <span class="vs">${fmtSize(v.size)}</span></div>`).join('')+'</div>';
}
function pickVersion(f){
  curVersion=f;
  renderDetails();
  const v=((_det[curGame.file]||{}).versions||[]).find(x=>x.file===f);
  if(v) snack('VERSION: '+v.label);
}

function overviewHtml(d){
  const m=d.meta;
  if(!m||!m.ov){
    if(d.meta===undefined) return '';
    if(!(state.meta&&state.meta.have&&state.meta.have.length))
      return `<div class="dim" style="margin:0 0 12px">No description &mdash; download the
        games database in <b>SETTINGS</b> to add descriptions for every game.</div>`;
    return `<div class="dim" style="margin:0 0 12px">No description found for this title.</div>`;
  }
  const long=m.ov.length>420;
  return `<div class="overview${long&&!_ovOpen?' clip':''}">${esc(m.ov)}</div>`+
    (long?`<span class="ovmore" onclick="toggleOv()">${_ovOpen?'▲ LESS':'▼ MORE'}</span>`:'');
}
function genresHtml(d){
  const m=d.meta;
  if(!m||!m.gen) return '';
  return '<div class="genres">'+m.gen.split(/[;,]/).map(x=>x.trim()).filter(Boolean)
    .map(x=>`<span class="gtag">${esc(x)}</span>`).join('')+'</div>';
}
function metaRows(d){
  const m=d.meta; if(!m) return '';
  const r=[];
  if(m.yr) r.push(['Released',m.yr]);
  if(m.dev) r.push(['Developer',m.dev]);
  if(m.pub) r.push(['Publisher',m.pub]);
  if(m.pl) r.push(['Players',m.pl]);
  if(m.rt) r.push(['Community',Number(m.rt).toFixed(1)+' / 5']);
  if(m.esrb) r.push(['Rated',m.esrb]);
  return r.map(x=>`<tr><td>${x[0]}</td><td>${esc(x[1])}</td></tr>`).join('');
}

async function toggleFav(){
  if(!curGame) return;
  const nv=!curGame.fav;
  curGame.fav=nv;
  await fetch('/api/meta',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({rom:curGame.file,fav:nv})});
  const g=allGames().find(x=>x.file===curGame.file); if(g) g.fav=nv;
  for(const s of state.systems) for(const x of s.games)
    if(x.file===curGame.file) x.fav=nv;
  snack(nv?'ADDED TO FAVOURITES':'REMOVED FROM FAVOURITES');
  render();
}
async function setRating(n){
  if(!curGame) return;
  const v=(curGame.rating===n)?0:n;
  curGame.rating=v;
  await fetch('/api/meta',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({rom:curGame.file,rating:v})});
  for(const s of state.systems) for(const x of s.games)
    if(x.file===curGame.file) x.rating=v;
  renderDetails();
}
function openFolder(){
  if(!curGame) return;
  const d=_det[curGame.file]||{};
  snack(d.folder||'unknown folder');
}

async function launch(sysId,rom,name){
  typeTerm('RUN "'+name.toUpperCase()+'"');
  showLoader(name);
  const r=await (await fetch('/api/launch',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({system:sysId,rom:rom})})).json();
  if(!r.ok){ hideLoader(); snack('ERROR: '+r.msg);
    if(r.msg.startsWith('no emulator')) setTab('systems'); }
  else { setTimeout(hideLoader,1750); setTimeout(()=>refresh(),900); }
}

/* ---------- systems / settings pages ---------- */
let dupes=null, dupeSel={};
async function loadDupes(force){
  const body=document.getElementById('pagebody');
  if(dupes && !force){ renderDupes(); return; }
  body.innerHTML='<div class="empty"><div class="big">SCANNING...</div>'
    +'looking for titles that appear more than once</div>';
  dupes=(await (await fetch('/api/dupes')).json()).groups;
  renderDupes();
}
function dupeCount(){ return Object.values(dupeSel).filter(Boolean).length; }
function dupeBytes(){
  let b=0;
  for(const g of dupes||[]) for(const f of g.files) if(dupeSel[f.file]) b+=f.size;
  return b;
}
function toggleDupe(f,el){
  dupeSel[f]=!dupeSel[f];
  el.closest('.dfile').classList.toggle('del',dupeSel[f]);
  document.getElementById('dsum').textContent=
    dupeCount()+' selected · '+fmtSize(dupeBytes());
  document.getElementById('dgo').disabled=dupeCount()===0;
}
function selectSuggested(){
  dupeSel={};
  for(const g of dupes||[]) for(const f of g.files) if(!f.keep) dupeSel[f.file]=true;
  renderDupes();
}
function clearDupeSel(){ dupeSel={}; renderDupes(); }
async function runQuarantine(){
  const files=Object.keys(dupeSel).filter(k=>dupeSel[k]);
  if(!files.length) return;
  if(!confirm('Move '+files.length+' file(s) to the quarantine folder?\n\n'
    +'They are NOT deleted - they move to RetroShelf\\quarantine\\ so you can '
    +'put them back or delete them yourself later.')) return;
  const r=await (await fetch('/api/quarantine',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({files:files})})).json();
  snack('MOVED '+r.moved+' FILE(S) TO QUARANTINE'+(r.failed.length?' - '+r.failed.length+' FAILED':''));
  dupeSel={}; await refresh(true); loadDupes(true);
}
function renderDupes(){
  const body=document.getElementById('pagebody');
  document.getElementById('count').textContent=
    (dupes?dupes.length:0)+' duplicate titles';
  if(!dupes || !dupes.length){
    body.innerHTML='<div class="empty"><div class="big">NO DUPLICATES</div>'
      +'Every title in your library appears once.<br>'
      +'Multi-disc sets are never treated as duplicates.</div>';
    return;
  }
  body.innerHTML=`<div class="dbar">
      <button class="outlined" onclick="selectSuggested()">SELECT SUGGESTED</button>
      <button class="outlined" onclick="clearDupeSel()">CLEAR</button>
      <span class="grow" id="dsum">${dupeCount()} selected · ${fmtSize(dupeBytes())}</span>
      <button class="danger" id="dgo" onclick="runQuarantine()"
        ${dupeCount()?'':'disabled'}>MOVE TO QUARANTINE</button>
    </div>
    <div class="dim" style="margin:0 0 14px">Nothing is deleted &mdash; selected files
      move to <b>${esc(state.emulators_root.replace(/emulators$/,'quarantine'))}\\</b>,
      keeping their folder structure, so you can put them back. Suggested keepers
      (marked <span style="color:var(--green)">KEEP</span>) prefer USA/World releases,
      then the largest file. Multi-disc games are excluded.</div>`
    + dupes.map(g=>`<div class="dgroup">
        <h4>${sysLogo(g.system,16)}${esc(g.title)} <small>${esc(g.sysname)} · ${g.files.length} files</small></h4>
        ${g.files.map(f=>`<div class="dfile${dupeSel[f.file]?' del':''}">
          <input type="checkbox" ${dupeSel[f.file]?'checked':''}
            onchange="toggleDupe(${JSON.stringify(f.file).replace(/"/g,'&quot;')},this)">
          <span class="kp">${f.keep?'KEEP':''}</span>
          <span class="fn">${esc(f.name)}</span>
          <span class="sz">${fmtSize(f.size)}</span></div>`).join('')}
      </div>`).join('');
}

function renderPage(){
  const body=document.getElementById('pagebody');
  const count=document.getElementById('count');
  if(tab==='dupes'){ loadDupes(); return; }
  if(tab==='systems'){
    count.textContent=state.systems.filter(s=>s.emu_found).length+' of '+
      state.systems.length+' emulators ready';
    body.innerHTML=state.systems.map(s=>{
      const d=(state.downloads||{})[s.id];
      const busy=d&&['resolving','downloading','extracting'].includes(d.status);
      let status;
      if(s.emu_found) status=`<span class="pill ok">READY</span>`;
      else if(busy){ const lbl=d.status==='downloading'?'DOWNLOADING '+(d.pct||0)+'%'
        :d.status.toUpperCase()+'...'; status=`<span class="pill dlp">${lbl}</span>`; }
      else status=`<span class="pill bad">EMULATOR MISSING</span>`;
      const err=(d&&d.status==='error')?`<span class="err">FAILED: ${esc(d.msg)}</span>`:'';
      const link=`<a href="${s.emu_url}" target="_blank">${esc(s.emu_site)}</a>`;
      let detail;
      if(s.emu_found) detail=`Using <b>${esc(s.emu_path)}</b>`;
      else if(s.dl==='auto') detail=`Needs <b>${esc(s.emu_name)}</b> &mdash; click DOWNLOAD and
        RetroShelf installs it into <b>${esc(s.emu_dir)}\\</b>, or get it from ${link}.`;
      else detail=`Needs <b>${esc(s.emu_name)}</b> &mdash; download from ${link}
        and unzip into <b>${esc(s.emu_dir)}\\</b>.`;
      const note=s.note?`<div class="dim">▲ ${esc(s.note)}</div>`:'';
      const bios=s.bios_needed?`<div class="dim">${s.bios_count
        ?`<span class="led ok"></span><span style="color:var(--green)">BIOS OK</span> &mdash; ${s.bios_count} file${s.bios_count===1?'':'s'} installed from <b>${esc(s.bios_dir)}\\</b>`
        :`<span class="led bad"></span><span style="color:var(--red)">BIOS NEEDED</span> &mdash; put the ${esc(s.bios_hint)} into <b>${esc(s.bios_dir)}\\</b> and RetroShelf installs it for you`}</div>`:'';
      const dlbtn=(!s.emu_found&&!busy&&s.dl==='auto')
        ?`<button class="filled" onclick="download('${s.id}')">DOWNLOAD ${esc(s.emu_name.toUpperCase())}</button>`:'';
      return `<div class="card">
        <div class="head">${sysLogo(s.id,22)}<span class="nm">${esc(s.name)}</span>${status}${err}</div>
        <div class="dim">${detail}<br>Game files: <b>${esc(s.exts.join(' '))}</b>
          &middot; ${s.games.length} found</div>${bios}${note}
        <div class="fields">${dlbtn}
          <input class="cfg" id="ep-${s.id}" placeholder="custom emulator exe path (optional)"
            value="${esc(s.emu_override)}">
          <input class="cfg" id="ar-${s.id}" value="${esc(s.args)}">
          <button class="txt" onclick="saveSystem('${s.id}')">SAVE</button>
        </div></div>`;
    }).join('');
  } else {
    count.textContent='';
    body.innerHTML=`<div class="card">
        <div class="head"><span class="nm">Folders</span></div>
        <div class="flabel">GAMES FOLDER (scanned recursively)</div>
        <div class="fields"><input class="cfg" id="root" value="${esc(state.library_root)}"></div>
        <div class="flabel">EMULATORS FOLDER</div>
        <div class="fields"><input class="cfg" id="emuroot" value="${esc(state.emulators_root)}"></div>
        <div class="flabel">ART FOLDER</div>
        <div class="fields"><input class="cfg" id="artroot" value="${esc(state.art_root)}"></div>
        <div class="fields">
          <button class="filled" onclick="saveSettings()">SAVE &amp; RESCAN</button>
          <button class="outlined" onclick="mkdirs()">CREATE FOLDER LAYOUT</button>
        </div></div>
      <div class="card">
        <div class="head"><span class="nm">Unpacked disc cache</span>
          <span class="pill ${state.cache_size?'ok':'bad'}">${fmtSize(state.cache_size)}</span></div>
        <div class="dim">Zip/7z/rar disc games (PlayStation, PS2, Dreamcast&hellip;) are
          unpacked here once so the emulator can load them; later launches are instant.
          Clearing it just means the next launch unpacks again.</div>
        <div class="fields">
          <button class="outlined" onclick="clearCache()">CLEAR CACHE</button>
        </div></div>
      <div class="card">
        <div class="head"><span class="nm">Game descriptions</span>${metaStatus()}</div>
        <div class="dim">Downloads the LaunchBox Games Database dump (~100 MB, one time)
          and builds a local index, giving every game a description plus developer,
          publisher, genre, release year, player count and community rating in the
          details panel. Re-run any time to refresh.</div>
        <div class="fields">
          <button class="filled" onclick="fetchMeta()">DOWNLOAD GAME DATABASE</button>
        </div></div>
      <div class="card">
        <div class="head"><span class="nm">Star ratings</span>${ratingStatus()}</div>
        <div class="dim">Fills every game's stars from the community rating in the
          games database. Ratings you set yourself are never overwritten.</div>
        <div class="fields">
          <button class="filled" onclick="importRatings(false)">IMPORT RATINGS</button>
          <button class="outlined" onclick="importRatings(true)">RE-IMPORT (OVERWRITE MINE)</button>
        </div></div>
      <div class="card">
        <div class="head"><span class="nm">Online art fetcher</span>${shotsStatus()}</div>
        <div class="dim">Downloads missing screenshots (and any missing covers) from the
          libretro thumbnail library, matched by title. Run it again any time &mdash;
          it only fetches what's missing.</div>
        <div class="fields">
          <button class="filled" onclick="fetchShots()">FETCH SCREENSHOTS &amp; COVERS</button>
        </div></div>
      <div class="card">
        <div class="head"><span class="nm">Cover art matcher</span>${coverStatus()}</div>
        <div class="dim">Matches a local folder of box art to your games by title and
          copies each hit into the art folder.</div>
        <div class="flabel">COVERS SOURCE FOLDER</div>
        <div class="fields">
          <input class="cfg" id="coversdir" value="${esc(state.covers_dir||'')}"
            placeholder="e.g. M:\\oldgames\\covers">
          <button class="filled" onclick="matchCovers()">MATCH COVERS</button>
        </div></div>
      <div class="card howto">
        <h3>HOW IT WORKS</h3>
        RetroShelf scans the games folder recursively and works out each game's system
        from its file extension, with folder names as hints.
        <h3>CONTROLS</h3>
        Click a game to select it, double-click (or the PLAY button) to launch.
        With a controller: d-pad moves, <b>A</b> plays, <b>X</b> favourites,
        <b>LB/RB</b> switch tabs, <b>Y</b> rescans. Xbox, PlayStation and generic
        USB pads work; PS3 pads need the DsHidMini driver.
        <h3>ART</h3>
        Covers: image named like the rom in <code>art\\&lt;system&gt;\\</code>.
        Screenshots: same name in <code>art\\&lt;system&gt;\\screens\\</code>.
      </div>`;
  }
}

function coverStatus(){
  const c=state.covers||{};
  if(!c.status) return '';
  if(c.status==='indexing') return `<span class="pill dlp">INDEXING...</span>`;
  if(c.status==='matching') return `<span class="pill dlp">MATCHING ${c.done||0}/${c.total||0} &middot; ${c.matched||0} FOUND</span>`;
  if(c.status==='error') return `<span class="pill bad">ERROR: ${esc(c.msg||'')}</span>`;
  return `<span class="pill ok">DONE &middot; ${c.matched} MATCHED &middot; ${c.copied} COPIED</span>`;
}
function shotsStatus(){
  const c=state.shots||{};
  if(!c.status) return '';
  if(c.status==='indexing') return `<span class="pill dlp">INDEXING...</span>`;
  if(c.status==='fetching') return `<span class="pill dlp">${esc(c.sys||'')} ${c.done||0}/${c.total||0} &middot; ${c.found||0} FETCHED</span>`;
  if(c.status==='error') return `<span class="pill bad">ERROR: ${esc(c.msg||'')}</span>`;
  return `<span class="pill ok">DONE &middot; ${c.found} IMAGES FETCHED</span>`;
}
function ratingStatus(){
  const r=state.ratings||{};
  if(r.status==='working') return `<span class="pill dlp">RATING ${r.done||0}/${r.total||0} &middot; ${r.set||0} SET</span>`;
  if(r.status==='error') return `<span class="pill bad">ERROR: ${esc(r.msg||'')}</span>`;
  const n=allGames().filter(g=>g.rating).length;
  return n?`<span class="pill ok">${n} GAMES RATED</span>`:`<span class="pill bad">NONE RATED</span>`;
}
let _ratePoll=null;
async function importRatings(overwrite){
  if(overwrite && !confirm('Replace the ratings you set yourself with the database ratings?')) return;
  const r=await (await fetch('/api/importratings',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({overwrite:!!overwrite})})).json();
  snack(r.ok?'IMPORTING RATINGS...':'ERROR: '+r.msg);
  if(r.ok&&!_ratePoll){
    _ratePoll=setInterval(async()=>{
      await refresh();
      const st=(state.ratings||{}).status;
      if(st!=='working'){ clearInterval(_ratePoll); _ratePoll=null;
        snack(st==='done'?'RATINGS IMPORTED: '+state.ratings.set+' GAMES'
          :'RATING ERROR: '+(state.ratings.msg||'')); }
    },1500);
  }
}

function metaStatus(){
  const m=state.meta||{};
  if(m.status==='downloading') return `<span class="pill dlp">DOWNLOADING ${m.pct||0}%</span>`;
  if(m.status==='parsing') return `<span class="pill dlp">PARSING ${(m.games||0).toLocaleString()} GAMES...</span>`;
  if(m.status==='error') return `<span class="pill bad">ERROR: ${esc(m.msg||'')}</span>`;
  if(m.have&&m.have.length) return `<span class="pill ok">INSTALLED &middot; ${m.have.length} PLATFORMS</span>`;
  return `<span class="pill bad">NOT INSTALLED</span>`;
}
let _metaPoll=null;
async function fetchMeta(){
  const r=await (await fetch('/api/fetchmeta',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'})).json();
  snack(r.ok?'DOWNLOADING GAME DATABASE (~100MB)...':'ERROR: '+r.msg);
  if(r.ok&&!_metaPoll){
    _metaPoll=setInterval(async()=>{
      await refresh();
      const st=(state.meta||{}).status;
      if(st!=='downloading'&&st!=='parsing'){ clearInterval(_metaPoll); _metaPoll=null;
        snack(st==='done'?'DESCRIPTIONS READY: '+(state.meta.games||0).toLocaleString()+' GAMES INDEXED'
          :'DATABASE ERROR: '+(state.meta.msg||''));
        _det={}; }
    },2000);
  }
}
let _shotPoll=null;
async function fetchShots(){
  const r=await (await fetch('/api/fetchshots',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'})).json();
  snack(r.ok?'FETCHING ART FROM LIBRETRO...':'ERROR: '+r.msg);
  if(r.ok&&!_shotPoll){
    _shotPoll=setInterval(async()=>{
      await refresh();
      const st=(state.shots||{}).status;
      if(st!=='indexing'&&st!=='fetching'){ clearInterval(_shotPoll); _shotPoll=null;
        snack(st==='done'?'ART FETCH DONE: '+state.shots.found+' IMAGES'
          :'ART FETCH ERROR: '+(state.shots.msg||'')); refresh(true); }
    },1500);
  }
}
let _covPoll=null;
async function matchCovers(){
  const dir=document.getElementById('coversdir').value.trim();
  const r=await (await fetch('/api/matchcovers',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({dir:dir})})).json();
  snack(r.ok?'MATCHING COVERS...':'ERROR: '+r.msg);
  if(r.ok&&!_covPoll){
    _covPoll=setInterval(async()=>{
      await refresh();
      const st=(state.covers||{}).status;
      if(st!=='indexing'&&st!=='matching'){ clearInterval(_covPoll); _covPoll=null;
        snack(st==='done'?'COVERS: '+state.covers.matched+' MATCHED':'COVERS ERROR');
        refresh(true); }
    },1500);
  }
}
let _poll=null;
function pollDownloads(){
  if(_poll) return;
  _poll=setInterval(async()=>{
    await refresh();
    const act=Object.values(state.downloads||{})
      .some(d=>['resolving','downloading','extracting'].includes(d.status));
    if(!act){ clearInterval(_poll); _poll=null; refresh(); }
  },1200);
}
async function download(id){
  const r=await (await fetch('/api/download',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})).json();
  snack(r.ok?'DOWNLOADING '+sysLabel(id)+' EMULATOR':'ERROR: '+r.msg);
  if(r.ok) pollDownloads();
}
async function saveSystem(id){
  await fetch('/api/system',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id,emu_path:document.getElementById('ep-'+id).value,
      args:document.getElementById('ar-'+id).value})});
  snack('SAVED'); refresh();
}
async function saveSettings(){
  await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({library_root:document.getElementById('root').value,
      emulators_root:document.getElementById('emuroot').value,
      art_root:document.getElementById('artroot').value})});
  snack('SAVED - RESCANNING...'); refresh(true);
}
async function clearCache(){
  const r=await (await fetch('/api/clearcache',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'})).json();
  snack(r.ok?'CACHE CLEARED':r.msg); refresh();
}
async function mkdirs(){
  const r=await (await fetch('/api/mkdirs',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'})).json();
  snack(r.ok?'FOLDERS CREATED':r.msg); refresh();
}

/* ---------- keyboard ---------- */
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT') return;
  if(tab!=='games') return;
  const cols=gridCols();
  let i=curGame?curList.findIndex(g=>g.file===curGame.file):-1;
  if(e.key==='ArrowRight'){ move(1); e.preventDefault(); }
  else if(e.key==='ArrowLeft'){ move(-1); e.preventDefault(); }
  else if(e.key==='ArrowDown'){ move(view==='grid'?cols:1); e.preventDefault(); }
  else if(e.key==='ArrowUp'){ move(view==='grid'?-cols:-1); e.preventDefault(); }
  else if(e.key==='Enter'){ if(i>=0) playIdx(i); }
  else if(e.key==='f'||e.key==='F'){ toggleFav(); }
});
function gridCols(){
  const g=document.getElementById('grid');
  if(!g) return 1;
  return Math.max(1,Math.round(g.clientWidth/(g.firstElementChild?
    g.firstElementChild.offsetWidth+16:164)));
}
function move(d){
  if(!curList.length) return;
  let i=curGame?curList.findIndex(g=>g.file===curGame.file):-1;
  i=Math.max(0,Math.min(curList.length-1,(i<0?0:i+d)));
  if(i>=shown-1&&shown<curList.length){ shown+=300; render(); }
  pick(i);
  const el=document.querySelector('.tile[data-i="'+i+'"],.row[data-i="'+i+'"]');
  if(el) el.scrollIntoView({block:'nearest'});
}

/* ---------- controller ---------- */
const gpState={last:{},heldDir:null,lastMove:0,fast:false,known:{}};
const TABS=['games','systems','dupes','settings'];
function padName(id){
  const s=(id||'').toLowerCase();
  if(s.includes('xbox')||s.includes('xinput')) return 'Xbox controller';
  if(s.includes('054c')||s.includes('sony')||s.includes('dualshock')||
     s.includes('dualsense')||s.includes('playstation')) return 'PlayStation controller';
  return 'Controller';
}
function activePad(){
  const gps=navigator.getGamepads?navigator.getGamepads():[];
  for(const g of gps) if(g&&g.connected) return g;
  return null;
}
function updatePadBadge(gp){
  const b=document.getElementById('padbadge'), hint=document.getElementById('gphint');
  if(gp){ b.classList.add('on'); b.title=padName(gp.id)+' — '+gp.id; hint.style.display='block'; }
  else { b.classList.remove('on'); b.title='No controller'; hint.style.display='none'; }
}
window.addEventListener('gamepadconnected',e=>{
  gpState.known[e.gamepad.index]=true; updatePadBadge(e.gamepad);
  snack(padName(e.gamepad.id).toUpperCase()+' CONNECTED');
});
window.addEventListener('gamepaddisconnected',e=>{
  delete gpState.known[e.gamepad.index];
  const gp=activePad(); updatePadBadge(gp);
  if(!gp) snack('CONTROLLER DISCONNECTED');
});
function cycleTab(d){ setTab(TABS[(TABS.indexOf(tab)+d+TABS.length)%TABS.length]); }
function pollPads(){
  requestAnimationFrame(pollPads);
  const gp=activePad(); if(!gp) return;
  if(!gpState.known[gp.index]){ gpState.known[gp.index]=true; updatePadBadge(gp); }
  const now=performance.now();
  const btn=i=>!!(gp.buttons[i]&&gp.buttons[i].pressed);
  const ax=i=>gp.axes[i]||0;
  const pressed={};
  for(const i of [0,1,2,3,4,5]){ pressed[i]=btn(i)&&!gpState.last[i]; gpState.last[i]=btn(i); }
  let dy=0,dx=0;
  if(btn(12)||ax(1)<-0.5) dy=-1; else if(btn(13)||ax(1)>0.5) dy=1;
  if(btn(14)||ax(0)<-0.5) dx=-1; else if(btn(15)||ax(0)>0.5) dx=1;
  if((dy||dx)&&tab==='games'){
    const dir=dx+','+dy, first=gpState.heldDir!==dir;
    if(first||now-gpState.lastMove>(gpState.fast?90:320)){
      if(!first) gpState.fast=true;
      gpState.heldDir=dir; gpState.lastMove=now;
      const cols=view==='grid'?gridCols():1;
      if(dy) move(dy*(view==='grid'?cols:1));
      if(dx) move(dx);
    }
  } else if(!dy&&!dx){ gpState.heldDir=null; gpState.fast=false; }
  if(pressed[0]&&tab==='games'){
    const i=curGame?curList.findIndex(g=>g.file===curGame.file):-1;
    if(i>=0) playIdx(i);
  }
  if(pressed[2]) toggleFav();
  if(pressed[3]){ snack('RESCANNING...'); refresh(true); }
  if(pressed[4]) cycleTab(-1);
  if(pressed[5]) cycleTab(1);
}
pollPads();
refresh();
</script>
</body>
</html>
"""


def _arg(name, default=None):
    """Read --name value or --name=value from the command line."""
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def main():
    host = _arg("--host", "127.0.0.1")
    port = int(_arg("--port", PORT))
    # --serve runs headless (no window), for a homelab box driven from elsewhere
    headless = "--serve" in sys.argv or "--no-browser" in sys.argv
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    server = None
    try:
        server = Server((host, port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=_watch_worker, daemon=True).start()
        print(f"RetroShelf running at {url}")
        if host not in ("127.0.0.1", "localhost"):
            print(f"Listening on {host}:{port} - reachable from the network. "
                  "Anyone who can reach it can launch games on this machine.")
    except OSError:
        pass    # already running — just open another window/tab on it

    if headless:        # server only: dev, or a homelab host
        if not server:
            return
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            return

    # native app window (Edge WebView2); --browser falls back to the old tab
    if "--browser" not in sys.argv:
        try:
            import webview
            webview.create_window(
                "RetroShelf", url, width=1280, height=840,
                min_size=(900, 600), background_color="#05040a")
            webview.start()
            return
        except Exception:
            pass    # no WebView2 runtime — fall through to browser

    webbrowser.open(url)
    if server:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
