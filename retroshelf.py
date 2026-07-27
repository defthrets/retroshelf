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
import json
import mimetypes
import os
import re
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
            "asset": r"^duckstation-windows-x64-release\.zip$"}},
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
    ".zip": ["arcade", "nes", "snes", "n64", "gb", "gba", "genesis", "atari2600"],
    ".7z": ["arcade"],
    ".iso": ["ps2", "psp", "gamecube", "wii"],
    ".bin": ["genesis", "atari2600", "ps1", "c64"],
    ".chd": ["ps1", "ps2", "dreamcast", "psp"],
    ".cue": ["ps1", "dreamcast"],
    ".img": ["ps1"],
    ".rar": ["amiga"],
}
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
    return {
        "library_root": "C:\\RetroShelf\\games",
        "emulators_root": "C:\\RetroShelf\\emulators",
        "art_root": "C:\\RetroShelf\\art",
        "covers_dir": "",
        "overrides": {}, "stats": {},
    }


def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = default_config()
    else:
        cfg = default_config()
    for key, val in default_config().items():
        cfg.setdefault(key, val)
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def find_emulator(sysdef, cfg):
    """Return Path to the emulator exe, or None."""
    override = cfg["overrides"].get(sysdef["id"], {}).get("emu_path", "")
    if override:
        p = Path(override)
        return p if p.is_file() else None
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
    for h in path_hints(parts):
        if h in cands:
            return h
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

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not skip_dir(d)]
        if any(skip_dir(part) for part in Path(dirpath).parts):
            continue
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
                "art": has_art(sysid, dp, stem, "cover"),
                "shot": has_art(sysid, dp, stem, "screen"),
            })
    for lst in games.values():
        lst.sort(key=lambda g: g["name"].lower())
    return games


_scan_cache = {"key": None, "time": 0.0, "data": None}
SCAN_TTL = 120


def get_games(cfg, force=False):
    key = (cfg["library_root"], cfg["art_root"])
    now = time.time()
    if (not force and _scan_cache["key"] == key
            and now - _scan_cache["time"] < SCAN_TTL):
        return _scan_cache["data"]
    data = scan_all(cfg)
    _scan_cache.update(key=key, time=now, data=data)
    return data


def find_art(sysdef, rom_path, cfg, kind="cover"):
    stem = rom_path.stem
    if kind == "screen":
        dirs = [rom_path.parent / "screens", rom_path.parent / "screenshots",
                Path(cfg["art_root"]) / sysdef["id"] / "screens"]
    else:
        dirs = [rom_path.parent, rom_path.parent / "art",
                rom_path.parent / "covers", Path(cfg["art_root"]) / sysdef["id"]]
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


def _resolve_dl_url(spec):
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


def _download_worker(sysdef, emulators_root):
    sysid = sysdef["id"]
    try:
        _set_dl(sysid, status="resolving", pct=0, msg="")
        url = _resolve_dl_url(sysdef["dl"])
        dest = Path(emulators_root) / sysid
        dest.mkdir(parents=True, exist_ok=True)
        tmp = dest / "_retroshelf_download.zip"
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
        droot = dest.resolve()
        with zipfile.ZipFile(tmp) as z:
            for m in z.infolist():
                tgt = (dest / m.filename).resolve()
                if tgt == droot or droot in tgt.parents:
                    z.extract(m, dest)
        tmp.unlink(missing_ok=True)
        _set_dl(sysid, status="done", msg="installed")
    except Exception as e:  # report any failure to the UI
        _set_dl(sysid, status="error", msg=str(e))


def start_download(sysid, cfg):
    sysdef = SYSTEMS_BY_ID.get(sysid)
    if not sysdef or not sysdef.get("dl"):
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
    exact, nospace, byletter = {}, {}, {}
    for href in re.findall(r'href="([^"]+\.png)"', html):
        fname = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        key = norm_title(fname[:-4])
        if not key:
            continue
        if key not in exact:
            exact[key] = fname
            nospace.setdefault(key.replace(" ", ""), fname)
            byletter.setdefault(key[:1], []).append(key)
    return exact, nospace, byletter


def _libretro_match(key, index):
    exact, nospace, byletter = index
    hit = exact.get(key) or nospace.get(key.replace(" ", ""))
    if not hit:
        close = difflib.get_close_matches(key, byletter.get(key[:1], []),
                                          n=1, cutoff=0.87)
        if close:
            hit = exact[close[0]]
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
        _scan_cache["time"] = 0
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
        with zipfile.ZipFile(zpath) as z, z.open("Metadata.xml") as f:
            for _ev, el in ET.iterparse(f, events=("end",)):
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
                        if any(rec.values()):
                            out[sysid][key] = rec
                            kept += 1
                            if kept % 2000 == 0:
                                _set_meta(games=kept)
                el.clear()
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
        entry = (idx, nospace, toks, list(idx.keys()))
        with _meta_lock:
            _meta_index[sysid] = entry
    idx, nospace, toks, keys = entry
    if not idx:
        return None
    key = norm_title(name)
    if key in idx:
        return idx[key]
    for alt in (nospace.get(key.replace(" ", "")), toks.get(_tokkey(key))):
        if alt:
            return idx[alt]
    close = difflib.get_close_matches(key, keys, n=1, cutoff=0.9)
    return idx[close[0]] if close else None


def meta_have():
    return sorted(p.stem for p in _meta_dir().glob("*.json"))


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


def extract_archive(archive, dest):
    exe = find_7z()
    if not exe:
        raise RuntimeError("7-Zip not found - install it from 7-zip.org")
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "x", "-y", "-o" + str(dest), str(archive)],
                       capture_output=True, creationflags=_NOWIN)
    if r.returncode != 0:
        raise RuntimeError("extract failed: "
                           + r.stderr.decode(errors="replace")[:200])


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

def build_state(cfg, rescan=False):
    games = get_games(cfg, force=rescan)
    systems = []
    for sysdef in SYSTEMS:
        emu = find_emulator(sysdef, cfg)
        override = cfg["overrides"].get(sysdef["id"], {})
        gl = []
        for g in games.get(sysdef["id"], []):
            st = cfg["stats"].get(g["file"], {})
            gl.append({**g, "plays": st.get("plays", 0), "last": st.get("last", 0),
                       "fav": st.get("fav", False),
                       "rating": st.get("rating", 0)})
        systems.append({
            "id": sysdef["id"],
            "name": sysdef["name"],
            "exts": exts_for(sysdef["id"]),
            "emu_name": sysdef["emu_name"],
            "emu_site": sysdef["emu_site"],
            "emu_url": sysdef["emu_url"],
            "dl": "auto" if sysdef.get("dl") else "manual",
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
    meta["have"] = meta_have()
    return {
        "meta": meta,
        "library_root": cfg["library_root"],
        "emulators_root": cfg["emulators_root"],
        "art_root": cfg["art_root"],
        "covers_dir": cfg.get("covers_dir", ""),
        "downloads": downloads,
        "covers": covers,
        "shots": shots,
        "systems": systems,
    }


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
    if system_id == "amiga":
        try:
            ok, msg = _amiga_launch(cfg, rom_path, emu)
        except Exception as e:
            ok, msg = False, str(e)
        if not ok:
            return False, msg
    else:
        args_tpl = cfg["overrides"].get(system_id, {}).get("args", sysdef["args"])
        cmd = (args_tpl
               .replace("{emu}", str(emu))
               .replace("{rom}", str(rom_path))
               .replace("{romname}", rom_path.stem)
               .replace("{romdir}", str(rom_path.parent)))
        try:
            subprocess.Popen(cmd, cwd=str(emu.parent))
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

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
                self.send_response(404)
                self.end_headers()
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
            self.send_response(404)
            self.end_headers()

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
                    save_config(cfg)
                self._json({"ok": True, "msg": "saved"})
            elif parsed.path == "/api/fetchshots":
                ok, msg = start_shots(cfg)
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
            elif parsed.path == "/api/mkdirs":
                try:
                    create_layout(cfg)
                    self._json({"ok": True, "msg": "folders created"})
                except OSError as e:
                    self._json({"ok": False, "msg": str(e)})
            else:
                self.send_response(404)
                self.end_headers()


HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>RetroShelf</title>
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAyUlEQVR4nGNkgAIuTrb/DHQE377/YgTRTAwDDBjp7XN0wMQwwIAFXeDcWk+aWmgUvH2Qh4D6r400tpJtkIfAzaeo/P7NlFlQ6Itfnolh0KUBaVS+MC9lFqCbN/hD4CZaGnj7mTIL0M0bdCHAiF4XfF3+i6YWckcOtXKA1oCJYbCXA8M+BBhxtYjO9VA3NxiVoKb+wZsGcKUF9PxLCBBbnjAxDNYQuElheUCsfiaG4d4v0P/WBKYvctWN0BAgBJgYBhgwwhgjtncMAOnbLhhwUHj1AAAAAElFTkSuQmCC">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=VT323&family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0906; --panel: #14110a; --panel2: #1d1810; --line: #3a2f14;
  --amber: #ffb000; --amber2: #ffd75e; --dim: #8a6d1f; --text: #e8d9b0;
  --muted: #9a8a5c; --green: #39ff88; --red: #ff5544;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
html { scrollbar-color: var(--dim) var(--bg); }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: rgba(0,0,0,.3); }
::-webkit-scrollbar-thumb { background: var(--line); border-radius: 5px; }
::-webkit-scrollbar-thumb:hover { background: var(--dim); }
body {
  background: radial-gradient(ellipse at 50% 20%, #14110a 0%, #0a0906 60%, #060503 100%);
  color: var(--text); font-family: VT323, monospace; font-size: 19px;
  overflow: hidden; display: flex; flex-direction: column;
}
a { color: var(--amber2); }
a:hover { text-shadow: 0 0 8px rgba(255,176,0,.6); }
/* ---- CRT tube ---- */
#gridfloor {
  position: fixed; bottom: -6vh; left: -50%; width: 200%; height: 44vh;
  pointer-events: none; z-index: 0; opacity: .22;
  background:
    repeating-linear-gradient(90deg, rgba(255,176,0,.16) 0 2px, transparent 2px 70px),
    repeating-linear-gradient(0deg, rgba(255,176,0,.16) 0 2px, transparent 2px 46px);
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
  background: radial-gradient(ellipse at 50% 42%, rgba(255,214,140,.05), transparent 62%);
  animation: hum 5.5s ease-in-out infinite; }
@keyframes hum { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
#stars { position: fixed; inset: 0; pointer-events: none; z-index: 0; }
.starlayer { position: absolute; top: 0; left: 0; background: transparent;
  animation: twinkle 4s infinite alternate ease-in-out; }
@keyframes twinkle { from { opacity: .2; } to { opacity: .8; } }
.pulseline { height: 3px; flex-shrink: 0; background: linear-gradient(90deg,
  transparent, var(--amber) 18%, var(--amber2) 50%, var(--amber) 82%, transparent);
  animation: pulseline 3.4s ease-in-out infinite; }
@keyframes pulseline {
  0%, 100% { opacity: .3; filter: none; }
  50% { opacity: 1; filter: drop-shadow(0 0 8px rgba(255,176,0,.8)); } }
@keyframes pulse { 50% { opacity: .45; } }
@keyframes blink { 50% { opacity: 0; } }
/* ---- boot + loader ---- */
#boot { position: fixed; inset: 0; z-index: 200; background: #060503; padding: 46px; }
#boot pre { font-family: VT323, monospace; font-size: 22px; color: var(--amber);
  text-shadow: 0 0 8px rgba(255,176,0,.6); line-height: 1.6; white-space: pre-wrap; }
#boot.off { animation: crtoff .55s ease-in forwards; }
@keyframes crtoff {
  0% { transform: scaleY(1); filter: brightness(1); opacity: 1; }
  55% { transform: scaleY(.008); filter: brightness(2.6); opacity: 1; background: #ffd75e; }
  100% { transform: scaleY(.008) scaleX(.01); filter: brightness(3); opacity: 0; background: #ffd75e; } }
#loader { position: fixed; inset: 0; z-index: 150; display: none;
  background: rgba(6,5,3,.93); text-align: center; }
#loader .inner { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  width: min(480px, 86vw); }
#loader .t1 { font-family: 'Press Start 2P', monospace; font-size: 18px;
  color: var(--amber); text-shadow: 0 0 14px rgba(255,176,0,.7);
  animation: pulse 1s steps(2) infinite; }
#loader .t2 { font-size: 28px; color: var(--text); margin: 22px 0 26px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#loader .barwrap { height: 22px; border: 2px solid var(--amber); padding: 3px; }
#loader .bar { height: 100%; width: 0; background: repeating-linear-gradient(90deg,
  var(--amber) 0 14px, #7a5500 14px 18px); }
#loader.go .bar { animation: fill 1.6s steps(24) forwards; }
@keyframes fill { to { width: 100%; } }
/* ---- header ---- */
header { flex-shrink: 0; background: rgba(10,9,6,.94); border-bottom: 1px solid var(--line);
  box-shadow: 0 4px 18px rgba(0,0,0,.5); position: relative; z-index: 12; }
.bar { display: flex; align-items: center; gap: 22px; padding: 10px 20px 8px; }
.logo { display: flex; align-items: flex-end; gap: 6px; white-space: nowrap;
  cursor: pointer; user-select: none; }
.logo pre { margin: 0; font-family: Consolas, "Cascadia Mono", monospace;
  font-size: 11px; line-height: 1.12; font-weight: 700;
  background: linear-gradient(90deg, #7a5c00, var(--amber) 28%, var(--amber2) 50%,
    var(--amber) 72%, #7a5c00);
  background-size: 200% 100%;
  -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 4.5s linear infinite;
  filter: drop-shadow(0 0 7px rgba(255,176,0,.55)); }
@keyframes shimmer { to { background-position-x: -200%; } }
.logo .cur { color: var(--amber); font-size: 17px; line-height: 1;
  animation: blink 1.1s steps(1) infinite; text-shadow: 0 0 10px rgba(255,176,0,.7); }
.searchwrap { flex: 1; max-width: 460px; position: relative; }
.searchwrap::before { content: ">"; position: absolute; left: 14px; top: 4px;
  color: var(--dim); font-size: 22px; }
#search { width: 100%; height: 38px; background: rgba(0,0,0,.45);
  border: 1px solid var(--line); border-radius: 4px; padding: 0 16px 0 34px;
  font: inherit; font-size: 20px; color: var(--amber2); outline: none; caret-color: var(--amber); }
#search::placeholder { color: var(--muted); }
#search:focus { border-color: var(--amber);
  box-shadow: 0 0 12px rgba(255,176,0,.35), inset 0 0 8px rgba(255,176,0,.08); }
#count { color: var(--muted); font-size: 18px; margin-left: auto; white-space: nowrap; }
#padbadge { display: inline-flex; width: 26px; height: 26px; flex-shrink: 0; }
#padbadge svg { width: 100%; height: 100%; fill: var(--line); transition: fill .2s; }
#padbadge.on svg { fill: var(--green); filter: drop-shadow(0 0 6px rgba(57,255,136,.7)); }
.tabs { display: flex; gap: 6px; padding: 0 20px; }
.tabs button { background: none; border: none; font-family: 'Press Start 2P', monospace;
  font-size: 10px; color: var(--muted); padding: 8px 12px 10px; cursor: pointer;
  border-bottom: 3px solid transparent; letter-spacing: 1px; }
.tabs button.on { color: var(--amber); border-bottom-color: var(--amber);
  text-shadow: 0 0 10px rgba(255,176,0,.7); }
.tabs button:hover:not(.on) { color: var(--text); }
#termline { padding: 4px 20px 5px; color: var(--amber); font-size: 18px;
  background: rgba(0,0,0,.35); border-bottom: 1px solid var(--line);
  white-space: nowrap; overflow: hidden; flex-shrink: 0;
  text-shadow: 0 0 8px rgba(255,176,0,.4); }
#termline .cur { animation: blink 1.1s steps(1) infinite; }
/* ---- three column shell ---- */
#shell { flex: 1; display: flex; min-height: 0; position: relative; z-index: 1; }
#sidebar { width: 226px; flex-shrink: 0; border-right: 1px solid var(--line);
  background: rgba(10,9,6,.75); overflow-y: auto; padding: 10px 0 20px; }
.side-h { font-family: 'Press Start 2P', monospace; font-size: 8px; color: var(--dim);
  letter-spacing: 1px; padding: 14px 16px 7px; }
.side-i { display: flex; align-items: center; gap: 9px; padding: 5px 16px;
  cursor: pointer; color: var(--text); white-space: nowrap; }
.side-i:hover { background: rgba(255,176,0,.07); color: var(--amber2); }
.side-i.on { background: rgba(255,176,0,.14); color: var(--amber);
  box-shadow: inset 3px 0 var(--amber); text-shadow: 0 0 8px rgba(255,176,0,.5); }
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
.tbtn.on { background: var(--amber); color: #0a0906; border-color: var(--amber);
  box-shadow: 0 0 12px rgba(255,176,0,.45); }
#toolbar .sp { margin-left: auto; color: var(--muted); font-size: 17px; }
#view { flex: 1; overflow-y: auto; padding: 16px; }
/* grid */
#grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 16px; }
.tile { cursor: pointer; position: relative; animation: rowin .3s ease-out both; }
@keyframes rowin { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
.tile .box { aspect-ratio: 3/4; border-radius: 5px; overflow: hidden; background: var(--panel2);
  border: 2px solid var(--line); display: flex; align-items: center; justify-content: center;
  position: relative; transition: border-color .13s, box-shadow .13s, transform .13s; }
.tile .box img { width: 100%; height: 100%; object-fit: cover; display: block; }
.tile .box .ph { font-family: 'Press Start 2P', monospace; font-size: 17px; color: #fff;
  text-shadow: 1px 2px 0 rgba(0,0,0,.45); }
.tile:hover .box { border-color: var(--dim); transform: translateY(-3px); }
.tile.sel .box { border-color: var(--amber); transform: translateY(-3px);
  box-shadow: 0 0 20px rgba(255,176,0,.45); }
.tile .fav { position: absolute; top: 5px; right: 6px; color: var(--amber);
  font-size: 20px; text-shadow: 0 0 6px rgba(0,0,0,.9); }
.tile .cap { font-size: 17px; color: var(--text); margin-top: 6px; line-height: 1.15;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; }
.tile.sel .cap { color: var(--amber2); }
.tile .sub { font-size: 15px; color: var(--muted); }
/* list */
.row { display: flex; align-items: center; gap: 16px; padding: 9px 12px;
  border-radius: 6px; cursor: pointer; background: var(--panel);
  border: 1px solid var(--line); margin-bottom: 8px;
  animation: rowin .3s ease-out both;
  transition: box-shadow .13s, border-color .13s, transform .13s; }
.row:hover { border-color: var(--dim); }
.row.sel { border-color: var(--amber); transform: translateX(5px);
  box-shadow: 0 0 18px rgba(255,176,0,.28); }
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
  text-shadow: 1px 0 rgba(255,80,80,.16), -1px 0 rgba(80,220,255,.16); }
.row.sel .nm { color: var(--amber2); }
.row .sub { font-size: 17px; color: var(--muted); display: flex; align-items: center; }
/* ---- details panel ---- */
#details { width: 320px; flex-shrink: 0; border-left: 1px solid var(--line);
  background: rgba(10,9,6,.8); overflow-y: auto; }
#details .empty2 { padding: 60px 22px; text-align: center; color: var(--muted); }
.dcover { width: 100%; aspect-ratio: 3/4; max-height: 320px; object-fit: contain;
  background: #000; border-bottom: 1px solid var(--line); display: block; }
.dcover.ph3 { display: flex; align-items: center; justify-content: center;
  font-family: 'Press Start 2P', monospace; font-size: 26px; color: #fff; }
.dbody { padding: 14px 18px 26px; }
.dtitle { font-size: 27px; color: var(--amber2); line-height: 1.1;
  text-shadow: 0 0 10px rgba(255,176,0,.35); }
.dsys { font-size: 17px; color: var(--muted); margin-top: 4px;
  display: flex; align-items: center; }
.playbig { width: 100%; margin: 14px 0 12px; background: var(--amber); border: none;
  color: #0a0906; font-family: 'Press Start 2P', monospace; font-size: 12px;
  padding: 13px 0; border-radius: 5px; cursor: pointer; letter-spacing: 1px;
  box-shadow: 0 0 16px rgba(255,176,0,.4); }
.playbig:hover { background: var(--amber2); box-shadow: 0 0 26px rgba(255,176,0,.75); }
.drow2 { display: flex; gap: 8px; margin-bottom: 14px; }
.drow2 button { flex: 1; background: rgba(0,0,0,.3); border: 1px solid var(--line);
  color: var(--text); font: inherit; font-size: 17px; padding: 5px 0;
  border-radius: 4px; cursor: pointer; }
.drow2 button:hover { border-color: var(--amber); color: var(--amber2); }
.stars { font-size: 24px; color: var(--dim); letter-spacing: 3px; cursor: pointer;
  margin-bottom: 12px; }
.stars b { color: var(--amber); font-weight: 400;
  text-shadow: 0 0 8px rgba(255,176,0,.6); }
.dshot { width: 100%; border-radius: 4px; border: 1px solid var(--line);
  display: block; margin-bottom: 14px; background: #000; }
.dshot-ph { width: 100%; aspect-ratio: 4/3; border-radius: 4px;
  border: 1px dashed var(--line); display: flex; align-items: center;
  justify-content: center; color: var(--muted); font-size: 16px; margin-bottom: 14px; }
.overview { font-size: 18px; color: var(--text); line-height: 1.45; margin-bottom: 12px;
  white-space: pre-wrap; }
.overview.clip { display: -webkit-box; -webkit-line-clamp: 7; -webkit-box-orient: vertical;
  overflow: hidden; }
.ovmore { color: var(--amber); font-size: 17px; cursor: pointer; margin-bottom: 12px;
  display: inline-block; }
.ovmore:hover { text-shadow: 0 0 8px rgba(255,176,0,.6); }
.genres { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 12px; }
.gtag { border: 1px solid var(--line); border-radius: 3px; padding: 1px 9px;
  font-size: 16px; color: var(--muted); }
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
.pill { border-radius: 3px; padding: 2px 12px; font-size: 17px; border: 1px solid; }
.pill.ok { border-color: var(--green); color: var(--green); text-shadow: 0 0 8px rgba(57,255,136,.5); }
.pill.bad { border-color: var(--red); color: var(--red); text-shadow: 0 0 8px rgba(255,85,68,.5); }
.pill.dlp { border-color: var(--amber); color: var(--amber);
  text-shadow: 0 0 8px rgba(255,176,0,.5); animation: pulse 1s steps(2) infinite; }
.dim { color: var(--muted); font-size: 18px; margin-top: 8px; line-height: 1.5; word-break: break-all; }
.dim b { color: var(--text); font-weight: 400; }
.err { color: var(--red); font-size: 17px; margin-left: 10px; }
.fields { display: flex; gap: 12px; margin-top: 12px; flex-wrap: wrap; align-items: center; }
.flabel { color: var(--dim); font-size: 17px; width: 100%; margin: 8px 0 -8px; }
input.cfg { background: rgba(0,0,0,.45); border: 1px solid var(--line); border-radius: 4px;
  padding: 7px 12px; font: inherit; font-size: 18px; color: var(--amber2);
  flex: 1; min-width: 220px; outline: none; caret-color: var(--amber); }
input.cfg::placeholder { color: var(--muted); }
input.cfg:focus { border-color: var(--amber); box-shadow: 0 0 10px rgba(255,176,0,.3); }
button.txt { background: none; border: 1px solid transparent; color: var(--amber);
  font: inherit; font-size: 19px; padding: 6px 14px; border-radius: 4px; cursor: pointer; }
button.txt:hover { border-color: var(--amber); box-shadow: 0 0 10px rgba(255,176,0,.35); }
button.filled { background: var(--amber); border: none; color: #0a0906; font: inherit;
  font-size: 19px; padding: 7px 22px; border-radius: 4px; cursor: pointer; }
button.filled:hover { background: var(--amber2); box-shadow: 0 0 16px rgba(255,176,0,.6); }
button.outlined { background: rgba(0,0,0,.3); border: 1px solid var(--amber);
  color: var(--amber); font: inherit; font-size: 19px; padding: 6px 22px;
  border-radius: 4px; cursor: pointer; }
button.outlined:hover { box-shadow: 0 0 12px rgba(255,176,0,.4); }
.empty { text-align: center; padding: 70px 20px; color: var(--muted); line-height: 2;
  background: rgba(0,0,0,.25); border: 1px dashed var(--line); border-radius: 6px; font-size: 20px; }
.empty .big { font-family: 'Press Start 2P', monospace; font-size: 15px; color: var(--amber);
  margin-bottom: 18px; text-shadow: 0 0 12px rgba(255,176,0,.6);
  animation: pulse 1.4s steps(2) infinite; }
.empty code { background: var(--panel2); border-radius: 3px; padding: 2px 8px;
  color: var(--amber2); font-family: inherit; }
.howto { color: var(--muted); font-size: 19px; line-height: 1.8; }
.howto code { background: rgba(0,0,0,.4); border-radius: 3px; padding: 1px 7px;
  font-size: 18px; color: var(--amber2); font-family: inherit; }
.howto h3 { color: var(--amber); font-size: 20px; font-weight: 400; margin: 14px 0 4px;
  text-shadow: 0 0 8px rgba(255,176,0,.4); }
#snack { position: fixed; bottom: 22px; left: 22px; background: rgba(6,5,3,.95);
  color: var(--amber); border: 1px solid var(--amber); border-radius: 4px;
  padding: 10px 22px; font-size: 20px; display: none; z-index: 160;
  box-shadow: 0 0 18px rgba(255,176,0,.35); max-width: 70vw;
  text-shadow: 0 0 8px rgba(255,176,0,.5); }
#gphint { position: fixed; bottom: 0; left: 0; right: 0; text-align: center;
  padding: 5px 10px; background: rgba(6,5,3,.92); border-top: 1px solid var(--line);
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
    <div class="logo" onclick="setTab('games')"><pre>
█▀█ █▀▀ ▀█▀ █▀█ █▀█ █▀▀ █ █ █▀▀ █   █▀▀
█▀▄ ██▄  █  █▀▄ █▄█ ▄▄█ █▀█ ██▄ █▄▄ █▀ </pre><span class="cur">▮</span></div>
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
    <button id="tab-settings" onclick="setTab('settings')">SETTINGS</button>
  </div>
  <div class="pulseline"></div>
  <div id="termline">&gt; <span id="termtext"></span><span class="cur">▮</span></div>
</header>
<div id="shell">
  <aside id="sidebar"></aside>
  <div id="centre">
    <div id="toolbar">
      <button class="tbtn" id="v-grid" onclick="setView('grid')">▦ GRID</button>
      <button class="tbtn" id="v-list" onclick="setView('list')">☰ LIST</button>
      <button class="tbtn" onclick="cycleSort()" id="sortbtn">SORT: NAME</button>
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
let shown = 300;
let curGame = null;
let curList = [];

const META = {
  nes:['#e60012','NES','cart'], snes:['#7b5aa6','SNES','cart'],
  n64:['#009e60','N64','cart'], gb:['#8b956d','GB','hand'],
  gba:['#5c67c6','GBA','hand'], nds:['#7f8ea3','DS','hand'],
  gamecube:['#6a5fc1','GC','disc'], wii:['#3aa6dd','WII','disc'],
  genesis:['#0060a8','MD','cart'], dreamcast:['#f0762f','DC','disc'],
  ps1:['#4f5bd5','PS1','disc'], ps2:['#2a3b8f','PS2','disc'],
  psp:['#8a8f98','PSP','hand'], arcade:['#d81b60','ARC','arc'],
  atari2600:['#b7410e','2600','cart'], c64:['#a97142','C64','comp'],
  amiga:['#d33f49','AMIGA','comp']
};
const ICONS = {
  cart: c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M4 3h16v10h-3v5H7v-5H4z"/><rect x="7.5" y="6" width="9" height="3.5" fill="rgba(0,0,0,.45)"/></svg>`,
  disc: c => `<svg viewBox="0 0 24 24" fill="${c}"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.6" fill="rgba(0,0,0,.6)"/></svg>`,
  hand: c => `<svg viewBox="0 0 24 24" fill="${c}"><rect x="6" y="2" width="12" height="20" rx="2"/><rect x="8" y="4.5" width="8" height="7" fill="rgba(0,0,0,.45)"/><circle cx="10" cy="16.5" r="1.7" fill="rgba(0,0,0,.45)"/><circle cx="14.5" cy="17.5" r="1.3" fill="rgba(0,0,0,.45)"/></svg>`,
  arc:  c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M5 2h14v7l-2 3v10H7V12L5 9z"/><rect x="8" y="4.5" width="8" height="4" fill="rgba(0,0,0,.45)"/><circle cx="10" cy="16" r="1.2" fill="rgba(0,0,0,.45)"/><rect x="13" y="15.2" width="4" height="1.6" fill="rgba(0,0,0,.45)"/></svg>`,
  comp: c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M3 8h18v7l1.5 5H1.5L3 15z"/><rect x="4.5" y="16.5" width="15" height="1.8" fill="rgba(0,0,0,.45)"/><rect x="5" y="9.8" width="14" height="3.4" fill="rgba(0,0,0,.3)"/></svg>`
};
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
  mk(50,2,4.6,'rgba(232,217,176,.5)'); mk(24,3,3.2,'rgba(255,176,0,.45)');
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
  let i=0; const t=setInterval(()=>{ i+=3; el.textContent=BOOT.slice(0,i)+'▮';
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
  else typeTerm('OPEN 15,8,15,"CONFIG": READY.');
}

function setView(v){ view=v; localStorage.setItem('rs_view',v); shown=300; render(); }
function cycleSort(){
  const modes=['name','recent','plays','system'];
  sortMode=modes[(modes.indexOf(sortMode)+1)%modes.length];
  localStorage.setItem('rs_sort',sortMode); render();
}
function onSearch(){ shown=300; render(); }

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
function coverUrl(g){ return '/api/art?system='+g.sysId+'&rom='+encodeURIComponent(g.file); }
function shotUrl(g){ return '/api/art?system='+g.sysId+'&kind=screen&rom='+encodeURIComponent(g.file); }

function currentGames(){
  let games;
  if(sel==='all') games=allGames();
  else if(sel==='fav') games=allGames().filter(g=>g.fav);
  else if(sel==='recent') games=allGames().filter(g=>g.last).sort((a,b)=>b.last-a.last);
  else { const s=state.systems.find(x=>x.id===sel)||{games:[],name:''};
         games=s.games.map(g=>({...g,sysId:s.id,sysName:s.name})); }
  const q=document.getElementById('search').value.trim().toLowerCase();
  if(q) games=games.filter(g=>g.name.toLowerCase().includes(q));
  if(sel!=='recent'){
    if(sortMode==='name') games.sort((a,b)=>a.name.toLowerCase()<b.name.toLowerCase()?-1:1);
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
      const bg=g.art?'':` style="background:linear-gradient(150deg,${sysColor(g.sysId)},#17130b)"`;
      return `<div class="tile${curGame&&curGame.file===g.file?' sel':''}" data-i="${i}"
        style="animation-delay:${Math.min(i,20)*22}ms"
        onclick="pick(${i})" ondblclick="playIdx(${i})">
        <div class="box"${bg}>${art}${g.fav?'<span class="fav">★</span>':''}</div>
        <div class="cap">${esc(g.name)}</div>
        <div class="sub">${esc(sysLabel(g.sysId))}${g.plays?' · '+g.plays+'▶':''}</div></div>`;
    }).join('')+'</div>'+moreBtn(total);
  } else {
    host.innerHTML=list.map((g,i)=>{
      const art=g.art?`<img loading="lazy" src="${coverUrl(g)}">`
        :`<span class="ph">${esc(g.name.slice(0,2).toUpperCase())}</span>`;
      const bg=g.art?'':` style="background:linear-gradient(150deg,${sysColor(g.sysId)},#17130b)"`;
      const shot=g.shot?`<img loading="lazy" src="${shotUrl(g)}">`
        :`<span class="ph2">NO SHOT</span>`;
      const sub=[g.sysName,g.plays?g.plays+(g.plays===1?' play':' plays'):null,
        g.last?'played '+ago(g.last):null].filter(Boolean).join(' · ');
      return `<div class="row${curGame&&curGame.file===g.file?' sel':''}" data-i="${i}"
        style="animation-delay:${Math.min(i,16)*22}ms"
        onclick="pick(${i})" ondblclick="playIdx(${i})">
        <div class="cover"${bg}>${art}</div>
        <div class="shot">${shot}</div>
        <div class="info"><div class="nm">${g.fav?'★ ':''}${esc(g.name)}</div>
          <div class="sub">${sysLogo(g.sysId,14)}${esc(sub)}</div></div></div>`;
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
    :`<div class="dcover ph3" style="background:linear-gradient(150deg,${sysColor(g.sysId)},#17130b)">
       ${esc(g.name.slice(0,2).toUpperCase())}</div>`;
  const shot=g.shot?`<img class="dshot" src="${shotUrl(g)}">`
    :`<div class="dshot-ph">no screenshot yet</div>`;
  let stars='';
  for(let i=1;i<=5;i++)
    stars+=(i<=(g.rating||0)?`<b onclick="setRating(${i})">★</b>`
                            :`<span onclick="setRating(${i})">☆</span>`);
  el.innerHTML=`${cover}<div class="dbody">
    <div class="dtitle">${esc(g.name)}</div>
    <div class="dsys">${sysLogo(g.sysId,15)}${esc(g.sysName||'')}</div>
    <button class="playbig" onclick="launch(${JSON.stringify(g.sysId)},${JSON.stringify(g.file)},${JSON.stringify(g.name)})">▶ PLAY</button>
    <div class="drow2">
      <button onclick="toggleFav()">${g.fav?'★ FAVOURITE':'☆ FAVOURITE'}</button>
      <button onclick="openFolder()">📁 FOLDER</button>
    </div>
    <div class="stars">${stars}</div>
    ${shot}
    ${overviewHtml(d)}
    ${genresHtml(d)}
    <table class="meta">
      <tr><td>Platform</td><td>${esc(g.sysName||'')}</td></tr>
      ${metaRows(d)}
      <tr><td>Play count</td><td>${g.plays||0}</td></tr>
      <tr><td>Last played</td><td>${g.last?esc(ago(g.last)):'never'}</td></tr>
      <tr><td>Your rating</td><td>${g.rating?g.rating+' / 5':'not rated'}</td></tr>
      <tr><td>File</td><td>${esc(d.file||'')}</td></tr>
      <tr><td>Size</td><td>${fmtSize(d.size)}</td></tr>
      <tr><td>Folder</td><td>${esc(d.folder||'')}</td></tr>
    </table></div>`;
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
function renderPage(){
  const body=document.getElementById('pagebody');
  const count=document.getElementById('count');
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
      const dlbtn=(!s.emu_found&&!busy&&s.dl==='auto')
        ?`<button class="filled" onclick="download('${s.id}')">DOWNLOAD ${esc(s.emu_name.toUpperCase())}</button>`:'';
      return `<div class="card">
        <div class="head">${sysLogo(s.id,22)}<span class="nm">${esc(s.name)}</span>${status}${err}</div>
        <div class="dim">${detail}<br>Game files: <b>${esc(s.exts.join(' '))}</b>
          &middot; ${s.games.length} found</div>${note}
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
        <div class="head"><span class="nm">Game descriptions</span>${metaStatus()}</div>
        <div class="dim">Downloads the LaunchBox Games Database dump (~100 MB, one time)
          and builds a local index, giving every game a description plus developer,
          publisher, genre, release year, player count and community rating in the
          details panel. Re-run any time to refresh.</div>
        <div class="fields">
          <button class="filled" onclick="fetchMeta()">DOWNLOAD GAME DATABASE</button>
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
const TABS=['games','systems','settings'];
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


def main():
    url = f"http://127.0.0.1:{PORT}"
    server = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"RetroShelf running at {url}")
    except OSError:
        pass    # already running — just open another window/tab on it

    if "--no-browser" in sys.argv:        # dev: server only
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
                min_size=(900, 600), background_color="#0a0906")
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
