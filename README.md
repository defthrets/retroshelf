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

## Notes

- Config and play stats live next to the script in `retroshelf.json`
  (gitignored).
- The server binds to 127.0.0.1 only. Scans are cached for 2 minutes; the
  RESCAN chip forces a fresh scan.
- Amiga: WinUAE needs your Kickstart ROMs configured once; `.adf` disks launch
  directly, WHDLoad `.lha`/`.rar` archives need a one-time WinUAE setup.
