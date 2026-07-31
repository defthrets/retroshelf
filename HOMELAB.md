# Installing RetroShelf on the homelab (Debian/Ubuntu)

Goal: the homelab runs the games and serves the library over HTTP; the
ThinkPad (or any terminal) browses it and tells it what to launch.

Only Python 3 is required. There is nothing to pip install.

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

**The games must be readable on this machine.** Either copy them over, or
mount the Windows share, e.g. in `/etc/fstab`:

```
//192.168.1.x/oldgames  /srv/games  cifs  guest,ro,uid=1000,iocharset=utf8,_netdev  0  0
```

## 3. Emulators

RetroShelf finds emulators on `PATH`, so packages are enough:

```bash
sudo apt update
sudo apt install -y retroarch mgba-qt snes9x-gtk mupen64plus-ui-console \
                    dolphin-emu mednafen stella vice fs-uae mame
```

DuckStation (PlayStation) and PCSX2 (PS2) are not in the Debian repos — use
Flatpak, then drop a small wrapper on `PATH` so RetroShelf can find it:

```bash
flatpak install -y flathub org.duckstation.DuckStation
sudo tee /usr/local/bin/duckstation-qt >/dev/null <<'EOF'
#!/bin/sh
exec flatpak run org.duckstation.DuckStation "$@"
EOF
sudo chmod +x /usr/local/bin/duckstation-qt
```

Anything not on `PATH` also works if you drop the binary or an AppImage into
`/srv/retroshelf/emulators/<system>/`.

BIOS files (PlayStation, 3DO, Dreamcast, Amiga Kickstarts) go in
`/srv/retroshelf/bios/<system>/` and are copied into place on first launch.

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
