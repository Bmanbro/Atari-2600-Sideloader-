#!/usr/bin/env python3
"""Add a legacy A78 header to an exactly identified, user-supplied 7800 ROM."""
import argparse
import hashlib
from pathlib import Path
import sys
import zipfile

# ProSystem's database identifies this ROM as SUPERCART_ROM (type 4), NTSC,
# two standard joysticks, no POKEY. Legacy header flags 0x000a select
# SuperGame banking with fixed ROM at $4000. No game data is included here.
PRESETS = {
    "ninja-golf-ntsc": {
        "sha256": "b5bb5a789a55ef60235edc43da72a8a88c4cb23e9fa99ac6acf9f62bbf810b3d",
        "size": 131072, "title": "Ninja Golf", "cartridge": 0x000a,
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Raw .bin or ZIP containing one raw ROM")
    parser.add_argument("--preset", choices=PRESETS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        preset = PRESETS[args.preset]
        if args.input.suffix.lower() == ".zip":
            with zipfile.ZipFile(args.input) as archive:
                entries = [e for e in archive.infolist() if not e.is_dir()
                           and Path(e.filename).suffix.lower() in (".bin", ".a78")]
                if len(entries) != 1 or entries[0].file_size != preset["size"]:
                    raise ValueError("Expected exactly one raw ROM of the preset's size in the ZIP")
                data = archive.read(entries[0])
        else:
            if args.input.stat().st_size != preset["size"]:
                raise ValueError("ROM size does not match this preset")
            data = args.input.read_bytes()
        if hashlib.sha256(data).hexdigest() != preset["sha256"]:
            raise ValueError("ROM checksum does not match this preset; cartridge settings were not guessed")
        if args.output.suffix.lower() != ".a78":
            raise ValueError("Choose an output filename ending in .a78")
        header = bytearray(128)
        header[0] = 3
        header[1:10] = b"ATARI7800"
        title = preset["title"].encode("ascii")
        header[17:17 + len(title)] = title
        header[49:53] = len(data).to_bytes(4, "big")
        header[53:55] = preset["cartridge"].to_bytes(2, "big")
        header[55:57] = b"\x01\x01"
        header[100:128] = b"ACTUAL CART DATA STARTS HERE"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(header + data)
        print(f"Created {args.output}: {len(data)} ROM bytes + 128-byte A78 header")
        print("Keep the original ZIP outside the active games folder to avoid a duplicate raw entry.")
        return 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
