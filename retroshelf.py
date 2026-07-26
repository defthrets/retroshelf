#!/usr/bin/env python3
"""RetroShelf — single-file retro game launcher.

Serves a local web GUI that scans a library folder for ROMs and emulators,
shows every game per system, and launches the matching emulator.

Layout it expects (created via Settings > Create Folder Layout):

    <library_root>\
        roms\<system_id>\        <- drop your game files here
        emulators\<system_id>\   <- drop the emulator (unzipped) here
        art\<system_id>\         <- optional box art, same filename as rom

Box art is also picked up from an image sitting next to the rom with the
same name, or from an "art"/"covers" subfolder inside the rom directory.

Run:  python retroshelf.py          (opens browser)
      python retroshelf.py --no-browser
"""

import json
import mimetypes
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 7830
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "retroshelf.json"

DEFAULT_ARGS = '"{emu}" "{rom}"'

# Each system: folder id, display name, rom extensions, exe names to look for,
# launch template, and where to get the emulator (shown in the Systems tab).
SYSTEMS = [
    {"id": "nes", "name": "Nintendo NES", "exts": [".nes", ".fds", ".zip"],
     "exes": ["mesen.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Mesen", "emu_site": "mesen.ca"},
    {"id": "snes", "name": "Super Nintendo", "exts": [".sfc", ".smc", ".zip"],
     "exes": ["snes9x-x64.exe", "snes9x.exe", "bsnes.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Snes9x", "emu_site": "snes9x.com"},
    {"id": "n64", "name": "Nintendo 64", "exts": [".z64", ".n64", ".v64", ".zip"],
     "exes": ["simple64-gui.exe", "project64.exe"], "args": DEFAULT_ARGS,
     "emu_name": "simple64", "emu_site": "simple64.github.io"},
    {"id": "gb", "name": "Game Boy / Color", "exts": [".gb", ".gbc", ".zip"],
     "exes": ["mgba.exe"], "args": DEFAULT_ARGS,
     "emu_name": "mGBA", "emu_site": "mgba.io"},
    {"id": "gba", "name": "Game Boy Advance", "exts": [".gba", ".zip"],
     "exes": ["mgba.exe"], "args": DEFAULT_ARGS,
     "emu_name": "mGBA", "emu_site": "mgba.io"},
    {"id": "nds", "name": "Nintendo DS", "exts": [".nds"],
     "exes": ["melonds.exe"], "args": DEFAULT_ARGS,
     "emu_name": "melonDS", "emu_site": "melonds.kuribo64.net"},
    {"id": "gamecube", "name": "GameCube", "exts": [".iso", ".gcm", ".rvz", ".ciso"],
     "exes": ["dolphin.exe"], "args": '"{emu}" -e "{rom}"',
     "emu_name": "Dolphin", "emu_site": "dolphin-emu.org"},
    {"id": "wii", "name": "Nintendo Wii", "exts": [".iso", ".wbfs", ".rvz"],
     "exes": ["dolphin.exe"], "args": '"{emu}" -e "{rom}"',
     "emu_name": "Dolphin", "emu_site": "dolphin-emu.org"},
    {"id": "genesis", "name": "Sega Mega Drive", "exts": [".md", ".gen", ".smd", ".bin", ".zip"],
     "exes": ["blastem.exe", "fusion.exe"], "args": DEFAULT_ARGS,
     "emu_name": "BlastEm", "emu_site": "retrodev.com/blastem"},
    {"id": "dreamcast", "name": "Sega Dreamcast", "exts": [".chd", ".gdi", ".cdi"],
     "exes": ["flycast.exe", "redream.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Flycast", "emu_site": "flycast.dev"},
    {"id": "ps1", "name": "PlayStation", "exts": [".cue", ".chd", ".pbp", ".m3u", ".bin", ".img"],
     "exes": ["duckstation-qt-x64-releaseltcg.exe", "duckstation-qt.exe", "duckstation.exe"],
     "args": DEFAULT_ARGS,
     "emu_name": "DuckStation", "emu_site": "duckstation.org"},
    {"id": "ps2", "name": "PlayStation 2", "exts": [".iso", ".chd", ".cso"],
     "exes": ["pcsx2-qt.exe", "pcsx2-qtx64-avx2.exe", "pcsx2.exe"], "args": DEFAULT_ARGS,
     "emu_name": "PCSX2", "emu_site": "pcsx2.net"},
    {"id": "psp", "name": "PlayStation Portable", "exts": [".iso", ".cso", ".chd"],
     "exes": ["ppssppwindows64.exe", "ppssppwindows.exe"], "args": DEFAULT_ARGS,
     "emu_name": "PPSSPP", "emu_site": "ppsspp.org"},
    {"id": "arcade", "name": "Arcade (MAME)", "exts": [".zip", ".7z"],
     "exes": ["mame.exe"], "args": '"{emu}" {romname} -rompath "{romdir}"',
     "emu_name": "MAME", "emu_site": "mamedev.org"},
    {"id": "atari2600", "name": "Atari 2600", "exts": [".a26", ".bin"],
     "exes": ["stella.exe"], "args": DEFAULT_ARGS,
     "emu_name": "Stella", "emu_site": "stella-emu.github.io"},
]

SYSTEMS_BY_ID = {s["id"]: s for s in SYSTEMS}
ART_EXTS = [".png", ".jpg", ".jpeg", ".webp"]

_lock = threading.Lock()


def default_config():
    return {"library_root": "C:\\RetroShelf", "overrides": {}, "stats": {}}


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
    emu_dir = Path(cfg["library_root"]) / "emulators" / sysdef["id"]
    if not emu_dir.is_dir():
        return None
    wanted = [n.lower() for n in sysdef["exes"]]
    exes = [p for p in emu_dir.rglob("*.exe")]
    for p in exes:
        if p.name.lower() in wanted:
            return p
    if len(exes) == 1:  # a lone exe in the folder is almost certainly it
        return exes[0]
    return None


def find_art(sysdef, rom_path, cfg, kind="cover"):
    stem = rom_path.stem
    root = Path(cfg["library_root"])
    if kind == "screen":
        dirs = [rom_path.parent / "screens", rom_path.parent / "screenshots",
                root / "art" / sysdef["id"] / "screens"]
    else:
        dirs = [rom_path.parent, rom_path.parent / "art", rom_path.parent / "covers",
                root / "art" / sysdef["id"]]
    for d in dirs:
        for ext in ART_EXTS:
            c = d / (stem + ext)
            if c.is_file():
                return c
    return None


def scan_games(sysdef, cfg):
    roms_dir = Path(cfg["library_root"]) / "roms" / sysdef["id"]
    games = []
    if not roms_dir.is_dir():
        return games, roms_dir
    exts = set(sysdef["exts"])
    files = sorted(roms_dir.rglob("*"), key=lambda p: p.name.lower())
    stems_with_cue = {p.stem.lower() for p in files if p.suffix.lower() in (".cue", ".gdi", ".m3u")}
    for p in files:
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if p.parent.name.lower() in ("art", "covers", "screens", "screenshots"):
            continue
        # hide raw discs that already have a cue/gdi/m3u pointing at them
        if p.suffix.lower() in (".bin", ".img", ".iso") and p.stem.lower() in stems_with_cue:
            continue
        stat = cfg["stats"].get(str(p), {})
        games.append({
            "name": p.stem,
            "file": str(p),
            "art": find_art(sysdef, p, cfg) is not None,
            "shot": find_art(sysdef, p, cfg, "screen") is not None,
            "plays": stat.get("plays", 0),
            "last": stat.get("last", 0),
        })
    return games, roms_dir


def build_state(cfg):
    systems = []
    for sysdef in SYSTEMS:
        emu = find_emulator(sysdef, cfg)
        games, roms_dir = scan_games(sysdef, cfg)
        override = cfg["overrides"].get(sysdef["id"], {})
        systems.append({
            "id": sysdef["id"],
            "name": sysdef["name"],
            "exts": sysdef["exts"],
            "emu_name": sysdef["emu_name"],
            "emu_site": sysdef["emu_site"],
            "emu_found": emu is not None,
            "emu_path": str(emu) if emu else "",
            "emu_override": override.get("emu_path", ""),
            "args": override.get("args", sysdef["args"]),
            "default_args": sysdef["args"],
            "roms_dir": str(roms_dir),
            "emu_dir": str(Path(cfg["library_root"]) / "emulators" / sysdef["id"]),
            "games": games,
        })
    return {"library_root": cfg["library_root"], "systems": systems}


def launch_game(cfg, system_id, rom):
    sysdef = SYSTEMS_BY_ID.get(system_id)
    if not sysdef:
        return False, "unknown system"
    rom_path = Path(rom)
    root = Path(cfg["library_root"]).resolve()
    try:
        rom_path.resolve().relative_to(root)
    except ValueError:
        return False, "rom is outside the library folder"
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
    root = Path(cfg["library_root"])
    for sub in ("roms", "emulators", "art"):
        for sysdef in SYSTEMS:
            (root / sub / sysdef["id"]).mkdir(parents=True, exist_ok=True)
    for sysdef in SYSTEMS:
        (root / "art" / sysdef["id"] / "screens").mkdir(parents=True, exist_ok=True)


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
            with _lock:
                self._json(build_state(load_config()))
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
            elif parsed.path == "/api/settings":
                root = body.get("library_root", "").strip()
                if root:
                    cfg["library_root"] = root
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
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {
  --blue: #4285f4; --red: #ea4335; --yellow: #fbbc05; --green: #34a853;
  --btn-blue: #1a73e8; --text: #202124; --sub: #5f6368; --line: #dadce0;
  --line2: #e8eaed; --hover: #f8f9fa; --ph: #f1f3f4;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #fff; color: var(--text);
       font-family: Roboto, Arial, sans-serif; font-size: 14px; }
header { position: sticky; top: 0; z-index: 10; background: #fff; }
.bar { display: flex; align-items: center; gap: 24px; padding: 14px 24px 8px; }
.logo { font-size: 24px; font-weight: 500; letter-spacing: -.5px; white-space: nowrap;
        cursor: pointer; user-select: none; }
.searchwrap { flex: 1; max-width: 640px; position: relative; }
#search {
  width: 100%; height: 44px; border: 1px solid var(--line); border-radius: 22px;
  padding: 0 20px 0 48px; font: inherit; font-size: 16px; color: var(--text);
  outline: none; background: #fff;
}
#search:hover, #search:focus { box-shadow: 0 1px 6px rgba(32,33,36,.24); border-color: transparent; }
.searchwrap svg { position: absolute; left: 16px; top: 12px; width: 20px; height: 20px;
                  fill: #9aa0a6; }
#count { color: var(--sub); font-size: 13px; margin-left: auto; white-space: nowrap; }
.tabs { display: flex; gap: 8px; padding: 0 24px; border-bottom: 1px solid var(--line2); }
.tabs button {
  background: none; border: none; font: inherit; font-size: 14px; color: var(--sub);
  padding: 12px 14px 9px; cursor: pointer; border-bottom: 3px solid transparent;
}
.tabs button.on { color: var(--btn-blue); border-bottom-color: var(--btn-blue);
                  font-weight: 500; }
.tabs button:hover:not(.on) { color: var(--text); }
.chips { display: flex; gap: 8px; padding: 14px 24px 4px; flex-wrap: wrap;
         max-width: 1020px; margin: 0 auto; }
.chip {
  border: 1px solid var(--line); border-radius: 16px; padding: 6px 14px;
  font-size: 13px; color: var(--text); cursor: pointer; background: #fff;
  white-space: nowrap;
}
.chip:hover { background: var(--hover); }
.chip.on { background: #e8f0fe; color: #1967d2; border-color: #e8f0fe; font-weight: 500; }
.chip span { color: var(--sub); font-size: 12px; margin-left: 4px; }
.chip.on span { color: #1967d2; }
main { max-width: 1020px; margin: 0 auto; padding: 8px 24px 60px; }
.row {
  display: flex; align-items: center; gap: 18px; padding: 12px 14px;
  border-radius: 12px; cursor: pointer; position: relative;
}
.row:hover { background: var(--hover); }
.row + .row { margin-top: 2px; }
.cover, .shot {
  border-radius: 8px; background: var(--ph); flex-shrink: 0; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
.cover { width: 72px; height: 96px; }
.shot { width: 170px; height: 96px; }
.cover img, .shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cover .ph { font-size: 24px; font-weight: 500; color: #bdc1c6; }
.shot .ph2 { font-size: 11px; color: #bdc1c6; }
.info { min-width: 0; flex: 1; }
.info .nm { font-size: 16px; font-weight: 500; color: var(--text);
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.info .sub { font-size: 13px; color: var(--sub); margin-top: 5px; }
.info .sub b { color: var(--sub); font-weight: 500; }
.playbtn {
  width: 44px; height: 44px; border-radius: 50%; background: var(--btn-blue);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 16px; flex-shrink: 0; opacity: 0; transition: opacity .12s;
  box-shadow: 0 1px 3px rgba(60,64,67,.3);
}
.row:hover .playbtn { opacity: 1; }
.empty { text-align: center; padding: 90px 20px; color: var(--sub); line-height: 2; }
.empty .big { font-size: 20px; color: var(--text); margin-bottom: 8px; }
.empty code { background: var(--ph); border-radius: 4px; padding: 2px 8px;
              font-size: 13px; }
.card {
  border: 1px solid var(--line); border-radius: 12px; padding: 18px 22px;
  margin: 14px 0; background: #fff;
}
.card .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.card .head .nm { font-size: 16px; font-weight: 500; }
.pill { border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 500; }
.pill.ok { background: #e6f4ea; color: #137333; }
.pill.bad { background: #fce8e6; color: #c5221f; }
.dim { color: var(--sub); font-size: 13px; margin-top: 8px; line-height: 1.7;
       word-break: break-all; }
.dim b { color: var(--text); font-weight: 500; }
.fields { display: flex; gap: 12px; margin-top: 12px; flex-wrap: wrap;
          align-items: center; }
input.cfg {
  border: 1px solid var(--line); border-radius: 6px; padding: 9px 12px;
  font: inherit; font-size: 13px; color: var(--text); flex: 1; min-width: 220px;
  outline: none;
}
input.cfg:focus { border-color: var(--btn-blue); box-shadow: 0 0 0 1px var(--btn-blue); }
button.txt {
  background: none; border: none; color: var(--btn-blue); font: inherit;
  font-size: 14px; font-weight: 500; padding: 8px 14px; border-radius: 6px;
  cursor: pointer;
}
button.txt:hover { background: #e8f0fe; }
button.filled {
  background: var(--btn-blue); border: none; color: #fff; font: inherit;
  font-size: 14px; font-weight: 500; padding: 9px 22px; border-radius: 6px;
  cursor: pointer;
}
button.filled:hover { background: #1765cc; box-shadow: 0 1px 3px rgba(60,64,67,.3); }
button.outlined {
  background: #fff; border: 1px solid var(--line); color: var(--btn-blue);
  font: inherit; font-size: 14px; font-weight: 500; padding: 8px 22px;
  border-radius: 6px; cursor: pointer;
}
button.outlined:hover { background: #f6f9fe; }
#snack {
  position: fixed; bottom: 24px; left: 24px; background: #323232; color: #fff;
  border-radius: 6px; padding: 13px 24px; font-size: 14px; display: none;
  z-index: 100; box-shadow: 0 3px 10px rgba(0,0,0,.3); max-width: 70vw;
}
.howto { color: var(--sub); font-size: 13px; line-height: 1.9; }
.howto code { background: var(--ph); border-radius: 4px; padding: 1px 7px;
              font-size: 12px; color: var(--text); }
.howto h3 { color: var(--text); font-size: 14px; font-weight: 500; margin: 16px 0 4px; }
@media (max-width: 700px) { .shot { display: none; } #count { display: none; } }
</style>
</head>
<body>
<header>
  <div class="bar">
    <div class="logo" onclick="setTab('games')">
      <span style="color:#4285f4">R</span><span style="color:#ea4335">e</span><span
       style="color:#fbbc05">t</span><span style="color:#4285f4">r</span><span
       style="color:#34a853">o</span><span style="color:#ea4335">S</span><span
       style="color:#4285f4">h</span><span style="color:#fbbc05">e</span><span
       style="color:#34a853">l</span><span style="color:#ea4335">f</span>
    </div>
    <div class="searchwrap">
      <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
      <input id="search" placeholder="Search your games" oninput="render()" autocomplete="off">
    </div>
    <span id="count"></span>
  </div>
  <div class="tabs">
    <button id="tab-games" class="on" onclick="setTab('games')">Games</button>
    <button id="tab-systems" onclick="setTab('systems')">Systems</button>
    <button id="tab-settings" onclick="setTab('settings')">Settings</button>
  </div>
  <div class="chips" id="chips"></div>
</header>
<main id="content"></main>
<div id="snack"></div>
<script>
let state = null;
let tab = 'games';
let sel = 'all';

async function refresh() {
  state = await (await fetch('/api/state')).json();
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
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(t._h);
  t._h = setTimeout(() => t.style.display = 'none', 3000);
}

async function launch(sysId, rom, name) {
  snack('Launching ' + name + '...');
  const r = await (await fetch('/api/launch', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({system: sysId, rom: rom})})).json();
  if (!r.ok) snack('Could not launch: ' + r.msg);
  else setTimeout(refresh, 800);
}

function allGames() {
  const out = [];
  for (const s of state.systems)
    for (const g of s.games) out.push({...g, sysId: s.id, sysName: s.name});
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
      `<div class="chip ${sel==='all'?'on':''}" onclick="sel='all';render()">All<span>${allGames().length}</span></div>` +
      withGames.map(s =>
        `<div class="chip ${sel===s.id?'on':''}" onclick="sel='${s.id}';render()">${esc(s.name)}<span>${s.games.length}</span></div>`
      ).join('');

    let games = sel === 'all' ? allGames()
      : (state.systems.find(s => s.id === sel) || {games:[]}).games
          .map(g => ({...g, sysId: sel,
                      sysName: (state.systems.find(s => s.id === sel) || {}).name || ''}));
    const q = search.value.trim().toLowerCase();
    if (q) games = games.filter(g => g.name.toLowerCase().includes(q));
    count.textContent = games.length + (games.length === 1 ? ' game' : ' games');

    if (!games.length) {
      content.innerHTML = `<div class="empty">
        <div class="big">No games yet</div>
        Drop your rom files into <code>${esc(state.library_root)}\\roms\\&lt;system&gt;\\</code><br>
        then refresh this page. The <b>Systems</b> tab lists every folder name.</div>`;
      return;
    }
    content.innerHTML = games.map(g => {
      const cover = g.art
        ? `<img loading="lazy" src="/api/art?system=${g.sysId}&rom=${encodeURIComponent(g.file)}">`
        : `<span class="ph">${esc(g.name.slice(0,2).toUpperCase())}</span>`;
      const shot = g.shot
        ? `<img loading="lazy" src="/api/art?system=${g.sysId}&kind=screen&rom=${encodeURIComponent(g.file)}">`
        : `<span class="ph2">no screenshot</span>`;
      const sub = [g.sysName, g.plays ? g.plays + (g.plays === 1 ? ' play' : ' plays') : null,
                   ago(g.last) || null].filter(Boolean).join(' · ');
      return `<div class="row" onclick='launch(${JSON.stringify(g.sysId)}, ${JSON.stringify(g.file)}, ${JSON.stringify(g.name)})'>
        <div class="cover">${cover}</div>
        <div class="shot">${shot}</div>
        <div class="info"><div class="nm">${esc(g.name)}</div>
          <div class="sub">${esc(sub)}</div></div>
        <div class="playbtn">▶</div></div>`;
    }).join('');

  } else if (tab === 'systems') {
    count.textContent = state.systems.filter(s => s.emu_found).length + ' of ' +
      state.systems.length + ' emulators ready';
    content.innerHTML = state.systems.map(s => {
      const status = s.emu_found
        ? `<span class="pill ok">Ready</span>`
        : `<span class="pill bad">Emulator missing</span>`;
      const detail = s.emu_found
        ? `Using <b>${esc(s.emu_path)}</b>`
        : `Download <b>${esc(s.emu_name)}</b> from <b>${esc(s.emu_site)}</b> and unzip it into
           <b>${esc(s.emu_dir)}\\</b> &mdash; the exe is picked up automatically.`;
      return `<div class="card">
        <div class="head"><span class="nm">${esc(s.name)}</span>${status}</div>
        <div class="dim">${detail}<br>
          Games go in <b>${esc(s.roms_dir)}\\</b> (${esc(s.exts.join(' '))})</div>
        <div class="fields">
          <input class="cfg" id="ep-${s.id}" placeholder="Custom emulator exe path (optional)"
             value="${esc(s.emu_override)}">
          <input class="cfg" id="ar-${s.id}" value="${esc(s.args)}"
             title="Placeholders: {emu} {rom} {romname} {romdir}">
          <button class="txt" onclick="saveSystem('${s.id}')">Save</button>
        </div></div>`;
    }).join('');

  } else {
    count.textContent = '';
    content.innerHTML = `<div class="card">
        <div class="head"><span class="nm">Library folder</span></div>
        <div class="fields">
          <input class="cfg" id="root" value="${esc(state.library_root)}">
          <button class="filled" onclick="saveSettings()">Save &amp; rescan</button>
          <button class="outlined" onclick="mkdirs()">Create folder layout</button>
        </div></div>
      <div class="card howto">
        <h3>How it works</h3>
        RetroShelf scans one library folder. Inside it:<br>
        <code>roms\&lt;system&gt;\</code> &mdash; your game files (subfolders are fine)<br>
        <code>emulators\&lt;system&gt;\</code> &mdash; the emulator, unzipped; the exe is found automatically<br>
        <code>art\&lt;system&gt;\</code> &mdash; cover images named exactly like the rom file<br>
        <code>art\&lt;system&gt;\screens\</code> &mdash; gameplay screenshots, same naming
        <h3>Art shortcuts</h3>
        A cover can also sit right next to the rom (same name, .png/.jpg), or in an
        <code>art\</code> / <code>covers\</code> subfolder beside it. Screenshots can sit in a
        <code>screens\</code> or <code>screenshots\</code> subfolder next to the roms.
        <h3>Launching</h3>
        Click a game and it opens in the matching emulator. The Systems tab lets you
        point at a custom exe or tweak launch arguments per system.
      </div>`;
  }
}

async function saveSystem(id) {
  await fetch('/api/system', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id: id,
      emu_path: document.getElementById('ep-' + id).value,
      args: document.getElementById('ar-' + id).value})});
  snack('Saved');
  refresh();
}

async function saveSettings() {
  await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({library_root: document.getElementById('root').value})});
  snack('Saved');
  refresh();
}

async function mkdirs() {
  const r = await (await fetch('/api/mkdirs', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: '{}'})).json();
  snack(r.ok ? 'Folders created' : r.msg);
  refresh();
}

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
