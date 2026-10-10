# Changelog

## 0.3.6 — source release and manual updater

- Cartridge-first boot with the exact observed empty-slot reader reply;
  user confirmed both menu startup and cartridge playback working.
- Existing working menu retains neutral startup, Fire selection, launch
  feedback and automatic screen refresh.
- Add a Linux helper for repeated game updates, retaining a backup and full
  write/readback record for each run.
- Add an exact-checksum Ninja Golf NTSC A78-header converter.
- Add installation, game management, recovery, technical and publishing guides.

The host tools and documentation were added without changing the 0.3.6
on-console startup, menu or emulator patches.

## Earlier development

- 0.3.5: attempted an empty-read fallback; it did not match the actual reader
  response and was superseded by 0.3.6.
- 0.3.4: restored physical cartridge-first startup.
- 0.3.3: periodic display refresh fixed the initially blank menu.
- 0.3.2: neutral input startup and visible launch feedback.
- 0.3.1: corrected Fire-button selection.
- 0.3.0: internal multi-game selector.
- 0.2.0: internal single-game loading.
- 0.1.0: initial payload and optional USB game source.
