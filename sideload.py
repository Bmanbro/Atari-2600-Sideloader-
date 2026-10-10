#!/usr/bin/env python3
"""Build a Pac-Man 2600+ sideload payload without mounting any filesystem.

Requires Python 3.10+, debugfs/e2fsck, and 7z when reading a Windows updater.
This program only edits local files. Device access is in device.py.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
PROFILE = json.loads((HERE / "profile.json").read_text())
VERSION = "0.3.6"
SUPPORTED_VERSIONS = ("0.1.0", "0.2.0", "0.3.0", "0.3.1", "0.3.2", "0.3.3", "0.3.4", "0.3.5", VERSION)
MENU_VERSIONS = ("0.3.0", "0.3.1", "0.3.2", "0.3.3", "0.3.4", "0.3.5", VERSION)
CARTRIDGE_FIRST_VERSIONS = ("0.3.4", "0.3.5", "0.3.6")
FORMAT = "ATARI_SIDELOAD_V1"
CATALOG_FORMAT = "ATARI_MENU_V1"


class Error(Exception):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def command(argv: list[str], *, ok: tuple[int, ...] = (0,),
            timeout: float | None = None) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise Error(f"Command timed out after {timeout} seconds: {argv[0]}") from exc
    output = result.stdout + result.stderr
    if result.returncode not in ok:
        raise Error(f"Command failed ({result.returncode}): {argv[0]}\n{output}")
    return output


def require_tools(*names: str) -> None:
    for name in names:
        if not shutil.which(name):
            raise Error(f"Missing tool: {name}")


def atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".sideload-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: dict) -> None:
    atomic_write(path, (json.dumps(value, indent=2) + "\n").encode())


def dump_file(image: Path, name: str) -> bytes:
    """Internal paths are constants, never user-supplied debugfs commands."""
    if any(c not in "/._-abcdefghijklmnopqrstuvwxyz0123456789" for c in name):
        raise Error("Invalid internal path")
    with tempfile.TemporaryDirectory(prefix="atari-dump-", dir="/tmp") as tmp:
        dest = Path(tmp) / "file"
        log = command(["debugfs", "-R", f"dump {name} {dest}", str(image)])
        if not dest.is_file():
            raise Error(f"Cannot read {name} from OEM image:\n{log}")
        return dest.read_bytes()


def validate_ext2(image: Path) -> None:
    size = image.stat().st_size
    with image.open("rb") as stream:
        stream.seek(1024)
        sb = stream.read(1024)
    if len(sb) != 1024 or sb[56:58] != b"\x53\xef":
        raise Error("Input is not a raw ext2 OEM filesystem")
    block_count = struct.unpack_from("<I", sb, 4)[0]
    block_log = struct.unpack_from("<I", sb, 24)[0]
    if block_log > 6 or block_count * (1024 << block_log) > size:
        raise Error("Truncated or invalid OEM filesystem")
    if size not in (PROFILE["oem_size_in_firmware"],
                    PROFILE["oem_partition_sectors"] * 512):
        raise Error("Unexpected OEM image size for this firmware profile")


def stock_files(image: Path) -> dict[str, bytes]:
    validate_ext2(image)
    found = {}
    for name, expected in PROFILE["files"].items():
        data = dump_file(image, name)
        if digest(data) != expected:
            raise Error(
                f"Unsupported or already modified firmware: {name}\n"
                f"Expected Pac-Man edition {PROFILE['firmware_version']} "
                f"SHA256 {expected}, got {digest(data)}.\n"
                "Use the matching official Pac-Man update first, then make a new backup."
            )
        found[name] = data
    return found


@contextmanager
def verified_firmware(source: Path) -> Iterator[Path]:
    """Yield the exact supported system image, extracting an updater if needed."""
    with tempfile.TemporaryDirectory(prefix="atari-extract-", dir="/tmp") as tmp:
        image = source
        if source.suffix.lower() == ".exe":
            require_tools("7z")
            image = Path(tmp) / "firmware.img"
            with image.open("wb") as stream:
                result = subprocess.run(
                    ["7z", "x", "-so", str(source),
                     "Atari2600_Firmware_Updater/Atari_Firmware.img"],
                    stdout=stream, stderr=subprocess.PIPE,
                )
            if result.returncode:
                raise Error("7z could not extract the firmware: " +
                            result.stderr.decode(errors="replace"))
        actual = file_digest(image)
        if actual != PROFILE["firmware_sha256"]:
            raise Error(
                "This is not the supported Pac-Man edition 2.0.1.6 system image.\n"
                f"SHA256: {actual}\n"
                "The original 2600+ updater is a different hardware profile."
            )
        yield image


def extract_firmware(source: Path, dest: Path) -> None:
    """Only this exact official package is understood; no guessed RKAF entries."""
    with verified_firmware(source) as image:
        with image.open("rb") as stream:
            stream.seek(PROFILE["oem_offset_in_firmware"])
            oem = stream.read(PROFILE["oem_size_in_firmware"])
        if digest(oem) != PROFILE["oem_sha256"]:
            raise Error("The firmware's OEM partition does not match the profile")
        dest.write_bytes(oem)


def rockchip_crc(data: bytes) -> int:
    """BOOT container CRC: Rockchip's 0x04c10db7 polynomial, initial value 0."""
    table = []
    for value in range(256):
        crc = value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04c10db7 if crc & 0x80000000 else 0)) & 0xffffffff
        table.append(crc)
    crc = 0
    for value in data:
        crc = ((crc << 8) & 0xffffffff) ^ table[(crc >> 24) ^ value]
    return crc


def patch_loader_reads(loader: bytes) -> bytes:
    """Patch two read-limit branches in the exact RAM loader; never flash this."""
    if digest(loader) != PROFILE["loader_sha256"]:
        raise Error("Unsupported RAM loader")
    if rockchip_crc(loader[:-4]) != struct.unpack_from("<I", loader, len(loader) - 4)[0]:
        raise Error("RAM loader checksum mismatch")
    profile = PROFILE["read_loader"]
    patched = bytearray(loader)
    for offset in profile["read_branch_offsets"]:
        # Decoded Thumb instructions: 05 d3 (blo) -> 05 e0 (b), preserving
        # their existing branch targets. RC4 is XOR-based, so the same byte
        # delta applies directly to this exact, hash-pinned encrypted blob.
        patched[profile["usbplug_offset"] + offset + 1] ^= 0xd3 ^ 0xe0
    struct.pack_into("<I", patched, len(patched) - 4, rockchip_crc(patched[:-4]))
    if digest(patched) != profile["sha256"]:
        raise Error("Patched RAM loader does not match the expected image")
    return bytes(patched)


def extract_loader(source: Path, dest: Path, *, enable_reads: bool = False) -> None:
    """Extract the pinned BOOT blob for rkdeveloptool's RAM-only db command."""
    with verified_firmware(source) as image:
        with image.open("rb") as stream:
            header = stream.read(102)
            if header[:4] != b"RKFW":
                raise Error("Missing Rockchip firmware header")
            offset, size = struct.unpack_from("<II", header, 25)
            if (offset != PROFILE["loader_offset_in_firmware"] or
                    size != PROFILE["loader_size_in_firmware"]):
                raise Error("Unexpected official loader bounds")
            stream.seek(offset)
            loader = stream.read(size)
        if loader[:4] != b"BOOT" or digest(loader) != PROFILE["loader_sha256"]:
            raise Error("The firmware's RAM loader does not match the profile")
        dest.write_bytes(patch_loader_reads(loader) if enable_reads else loader)


def patch_core(data: bytes, name: str) -> bytes:
    if digest(data) != PROFILE["files"][name]:
        raise Error(f"Unsupported core: {name}")
    patches = [(PROFILE["keys_path"], PROFILE["virtual_keys_path"],
                PROFILE["core_path_offsets"][name])]
    if name == "/retroarch/cores/prosystem_libretro.so":
        # The factory core prefers this file over libretro's supplied content.
        # Point it at the selected ROM, even if an old OEM override exists.
        patches.append((PROFILE["prosystem_rom_path"], PROFILE["virtual_prosystem_rom_path"],
                        PROFILE["prosystem_rom_path_offset"]))
    for old, new, offset in patches:
        original = old.encode() + b"\0"
        replacement = new.encode() + b"\0"
        if len(replacement) > len(original) or data.count(original) != 1:
            raise Error(f"Unexpected path {old} in {name}")
        if data[offset:offset + len(original)] != original:
            raise Error(f"Unexpected path offset in {name}")
        # Same length: ELF layout and every instruction stay intact.
        data = data[:offset] + replacement.ljust(len(original), b"\0") + \
            data[offset + len(original):]
    return data


def patch_cartridge_frontend(data: bytes, version: str = VERSION) -> bytes:
    """Create the pinned private frontend for this specific payload version."""
    if digest(data) != PROFILE["files"]["/vendor/bin/dmenu2.bin"]:
        raise Error("Unsupported cartridge frontend")
    if version not in CARTRIDGE_FIRST_VERSIONS:
        raise Error("Unsupported cartridge frontend version")
    base = PROFILE["cartridge_frontend_patch"]
    patches = [base]
    expected = base["sha256"]
    if version == "0.3.5":
        empty_read = PROFILE["cartridge_empty_read_patch"]
        patches += empty_read["patches"]
        expected = empty_read["sha256"]
    elif version == "0.3.6":
        reader_reply = PROFILE["cartridge_reader_reply_patch"]
        patches += reader_reply["patches"]
        expected = reader_reply["sha256"]
    # ARM code at 0x16048: call the existing cleanup, then exit(42).
    # 0.3.5 added a zero-byte/GPIO-clear fallback. 0.3.6 replaces that
    # fallback with the exact observed 55 AA 02 FF EA E8 reply plus GPIO
    # clear. Other responses and cartridge ROMs retain stock decoding.
    patched = bytearray(data)
    covered = set()
    for patch in patches:
        offset = patch["offset"]
        original = bytes.fromhex(patch["original"])
        replacement = bytes.fromhex(patch["replacement"])
        if (not original or len(replacement) != len(original) or
                offset < 0 or offset + len(original) > len(data)):
            raise Error("Unexpected cartridge frontend patch bounds")
        positions = set(range(offset, offset + len(original)))
        if positions & covered or data[offset:offset + len(original)] != original:
            raise Error("Unexpected or overlapping cartridge frontend patch")
        covered.update(positions)
        patched[offset:offset + len(original)] = replacement
    if digest(patched) != expected:
        raise Error("Patched cartridge frontend checksum mismatch")
    return bytes(patched)


def build(source: Path, output: Path, *, firmware: bool = False,
          rom: Path | None = None, system: str = "auto",
          games: Path | None = None, menu: Path | None = None,
          installed_bundle: Path | None = None) -> dict:
    require_tools("debugfs", "e2fsck")
    if output.exists():
        raise Error(f"Output already exists; choose a new directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atari-build-", dir="/tmp") as tmp:
        work = Path(tmp)
        original = source
        if firmware:
            original = work / "stock.img"
            extract_firmware(source, original)
        previous = None
        if installed_bundle is not None:
            if firmware:
                raise Error("An installed bundle can only accompany a live OEM backup")
            previous = read_bundle(installed_bundle)
            validate_payload(original, previous)
            files = {name: dump_file(original, name if name != "/vendor/bin/dmenu.bin"
                                     else "/sideload/dmenu.stock") for name in PROFILE["files"]}
        else:
            files = stock_files(original)
        source_hash = file_digest(original)
        target = work / "oem-sideload.img"
        shutil.copyfile(original, target)
        logs = command(["e2fsck", "-fy", str(target)], ok=(0, 1, 2))

        stock_launcher = files["/vendor/bin/dmenu.bin"]
        needle = b"/oem/vendor/bin/dmenu2.bin\n"
        if stock_launcher.count(needle) != 1:
            raise Error("Unexpected stock launcher")
        new_launcher = stock_launcher.replace(needle,
            b"/bin/sh /oem/sideload/boot.sh\n")
        additions = {
            "/sideload/dmenu.stock": stock_launcher,
            "/sideload/boot.sh": (HERE / "payload/boot.sh").read_bytes(),
            "/sideload/cart-first.bin": patch_cartridge_frontend(
                files["/vendor/bin/dmenu2.bin"]),
            "/sideload/launch.sh": (HERE / "payload/launch.sh").read_bytes(),
            "/sideload/keys.sh": (HERE / "payload/keys.sh").read_bytes(),
            "/sideload/stella_usb.so": patch_core(
                files["/retroarch/cores/stella_libretro.so"],
                "/retroarch/cores/stella_libretro.so"),
            "/sideload/prosystem_usb.so": patch_core(
                files["/retroarch/cores/prosystem_libretro.so"],
                "/retroarch/cores/prosystem_libretro.so"),
            "/vendor/bin/dmenu.bin": new_launcher,
        }
        embedded_game = None
        catalog_games = None
        if games is not None:
            if rom is not None or menu is None:
                raise Error("A game menu requires --games and --menu, without --rom")
            menu_data = menu.read_bytes()
            if (menu_data[:6] != b"\x7fELF\x01\x01" or len(menu_data) < 52 or
                    menu_data[18:20] != b"\x28\x00"):
                raise Error("Menu must be a little-endian ARM ELF32 executable")
            game_additions, catalog_games = prepare_catalog(games, system)
            additions.update(game_additions)
            additions["/sideload/menu"] = menu_data
            additions["/sideload/catalog.txt"] = catalog_bytes(catalog_games)
        elif menu is not None:
            raise Error("--menu requires --games")
        if rom is not None:
            game_files, embedded_game = prepare_game(rom, system)
            additions.update({f"/sideload/internal/{name}": data
                              for name, data in game_files.items()})
        marker = {
            "payload_version": VERSION, "model": PROFILE["model"],
            "firmware_version": PROFILE["firmware_version"],
            "source_sha256": source_hash,
            "source_kind": "official-firmware" if firmware else "oem-backup",
            "embedded_game": embedded_game,
            "games": catalog_games,
            "files": {name: digest(data) for name, data in additions.items()},
        }
        additions["/sideload/build.json"] = (json.dumps(marker, indent=2) + "\n").encode()
        instructions = []
        if previous is not None:
            # Remove only the files/directories claimed by the validated old
            # payload. Preserve the rest of the live OEM, including preferences.
            managed = set(previous["files"]) | {"/sideload/build.json"}
            instructions.extend(f"rm {name}" for name in sorted(managed))
            directories = {str(parent) for name in managed for parent in Path(name).parents
                           if str(parent).startswith("/sideload")}
            instructions.extend(f"rmdir {name}" for name in sorted(
                directories, key=lambda name: (-name.count('/'), name)))
        else:
            instructions.append("rm /vendor/bin/dmenu.bin")
        directories = {str(parent) for name in additions for parent in Path(name).parents
                       if str(parent).startswith("/sideload")}
        instructions.extend(f"mkdir {name}" for name in sorted(
            directories, key=lambda name: (name.count('/'), name)))
        for number, (name, data) in enumerate(additions.items()):
            local = work / f"file-{number}"
            local.write_bytes(data)
            mode = "0100755" if name.endswith((".sh", ".so", ".bin")) or name == "/sideload/menu" else "0100644"
            instructions += [f"write {local} {name}",
                             f"set_inode_field {name} mode {mode}",
                             f"set_inode_field {name} uid 0",
                             f"set_inode_field {name} gid 0"]
        commands = work / "debugfs.commands"
        commands.write_text("\n".join(instructions) + "\n")
        logs += command(["debugfs", "-w", "-f", str(commands), str(target)])
        # debugfs may return zero on command errors. Read every written file back.
        for name, data in additions.items():
            if dump_file(target, name) != data:
                raise Error(f"Filesystem write verification failed: {name}")
        for name, expected in files.items():
            if name != "/vendor/bin/dmenu.bin" and dump_file(target, name) != expected:
                raise Error(f"Stock file was unexpectedly changed: {name}")
        logs += command(["e2fsck", "-fn", str(target)])
        if target.stat().st_size != original.stat().st_size:
            raise Error("OEM image changed size")
        manifest = {
            **marker,
            "image": "oem-sideload.img", "image_size": target.stat().st_size,
            "image_sha256": file_digest(target),
            "oem_start_sector": PROFILE["oem_start_sector"],
            "oem_partition_sectors": PROFILE["oem_partition_sectors"],
            "hardware_tested": False,
        }
        # Publish a complete bundle only after all local checks pass.
        stage = work / "bundle"
        stage.mkdir()
        shutil.copyfile(target, stage / manifest["image"])
        write_json(stage / "bundle.json", manifest)
        (stage / "build.log").write_text(logs)
        kind = ("Reference build: rebuild from a complete live OEM backup before installation.\n"
                "device.py refuses to install this reference image.\n" if firmware else
                "Install with device.py and the matching backup directory.\n")
        (stage / "README.txt").write_text(
            f"{PROFILE['model']} sideload {VERSION}\n"
            f"Firmware profile: {PROFILE['firmware_version']}\n"
            + (f"Internal game menu: {len(catalog_games)} games\n"
               "Boot with an empty cartridge slot. Joystick up/down selects; fire starts.\n"
               if catalog_games else
               f"Internal game: {embedded_game['original_name']}\n"
               "Boot without a cartridge to play; no external drive is needed.\n"
               if embedded_game is not None else "Game source: prepared USB drive\n") +
            "This newly built image still needs a console boot/play check.\n\n" + kind +
            "This image contains only OEM partition data. Do not pass it to a full firmware updater.\n"
            "Keep bundle.json and build.log with the image.\n"
            "Setup and recovery instructions: README.md beside sideload.py in the source repository.\n"
        )
        shutil.copytree(stage, output)
        return manifest


def validate_rom(data: bytes, name: str, system: str = "auto") -> str:
    has_header = data[1:10] == b"ATARI7800"
    if system == "auto":
        system = "7800" if has_header else "2600"
        if Path(name).suffix.lower() == ".a78" and not has_header:
            raise Error("An .a78 ROM must include its 128-byte Atari 7800 header")
    if system == "7800":
        if not has_header or len(data) < 128:
            raise Error("7800 ROMs require a 128-byte A78 header")
        declared = int.from_bytes(data[49:53], "big")
        if declared < 1 or declared > 4 * 1024 * 1024 or len(data) != declared + 128:
            raise Error("A78 header size does not match the ROM payload")
    elif system == "2600":
        if has_header:
            raise Error("A78 header found but 2600 was explicitly selected")
        if not 2048 <= len(data) <= 1024 * 1024:
            raise Error("2600 ROM size must be between 2 KiB and 1 MiB")
    else:
        raise Error("Unknown system")
    if Path(name).suffix.lower() in (".zip", ".7z", ".rar", ".gz"):
        raise Error("Extract the ROM from its archive first")
    return system


def game_data(data: bytes, name: str, system: str) -> tuple[dict[str, bytes], dict]:
    system = validate_rom(data, name, system)
    checksum = digest(data)
    info = {"original_name": name, "system": system,
            "sha256": checksum, "size": len(data)}
    return {
        "game.rom": data,
        "game.json": (json.dumps(info, indent=2) + "\n").encode(),
        "manifest.txt": f"{FORMAT}\n{system}\n{checksum}\n{len(data)}\n".encode(),
    }, info


def prepare_game(rom: Path, system: str) -> tuple[dict[str, bytes], dict]:
    if rom.stat().st_size > 4 * 1024 * 1024 + 128:
        raise Error("ROM exceeds the supported size limit")
    return game_data(rom.read_bytes(), rom.name, system)


def catalog_bytes(games: list[dict]) -> bytes:
    return (CATALOG_FORMAT + "\n" + "".join(
        f"{game['sha256']}\t{game['system']}\t{game['title']}\n" for game in games)).encode('ascii')


def prepare_catalog(directory: Path, system: str) -> tuple[dict[str, bytes], list[dict]]:
    found = {}
    extensions = {'.a26', '.bin', '.a78'}
    def add(data: bytes, name: str):
        files, info = game_data(data, name, system)
        title = re.sub(r'\s+\((?:USA|Europe|Japan|World|PAL|NTSC|Rev [^)]+)\)', '',
                       Path(name).stem, flags=re.I)
        title = ''.join(c if 32 <= ord(c) <= 126 else '?' for c in title).strip()[:96]
        if not title:
            raise Error('ROM has an empty display title')
        info['title'] = title
        files['game.json'] = (json.dumps(info, indent=2) + '\n').encode()
        found.setdefault(info['sha256'], (files, info))
        if len(found) > 128:
            raise Error('This menu supports at most 128 distinct games')
    for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file():
            continue
        if path.suffix.lower() in extensions:
            if path.stat().st_size > 4194432:
                raise Error(f'ROM too large: {path.name}')
            add(path.read_bytes(), path.name)
        elif path.suffix.lower() == '.zip':
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    name = Path(entry.filename.replace('\\', '/')).name
                    if entry.is_dir() or Path(name).suffix.lower() not in extensions:
                        continue
                    if entry.file_size > 4194432 or entry.flag_bits & 1:
                        raise Error(f'Encrypted or oversized ROM in {path.name}: {name}')
                    add(archive.read(entry), name)
    if not found:
        raise Error('No supported ROMs found in the games directory')
    additions, games = {}, []
    for files, info in sorted(found.values(), key=lambda pair: (pair[1]['title'].casefold(), pair[1]['sha256'])):
        games.append(info)
        additions.update({f"/sideload/games/{info['sha256']}/{name}": data for name, data in files.items()})
    return additions, games


def validate_payload(image: Path, record: dict) -> None:
    """Check managed files of a known bundle, allowing live OEM preferences to change."""
    validate_ext2(image)
    if (record.get('payload_version') not in SUPPORTED_VERSIONS or
            record.get('model') != PROFILE['model'] or
            record.get('firmware_version') != PROFILE['firmware_version']):
        raise Error('Unsupported installed payload profile')
    marker = json.loads(dump_file(image, '/sideload/build.json'))
    for field in ('payload_version', 'model', 'firmware_version', 'source_sha256',
                  'source_kind', 'files', 'embedded_game', 'games'):
        if marker.get(field) != record.get(field):
            raise Error(f'Installed payload marker mismatch: {field}')
    expected = {'/sideload/dmenu.stock', '/sideload/launch.sh', '/sideload/keys.sh',
                '/sideload/stella_usb.so', '/sideload/prosystem_usb.so', '/vendor/bin/dmenu.bin'}
    if record['payload_version'] in CARTRIDGE_FIRST_VERSIONS:
        expected.update({'/sideload/boot.sh', '/sideload/cart-first.bin'})
        stock_frontend = dump_file(image, '/vendor/bin/dmenu2.bin')
        if dump_file(image, '/sideload/cart-first.bin') != patch_cartridge_frontend(
                stock_frontend, record['payload_version']):
            raise Error('Private cartridge frontend does not match its pinned patch')
    single, games = record.get('embedded_game'), record.get('games')
    if single is not None and games is not None:
        raise Error('Ambiguous game source in bundle')
    content = []
    if single is not None:
        content.append(('/sideload/internal', single))
    if games is not None:
        if record['payload_version'] not in MENU_VERSIONS or not isinstance(games, list) or not 1 <= len(games) <= 128:
            raise Error('Invalid game catalog')
        expected.update({'/sideload/menu', '/sideload/catalog.txt'})
        seen = set()
        for game in games:
            if not isinstance(game, dict) or not re.fullmatch('[0-9a-f]{64}', game.get('sha256', '')):
                raise Error('Invalid catalog game hash')
            if game['sha256'] in seen:
                raise Error('Duplicate catalog game')
            seen.add(game['sha256'])
            title = game.get('title', '')
            if not isinstance(title, str) or not 1 <= len(title) <= 96 or any(not 32 <= ord(c) <= 126 for c in title):
                raise Error('Invalid catalog title')
            content.append((f"/sideload/games/{game['sha256']}", game))
        if dump_file(image, '/sideload/catalog.txt') != catalog_bytes(games):
            raise Error('Catalog does not match its bundle')
        menu = dump_file(image, '/sideload/menu')
        if menu[:6] != b'\x7fELF\x01\x01' or menu[18:20] != b'\x28\x00':
            raise Error('Invalid ARM menu executable')
    for directory, info in content:
        expected.update(directory+'/'+name for name in ('game.rom', 'game.json', 'manifest.txt'))
        data = dump_file(image, directory+'/game.rom')
        if (not isinstance(info, dict) or info.get('system') not in ('2600', '7800') or
                validate_rom(data, info['original_name'], info['system']) != info['system'] or
                len(data) != info['size'] or digest(data) != info['sha256'] or
                json.loads(dump_file(image, directory+'/game.json')) != info):
            raise Error('Internal game metadata or ROM mismatch')
        manifest = f"{FORMAT}\n{info['system']}\n{digest(data)}\n{len(data)}\n".encode()
        if dump_file(image, directory+'/manifest.txt') != manifest:
            raise Error('Internal game manifest mismatch')
    if set(record['files']) != expected:
        raise Error('Bundle does not contain the expected payload files')
    for name, checksum in record['files'].items():
        if digest(dump_file(image, name)) != checksum:
            raise Error(f'Installed payload file mismatch: {name}')
    for name, checksum in PROFILE['files'].items():
        actual_path = '/sideload/dmenu.stock' if name == '/vendor/bin/dmenu.bin' else name
        if digest(dump_file(image, actual_path)) != checksum:
            raise Error(f'Installed payload changed a stock file: {name}')


def read_bundle(directory: Path) -> dict:
    record = json.loads((directory/'bundle.json').read_text())
    if (record.get('source_kind') != 'oem-backup' or record.get('image') != 'oem-sideload.img' or
            record.get('oem_start_sector') != PROFILE['oem_start_sector'] or
            record.get('oem_partition_sectors') != PROFILE['oem_partition_sectors']):
        raise Error('An installable bundle from a complete OEM backup is required')
    image = directory/'oem-sideload.img'
    if (image.stat().st_size != PROFILE['oem_partition_sectors']*512 or
            image.stat().st_size != record['image_size'] or file_digest(image) != record['image_sha256']):
        raise Error('Bundle image checksum or size mismatch')
    validate_payload(image, record)
    return record


def prepare_usb(rom: Path, directory: Path, system: str, replace: bool) -> dict:
    game_files, info = prepare_game(rom, system)
    target = directory / "atari-sideload"
    if target.is_symlink():
        raise Error("Refusing a symlink for the managed atari-sideload directory")
    if target.exists():
        marker = target / "manifest.txt"
        if not replace:
            raise Error("Prepared game already exists; pass --replace to change it")
        if not marker.is_file() or marker.read_bytes().splitlines()[:1] != [FORMAT.encode()]:
            raise Error("Existing directory is not a managed sideload payload")
    target.mkdir(parents=True, exist_ok=True)
    # Commit the manifest last; an interrupted copy fails runtime SHA256 validation.
    for name, data in game_files.items():
        atomic_write(target / name, data)
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("build", help="Build a local OEM image; never flashes a device")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--firmware", type=Path, help="Official Pac-Man .exe or .img (reference build)")
    source.add_argument("--oem", type=Path, help="Live OEM backup from device.py (installable build)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--rom", type=Path, help="Include one game in the console's internal OEM storage")
    p.add_argument("--games", type=Path, help="Folder of raw ROMs and ZIPs for the internal game menu")
    p.add_argument("--menu", type=Path, help="ARM menu executable produced by build_menu.py")
    p.add_argument("--installed-bundle", type=Path, help="Previous bundle when building from a modified live backup")
    p.add_argument("--system", choices=("auto", "2600", "7800"), default="auto")
    p = sub.add_parser("inspect", help="Validate model and firmware of a raw OEM image")
    p.add_argument("oem", type=Path)
    p = sub.add_parser("prepare-usb", help="Prepare a selected ROM in a folder or mounted USB drive")
    p.add_argument("rom", type=Path)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--system", choices=("auto", "2600", "7800"), default="auto")
    p.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "build":
            result = build((args.firmware or args.oem).resolve(), args.output.resolve(),
                           firmware=args.firmware is not None,
                           rom=args.rom.resolve() if args.rom else None, system=args.system,
                           games=args.games.resolve() if args.games else None,
                           menu=args.menu.resolve() if args.menu else None,
                           installed_bundle=args.installed_bundle.resolve() if args.installed_bundle else None)
            print(json.dumps(result, indent=2))
        elif args.action == "inspect":
            require_tools("debugfs")
            stock_files(args.oem.resolve())
            print(f"Supported: {PROFILE['model']}, firmware {PROFILE['firmware_version']}")
        elif args.action == "prepare-usb":
            print(json.dumps(prepare_usb(args.rom, args.directory, args.system, args.replace), indent=2))
        return 0
    except (Error, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
