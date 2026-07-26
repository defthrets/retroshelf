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


def find_art(sysdef, rom_path, cfg):
    stem = rom_path.stem
    candidates = []
    for ext in ART_EXTS:
        candidates.append(rom_path.parent / (stem + ext))
        candidates.append(rom_path.parent / "art" / (stem + ext))
        candidates.append(rom_path.parent / "covers" / (stem + ext))
        candidates.append(Path(cfg["library_root"]) / "art" / sysdef["id"] / (stem + ext))
    for c in candidates:
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
        if p.parent.name.lower() in ("art", "covers"):
            continue
        # hide raw discs that already have a cue/gdi/m3u pointing at them
        if p.suffix.lower() in (".bin", ".img", ".iso") and p.stem.lower() in stems_with_cue:
            continue
        stat = cfg["stats"].get(str(p), {})
        games.append({
            "name": p.stem,
            "file": str(p),
            "art": find_art(sysdef, p, cfg) is not None,
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
            sysdef = SYSTEMS_BY_ID.get(system_id)
            cfg = load_config()
            art = find_art(sysdef, Path(rom), cfg) if sysdef and rom else None
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
<title>RETROSHELF</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --bg: #0b0b0d; --panel: #131316; --panel2: #1a1a1f; --line: #2a2a30;
  --amber: #ffb000; --amber-dim: #b87e00; --text: #d8d2c4; --muted: #6f6a5e;
  --green: #3fd97a; --red: #ff4d4d;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--text);
  font-family: Consolas, "Cascadia Mono", monospace; font-size: 14px;
  min-height: 100vh;
}
body::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 99;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,.14) 0 1px, transparent 1px 3px);
}
header {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  padding: 12px 18px; border-bottom: 1px solid var(--line); background: var(--panel);
  position: sticky; top: 0; z-index: 10;
}
h1 { font-size: 20px; letter-spacing: 4px; color: var(--amber);
     text-shadow: 0 0 12px rgba(255,176,0,.45); }
h1 .blink { animation: blink 1.2s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
nav { display: flex; gap: 4px; }
nav button {
  background: none; border: 1px solid var(--line); color: var(--muted);
  font: inherit; padding: 6px 14px; cursor: pointer; letter-spacing: 2px;
}
nav button.on { color: var(--bg); background: var(--amber); border-color: var(--amber); font-weight: bold; }
nav button:hover:not(.on) { color: var(--amber); border-color: var(--amber-dim); }
#search {
  background: var(--bg); border: 1px solid var(--line); color: var(--text);
  font: inherit; padding: 6px 10px; width: 220px;
}
#search:focus { outline: none; border-color: var(--amber-dim); }
#count { color: var(--muted); margin-left: auto; letter-spacing: 1px; }
main { display: flex; min-height: calc(100vh - 54px); }
aside {
  width: 220px; flex-shrink: 0; border-right: 1px solid var(--line);
  background: var(--panel); padding: 10px 0;
}
aside div {
  padding: 7px 18px; cursor: pointer; color: var(--muted);
  display: flex; justify-content: space-between; letter-spacing: 1px;
}
aside div:hover { color: var(--text); }
aside div.on { color: var(--amber); background: var(--panel2);
               border-left: 3px solid var(--amber); padding-left: 15px; }
aside div span { color: var(--muted); font-size: 12px; }
#content { flex: 1; padding: 18px; }
#grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; }
.card {
  background: var(--panel); border: 1px solid var(--line); cursor: pointer;
  display: flex; flex-direction: column; transition: border-color .1s, transform .1s;
}
.card:hover { border-color: var(--amber); transform: translateY(-2px); }
.card .box {
  aspect-ratio: 3/4; display: flex; align-items: center; justify-content: center;
  background: var(--panel2); overflow: hidden; position: relative;
}
.card .box img { width: 100%; height: 100%; object-fit: cover; }
.card .box .ph {
  font-size: 30px; color: var(--amber-dim); letter-spacing: 2px;
  text-shadow: 0 0 10px rgba(255,176,0,.3);
}
.card .box .play {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: rgba(11,11,13,.75); opacity: 0; transition: opacity .1s;
  color: var(--amber); font-size: 34px;
}
.card:hover .box .play { opacity: 1; }
.card .meta { padding: 8px 10px; }
.card .meta .nm { color: var(--text); font-size: 13px; word-break: break-word; }
.card .meta .sub { color: var(--muted); font-size: 11px; margin-top: 4px; letter-spacing: 1px; }
.empty {
  border: 1px dashed var(--line); padding: 40px; text-align: center;
  color: var(--muted); line-height: 2; letter-spacing: 1px;
}
.empty b { color: var(--amber); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { color: var(--amber); letter-spacing: 2px; font-size: 12px; }
td { font-size: 13px; }
.ok { color: var(--green); }
.bad { color: var(--red); }
.dim { color: var(--muted); font-size: 12px; }
input.cfg {
  background: var(--bg); border: 1px solid var(--line); color: var(--text);
  font: inherit; font-size: 12px; padding: 5px 8px; width: 100%; margin-top: 4px;
}
input.cfg:focus { outline: none; border-color: var(--amber-dim); }
button.act {
  background: none; border: 1px solid var(--amber-dim); color: var(--amber);
  font: inherit; font-size: 12px; padding: 5px 14px; cursor: pointer;
  letter-spacing: 1px; margin-top: 6px;
}
button.act:hover { background: var(--amber); color: var(--bg); }
#toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: var(--panel); border: 1px solid var(--amber); color: var(--amber);
  padding: 10px 24px; letter-spacing: 2px; display: none; z-index: 100;
  box-shadow: 0 0 20px rgba(255,176,0,.25);
}
.settings-box { max-width: 640px; }
.settings-box h2 { color: var(--amber); letter-spacing: 2px; font-size: 14px;
                   margin: 18px 0 8px; }
.settings-box p { color: var(--muted); font-size: 12px; line-height: 1.7; margin: 6px 0; }
.settings-box code { color: var(--text); }
</style>
</head>
<body>
<header>
  <h1>RETROSHELF<span class="blink">_</span></h1>
  <nav>
    <button id="tab-library" class="on" onclick="setTab('library')">LIBRARY</button>
    <button id="tab-systems" onclick="setTab('systems')">SYSTEMS</button>
    <button id="tab-settings" onclick="setTab('settings')">SETTINGS</button>
  </nav>
  <input id="search" placeholder="search..." oninput="render()">
  <span id="count"></span>
</header>
<main>
  <aside id="sidebar"></aside>
  <div id="content"></div>
</main>
<div id="toast"></div>
<script>
let state = null;
let tab = 'library';
let sel = 'all';

async function refresh() {
  state = await (await fetch('/api/state')).json();
  render();
}

function setTab(t) {
  tab = t;
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('on'));
  document.getElementById('tab-' + t).classList.add('on');
  render();
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toast(msg, bad) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.borderColor = bad ? 'var(--red)' : 'var(--amber)';
  t.style.color = bad ? 'var(--red)' : 'var(--amber)';
  t.style.display = 'block';
  clearTimeout(t._h);
  t._h = setTimeout(() => t.style.display = 'none', 2600);
}

async function launch(sysId, rom, name) {
  toast('▶ LAUNCHING ' + name.toUpperCase());
  const r = await (await fetch('/api/launch', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({system: sysId, rom: rom})})).json();
  if (!r.ok) toast('▲ ' + r.msg.toUpperCase(), true);
  else setTimeout(refresh, 800);
}

function allGames() {
  const out = [];
  for (const s of state.systems)
    for (const g of s.games) out.push({...g, sysId: s.id, sysName: s.name});
  return out;
}

function render() {
  if (!state) return;
  const side = document.getElementById('sidebar');
  const content = document.getElementById('content');
  const search = document.getElementById('search');
  search.style.display = tab === 'library' ? '' : 'none';
  side.style.display = tab === 'library' ? '' : 'none';

  if (tab === 'library') {
    const withGames = state.systems.filter(s => s.games.length);
    let html = `<div class="${sel==='all'?'on':''}" onclick="sel='all';render()">ALL GAMES <span>${allGames().length}</span></div>`;
    for (const s of withGames)
      html += `<div class="${sel===s.id?'on':''}" onclick="sel='${s.id}';render()">${esc(s.name.toUpperCase())} <span>${s.games.length}</span></div>`;
    side.innerHTML = html;

    let games = sel === 'all' ? allGames()
      : (state.systems.find(s => s.id === sel) || {games:[]}).games
          .map(g => ({...g, sysId: sel, sysName: ''}));
    const q = search.value.trim().toLowerCase();
    if (q) games = games.filter(g => g.name.toLowerCase().includes(q));
    document.getElementById('count').textContent = games.length + ' GAMES';

    if (!games.length) {
      content.innerHTML = `<div class="empty">NO GAMES FOUND<br>
        drop rom files into <b>${esc(state.library_root)}\\roms\\&lt;system&gt;\\</b><br>
        then refresh this page &mdash; see the <b>SYSTEMS</b> tab for folder names</div>`;
      return;
    }
    content.innerHTML = '<div id="grid">' + games.map(g => {
      const art = g.art
        ? `<img loading="lazy" src="/api/art?system=${g.sysId}&rom=${encodeURIComponent(g.file)}">`
        : `<span class="ph">${esc(g.name.slice(0,2).toUpperCase())}</span>`;
      const sub = [g.sysName ? esc(g.sysName.toUpperCase()) : null,
                   g.plays ? '▶ ' + g.plays : null].filter(Boolean).join(' &nbsp;·&nbsp; ');
      return `<div class="card" onclick='launch(${JSON.stringify(g.sysId)}, ${JSON.stringify(g.file)}, ${JSON.stringify(g.name)})'>
        <div class="box">${art}<div class="play">▶</div></div>
        <div class="meta"><div class="nm">${esc(g.name)}</div>
        ${sub ? '<div class="sub">' + sub + '</div>' : ''}</div></div>`;
    }).join('') + '</div>';
    document.getElementById('count').textContent = games.length + ' GAMES';

  } else if (tab === 'systems') {
    document.getElementById('count').textContent =
      state.systems.filter(s => s.emu_found).length + '/' + state.systems.length + ' EMULATORS READY';
    content.innerHTML = '<table><tr><th>SYSTEM</th><th>EMULATOR</th><th>SETUP</th></tr>' +
      state.systems.map(s => {
        const status = s.emu_found
          ? `<span class="ok">● FOUND</span><div class="dim">${esc(s.emu_path)}</div>`
          : `<span class="bad">● MISSING</span>
             <div class="dim">get <b>${esc(s.emu_name)}</b> (${esc(s.emu_site)})<br>
             unzip into ${esc(s.emu_dir)}\\</div>`;
        return `<tr><td><b>${esc(s.name)}</b>
            <div class="dim">roms: ${esc(s.exts.join(' '))}<br>${esc(s.roms_dir)}\\</div></td>
          <td>${status}</td>
          <td><input class="cfg" id="ep-${s.id}" placeholder="custom exe path (optional)"
                 value="${esc(s.emu_override)}">
              <input class="cfg" id="ar-${s.id}" value="${esc(s.args)}"
                 title="placeholders: {emu} {rom} {romname} {romdir}">
              <button class="act" onclick="saveSystem('${s.id}')">SAVE</button></td></tr>`;
      }).join('') + '</table>';

  } else {
    document.getElementById('count').textContent = '';
    content.innerHTML = `<div class="settings-box">
      <h2>LIBRARY FOLDER</h2>
      <input class="cfg" id="root" value="${esc(state.library_root)}">
      <button class="act" onclick="saveSettings()">SAVE &amp; RESCAN</button>
      <button class="act" onclick="mkdirs()">CREATE FOLDER LAYOUT</button>
      <h2>HOW IT WORKS</h2>
      <p>RetroShelf scans one library folder. Inside it:</p>
      <p><code>roms\&lt;system&gt;\</code> &mdash; your game files (subfolders are fine)<br>
         <code>emulators\&lt;system&gt;\</code> &mdash; the emulator, unzipped; the exe is found automatically<br>
         <code>art\&lt;system&gt;\</code> &mdash; optional box art named exactly like the rom file</p>
      <p>Box art is also picked up from an image next to the rom, or an
         <code>art\</code> / <code>covers\</code> subfolder beside it.</p>
      <p>The SYSTEMS tab shows every folder name, which emulator to download for it,
         and lets you point at a custom exe or edit the launch arguments.</p>
    </div>`;
  }
}

async function saveSystem(id) {
  await fetch('/api/system', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id: id,
      emu_path: document.getElementById('ep-' + id).value,
      args: document.getElementById('ar-' + id).value})});
  toast('★ SAVED');
  refresh();
}

async function saveSettings() {
  await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({library_root: document.getElementById('root').value})});
  toast('★ SAVED');
  refresh();
}

async function mkdirs() {
  const r = await (await fetch('/api/mkdirs', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: '{}'})).json();
  toast(r.ok ? '★ FOLDERS CREATED' : '▲ ' + r.msg.toUpperCase(), !r.ok);
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
