# Architecture and maintenance

## Scope

This release is pinned to Pac-Man Edition firmware 2.0.1.6. The profile checks
the firmware image, loader, stock launcher, frontend and emulator-core hashes.
The firmware-image SHA-256 refers to the inner `Atari_Firmware.img`, not the
outer updater EXE. A different package must be analyzed and given a separate
profile before it can be supported.

Device operations parse and validate GPT CRCs and bounds. OEM must start at
sector `0x1e800` and occupy 204,800 sectors of 512 bytes, or 100 MiB. Only OEM
is writable through `device.py`. The temporary Rockchip loader is derived
from the official package and patched in RAM to allow complete reads. No
bootloader, GPT, rootfs or cartridge-dumper flash operation is implemented.

## Startup

1. The existing firmware startup script invokes `/oem/vendor/bin/dmenu.bin`.
2. That small launcher invokes `/oem/sideload/boot.sh`.
3. A private copy of the cartridge frontend tries normal cartridge loading.
4. A successful cartridge read retains the stock `/tmp/.next` handoff and exits
   with status 0. Stock cartridge cores and original frontend remain intact.
5. The no-game path cleans up its display/UART and returns status 42, which
   opens the internal selector. Version 0.3.6 recognizes both the original
   no-cart path and the observed `55 AA 02 FF EA E8` reply when physical
   cartridge status is clear. Other reader results keep stock handling.
6. Up/down and Fire select a catalog entry. `launch.sh` validates the ROM and
   copies it into RAM before launching the appropriate private core.

The private cores read a virtual console-key file. Its cartridge-present bit
is synthesized only for internal play; the real Reset, Select and other
switches continue to be forwarded. ProSystem's fixed game-file path is also
redirected to the RAM copy of the selected `.a78` file.

The native menu reads Linux joystick events and uses SDL from the firmware.
It ignores synthetic initialization input for selection, refreshes the screen
periodically, and displays a short STARTING GAME message before handing off.
There is one sideload attempt per power cycle, and no in-game return shortcut.

## Patch provenance

All expected byte sequences and hashes are in `profile.json` and checked
before applying patches. The current private frontend uses:

- The cleanup/exit-42 change at file offset `0x6048`.
- A branch at `0x5058` into the no-game reply check at `0x6054`.
- The exact observed reader reply plus physical cartridge status to select
  the fallback. The speculative zero-byte behavior from 0.3.5 was removed.

The resulting frontend SHA-256 is
`b9c86cbd62c757471bbd3d18cf1695e483860d67a719c4935536d8c07680d1ae`.
Earlier payload profiles are retained to recognize and restore their bundles.
Changing the frontend or key behavior can affect physical cartridges; preserve
the cartridge success path when developing new revisions.

## Host workflow

`sideload.py` works on local image copies using `debugfs`; no filesystem mount
is needed. It checks every written file, retained stock files, filesystem
consistency, total image size and image hash before publishing a build bundle.
An installable image must come from the full live OEM backup. Reference images
built with `--firmware` cannot be installed through `device.py`.

`update_games.py` orchestrates existing primitives. It locks its state folder,
records a recovery plan, backs up the console, recognizes a previous payload,
builds the next catalog, installs it, and advances `current.json` only after a
complete matching readback. Do not run separate updates against the same
console concurrently, including with different state folders.

The equivalent low-level update, with paths replaced for your installation:

```sh
python3 device.py boot-loader --enable-reads --firmware path/to/updater.exe
python3 device.py backup --output path/to/new-backup
python3 device.py recognize-backup \
  --backup path/to/new-backup \
  --installed-bundle path/to/previous-bundle \
  --original-backup path/to/previous-source-backup
python3 sideload.py build \
  --oem path/to/new-backup/oem.img \
  --installed-bundle path/to/previous-bundle \
  --games path/to/games --menu path/to/compiled-menu \
  --output path/to/new-bundle
python3 device.py install \
  --backup path/to/new-backup --bundle path/to/new-bundle \
  --record path/to/new-install-record
```

On a first stock install, omit `recognize-backup` and `--installed-bundle`.
Keep every matching backup/bundle pair for rollback. Manual installs do not
automatically advance the helper's state; adopt the resulting installation
with its explicit previous-bundle/source-backup arguments on the next update.

## Building and extending

```sh
python3 build_menu.py --output local/menu-new
```

This builds ARMv7 hard-float Linux code targeting a glibc 2.17 ABI, below the
firmware's glibc version. It needs Zig but no copied firmware libraries on the
host. Supply the new path with `--menu` when installing a rebuilt menu.

The latest reported hardware check covered an empty-slot menu and cartridge
playback on one Pac-Man console. Internal 2600 gameplay was also reported
working. A new ROM or newly built image still needs its own console check.
Save persistence, a broad 7800 compatibility matrix, the optional external-USB
path and recovery after actual power loss have not been established by those
reports. There is no automated test suite in this source release.
