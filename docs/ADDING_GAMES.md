# Add games yourself

Once the first install succeeds, no AI assistant or Windows program is needed.

1. Put new games in the games folder configured at installation.
2. Keep all games you still want in that folder. It represents the **whole
   desired collection**; removing a game from it removes that game on the next
   update. Rename a ROM to change its menu title.
3. Connect the console in [system update mode](INSTALL.md#2-connect-in-system-update-mode).
4. Open a terminal in this source directory and run:

   ```sh
   python3 update_games.py
   ```

5. Wait for **“Game update complete.”** Power off before reconnecting the TV.

If you used a custom state folder on the first run, keep using it:

```sh
python3 update_games.py --state /path/to/your/private-state-folder
```

Settings are stored there in `current.json`. You may supply `--games`, `--menu`
or `--firmware` to change a remembered path. New paths are remembered only
after a successful update. Preserve the state folder, compiled menu, firmware
package and any older installation records it references.

## Game formats

| System | Input |
| --- | --- |
| 2600 | Raw `.a26` or `.bin`; ZIPs containing these are accepted |
| 7800 | `.a78` with a valid 128-byte Atari 7800 header; ZIPs containing these are accepted |

The folder scan is not recursive. ZIPs are read without extracting their
contents to disk. Other files inside a ZIP are ignored. Identical ROM bytes
are deduplicated. Games are sorted by title, with up to 128 distinct games
and six menu entries per page, subject to available space in OEM.

The updater detects a 7800 game by its A78 header. **A raw 7800 `.bin` is
ambiguous and would otherwise be treated as a 2600 ROM.** Renaming `.bin` to
`.a78` does not add a header. Obtain the correct header or use a conversion
tool with the game's verified cartridge settings. Do not force `--system
7800` for a mixed collection; it does not manufacture missing headers.

## Ninja Golf (NTSC)

The included converter recognizes one exact raw Ninja Golf release and adds
a legacy version-3 A78 header. It does not include or download the game.

```sh
# Use the games directory you configured; these paths show the default layout.
mkdir -p local/games/originals
mv 'local/games/Ninja Golf (USA).zip' local/games/originals/
python3 prepare_7800.py 'local/games/originals/Ninja Golf (USA).zip' \
  --preset ninja-golf-ntsc \
  --output 'local/games/Ninja Golf (USA).a78'
python3 update_games.py
```

Connect in system update mode before the final command. The original ZIP is
preserved in a subfolder so its raw `.bin` does not become a second menu item.
The converter also accepts a raw `.bin` directly and refuses to overwrite an
existing output file.

This preset requires 131,072 bytes and SHA-256
`b5bb5a789a55ef60235edc43da72a8a88c4cb23e9fa99ac6acf9f62bbf810b3d`.
It uses SuperGame banking with fixed ROM at `$4000`, NTSC, two standard
joystick ports and no POKEY. These settings match the exact ROM's entry in
the [ProSystem database](https://github.com/libretro/prosystem-libretro/blob/master/core/Database.c)
(MD5 `220121f771fc4b98cef97dc040e8d378`) and the NTSC entry in
[MAME's 7800 list](https://github.com/mamedev/mame/blob/master/hash/a7800.xml).
The [A78 format specification](https://7800.8bitdev.org/index.php/A78_Header_Specification)
describes the header fields. Other revisions or games need their own settings.

Use a compatible two-button 7800 controller for the game's full controls.
The firmware's ProSystem emulator determines game compatibility; a successful
copy/readback is not a substitute for playing the game on the console.

## What the command changes

It rebuilds the internal catalog from the current folder and writes a new OEM
image derived from a fresh backup. It preserves the working cartridge-first
startup and the stock cartridge emulator files. It does not update system
firmware, modify the cartridge dumper, or write the separate userdata partition.
USB is used for this transfer; no external drive is needed during play.

For low-level commands or a failed update, see [recovery](RECOVERY.md).
