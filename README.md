# RetroShelf

Single-file retro game launcher for Windows. No dependencies — just Python 3.
Serves a local web GUI (http://127.0.0.1:7830) with a dark CRT terminal look:
one big searchable list of your games with cover art, gameplay screenshots and
per-system logos. Click a game and it launches straight into the right
emulator.

## Run

Double-click `RetroShelf.bat` (or `python retroshelf.py`). The browser opens
automatically.

## Games

Point the Settings tab at your games folder (e.g. `M:\oldgames`). RetroShelf
scans it **recursively — any folder structure works**. Each file's system is
detected from its extension; folder names act as hints for ambiguous
extensions (a folder with "n64", "psx" or "amiga" in its name tips the
balance for `.zip` / `.iso` / `.bin` / `.rar` files, and `.zip` contents are
peeked at as a fallback). Filenames are cleaned for display
(`AlienBreed2_v1.4_AGA` becomes "Alien Breed 2").

## Emulators

The **Systems** tab lists every system with a live status:

- **One-click DOWNLOAD** — where the emulator ships as a plain zip, RetroShelf
  downloads it from the official release (GitHub API or direct URL) and unzips
  it into `emulators\<system>\`, with a progress readout.
- **Manual link** — where it ships as a 7z/installer (Dolphin, PCSX2, MAME),
  the card links the official download page and tells you which folder to
  install into.

| System | Emulator | Auto-download |
|---|---|---|
| Nintendo NES | Mesen | yes (needs .NET 8 runtime) |
| Super Nintendo | Snes9x | yes |
| Nintendo 64 | simple64 (mupen64plus core) | yes |
| Game Boy / Color / Advance | VisualBoyAdvance-M | yes |
| Nintendo DS | melonDS | yes |
| GameCube / Wii | Dolphin | manual (7z) |
| Sega Mega Drive | ares | yes |
| Sega Dreamcast | Flycast | yes |
| PlayStation | DuckStation | yes |
| PlayStation 2 | PCSX2 | manual (7z, needs BIOS) |
| PSP | PPSSPP | yes |
| Arcade | MAME | manual (self-extracting exe) |
| Atari 2600 | Stella | yes |
| Commodore 64 | VICE | yes |
| Commodore Amiga | WinUAE | yes (needs Kickstart ROMs) |

Custom exe paths and launch-argument templates (placeholders `{emu}` `{rom}`
`{romname}` `{romdir}`) are editable per system.

## Art

- Cover: image named exactly like the rom — next to it, in an `art\`/`covers\`
  subfolder beside it, or centrally in `<art_root>\<system>\`.
- Screenshot: same naming, in a `screens\`/`screenshots\` subfolder or
  `<art_root>\<system>\screens\`.
- **Cover matcher** (Settings tab): point it at any folder of box-art images
  and it matches them to your games by title (exact, then space-insensitive,
  then fuzzy) and copies each hit into the art folder under the rom's name.

## Building the exe

```
python -m pip install pyinstaller pillow pywebview
python make_icon.py                       # generates retroshelf.ico
python -m PyInstaller --onefile --noconsole --icon retroshelf.ico ^
       --collect-all webview ^
       --name RetroShelf retroshelf.py    # -> dist\RetroShelf.exe
```

The exe opens in its own native window (Edge WebView2) with the cartridge
icon — run with `--browser` to use a browser tab instead, or `--no-browser`
for server-only. Config (`retroshelf.json`) lives next to the exe.
Double-clicking while it's already running opens another window on the same
instance.

## Notes

- Config and play stats live next to the script in `retroshelf.json`
  (gitignored).
- The server binds to 127.0.0.1 only. Scans are cached for 2 minutes; the
  RESCAN chip forces a fresh scan.
- Amiga: WinUAE needs your Kickstart ROMs configured once; `.adf` disks launch
  directly, WHDLoad `.lha`/`.rar` archives need a one-time WinUAE setup.

## Terminal version (old hardware, SSH, headless)

`retroshelf_tui.py` is the same library in a curses interface — stdlib only,
no desktop, fits an 80x24 screen. Copy `retroshelf.py` and `retroshelf_tui.py`
together; Python 3 is the only requirement.

**Thin client + homelab** — the terminal browses, the homelab runs the games:

```
homelab$   python3 retroshelf.py --serve --host 0.0.0.0
thinkpad$  python3 retroshelf_tui.py --host homelab
```

Choosing a game launches it on the homelab, where the emulators, discs and
graphics power live. `--host 0.0.0.0` puts the API on your LAN — anyone who
can reach it can start games on that box, so keep it to a trusted network.

**Standalone** on one machine: `python3 retroshelf_tui.py --local`

Without flags it uses a server on this machine if one is running, otherwise
it scans locally.

Keys: arrows or `j`/`k` move, `tab` switches pane, `/` search, `enter` plays,
`f` favourite, `r` rescan, `q` quit.

Scriptable modes, no UI:

```
python3 retroshelf_tui.py --list                 # print the library
python3 retroshelf_tui.py --list --system ps1
python3 retroshelf_tui.py --play "metal gear"    # launch by name
```

On Linux and macOS emulators are found on `PATH` (mgba, snes9x-gtk,
mupen64plus, duckstation-qt, pcsx2-qt, dolphin-emu, flycast, mame, stella,
x64sc, fs-uae, ppsspp, retroarch), or you can drop a binary or AppImage into
`emulators/<system>/`.
