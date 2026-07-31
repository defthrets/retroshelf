#!/usr/bin/env bash
# RetroShelf homelab setup: emulators, folders, config, service.
# Safe to run more than once - it skips whatever is already in place.
#
#   ./homelab-install.sh                 # auto-detect the games folder
#   GAMES=/mnt/oldgames ./homelab-install.sh
#   SKIP_APT=1 ./homelab-install.sh      # config only, no package installs
#
set -u

RS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-/srv/retroshelf}"
PORT="${PORT:-7830}"
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   [ok]   %s\n' "$*"; }
warn() { printf '   [--]   %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if sudo -n true 2>/dev/null; then SUDO="sudo"
  elif command -v sudo >/dev/null; then SUDO="sudo"; warn "sudo may prompt for a password"
  else warn "no sudo - package installs will be skipped"; SKIP_APT=1
  fi
fi

# ---------------------------------------------------------------- games dir
say "Games folder"
if [ -z "${GAMES:-}" ]; then
  for cand in /mnt/oldgames /srv/games /mnt/games /media/games "$HOME/games"; do
    if [ -d "$cand" ]; then GAMES="$cand"; break; fi
  done
fi
if [ -z "${GAMES:-}" ]; then
  GAMES="/srv/games"
  $SUDO mkdir -p "$GAMES" 2>/dev/null || mkdir -p "$GAMES" 2>/dev/null
  warn "no existing games folder found; created $GAMES (put roms in there)"
else
  n=$(find "$GAMES" -maxdepth 3 -type f 2>/dev/null | head -2000 | wc -l)
  ok "using $GAMES ($n files seen)"
fi

# ---------------------------------------------------------------- emulators
if [ "${SKIP_APT:-0}" != "1" ]; then
  say "Emulators (apt)"
  $SUDO apt-get update -qq 2>/dev/null || warn "apt update failed - carrying on"
  # installed one at a time so a package missing from this release cannot
  # abort the rest
  for pkg in retroarch mgba-qt snes9x-gtk mupen64plus-ui-console dolphin-emu \
             mednafen stella vice fs-uae mame desmume melonds blastem; do
    if dpkg -s "$pkg" >/dev/null 2>&1; then
      ok "$pkg (already installed)"
    elif DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq \
         --no-install-recommends "$pkg" >/dev/null 2>&1; then
      ok "$pkg"
    else
      warn "$pkg not available in this release"
    fi
  done

  say "Emulators (flatpak: DuckStation, PCSX2)"
  if ! command -v flatpak >/dev/null; then
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq flatpak \
      >/dev/null 2>&1 && ok "flatpak installed" || warn "could not install flatpak"
  fi
  if command -v flatpak >/dev/null; then
    $SUDO flatpak remote-add --if-not-exists flathub \
      https://dl.flathub.org/repo/flathub.flatpakrepo >/dev/null 2>&1
    add_wrapper() {   # $1 = flatpak id, $2 = command name to expose
      if $SUDO flatpak install -y --noninteractive flathub "$1" >/dev/null 2>&1 \
         || flatpak info "$1" >/dev/null 2>&1; then
        printf '#!/bin/sh\nexec flatpak run %s "$@"\n' "$1" \
          | $SUDO tee "/usr/local/bin/$2" >/dev/null
        $SUDO chmod +x "/usr/local/bin/$2"
        ok "$2 -> $1"
      else
        warn "$1 not installed"
      fi
    }
    add_wrapper org.duckstation.DuckStation duckstation-qt
    add_wrapper net.pcsx2.PCSX2 pcsx2-qt
  fi
fi

# ------------------------------------------------------------------ folders
say "Folders"
for d in "$ROOT/emulators" "$ROOT/art" "$ROOT/bios"; do
  if [ -d "$d" ]; then
    ok "$d"
  elif $SUDO mkdir -p "$d" 2>/dev/null || mkdir -p "$d" 2>/dev/null; then
    ok "created $d"
    $SUDO chown -R "$(id -un):$(id -gn)" "$ROOT" 2>/dev/null   # best effort
  else
    warn "could not create $d"
  fi
done

# ------------------------------------------------------------------- config
say "Config"
CFG="$RS_DIR/retroshelf.json"
if [ -f "$CFG" ] && grep -q '"library_root"' "$CFG" 2>/dev/null; then
  cur=$(python3 -c "import json;print(json.load(open('$CFG'))['library_root'])" 2>/dev/null)
  if [ "$cur" = "$GAMES" ]; then ok "already points at $GAMES"
  else
    python3 - "$CFG" "$GAMES" "$ROOT" <<'PY'
import json, sys
p, games, root = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(p))
cfg["library_root"] = games
cfg["emulators_root"] = root + "/emulators"
cfg["art_root"] = root + "/art"
json.dump(cfg, open(p, "w"), indent=2)
PY
    ok "updated (was $cur, now $GAMES)"
  fi
else
  cat > "$CFG" <<EOF
{
  "library_root": "$GAMES",
  "emulators_root": "$ROOT/emulators",
  "art_root": "$ROOT/art",
  "overrides": {},
  "stats": {}
}
EOF
  ok "wrote $CFG"
fi

# ------------------------------------------------------------------ restart
say "Server"
pkill -f "retroshelf.py --serve" 2>/dev/null && ok "stopped the old one"
sleep 1
nohup python3 "$RS_DIR/retroshelf.py" --serve --host 0.0.0.0 --port "$PORT" \
  >/tmp/retroshelf.log 2>&1 &
sleep 4

say "Result"
python3 - "$PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
try:
    d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=20))
except Exception as e:
    print("   server not answering:", e); raise SystemExit(1)
games = sum(len(s["games"]) for s in d["systems"])
found = [s for s in d["systems"] if s["emu_found"]]
print(f"   library    : {d['library_root']}")
print(f"   games      : {games}")
print(f"   emulators  : {len(found)} of {len(d['systems'])}")
for s in found:
    print(f"       {s['id']:10} {s['emu_path']}")
missing = [s["id"] for s in d["systems"] if not s["emu_found"] and s["games"]]
if missing:
    print("   no emulator for systems that have games:", " ".join(missing))
PY
echo
echo "   log:     tail -f /tmp/retroshelf.log"
echo "   browse:  python3 $RS_DIR/retroshelf_tui.py --local"
echo "   remote:  python3 retroshelf_tui.py --host $(hostname -I 2>/dev/null | awk '{print $1}')"
