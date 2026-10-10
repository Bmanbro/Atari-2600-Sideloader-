# Backups, rollback, and troubleshooting

## Keep the installation records

Every `update_games.py` run has its own directory:

```text
local/current.json                 remembered paths and installed bundle
local/runs/<timestamp>-<id>/
  plan.json                        paths and previous-installation settings
  backup/backup.json               backup metadata and checksums
  backup/oem.img                   complete pre-update OEM image
  backup/gpt-*.bin                 partition-table snapshot
  bundle/bundle.json               payload manifest
  bundle/oem-sideload.img          the newly prepared OEM image
  install/operation.json          pre-write receipt and final status
  install/before.img              OEM immediately before the write
  install/after.img               complete readback after the write
```

The backups are tied to your console and partition layout. Keep the whole
state folder and any older bundle/backup directories referenced by `plan.json`.
These contain game and firmware data; do not publish them.

## Undo the most recent completed update

Read `local/current.json` to find `last_run`. Set `RUN` below to that actual
directory, and substitute your firmware path. Use the same custom state path
if you originally supplied `--state`.

```sh
RUN='local/runs/REPLACE-WITH-YOUR-RUN-DIRECTORY'
python3 device.py boot-loader --enable-reads \
  --firmware local/firmware/Atari2600PacMan_Firmware_Updater_2.0.1.6.exe
python3 device.py restore \
  --backup "$RUN/backup" --bundle "$RUN/bundle" \
  --record "$RUN/restore-001"
```

Connect in system update mode first. `restore-001` must be a new directory;
choose another number if it already exists. The restore writes the preceding
OEM image and compares the complete readback. Power off before reconnecting
for play.

After a successful restore, set the updater's remembered installation back
to the previous one. The following uses the saved plan; run it **only after
the restore has completed successfully**:

```sh
python3 - "$RUN" <<'PY'
import json
from pathlib import Path
import sys
from sideload import write_json

run = Path(sys.argv[1]).resolve()
plan = json.loads((run / 'plan.json').read_text())
current = run.parent.parent / 'current.json'
previous = plan['previous_settings']
if not previous and plan['previous_bundle']:
    previous = {key: plan[key] for key in ('format', 'firmware', 'games', 'menu')}
    previous.update(installed_bundle=plan['previous_bundle'],
                    original_backup=plan['previous_backup'])
if previous:
    write_json(current, previous)
else:
    current.unlink(missing_ok=True)  # The first install was restored to stock.
print('Remembered installation updated; all backups were retained.')
PY
```

A rollback undoes **one** installation. To reach original stock firmware,
restore successive updates in reverse order using each matching backup and
bundle. If you adopted a pre-existing setup, its older records are needed too.

## Interrupted or failed write

If writing or readback fails, keep the backup, bundle and installation receipt.
Keep the console in update mode if possible. If power was lost, re-enter
system update mode and prepare the temporary loader again.

Use the failed run's directory as `RUN`. The special recovery flag permits
restoring its backup even if the interrupted write damaged the OEM filesystem:

```sh
python3 device.py restore \
  --backup "$RUN/backup" --bundle "$RUN/bundle" \
  --failed-install "$RUN/install" --record "$RUN/recovery-001"
```

This requires a matching pre-write receipt whose status is `writing`, `failed`
or `readback-mismatch`. It still checks the console's partition table and the
saved backup. Choose a fresh recovery record directory each time. If the tool
stopped before writing, there may be no install receipt and no flash recovery
is needed. A failed updater run normally leaves `current.json` unchanged.

If the flash write succeeded but saving `current.json` was interrupted, the
run's `install/operation.json` and `plan.json` describe the installed bundle.
You can either restore that run, or use the adoption arguments in
[INSTALL.md](INSTALL.md#adopting-an-existing-installation) with that run's
`bundle/` and `backup/` paths.

## Common messages

- **No device:** check power, a data-capable USB cable, update-mode entry and
  Linux USB permissions. Connect exactly one console.
- **Maskrom:** use `device.py boot-loader --enable-reads` first, or let
  `update_games.py` do it. Do not use raw write commands to work around it.
- **Unsupported or already modified firmware during an update:** a fresh
  backup initially reports the changed launcher. For an existing installation,
  the helper then checks it against your previous bundle and should report
  **Verified installed sideload**. A later validation error is a stop.
- **Missing A78 header:** renaming a raw 7800 ROM is insufficient. See
  [Ninja Golf conversion](ADDING_GAMES.md#ninja-golf-ntsc).
- **Game appears but will not start:** some 2600 games require the console's
  Reset switch to begin play. Confirm the game's controller requirements too.
- **No Game Detected instead of the menu:** allow the reader startup time,
  remove any cartridge before power-on, and record which payload/firmware is
  installed. Do not replace the cartridge-first startup with an unconditional
  menu launch; that breaks normal cartridge use.

Runtime diagnostics are saved on the console at
`/userdata/sideload/last-cartridge.log` and `last-boot.log`. Those paths are
inside its userdata filesystem, not files exposed as a USB drive. The normal
backup command does not extract userdata. A cartridge-only boot can leave the
previous internal-game log in place.
