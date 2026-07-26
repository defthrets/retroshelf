# RetroShelf

Single-file retro game launcher for Windows. No dependencies — just Python 3.
Serves a local web GUI (http://127.0.0.1:7830) that scans your library folder,
shows every game with box art, and launches the right emulator per system.

You supply the games and emulators yourself; RetroShelf just organises and
launches them.

## Run

Double-click `RetroShelf.bat` (or `python retroshelf.py`). The browser opens
automatically.

## Library layout

Everything lives under one folder (default `C:\RetroShelf`, changeable in
Settings). Use **Settings > Create Folder Layout** to build it:

```
C:\RetroShelf\
  roms\<system>\        drop game files here (subfolders fine)
  emulators\<system>\   unzip the emulator here — the exe is found automatically
  art\<system>\         optional box art, named exactly like the rom file
```

Box art is also picked up from an image sitting next to the rom, or from an
`art\` / `covers\` subfolder beside it.

## Supported systems

| Folder | System | Suggested emulator |
|---|---|---|
| nes | Nintendo NES | Mesen (mesen.ca) |
| snes | Super Nintendo | Snes9x (snes9x.com) |
| n64 | Nintendo 64 | simple64 (simple64.github.io) |
| gb | Game Boy / Color | mGBA (mgba.io) |
| gba | Game Boy Advance | mGBA (mgba.io) |
| nds | Nintendo DS | melonDS (melonds.kuribo64.net) |
| gamecube | GameCube | Dolphin (dolphin-emu.org) |
| wii | Nintendo Wii | Dolphin (dolphin-emu.org) |
| genesis | Sega Mega Drive | BlastEm (retrodev.com/blastem) |
| dreamcast | Sega Dreamcast | Flycast (flycast.dev) |
| ps1 | PlayStation | DuckStation (duckstation.org) |
| ps2 | PlayStation 2 | PCSX2 (pcsx2.net) |
| psp | PSP | PPSSPP (ppsspp.org) |
| arcade | Arcade | MAME (mamedev.org) |
| atari2600 | Atari 2600 | Stella (stella-emu.github.io) |

The SYSTEMS tab shows live status (● FOUND / ● MISSING) for each emulator,
and lets you point at a custom exe or edit the launch argument template
(placeholders: `{emu}` `{rom}` `{romname}` `{romdir}`).

Adding a new system is one entry in the `SYSTEMS` list at the top of
`retroshelf.py`.

## Notes

- Config and play stats are stored next to the script in `retroshelf.json`.
- The server binds to 127.0.0.1 only.
- Games are launched with the emulator's own folder as working directory.
