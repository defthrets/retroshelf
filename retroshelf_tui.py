#!/usr/bin/env python3
"""RetroShelf TUI — the game library in a terminal, no desktop required.

Built for old hardware and SSH sessions: pure curses, stdlib only, works on
an 80x24 screen. Two ways to run it:

  Remote (thin client)   the games and emulators live on another machine
      homelab$  python3 retroshelf.py --serve --host 0.0.0.0
      thinkpad$ python3 retroshelf_tui.py --host homelab
    Choosing a game launches it on the homelab, which is where the emulator,
    the discs and the graphics power are.

  Browse remote, play here   the collection lives on the homelab, the
                             emulator runs on this machine, which has a screen
      thinkpad$ python3 retroshelf_tui.py --host homelab --play-here \
                        --map /mnt/oldgames=/media/games
    Drop --map if the games are mounted at the same path on both machines.

  Local                  everything on this machine
      python3 retroshelf_tui.py --local

Other modes:
      python3 retroshelf_tui.py --list            print the library and exit
      python3 retroshelf_tui.py --list --system ps1
      python3 retroshelf_tui.py --play "metal gear"   launch by name, no UI

Keys: arrows / jk move, tab switches pane, / search, enter plays,
      f favourite, r rescan, q quit.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 7830


def arg(name, default=None):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def has(flag):
    return flag in sys.argv


# --- talking to a RetroShelf server ----------------------------------------

class Remote:
    """Client for a RetroShelf server, local or on another machine."""

    kind = "remote"

    def __init__(self, host, port):
        if "://" in host:
            self.base = host.rstrip("/")
        else:
            if ":" in host and not host.startswith("["):
                host, _, p = host.partition(":")
                port = int(p)
            self.base = f"http://{host}:{port}"

    def _get(self, path, timeout=30):
        req = urllib.request.Request(self.base + path,
                                     headers={"User-Agent": "RetroShelf-TUI"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def _post(self, path, payload, timeout=120):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "RetroShelf-TUI"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def state(self, rescan=False):
        return self._get("/api/state" + ("?rescan=1" if rescan else ""))

    def details(self, game):
        q = urllib.parse.urlencode({"rom": game["file"], "system": game["sysId"],
                                    "name": game["name"]})
        try:
            return self._get("/api/details?" + q, timeout=15)
        except Exception:
            return {}

    def launch(self, game):
        try:
            r = self._post("/api/launch",
                           {"system": game["sysId"], "rom": game["file"]})
            return bool(r.get("ok")), r.get("msg", "")
        except urllib.error.URLError as e:
            return False, str(e.reason)
        except Exception as e:
            return False, str(e)

    def favourite(self, game, on):
        try:
            self._post("/api/meta", {"rom": game["file"], "fav": on}, timeout=15)
        except Exception:
            pass

    def where(self):
        return self.base


class Hybrid:
    """Browse a remote library, but run the game on this machine.

    The homelab keeps the collection, the artwork and the play counts; the
    emulator runs here, where there is a screen. Use --map when the games are
    mounted at a different path locally, e.g.
        --map /mnt/oldgames=/media/games
    """

    kind = "browse remote, play here"

    def __init__(self, remote, mappings):
        self.remote = remote
        self.maps = mappings
        sys.path.insert(0, str(HERE))
        import retroshelf as rs           # noqa: E402
        self.rs = rs

    def _localise(self, path):
        for src, dst in self.maps:
            if path.startswith(src):
                path = dst + path[len(src):]
                break
        return path.replace("\\", "/")

    def state(self, rescan=False):
        return self.remote.state(rescan)

    def details(self, game):
        return self.remote.details(game)

    def favourite(self, game, on):
        self.remote.favourite(game, on)

    def launch(self, game):
        rom = self._localise(game["file"])
        if not Path(rom).exists():
            return False, (f"{rom} is not readable here - mount the games "
                           "folder, or pass --map remote=local")
        cfg = self.rs.load_config()
        try:
            ok, msg = self.rs.launch_game(cfg, game["sysId"], rom)
        except Exception as e:
            return False, str(e)
        if ok:                     # keep the homelab's play count in step
            try:
                self.remote._post("/api/meta", {"rom": game["file"]}, timeout=10)
            except Exception:
                pass
        return ok, msg

    def where(self):
        return self.remote.base + " (playing here)"


class Local:
    """Runs the scan and launches games in this process, no server needed."""

    kind = "local"

    def __init__(self):
        sys.path.insert(0, str(HERE))
        import retroshelf as rs           # noqa: E402  (same folder)
        self.rs = rs
        self._state = None

    def state(self, rescan=False):
        cfg = self.rs.load_config()
        if rescan:
            self.rs.get_games(cfg, force=True)
        self._state = self.rs.build_state(cfg)
        return self._state

    def details(self, game):
        p = Path(game["file"])
        info = {"folder": str(p.parent), "file": p.name, "size": 0}
        try:
            info["size"] = p.stat().st_size
        except OSError:
            pass
        try:
            info["meta"] = self.rs.meta_lookup(game["sysId"], game["name"])
        except Exception:
            info["meta"] = None
        return info

    def launch(self, game):
        cfg = self.rs.load_config()
        try:
            return self.rs.launch_game(cfg, game["sysId"], game["file"])
        except Exception as e:
            return False, str(e)

    def favourite(self, game, on):
        cfg = self.rs.load_config()
        cfg["stats"].setdefault(game["file"], {})["fav"] = on
        self.rs.save_config(cfg)

    def where(self):
        return str(self.rs.CONFIG_PATH.parent)


def flatten(state):
    """All games with their system attached, sorted by name."""
    out = []
    for s in state.get("systems", []):
        for g in s.get("games", []):
            g = dict(g)
            g["sysId"] = s["id"]
            g["sysName"] = s["name"]
            out.append(g)
    out.sort(key=lambda g: g["name"].lower())
    return out


def fmt_size(n):
    if not n:
        return "-"
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return str(n)


# --- plain text modes -------------------------------------------------------

def cmd_list(backend):
    state = backend.state()
    want = arg("--system")
    games = flatten(state)
    if want:
        games = [g for g in games if g["sysId"] == want]
    for s in state["systems"]:
        if s["games"]:
            mark = "ok " if s["emu_found"] else "no "
            print(f"  [{mark}] {s['id']:10} {len(s['games']):5} games   "
                  f"{s['emu_name'] if not s['emu_found'] else s['emu_path']}")
    print(f"\n{len(games)} games\n")
    for g in games:
        star = "*" if g.get("fav") else " "
        rating = ("%d" % g["rating"]) if g.get("rating") else "-"
        print(f" {star}{g['sysId']:10} {rating}  {g['name']}")
    return 0


def cmd_play(backend, needle):
    games = flatten(backend.state())
    needle = needle.lower()
    hits = [g for g in games if needle in g["name"].lower()]
    if not hits:
        print("no game matches", needle)
        return 1
    if len(hits) > 1:
        exact = [g for g in hits if g["name"].lower() == needle]
        if exact:
            hits = exact
        else:
            print(f"{len(hits)} matches, be more specific:")
            for g in hits[:15]:
                print("   ", g["sysId"], "-", g["name"])
            return 1
    g = hits[0]
    print(f"launching {g['name']} ({g['sysName']}) on {backend.where()} ...")
    ok, msg = backend.launch(g)
    print(("ok: " if ok else "failed: ") + msg)
    return 0 if ok else 1


# --- curses interface -------------------------------------------------------

def run_tui(backend):
    import curses

    def inner(scr):
        curses.curs_set(0)
        scr.nodelay(False)
        scr.keypad(True)
        colours = {}
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            pairs = [("hdr", curses.COLOR_YELLOW), ("sel", curses.COLOR_BLACK),
                     ("dim", curses.COLOR_CYAN), ("ok", curses.COLOR_GREEN),
                     ("bad", curses.COLOR_RED), ("fav", curses.COLOR_MAGENTA)]
            for i, (name, col) in enumerate(pairs, start=1):
                if name == "sel":
                    curses.init_pair(i, curses.COLOR_BLACK, curses.COLOR_YELLOW)
                else:
                    curses.init_pair(i, col, bg)
                colours[name] = curses.color_pair(i)

        def C(name, extra=0):
            return colours.get(name, 0) | extra

        state = backend.state()
        systems = [s for s in state.get("systems", []) if s["games"]]
        sel_sys = 0            # 0 = all games, then systems
        sel_game = 0
        top = 0
        pane = 1               # 0 = systems, 1 = games
        query = ""
        status = f"{backend.kind} {backend.where()}"
        details_cache = {}

        def visible_games():
            games = flatten(state)
            if sel_sys > 0:
                sid = systems[sel_sys - 1]["id"]
                games = [g for g in games if g["sysId"] == sid]
            if query:
                q = query.lower()
                games = [g for g in games if q in g["name"].lower()]
            return games

        while True:
            h, w = scr.getmaxyx()
            games = visible_games()
            if sel_game >= len(games):
                sel_game = max(0, len(games) - 1)
            listh = max(1, h - 6)
            if sel_game < top:
                top = sel_game
            elif sel_game >= top + listh:
                top = sel_game - listh + 1

            scr.erase()
            title = "RETROSHELF"
            scr.addstr(0, 0, title[:w - 1], C("hdr", curses.A_BOLD))
            right = f"{len(games)} games"
            if len(title) + len(right) + 4 < w:
                scr.addstr(0, w - len(right) - 1, right, C("dim"))
            scr.hline(1, 0, curses.ACS_HLINE, w)

            sidew = min(22, max(12, w // 5))
            for i in range(min(len(systems) + 1, h - 4)):
                name = "All games" if i == 0 else systems[i - 1]["name"]
                count = (len(flatten(state)) if i == 0
                         else len(systems[i - 1]["games"]))
                line = f"{name[:sidew - 7]:<{sidew - 7}}{count:>5}"
                attr = C("sel") if (i == sel_sys and pane == 0) else (
                    C("hdr") if i == sel_sys else 0)
                try:
                    scr.addstr(2 + i, 0, line[:sidew - 1], attr)
                except curses.error:
                    pass

            gx = sidew
            gw = w - sidew - 1
            for row in range(listh):
                idx = top + row
                if idx >= len(games):
                    break
                g = games[idx]
                star = "*" if g.get("fav") else " "
                rate = ("%d" % g["rating"]) if g.get("rating") else " "
                sysid = g["sysId"] if sel_sys == 0 else ""
                text = f"{star}{rate} {g['name']}"
                if sysid:
                    text = f"{text[:max(4, gw - 12)]:<{max(4, gw - 12)}} {sysid}"
                attr = C("sel") if (idx == sel_game and pane == 1) else (
                    C("fav") if g.get("fav") else 0)
                try:
                    scr.addstr(2 + row, gx, text[:gw], attr)
                except curses.error:
                    pass

            # details of the highlighted game
            if games:
                g = games[sel_game]
                key = g["file"]
                if key not in details_cache:
                    details_cache[key] = backend.details(g)
                    if len(details_cache) > 200:
                        details_cache.clear()
                        details_cache[key] = backend.details(g)
                d = details_cache.get(key) or {}
                meta = d.get("meta") or {}
                bits = [g["sysName"]]
                if meta.get("yr"):
                    bits.append(meta["yr"])
                if meta.get("dev"):
                    bits.append(meta["dev"])
                if d.get("size"):
                    bits.append(fmt_size(d["size"]))
                if g.get("plays"):
                    bits.append(f"{g['plays']} plays")
                try:
                    scr.hline(h - 4, 0, curses.ACS_HLINE, w)
                    scr.addstr(h - 3, 0, " | ".join(bits)[:w - 1], C("dim"))
                    ov = (meta.get("ov") or "").replace("\n", " ")
                    scr.addstr(h - 2, 0, ov[:w - 1])
                except curses.error:
                    pass

            footer = ("/ search  enter play  f fav  tab pane  r rescan  q quit"
                      if not query else f"search: {query}_  (esc clears)")
            try:
                scr.addstr(h - 1, 0, footer[:w - 1].ljust(w - 1),
                           C("hdr", curses.A_REVERSE))
                if status:
                    scr.addstr(h - 1, max(0, w - len(status) - 2), status[:w - 1],
                               C("hdr", curses.A_REVERSE))
            except curses.error:
                pass
            scr.refresh()

            try:
                ch = scr.getch()
            except KeyboardInterrupt:
                return

            if query and ch in (27,):                 # esc clears the search
                query = ""
                continue
            if ch in (ord("q"), ord("Q")) and not query:
                return
            if ch in (curses.KEY_DOWN, ord("j")):
                if pane == 1:
                    sel_game = min(len(games) - 1, sel_game + 1)
                else:
                    sel_sys = min(len(systems), sel_sys + 1)
                    sel_game = top = 0
            elif ch in (curses.KEY_UP, ord("k")):
                if pane == 1:
                    sel_game = max(0, sel_game - 1)
                else:
                    sel_sys = max(0, sel_sys - 1)
                    sel_game = top = 0
            elif ch == curses.KEY_NPAGE:
                sel_game = min(len(games) - 1, sel_game + listh)
            elif ch == curses.KEY_PPAGE:
                sel_game = max(0, sel_game - listh)
            elif ch in (curses.KEY_HOME,):
                sel_game = 0
            elif ch in (curses.KEY_END,):
                sel_game = max(0, len(games) - 1)
            elif ch == 9:                              # tab
                pane = 1 - pane
            elif ch in (curses.KEY_LEFT,):
                pane = 0
            elif ch in (curses.KEY_RIGHT,):
                pane = 1
            elif ch == ord("/"):
                query = " "                            # enter search mode
                query = ""
                curses.echo()
                scr.addstr(h - 1, 0, "search: ".ljust(w - 1),
                           C("hdr", curses.A_REVERSE))
                scr.move(h - 1, 8)
                try:
                    query = scr.getstr(h - 1, 8, 40).decode(errors="replace")
                except Exception:
                    query = ""
                curses.noecho()
                sel_game = top = 0
            elif ch in (10, 13, curses.KEY_ENTER):
                if games:
                    g = games[sel_game]
                    status = "launching " + g["name"][:24] + " ..."
                    scr.addstr(h - 1, 0, status[:w - 1].ljust(w - 1),
                               C("hdr", curses.A_REVERSE))
                    scr.refresh()
                    ok, msg = backend.launch(g)
                    status = ("playing " if ok else "FAILED: ") + msg[:w - 12]
                    if ok:
                        g["plays"] = g.get("plays", 0) + 1
            elif ch in (ord("f"), ord("F")) and not query:
                if games:
                    g = games[sel_game]
                    newv = not g.get("fav")
                    g["fav"] = newv
                    backend.favourite(g, newv)
                    status = ("favourited " if newv else "unfavourited ") \
                        + g["name"][:20]
            elif ch in (ord("r"), ord("R")) and not query:
                status = "rescanning ..."
                scr.addstr(h - 1, 0, status.ljust(w - 1),
                           C("hdr", curses.A_REVERSE))
                scr.refresh()
                try:
                    state = backend.state(rescan=True)
                    systems = [s for s in state.get("systems", []) if s["games"]]
                    details_cache.clear()
                    status = f"{backend.kind} {backend.where()}"
                except Exception as e:
                    status = "rescan failed: " + str(e)[:40]
            elif ch == curses.KEY_RESIZE:
                continue

    curses.wrapper(inner)


def main():
    if has("--help") or has("-h"):
        print(__doc__)
        return 0

    if has("--local"):
        backend = Local()
    else:
        host = arg("--host")
        if host:
            remote = Remote(host, int(arg("--port", DEFAULT_PORT)))
            if has("--play-here"):
                maps = []
                for i, a in enumerate(sys.argv):
                    val = None
                    if a == "--map" and i + 1 < len(sys.argv):
                        val = sys.argv[i + 1]
                    elif a.startswith("--map="):
                        val = a.split("=", 1)[1]
                    if val and "=" in val:
                        src, _, dst = val.partition("=")
                        maps.append((src.rstrip("/"), dst.rstrip("/")))
                backend = Hybrid(remote, maps)
            else:
                backend = remote
        else:
            # no host given: try a server on this machine, else run standalone
            try:
                probe = Remote("127.0.0.1", int(arg("--port", DEFAULT_PORT)))
                probe.state()
                backend = probe
            except Exception:
                backend = Local()

    try:
        if has("--list"):
            return cmd_list(backend)
        play = arg("--play")
        if play:
            return cmd_play(backend, play)
        return run_tui(backend)
    except urllib.error.URLError as e:
        print(f"cannot reach {backend.where()}: {e.reason}")
        print("is the server running?  python3 retroshelf.py --serve --host 0.0.0.0")
        return 1
    except ImportError:
        print("retroshelf.py must sit next to this script for --local mode")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
