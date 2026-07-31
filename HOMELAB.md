# Installing RetroShelf on the homelab (Debian/Ubuntu)

Goal: the homelab runs the games and serves the library over HTTP; the
ThinkPad (or any terminal) browses it and tells it what to launch.

Only Python 3 is required. There is nothing to pip install.

## Quick install (one script)

```bash
git clone https://github.com/defthrets/retroshelf.git ~/retroshelf
cd ~/retroshelf
chmod +x homelab-install.sh
./homelab-install.sh
```

It finds the games folder, installs the emulators, writes the config and
starts the server, then prints what it detected. Point it somewhere specific
with `GAMES=/mnt/oldgames ./homelab-install.sh`.

The steps below are the same thing done by hand.

## 1. Get the code

```bash
git clone https://github.com/defthrets/retroshelf.git ~/retroshelf
cd ~/retroshelf
python3 -V            # 3.8 or newer
```

## 2. Folders

Games can live anywhere; point the config at them. Example layout:

```bash
mkdir -p /srv/games /srv/retroshelf/emulators /srv/retroshelf/art /srv/retroshelf/bios
```

Write `~/retroshelf/retroshelf.json`:

```json
{
  "library_root": "/srv/games",
  "emulators_root": "/srv/retroshelf/emulators",
  "art_root": "/srv/retroshelf/art"
}
```

The scanner walks `library_root` recursively and works out each game's system
from its file extension, using folder names as hints — so
`/srv/games/ps1/…`, `/srv/games/n64/…`, `/srv/games/amiga/…` is ideal, but any
layout works.

**The games must be readable on this machine — as a local path, not a network
mount of itself.** If this box already serves the games over SMB (the Windows
PC has `M:` mapped to `\\this-box\mnt`), then they are local here: point
`library_root` at the directory behind that share, e.g. `/mnt/oldgames`.
Find it with:

```bash
testparm -s 2>/dev/null | grep -A3 '^\[mnt\]'     # samba share -> real path
ls /mnt/oldgames
```

Only if the games really live on another machine do you need a mount, e.g. in
`/etc/fstab`:

```
//192.168.1.x/share  /srv/games  cifs  guest,ro,uid=1000,iocharset=utf8,_netdev  0  0
```

## 3. Emulators

Debian 13 dropped most emulator packages, so the reliable route is **RetroArch
plus libretro cores** — RetroShelf downloads the cores itself:

```bash
sudo apt install -y retroarch
curl -s -XPOST http://localhost:7830/api/cores -d '{}' -H 'Content-Type: application/json'
```

`/api/cores` installs a core for every system that has games but no emulator,
into `~/.config/retroarch/cores`. One system at a time instead:

```bash
curl -s -XPOST http://localhost:7830/api/download -d '{"id":"snes"}' -H 'Content-Type: application/json'
```

Cores used: snes9x, mupen64plus_next, swanstation (PlayStation), puae (Amiga),
fbneo (arcade), opera (3DO), mesen, gambatte, mgba, genesis_plus_gx,
stella2014, vice_x64, flycast, ppsspp, melonds.

Standalone emulators are still preferred when present, so anything you install
by hand (apt, Flatpak, an AppImage in `emulators/<system>/`) wins over the
core. Flatpak versions of DuckStation and PCSX2 need a wrapper on PATH:

```bash
flatpak install -y flathub org.duckstation.DuckStation
sudo tee /usr/local/bin/duckstation-qt >/dev/null <<'EOF2'
#!/bin/sh
exec flatpak run org.duckstation.DuckStation "$@"
EOF2
sudo chmod +x /usr/local/bin/duckstation-qt
```

BIOS files (PlayStation, 3DO, Dreamcast, Amiga Kickstarts) go in
`/srv/retroshelf/bios/<system>/`. RetroArch cores look in
`~/.config/retroarch/system/` — copy them there too if a core complains.

## 4. Run it

```bash
python3 ~/retroshelf/retroshelf.py --serve --host 0.0.0.0
```

`--serve` means headless: no window, just the HTTP API and the scanner.

**`--host 0.0.0.0` publishes the API to the LAN, and anyone who can reach it
can start games on this box.** Keep it to a trusted network, or bind to the
tailnet address instead of `0.0.0.0`.

To keep it running, as a user service:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/retroshelf.service <<EOF
[Unit]
Description=RetroShelf game library
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 %h/retroshelf/retroshelf.py --serve --host 0.0.0.0
Restart=on-failure

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now retroshelf
loginctl enable-linger "$USER"        # survives logout/reboot
```

## 4b. If systemd says there is no user bus

`systemctl --user` needs a login session. From an SSH shell that has one:

```bash
loginctl enable-linger "$USER"
systemctl --user daemon-reload && systemctl --user enable --now retroshelf
```

If the user bus still is not available (some agent/automation shells), install
it as a system service instead:

```bash
sudo tee /etc/systemd/system/retroshelf.service >/dev/null <<EOF
[Unit]
Description=RetroShelf game library
After=network-online.target

[Service]
User=$USER
ExecStart=/usr/bin/python3 /home/$USER/retroshelf/retroshelf.py --serve --host 0.0.0.0
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now retroshelf
```

## 5. Check it works

```bash
curl -s http://localhost:7830/api/state | head -c 200
python3 ~/retroshelf/retroshelf_tui.py --local --list | head
```

The second command prints the detected systems with `[ok ]` or `[no ]` for
whether an emulator was found, then the games.

Check the paths it is actually using — if `library_root` looks like a Windows
path, `retroshelf.json` was not picked up. It must sit next to
`retroshelf.py` (`~/retroshelf/retroshelf.json`):

```bash
curl -s http://localhost:7830/api/state | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(d['library_root'], d['emulators_root'])"
```

After adding games, rescan without restarting:

```bash
curl -s -XPOST http://localhost:7830/api/scan -d '{}' -H 'Content-Type: application/json'
```

(New files are also picked up automatically within about 20 seconds, and `r`
in the TUI forces a scan.)

If a system shows `[no ]`, the emulator is not on `PATH` — check with
`which mgba-qt retroarch dolphin-emu`, and see step 3.

## 6. Connect from the ThinkPad

```bash
git clone https://github.com/defthrets/retroshelf.git ~/retroshelf
python3 ~/retroshelf/retroshelf_tui.py --host <homelab-ip>
```

Games chosen there launch on the homelab. Keys: arrows or `j`/`k` move, `tab`
switches pane, `/` search, `enter` plays, `f` favourite, `r` rescan, `q` quit.

## 7. Art and descriptions (optional)

With the server running:

```bash
curl -s -XPOST http://localhost:7830/api/fetchmeta  -d '{}' -H 'Content-Type: application/json'
curl -s -XPOST http://localhost:7830/api/fetchshots -d '{}' -H 'Content-Type: application/json'
```

The first downloads the games database (~100 MB, once) for descriptions and
ratings; the second fetches covers and screenshots. Progress shows in
`/api/state` under `meta` and `shots`. Covers do not matter in the terminal,
but the descriptions show up in the details line.

## 8. Where should the emulator actually run?

The homelab is headless, so anything it launches has nowhere to draw. Three
ways out, in the order they are worth trying:

**Play on the terminal machine (recommended).** The homelab keeps the
collection, artwork and play counts; the emulator runs where there is a
screen. A Core 2 Duo laptop handles Amiga, SNES, NES, Mega Drive and
PlayStation comfortably.

```bash
# on the ThinkPad: mount the games, install RetroArch, get the cores
sudo mount -t cifs //192.168.1.253/mnt /media/homelab -o guest,ro,uid=1000
sudo apt install -y retroarch
python3 ~/retroshelf/retroshelf.py --serve --no-browser &   # local, for cores
curl -s -XPOST http://localhost:7830/api/cores -d '{}' -H 'Content-Type: application/json'

# browse the homelab's library, play here
python3 ~/retroshelf/retroshelf_tui.py --host 192.168.1.253 --play-here         --map /mnt/oldgames=/media/homelab/oldgames
```

`--map` translates the server's paths to wherever they are mounted locally;
leave it out if both machines see the same path.

**Give the homelab a display.** A virtual X server plus VNC, if you would
rather the homelab do the work:

```bash
sudo apt install -y xvfb x11vnc
Xvfb :99 -screen 0 1280x720x24 &
x11vnc -display :99 -forever -nopw -localhost &   # then ssh -L 5900:localhost:5900
DISPLAY=:99 python3 ~/retroshelf/retroshelf.py --serve --host 0.0.0.0
```

Everything launched will render on `:99`. Software rendering only, so 2D
systems are fine and 3D ones will not be.

**X11 forwarding** (`ssh -X thinkpad -> homelab`) works for simple SDL
emulators but uses indirect GLX, which most modern emulators either refuse or
run at a crawl. Quick to try, rarely the answer.

Note that a play being logged only means the emulator process started — it is
not proof anything appeared on a screen.
