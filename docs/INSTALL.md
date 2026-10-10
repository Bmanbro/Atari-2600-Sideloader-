# First installation on Linux

## Requirements

- **Atari 2600+ Pac-Man Edition**, already running firmware **2.0.1.6**.
- A USB data cable and access to the console's system update mode.
- Python 3.10+, `debugfs` and `e2fsck` (e2fsprogs), `7z`, `rkdeveloptool`, and Zig.
- Your own game files and the official Pac-Man 2.0.1.6 firmware updater package.
- Several GB of free disk space for backups. Each update retains roughly
  400 MiB of OEM images, plus a temporary working image while building.

This source was built with Zig 0.16.0. Install a suitable Zig toolchain from
[Zig's downloads](https://ziglang.org/download/) if your distribution does not
provide it. The build uses `zig cc`; Python packages from pip are not needed.
Install the remaining tools through your distribution where available.
For `rkdeveloptool`, follow
[Rockchip's build instructions](https://github.com/rockchip-linux/rkdeveloptool).

On the development CachyOS computer the package names were `python`,
`e2fsprogs`, `7zip`, `zig`, and `rkdeveloptool-git`. Package names differ across
distributions. Confirm that the command `7z` is available, even if the package
uses a different name.

Obtain the **Pac-Man** updater from
[Atari's firmware page](https://atari.com/blogs/newsroom/updating-atari-firmware).
The similarly numbered updater for the original 2600+ is a different package.
This tool prepares a temporary loader from the Windows EXE on Linux; it does
not run the EXE or upgrade the system firmware. If your console is older than
2.0.1.6, complete the appropriate official update first. Exact hashes are
checked before patching; do not bypass a mismatch.

## 1. Prepare local files

Open a terminal in this source directory:

```sh
mkdir -p local/games local/firmware
python3 build_menu.py --output local/menu
```

Put the updater in `local/firmware/`, and the complete collection of games you
want in `local/games/`. See [supported game formats](ADDING_GAMES.md).
The `local/` folder is ignored by Git and is private to your setup.

## 2. Connect in system update mode

1. Power off the console and remove its cartridge.
2. Set TV Type to **Color**.
3. Connect USB to the Linux computer using a data cable.
4. Hold **Reset** while switching power on; release Reset after 3–5 seconds.
5. Keep the console connected and powered during the entire update.

Only one Rockchip console should be connected. `rkdeveloptool ld` should show
USB ID `2207:310c`. A reported **Maskrom** mode is expected on this console:
the updater prepares the temporary RAM loader automatically and checks actual
storage readability. The label can still say Maskrom after storage is ready.

If the device is visible only with administrator privileges, configure your
distribution's USB permissions for this device. A typical desktop udev rule is:

```text
SUBSYSTEM=="usb", ATTR{idVendor}=="2207", ATTR{idProduct}=="310c", TAG+="uaccess"
```

A system administrator can place that line in
`/etc/udev/rules.d/70-atari-pacman.rules`, reload the rules, then reconnect the
console. Avoid mixing normal-user and root runs, which can create inaccessible
backup files.

## 3. Install

```sh
python3 update_games.py \
  --firmware local/firmware/Atari2600PacMan_Firmware_Updater_2.0.1.6.exe \
  --games local/games \
  --menu local/menu
```

The command lists the games, prepares the temporary loader, backs up GPT and
the full OEM partition, checks the stock files, builds the payload, and writes
OEM. It then reads all 100 MiB of OEM back and compares its SHA-256 with the
prepared image. Settings are saved in `local/current.json` only after that
comparison succeeds.

Wait for **“Game update complete”** before disconnecting. The displayed
`local/runs/<timestamp>-<id>/` directory contains that run's backup, bundle,
installation receipt and recovery plan. Preserve it and all earlier backups.
The backup covers GPT and OEM, not the entire flash or userdata/save partition.

## 4. Play

Power off before changing connections. Reconnect normal power, HDMI and the
controller. With no cartridge inserted, power on without holding Reset and
allow about 15 seconds for the reader and menu to start. Use up/down and Fire.
Some 2600 games, including Pitfall II and River Raid, require the console's
Reset switch to start play. Power cycle to return to the selector.

For a cartridge, power off, insert the cartridge, and power on normally.

## Adopting an existing installation

If this payload is already installed but `local/current.json` does not exist,
you also need the existing installation's bundle and the backup it was built
from. These are your own files, not files from another console:

```sh
python3 update_games.py \
  --firmware path/to/Atari2600PacMan_Firmware_Updater_2.0.1.6.exe \
  --games path/to/games --menu path/to/compiled-menu \
  --installed-bundle path/to/existing-bundle \
  --original-backup path/to/that-bundles-source-backup
```

The helper validates the fresh device backup against those records before
writing. Missing records or unknown modifications cause a stop; they are not
treated as stock firmware.
