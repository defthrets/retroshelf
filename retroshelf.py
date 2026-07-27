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

import json
import mimetypes
import os
import re
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
     "note": "Needs Amiga Kickstart ROMs: drop the .rom files into "
             "emulators\\amiga\\kickstarts\\ then in WinUAE do Paths > "
             "System ROMs > point at that folder > Rescan ROMs (one time). "
             "ADF disks launch directly; WHDLoad archives (.lha/.rar) need "
             "a one-time WinUAE Quickstart setup."},
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
SKIP_DIRS = {"art", "covers", "screens", "screenshots", "emulators"}

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
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
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
            gl.append({**g, "plays": st.get("plays", 0), "last": st.get("last", 0)})
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
    return {
        "library_root": cfg["library_root"],
        "emulators_root": cfg["emulators_root"],
        "art_root": cfg["art_root"],
        "downloads": downloads,
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
            elif parsed.path == "/api/settings":
                for key in ("library_root", "emulators_root", "art_root"):
                    val = body.get(key, "").strip()
                    if val:
                        cfg[key] = val
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
html { scrollbar-color: var(--dim) var(--bg); }
::-webkit-scrollbar { width: 10px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--line); border-radius: 5px; }
::-webkit-scrollbar-thumb:hover { background: var(--dim); }
body {
  background: radial-gradient(ellipse at 50% 20%, #14110a 0%, #0a0906 60%, #060503 100%);
  color: var(--text); font-family: VT323, monospace; font-size: 19px;
  min-height: 100vh;
}
a { color: var(--amber2); }
a:hover { text-shadow: 0 0 8px rgba(255,176,0,.6); }
/* ---- animated CRT layers ---- */
#gridfloor {
  position: fixed; bottom: -6vh; left: -50%; width: 200%; height: 44vh;
  pointer-events: none; z-index: 0; opacity: .5;
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
  background: repeating-linear-gradient(0deg, rgba(0,0,0,.22) 0 1px, transparent 1px 3px);
  animation: flicker 5s infinite; }
#crt::before { content: ""; position: absolute; inset: 0;
  background: radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,.5) 100%); }
#crt::after { content: ""; position: absolute; left: 0; right: 0; height: 140px;
  background: linear-gradient(to bottom, transparent, rgba(255,214,140,.05) 45%,
              rgba(255,214,140,.08) 50%, rgba(255,214,140,.05) 55%, transparent);
  animation: sweep 7s linear infinite; }
@keyframes sweep { from { top: -20%; } to { top: 110%; } }
@keyframes flicker {
  0%, 100% { opacity: 1; } 3% { opacity: .82; } 4% { opacity: 1; }
  31% { opacity: 1; } 32% { opacity: .87; } 33% { opacity: 1; }
  67% { opacity: 1; } 68% { opacity: .9; } 69% { opacity: 1; }
}
/* ---- boot + loading overlays ---- */
#boot { position: fixed; inset: 0; z-index: 200; background: #060503;
  padding: 46px; transition: opacity .45s; }
#boot pre { font-family: VT323, monospace; font-size: 22px; color: var(--amber);
  text-shadow: 0 0 8px rgba(255,176,0,.6); line-height: 1.6; white-space: pre-wrap; }
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
@keyframes pulse { 50% { opacity: .45; } }
@keyframes blink { 50% { opacity: 0; } }
/* ---- header ---- */
header { position: sticky; top: 0; z-index: 10;
  background: rgba(10,9,6,.94); backdrop-filter: blur(2px);
  border-bottom: 1px solid var(--line);
  box-shadow: 0 4px 18px rgba(0,0,0,.5); }
.bar { display: flex; align-items: center; gap: 22px; padding: 14px 24px 10px; }
.logo { font-family: 'Press Start 2P', monospace; font-size: 15px; color: var(--amber);
  white-space: nowrap; cursor: pointer; user-select: none;
  text-shadow: 0 0 12px rgba(255,176,0,.65); animation: glowpulse 3s ease-in-out infinite; }
.logo b { font-weight: 400; color: var(--amber2); }
.logo .cur { animation: blink 1.1s steps(1) infinite; }
@keyframes glowpulse {
  0%, 100% { text-shadow: 0 0 12px rgba(255,176,0,.65); }
  50% { text-shadow: 0 0 20px rgba(255,176,0,.95); }
}
.searchwrap { flex: 1; max-width: 520px; position: relative; }
.searchwrap::before { content: ">"; position: absolute; left: 14px; top: 6px;
  color: var(--dim); font-size: 22px; }
#search { width: 100%; height: 42px; background: rgba(0,0,0,.45);
  border: 1px solid var(--line); border-radius: 4px; padding: 0 16px 0 34px;
  font: inherit; font-size: 21px; color: var(--amber2); outline: none; caret-color: var(--amber); }
#search::placeholder { color: var(--muted); }
#search:focus { border-color: var(--amber);
  box-shadow: 0 0 12px rgba(255,176,0,.35), inset 0 0 8px rgba(255,176,0,.08); }
#count { color: var(--muted); font-size: 18px; margin-left: auto; white-space: nowrap; }
#padbadge { display: inline-flex; width: 26px; height: 26px; flex-shrink: 0; }
#padbadge svg { width: 100%; height: 100%; fill: var(--line); transition: fill .2s; }
#padbadge.on svg { fill: var(--green);
  filter: drop-shadow(0 0 6px rgba(57,255,136,.7)); }
#gphint { position: fixed; bottom: 0; left: 0; right: 0; text-align: center;
  padding: 7px 10px; background: rgba(6,5,3,.92); border-top: 1px solid var(--line);
  color: var(--muted); font-size: 17px; z-index: 96; display: none; }
#gphint .kb { border: 1px solid var(--dim); border-radius: 3px; padding: 0 7px;
  color: var(--amber); margin: 0 3px 0 10px; }
.row.gpsel { border-color: var(--amber);
  box-shadow: 0 0 18px rgba(255,176,0,.28), inset 0 0 24px rgba(255,176,0,.05);
  transform: translateX(5px); }
.row.gpsel .info .nm { color: var(--amber2); text-shadow: 0 0 10px rgba(255,176,0,.5); }
.row.gpsel .playbtn { opacity: 1; animation: playpulse 1s ease-in-out infinite; }
.tabs { display: flex; gap: 6px; padding: 0 24px; }
.tabs button { background: none; border: none; font-family: 'Press Start 2P', monospace;
  font-size: 11px; color: var(--muted); padding: 10px 14px 12px; cursor: pointer;
  border-bottom: 3px solid transparent; letter-spacing: 1px; }
.tabs button.on { color: var(--amber); border-bottom-color: var(--amber);
  text-shadow: 0 0 10px rgba(255,176,0,.7); }
.tabs button:hover:not(.on) { color: var(--text); }
.chips { display: flex; gap: 8px; padding: 12px 24px 12px; flex-wrap: wrap;
  max-width: 1020px; margin: 0 auto; }
.chip { border: 1px solid var(--line); border-radius: 4px; padding: 4px 13px;
  font-size: 18px; color: var(--text); cursor: pointer; background: rgba(0,0,0,.3);
  white-space: nowrap; transition: box-shadow .12s, border-color .12s;
  display: flex; align-items: center; }
.chip:hover { border-color: var(--dim); }
.chip.on { background: var(--amber); color: #0a0906; border-color: var(--amber);
  box-shadow: 0 0 14px rgba(255,176,0,.5); }
.chip span.n { color: var(--muted); font-size: 16px; margin-left: 5px; }
.chip.on span.n { color: #4d3500; }
.slogo { display: inline-flex; margin-right: 7px; flex-shrink: 0;
  filter: drop-shadow(0 0 3px rgba(255,255,255,.2)); }
.slogo svg { width: 100%; height: 100%; }
/* ---- game list ---- */
main { max-width: 1020px; margin: 0 auto; padding: 16px 24px 60px;
  position: relative; z-index: 1; }
.row { display: flex; align-items: center; gap: 18px; padding: 12px 14px;
  border-radius: 6px; cursor: pointer; position: relative;
  background: var(--panel); border: 1px solid var(--line); margin-bottom: 10px;
  transition: box-shadow .15s, border-color .15s, transform .15s; }
.row:hover { border-color: var(--amber);
  box-shadow: 0 0 18px rgba(255,176,0,.28), inset 0 0 24px rgba(255,176,0,.05);
  transform: translateX(5px); }
.cover, .shot { border-radius: 4px; background: var(--panel2); flex-shrink: 0;
  overflow: hidden; display: flex; align-items: center; justify-content: center;
  position: relative; border: 1px solid rgba(255,176,0,.12); }
.cover::after, .shot::after { content: ""; position: absolute; inset: 0;
  pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,.16) 0 1px, transparent 1px 3px); }
.cover { width: 72px; height: 96px; }
.shot { width: 170px; height: 96px; }
.cover img, .shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cover .ph { font-family: 'Press Start 2P', monospace; font-size: 15px; color: #fff;
  text-shadow: 1px 2px 0 rgba(0,0,0,.45); }
.shot .ph2 { font-family: 'Press Start 2P', monospace; font-size: 7px; color: var(--muted); }
.info { min-width: 0; flex: 1; }
.info .nm { font-size: 26px; color: var(--text); line-height: 1.1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  transition: color .12s, text-shadow .12s; }
.row:hover .info .nm { color: var(--amber2); text-shadow: 0 0 10px rgba(255,176,0,.5); }
.info .sub { font-size: 18px; color: var(--muted); margin-top: 3px;
  display: flex; align-items: center; }
.playbtn { width: 46px; height: 46px; border-radius: 50%; border: 2px solid var(--amber);
  color: var(--amber); background: rgba(0,0,0,.4); display: flex; align-items: center;
  justify-content: center; font-size: 17px; flex-shrink: 0; opacity: 0;
  transition: opacity .15s; padding-left: 4px; }
.row:hover .playbtn { opacity: 1; animation: playpulse 1s ease-in-out infinite; }
@keyframes playpulse {
  0%, 100% { box-shadow: 0 0 6px rgba(255,176,0,.5); }
  50% { box-shadow: 0 0 20px rgba(255,176,0,.9); }
}
.more { text-align: center; color: var(--muted); padding: 14px; font-size: 18px; }
.empty { text-align: center; padding: 80px 20px; color: var(--muted); line-height: 2;
  background: rgba(0,0,0,.25); border: 1px dashed var(--line); border-radius: 6px;
  font-size: 20px; }
.empty .big { font-family: 'Press Start 2P', monospace; font-size: 16px;
  color: var(--amber); margin-bottom: 20px; text-shadow: 0 0 12px rgba(255,176,0,.6);
  animation: pulse 1.4s steps(2) infinite; }
.empty code { background: var(--panel2); border-radius: 3px; padding: 2px 8px;
  color: var(--amber2); font-family: inherit; }
/* ---- cards (systems / settings) ---- */
.card { border: 1px solid var(--line); border-radius: 6px; padding: 16px 20px;
  margin: 14px 0; background: var(--panel); }
.card:hover { border-color: var(--dim); }
.card .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.card .head .nm { font-size: 24px; color: var(--amber2); }
.pill { border-radius: 3px; padding: 2px 12px; font-size: 17px; border: 1px solid; }
.pill.ok { border-color: var(--green); color: var(--green);
  text-shadow: 0 0 8px rgba(57,255,136,.5); }
.pill.bad { border-color: var(--red); color: var(--red);
  text-shadow: 0 0 8px rgba(255,85,68,.5); animation: pulse 1.6s steps(2) infinite; }
.pill.dlp { border-color: var(--amber); color: var(--amber);
  text-shadow: 0 0 8px rgba(255,176,0,.5); animation: pulse 1s steps(2) infinite; }
.dim { color: var(--muted); font-size: 18px; margin-top: 8px; line-height: 1.5;
  word-break: break-all; }
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
#snack { position: fixed; bottom: 22px; left: 22px; background: rgba(6,5,3,.95);
  color: var(--amber); border: 1px solid var(--amber); border-radius: 4px;
  padding: 10px 22px; font-size: 20px; display: none; z-index: 160;
  box-shadow: 0 0 18px rgba(255,176,0,.35); max-width: 70vw;
  text-shadow: 0 0 8px rgba(255,176,0,.5); }
.howto { color: var(--muted); font-size: 19px; line-height: 1.8; }
.howto code { background: rgba(0,0,0,.4); border-radius: 3px; padding: 1px 7px;
  font-size: 18px; color: var(--amber2); font-family: inherit; }
.howto h3 { color: var(--amber); font-size: 20px; font-weight: 400; margin: 14px 0 4px;
  text-shadow: 0 0 8px rgba(255,176,0,.4); }
@media (max-width: 700px) { .shot { display: none; } #count { display: none; } }
</style>
</head>
<body>
<div id="gridfloor"></div>
<header>
  <div class="bar">
    <div class="logo" onclick="setTab('games')">RETRO<b>SHELF</b><span class="cur">▮</span></div>
    <div class="searchwrap">
      <input id="search" placeholder="search games..." oninput="render()" autocomplete="off">
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
  <div class="chips" id="chips"></div>
</header>
<main id="content"></main>
<div id="gphint"><span class="kb">▲▼</span> move <span class="kb">A</span> play
  <span class="kb">◀▶</span> system <span class="kb">LB RB</span> tab
  <span class="kb">Y</span> rescan <span class="kb">B</span> top</div>
<div id="snack"></div>
<div id="loader"><div class="inner">
  <div class="t1">NOW LOADING</div>
  <div class="t2" id="loadname"></div>
  <div class="barwrap"><div class="bar"></div></div>
</div></div>
<div id="crt"></div>
<div id="boot"><pre id="boottext"></pre></div>
<script>
let state = null;
let tab = 'games';
let sel = 'all';
let shown = 400;

/* per-system colour, short label, icon shape */
const META = {
  nes:       ['#e60012', 'NES',   'cart'],
  snes:      ['#7b5aa6', 'SNES',  'cart'],
  n64:       ['#009e60', 'N64',   'cart'],
  gb:        ['#8b956d', 'GB',    'hand'],
  gba:       ['#5c67c6', 'GBA',   'hand'],
  nds:       ['#7f8ea3', 'DS',    'hand'],
  gamecube:  ['#6a5fc1', 'GC',    'disc'],
  wii:       ['#3aa6dd', 'WII',   'disc'],
  genesis:   ['#0060a8', 'MD',    'cart'],
  dreamcast: ['#f0762f', 'DC',    'disc'],
  ps1:       ['#4f5bd5', 'PS1',   'disc'],
  ps2:       ['#2a3b8f', 'PS2',   'disc'],
  psp:       ['#8a8f98', 'PSP',   'hand'],
  arcade:    ['#d81b60', 'ARC',   'arc'],
  atari2600: ['#b7410e', '2600',  'cart'],
  c64:       ['#a97142', 'C64',   'comp'],
  amiga:     ['#d33f49', 'AMIGA', 'comp']
};
const ICONS = {
  cart: c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M4 3h16v10h-3v5H7v-5H4z"/><rect x="7.5" y="6" width="9" height="3.5" fill="rgba(0,0,0,.45)"/></svg>`,
  disc: c => `<svg viewBox="0 0 24 24" fill="${c}"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.6" fill="rgba(0,0,0,.6)"/></svg>`,
  hand: c => `<svg viewBox="0 0 24 24" fill="${c}"><rect x="6" y="2" width="12" height="20" rx="2"/><rect x="8" y="4.5" width="8" height="7" fill="rgba(0,0,0,.45)"/><circle cx="10" cy="16.5" r="1.7" fill="rgba(0,0,0,.45)"/><circle cx="14.5" cy="17.5" r="1.3" fill="rgba(0,0,0,.45)"/></svg>`,
  arc:  c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M5 2h14v7l-2 3v10H7V12L5 9z"/><rect x="8" y="4.5" width="8" height="4" fill="rgba(0,0,0,.45)"/><circle cx="10" cy="16" r="1.2" fill="rgba(0,0,0,.45)"/><rect x="13" y="15.2" width="4" height="1.6" fill="rgba(0,0,0,.45)"/></svg>`,
  comp: c => `<svg viewBox="0 0 24 24" fill="${c}"><path d="M3 8h18v7l1.5 5H1.5L3 15z"/><rect x="4.5" y="16.5" width="15" height="1.8" fill="rgba(0,0,0,.45)"/><rect x="5" y="9.8" width="14" height="3.4" fill="rgba(0,0,0,.3)"/></svg>`
};
function sysColor(id) { return (META[id] || ['#9a8a5c'])[0]; }
function sysLabel(id) { return (META[id] || [0, id.toUpperCase()])[1]; }
function sysLogo(id, size) {
  const m = META[id];
  if (!m) return '';
  return `<span class="slogo" style="width:${size}px;height:${size}px">${ICONS[m[2]](m[0])}</span>`;
}

/* boot sequence */
const BOOT = 'RETROSHELF BIOS v4.0\nMEMORY TEST: 640K OK\nCRT DRIVER ........ OK\nSCANNING GAME LIBRARY ...\n\nREADY.';
(function boot() {
  const el = document.getElementById('boottext');
  const box = document.getElementById('boot');
  let i = 0;
  const t = setInterval(() => {
    i += 3;
    el.textContent = BOOT.slice(0, i) + '▮';
    if (i >= BOOT.length) {
      clearInterval(t);
      el.textContent = BOOT;
      setTimeout(() => { box.style.opacity = '0'; }, 260);
      setTimeout(() => box.remove(), 750);
    }
  }, 16);
})();

async function refresh(rescan) {
  state = await (await fetch('/api/state' + (rescan ? '?rescan=1' : ''))).json();
  render();
}

function setTab(t) {
  tab = t;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('on'));
  document.getElementById('tab-' + t).classList.add('on');
  render();
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function snack(msg) {
  const t = document.getElementById('snack');
  t.textContent = '> ' + msg;
  t.style.display = 'block';
  clearTimeout(t._h);
  t._h = setTimeout(() => t.style.display = 'none', 3200);
}

function showLoader(name) {
  const l = document.getElementById('loader');
  document.getElementById('loadname').textContent = name;
  l.classList.remove('go');
  void l.offsetWidth;
  l.style.display = 'block';
  l.classList.add('go');
}
function hideLoader() { document.getElementById('loader').style.display = 'none'; }

async function launch(sysId, rom, name) {
  showLoader(name);
  const r = await (await fetch('/api/launch', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({system: sysId, rom: rom})})).json();
  if (!r.ok) {
    hideLoader();
    snack('ERROR: ' + r.msg);
    if (r.msg.startsWith('no emulator')) setTab('systems');
  } else {
    setTimeout(hideLoader, 1750);
    setTimeout(() => refresh(), 900);
  }
}

let _poll = null;
function pollDownloads() {
  if (_poll) return;
  _poll = setInterval(async () => {
    await refresh();
    const act = Object.values(state.downloads || {})
      .some(d => ['resolving', 'downloading', 'extracting'].includes(d.status));
    if (!act) { clearInterval(_poll); _poll = null; refresh(); }
  }, 1200);
}

async function download(id) {
  const r = await (await fetch('/api/download', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id: id})})).json();
  snack(r.ok ? 'DOWNLOADING ' + sysLabel(id) + ' EMULATOR' : 'ERROR: ' + r.msg);
  if (r.ok) pollDownloads();
}

function allGames() {
  const out = [];
  for (const s of state.systems)
    for (const g of s.games) out.push({...g, sysId: s.id, sysName: s.name});
  out.sort((a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1);
  return out;
}

function ago(ts) {
  if (!ts) return '';
  const d = Math.floor(Date.now()/1000 - ts);
  if (d < 3600) return 'played just now';
  if (d < 86400) return 'played ' + Math.floor(d/3600) + 'h ago';
  if (d < 86400*30) return 'played ' + Math.floor(d/86400) + 'd ago';
  return 'played ' + new Date(ts*1000).toLocaleDateString();
}

function render() {
  if (!state) return;
  const content = document.getElementById('content');
  const chips = document.getElementById('chips');
  const search = document.getElementById('search');
  const count = document.getElementById('count');
  chips.style.display = tab === 'games' ? '' : 'none';

  if (tab === 'games') {
    const withGames = state.systems.filter(s => s.games.length);
    chips.innerHTML =
      `<div class="chip ${sel==='all'?'on':''}" onclick="sel='all';shown=400;render()">ALL<span class="n">${allGames().length}</span></div>` +
      withGames.map(s =>
        `<div class="chip ${sel===s.id?'on':''}" onclick="sel='${s.id}';shown=400;render()">${sysLogo(s.id,17)}${esc(s.name)}<span class="n">${s.games.length}</span></div>`
      ).join('') +
      `<div class="chip" onclick="snack('RESCANNING...');refresh(true)">⟳ RESCAN</div>`;

    let games = sel === 'all' ? allGames()
      : (state.systems.find(s => s.id === sel) || {games:[]}).games
          .map(g => ({...g, sysId: sel,
                      sysName: (state.systems.find(s => s.id === sel) || {}).name || ''}));
    const q = search.value.trim().toLowerCase();
    if (q) games = games.filter(g => g.name.toLowerCase().includes(q));
    count.textContent = games.length + (games.length === 1 ? ' game' : ' games');

    if (!games.length) {
      content.innerHTML = `<div class="empty">
        <div class="big">INSERT CARTRIDGE</div>
        No games found in <code>${esc(state.library_root)}</code><br>
        Point RetroShelf at your games folder in the <b>SETTINGS</b> tab &mdash;
        it scans every subfolder automatically.</div>`;
      return;
    }
    const total = games.length;
    games = games.slice(0, shown);
    content.innerHTML = games.map(g => {
      const cover = g.art
        ? `<img loading="lazy" src="/api/art?system=${g.sysId}&rom=${encodeURIComponent(g.file)}">`
        : `<span class="ph">${esc(g.name.slice(0,2).toUpperCase())}</span>`;
      const coverBg = g.art ? '' :
        ` style="background:linear-gradient(150deg, ${sysColor(g.sysId)}, #17130b)"`;
      const shot = g.shot
        ? `<img loading="lazy" src="/api/art?system=${g.sysId}&kind=screen&rom=${encodeURIComponent(g.file)}">`
        : `<span class="ph2">NO SCREENSHOT</span>`;
      const sub = [g.sysName, g.plays ? g.plays + (g.plays === 1 ? ' play' : ' plays') : null,
                   ago(g.last) || null].filter(Boolean).join(' · ');
      return `<div class="row" onclick='launch(${JSON.stringify(g.sysId)}, ${JSON.stringify(g.file)}, ${JSON.stringify(g.name)})'>
        <div class="cover"${coverBg}>${cover}</div>
        <div class="shot">${shot}</div>
        <div class="info"><div class="nm">${esc(g.name)}</div>
          <div class="sub">${sysLogo(g.sysId,15)}${esc(sub)}</div></div>
        <div class="playbtn">▶</div></div>`;
    }).join('') + (total > shown
      ? `<div class="more"><button class="outlined" onclick="shown+=400;render()">SHOW MORE (${total - shown} left)</button></div>` : '');
    if (activePad()) applyGpSel(false);

  } else if (tab === 'systems') {
    count.textContent = state.systems.filter(s => s.emu_found).length + ' of ' +
      state.systems.length + ' emulators ready';
    content.innerHTML = state.systems.map(s => {
      const d = (state.downloads || {})[s.id];
      const busy = d && ['resolving', 'downloading', 'extracting'].includes(d.status);
      let status;
      if (s.emu_found) status = `<span class="pill ok">READY</span>`;
      else if (busy) {
        const lbl = d.status === 'downloading' ? 'DOWNLOADING ' + (d.pct || 0) + '%'
                  : d.status.toUpperCase() + '...';
        status = `<span class="pill dlp">${lbl}</span>`;
      } else status = `<span class="pill bad">EMULATOR MISSING</span>`;
      const err = (d && d.status === 'error')
        ? `<span class="err">DOWNLOAD FAILED: ${esc(d.msg)}</span>` : '';
      let detail;
      const link = `<a href="${s.emu_url}" target="_blank">${esc(s.emu_site)}</a>`;
      if (s.emu_found) detail = `Using <b>${esc(s.emu_path)}</b>`;
      else if (s.dl === 'auto')
        detail = `Needs <b>${esc(s.emu_name)}</b> &mdash; click DOWNLOAD and RetroShelf
          installs it into <b>${esc(s.emu_dir)}\\</b>, or get it yourself from ${link}.`;
      else
        detail = `Needs <b>${esc(s.emu_name)}</b> &mdash; download it from ${link}
          and unzip into <b>${esc(s.emu_dir)}\\</b>.`;
      const note = s.note ? `<div class="dim">▲ ${esc(s.note)}</div>` : '';
      const dlbtn = (!s.emu_found && !busy && s.dl === 'auto')
        ? `<button class="filled" onclick="download('${s.id}')">DOWNLOAD ${esc(s.emu_name.toUpperCase())}</button>` : '';
      return `<div class="card">
        <div class="head">${sysLogo(s.id,22)}<span class="nm">${esc(s.name)}</span>${status}${err}</div>
        <div class="dim">${detail}<br>Game files: <b>${esc(s.exts.join(' '))}</b>
          &middot; ${s.games.length} found</div>
        ${note}
        <div class="fields">
          ${dlbtn}
          <input class="cfg" id="ep-${s.id}" placeholder="custom emulator exe path (optional)"
             value="${esc(s.emu_override)}">
          <input class="cfg" id="ar-${s.id}" value="${esc(s.args)}"
             title="Placeholders: {emu} {rom} {romname} {romdir}">
          <button class="txt" onclick="saveSystem('${s.id}')">SAVE</button>
        </div></div>`;
    }).join('');

  } else {
    count.textContent = '';
    content.innerHTML = `<div class="card">
        <div class="head"><span class="nm">Folders</span></div>
        <div class="flabel">GAMES FOLDER (scanned recursively, subfolders included)</div>
        <div class="fields"><input class="cfg" id="root" value="${esc(state.library_root)}"></div>
        <div class="flabel">EMULATORS FOLDER (one subfolder per system)</div>
        <div class="fields"><input class="cfg" id="emuroot" value="${esc(state.emulators_root)}"></div>
        <div class="flabel">ART FOLDER (covers + screenshots)</div>
        <div class="fields"><input class="cfg" id="artroot" value="${esc(state.art_root)}"></div>
        <div class="fields">
          <button class="filled" onclick="saveSettings()">SAVE &amp; RESCAN</button>
          <button class="outlined" onclick="mkdirs()">CREATE FOLDER LAYOUT</button>
        </div></div>
      <div class="card howto">
        <h3>HOW IT WORKS</h3>
        RetroShelf scans the games folder recursively and works out each game's
        system from its file extension, with folder names as hints
        (a folder with "n64" or "amiga" in the name tips the balance for
        ambiguous files like .zip / .iso / .bin / .rar).
        <h3>EMULATORS</h3>
        The SYSTEMS tab shows every system: one-click DOWNLOAD where the emulator
        ships as a plain zip (fetched from its official releases), or a link to
        the download page where it needs a manual install. Emulators land in
        <code>emulators\\&lt;system&gt;\\</code> and the exe is found automatically.
        <h3>ART</h3>
        Covers: image named like the rom, next to it, or in
        <code>art\\&lt;system&gt;\\</code>. Screenshots: same name in a
        <code>screens\\</code> subfolder or <code>art\\&lt;system&gt;\\screens\\</code>.
        <h3>CONTROLLERS</h3>
        Plug in a pad and press any button — the indicator top-right lights up
        and you can drive the whole launcher from the controller:
        d-pad/stick to move, A to play, LB/RB to switch tab, Y to rescan.
        Works with Xbox One/Series pads (native), PS4/PS5 pads, and any
        DirectInput USB pad. PS3 pads need a Windows driver first (DsHidMini).
        In-game controls are handled by each emulator &mdash; every bundled
        emulator supports Xbox pads out of the box; check its input settings
        to remap.
      </div>`;
  }
}

async function saveSystem(id) {
  await fetch('/api/system', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id: id,
      emu_path: document.getElementById('ep-' + id).value,
      args: document.getElementById('ar-' + id).value})});
  snack('SAVED');
  refresh();
}

async function saveSettings() {
  await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      library_root: document.getElementById('root').value,
      emulators_root: document.getElementById('emuroot').value,
      art_root: document.getElementById('artroot').value})});
  snack('SAVED - RESCANNING...');
  refresh(true);
}

async function mkdirs() {
  const r = await (await fetch('/api/mkdirs', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: '{}'})).json();
  snack(r.ok ? 'FOLDERS CREATED' : r.msg);
  refresh();
}

/* ---- controller support (Gamepad API: Xbox, PlayStation, generic pads) ---- */
let gpIndex = 0;
const gpState = {last: {}, heldDir: null, lastMove: 0, fast: false, known: {}};
const TABS = ['games', 'systems', 'settings'];

function padName(id) {
  const s = (id || '').toLowerCase();
  if (s.includes('xbox') || s.includes('xinput')) return 'Xbox controller';
  if (s.includes('054c') || s.includes('sony') || s.includes('dualshock') ||
      s.includes('dualsense') || s.includes('playstation')) return 'PlayStation controller';
  return 'Controller';
}

function activePad() {
  const gps = navigator.getGamepads ? navigator.getGamepads() : [];
  for (const g of gps) if (g && g.connected) return g;
  return null;
}

function updatePadBadge(gp) {
  const b = document.getElementById('padbadge');
  const hint = document.getElementById('gphint');
  if (gp) {
    b.classList.add('on');
    b.title = padName(gp.id) + ' connected — ' + gp.id;
    hint.style.display = 'block';
  } else {
    b.classList.remove('on');
    b.title = 'No controller — press any button on the pad';
    hint.style.display = 'none';
  }
}

window.addEventListener('gamepadconnected', e => {
  gpState.known[e.gamepad.index] = true;
  updatePadBadge(e.gamepad);
  snack(padName(e.gamepad.id).toUpperCase() + ' CONNECTED');
});
window.addEventListener('gamepaddisconnected', e => {
  delete gpState.known[e.gamepad.index];
  const gp = activePad();
  updatePadBadge(gp);
  if (!gp) snack('CONTROLLER DISCONNECTED');
});

function applyGpSel(scroll) {
  const rows = document.querySelectorAll('.row');
  if (!rows.length) return;
  gpIndex = Math.max(0, Math.min(gpIndex, rows.length - 1));
  rows.forEach(r => r.classList.remove('gpsel'));
  rows[gpIndex].classList.add('gpsel');
  if (scroll) rows[gpIndex].scrollIntoView({block: 'center'});
}

function moveSel(dy) {
  if (tab !== 'games') return;
  gpIndex += dy;
  applyGpSel(true);
}

function moveChip(dx) {
  if (tab !== 'games') return;
  const ids = ['all'].concat(state.systems.filter(s => s.games.length).map(s => s.id));
  let i = ids.indexOf(sel) + dx;
  i = (i + ids.length) % ids.length;
  sel = ids[i];
  gpIndex = 0;
  shown = 400;
  render();
  applyGpSel(true);
}

function activateSel() {
  if (tab !== 'games') return;
  const rows = document.querySelectorAll('.row');
  if (rows[gpIndex]) rows[gpIndex].click();
}

function cycleTab(d) {
  let i = (TABS.indexOf(tab) + d + TABS.length) % TABS.length;
  setTab(TABS[i]);
  if (tab === 'games') applyGpSel(false);
}

function pollPads() {
  requestAnimationFrame(pollPads);
  const gp = activePad();
  if (!gp) return;
  if (!gpState.known[gp.index]) {         // pad was connected before page load
    gpState.known[gp.index] = true;
    updatePadBadge(gp);
  }
  const now = performance.now();
  const btn = i => !!(gp.buttons[i] && gp.buttons[i].pressed);
  const ax = i => gp.axes[i] || 0;
  const pressed = {};
  for (const i of [0, 1, 3, 4, 5]) {
    pressed[i] = btn(i) && !gpState.last[i];
    gpState.last[i] = btn(i);
  }
  let dy = 0, dx = 0;
  if (btn(12) || ax(1) < -0.5) dy = -1;
  else if (btn(13) || ax(1) > 0.5) dy = 1;
  if (btn(14) || ax(0) < -0.5) dx = -1;
  else if (btn(15) || ax(0) > 0.5) dx = 1;
  if (dy || dx) {
    const dir = dx + ',' + dy;
    const first = gpState.heldDir !== dir;
    if (first || now - gpState.lastMove > (gpState.fast ? 90 : 320)) {
      if (!first) gpState.fast = true;
      gpState.heldDir = dir;
      gpState.lastMove = now;
      if (dy) moveSel(dy);
      if (dx) moveChip(dx);
    }
  } else {
    gpState.heldDir = null;
    gpState.fast = false;
  }
  if (pressed[0]) activateSel();                                  // A / Cross
  if (pressed[1]) { gpIndex = 0; applyGpSel(true); }              // B / Circle
  if (pressed[3]) { snack('RESCANNING...'); refresh(true); }      // Y / Triangle
  if (pressed[4]) cycleTab(-1);                                   // LB / L1
  if (pressed[5]) cycleTab(1);                                    // RB / R1
}
pollPads();

refresh();
</script>
</body>
</html>
"""


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"RetroShelf running at {url}  (Ctrl+C to quit)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
