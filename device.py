#!/usr/bin/env python3
"""Back up or install the Pac-Man sideload payload through Rockchip Loader USB.

Only the OEM partition can be written to flash. boot-loader prepares a temporary
RAM loader from the official package, optionally enabling unrestricted reads.
No erase, bootloader flash, GPT or dumper writes are implemented.
Install and restore are explicit subcommands.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import zlib

from sideload import (Error, PROFILE, VERSION, command, digest, dump_file, file_digest,
                      extract_loader, require_tools, stock_files, validate_ext2,
                      validate_rom, write_json, FORMAT, read_bundle, validate_payload)

SECTOR = 512
BACKUP_FORMAT = "ATARI_SIDELOAD_BACKUP_V1"


class ReadRestricted(Error):
    """The running USB service returns placeholder bytes instead of storage."""


def parse_header(data: bytes) -> dict:
    if len(data) != 2 * SECTOR or data[512:520] != b"EFI PART":
        raise Error("A valid primary GPT is required; refusing guessed flash offsets")
    header = bytearray(data[512:])
    revision, size, crc = struct.unpack_from("<III", header, 8)
    if revision != 0x10000 or not 92 <= size <= 512:
        raise Error("Unsupported GPT header")
    struct.pack_into("<I", header, 16, 0)
    if zlib.crc32(header[:size]) != crc:
        raise Error("GPT header checksum mismatch")
    current, backup, first, last = struct.unpack_from("<4Q", header, 24)
    lba, count, entry_size, table_crc = struct.unpack_from("<QIII", header, 72)
    table_bytes = count * entry_size
    sectors = (table_bytes + SECTOR - 1) // SECTOR
    if (current != 1 or backup <= last or first > last or
            entry_size < 128 or entry_size % 128 or
            not 1 <= count <= 1024 or not 1 <= sectors <= 256 or
            lba < 2 or lba + sectors > first):
        raise Error("Invalid GPT bounds")
    return {"disk_guid": bytes(header[56:72]).hex(), "first": first, "last": last,
            "table_lba": lba, "count": count, "entry_size": entry_size,
            "table_crc": table_crc, "table_bytes": table_bytes, "table_sectors": sectors}


def parse_oem(header: dict, data: bytes) -> dict:
    if len(data) != header["table_sectors"] * SECTOR:
        raise Error("Truncated GPT entries")
    if zlib.crc32(data[:header["table_bytes"]]) != header["table_crc"]:
        raise Error("GPT entry checksum mismatch")
    partitions = []
    matches = []
    for i in range(header["count"]):
        entry = data[i * header["entry_size"]:(i + 1) * header["entry_size"]]
        if entry[:16] == bytes(16):
            continue
        first, last = struct.unpack_from("<QQ", entry, 32)
        if not header["first"] <= first <= last <= header["last"]:
            raise Error("Partition outside GPT usable range")
        name = entry[56:128].decode("utf-16-le").split("\0", 1)[0]
        partitions.append((first, last))
        if name == "oem":
            matches.append({"start_sector": first, "sectors": last - first + 1,
                            "guid": entry[16:32].hex()})
    ordered = sorted(partitions)
    if any(a[1] >= b[0] for a, b in zip(ordered, ordered[1:])):
        raise Error("Overlapping GPT partitions")
    if len(matches) != 1:
        raise Error("Expected exactly one OEM partition")
    result = matches[0]
    if (result["start_sector"] != PROFILE["oem_start_sector"] or
            result["sectors"] != PROFILE["oem_partition_sectors"]):
        raise Error("OEM layout does not match the Pac-Man firmware profile")
    return result


class Device:
    def __init__(self, executable: str):
        require_tools(executable)
        self.executable = executable

    def describe(self) -> tuple[str, str]:
        # Upstream returns -1 (255) when no devices are found, and prints the
        # mode as a bare final word. Some builds prefix that word with Mode=.
        output = command([self.executable, "ld"], ok=(0, 255))
        rows = [line for line in output.splitlines() if re.search(r"DevNo\s*=", line)]
        if len(rows) != 1:
            raise Error("Connect exactly one console in system update mode. "
                        "Check the USB data cable and Linux USB permissions.\n" +
                        (output.strip() or "No Rockchip device is visible."))
        row = rows[0].strip()
        ids = re.search(r"\bVid\s*=\s*0x([0-9a-f]+)\s*,\s*Pid\s*=\s*0x([0-9a-f]+)",
                        row, re.I)
        if (not ids or int(ids[1], 16) != PROFILE["usb_vendor_id"] or
                int(ids[2], 16) != PROFILE["usb_product_id"]):
            raise Error("Unexpected USB device; expected Rockchip 2207:310c.\n" + row)
        mode = re.search(r"(?:\bMode\s*=\s*|\s)(Maskrom|Loader)\s*$", row, re.I)
        if not mode:
            raise Error("Cannot determine Rockchip USB mode:\n" + row)
        return row, mode[1].lower()

    def probe_storage(self) -> None:
        """Probe OEM itself: GPT can be readable while higher sectors are blocked."""
        with tempfile.TemporaryDirectory(prefix="atari-ready-", dir="/tmp") as tmp:
            output = Path(tmp) / "oem-head.bin"
            command([self.executable, "rl", hex(PROFILE["oem_start_sector"]),
                     "0x8", str(output)], timeout=8)
            if not output.is_file() or output.stat().st_size != 8 * SECTOR:
                raise Error("Incomplete storage readiness read")
            if output.read_bytes() == b"\xcc" * (8 * SECTOR):
                raise ReadRestricted("The current USB service returns 0xCC placeholders "
                                     "for OEM reads; this is not backup data")

    def identify(self) -> str:
        row, mode = self.describe()
        if mode == "maskrom":
            try:
                self.probe_storage()
            except Error as exc:
                raise Error(
                    "The console reports Maskrom and storage is not readable. "
                    "Prepare the temporary RAM loader first:\n"
                    "  python3 device.py boot-loader --enable-reads --firmware "
                    "path/to/Atari2600PacMan_Firmware_Updater_2.0.1.6.exe\n"
                    "Then retry without power-cycling the console. If already prepared, "
                    "check USB permissions and the data cable.\n" + row + "\n" + str(exc)
                ) from exc
        return row

    def boot_loader(self, firmware: Path, *, enable_reads: bool = False) -> None:
        identity, mode = self.describe()
        reset_to_bootrom = False
        try:
            self.probe_storage()
        except ReadRestricted:
            if not enable_reads:
                raise Error("OEM reads are restricted. Repeat boot-loader with --enable-reads "
                            "to use the pinned temporary loader with its two read-limit "
                            "branches patched in RAM. No flash write is involved.")
            reset_to_bootrom = True
        except Error:
            if mode != "maskrom":
                raise
        else:
            print(f"OEM storage is already readable (reported mode: {mode}); ready for backup.")
            return
        with tempfile.TemporaryDirectory(prefix="atari-loader-", dir="/tmp") as tmp:
            loader = Path(tmp) / "pacman-loader.bin"
            print("Checking the official Pac-Man package and preparing its RAM loader...", flush=True)
            extract_loader(firmware, loader, enable_reads=enable_reads)
            if enable_reads:
                print("Prepared the pinned RAM loader with unrestricted reads: "
                      + PROFILE["read_loader"]["sha256"], flush=True)
            if self.describe() != (identity, mode):
                raise Error("USB device changed while preparing the loader; reconnect and retry")
            if reset_to_bootrom:
                print("The current USB service restricts reads. Restarting into chip download mode...",
                      flush=True)
                print(command([self.executable, "rd", "3"]).strip(), flush=True)
                time.sleep(1)
                deadline = time.monotonic() + 15
                while True:
                    try:
                        _, reset_mode = self.describe()
                        if reset_mode == "maskrom":
                            break
                    except Error:
                        pass
                    if time.monotonic() >= deadline:
                        raise Error("The console did not reappear in chip download mode. "
                                    "Power off and re-enter system update mode before retrying.")
                    time.sleep(0.5)
            print("Starting the temporary loader with rkdeveloptool db...", flush=True)
            log = command([self.executable, "db", str(loader)])
            if log.strip():
                print(log.strip(), flush=True)
        # The USB device disconnects briefly while the RAM loader starts.
        deadline = time.monotonic() + 15
        last_error = "The device's storage is not readable yet."
        while time.monotonic() < deadline:
            try:
                self.identify()
                self.probe_storage()
                print("OEM storage ready. Keep power connected and run the backup command.")
                return
            except Error as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise Error("The temporary loader did not become ready within 15 seconds.\n" + last_error)

    def read(self, start: int, count: int, output: Path) -> None:
        self.identify()
        if output.exists():
            raise Error(f"Readback path already exists: {output}")
        print(f"Reading {count * SECTOR // 1024} KiB from sector {start:#x}...", flush=True)
        log = command([self.executable, "rl", hex(start), hex(count), str(output)])
        if not output.is_file() or output.stat().st_size != count * SECTOR:
            raise Error("Incomplete device read:\n" + log)

    def layout(self, directory: Path) -> tuple[dict, dict]:
        self.read(0, 2, directory / "gpt-header.bin")
        header = parse_header((directory / "gpt-header.bin").read_bytes())
        self.read(header["table_lba"], header["table_sectors"], directory / "gpt-entries.bin")
        partition = parse_oem(header, (directory / "gpt-entries.bin").read_bytes())
        return header, partition

    def write_oem(self, image: Path) -> str:
        self.identify()
        if image.stat().st_size != PROFILE["oem_partition_sectors"] * SECTOR:
            raise Error("Only a complete, backed-up OEM partition may be written")
        print("Writing the OEM partition. Keep USB and power connected.", flush=True)
        return command([self.executable, "wl", hex(PROFILE["oem_start_sector"]), str(image)])


def new_directory(path: Path, *, allow_empty: bool = False) -> None:
    if path.exists():
        if allow_empty and path.is_dir() and not any(path.iterdir()):
            return
        raise Error(f"Choose a new output directory: {path}")
    path.mkdir(parents=True)


def backup_device(device: Device, output: Path) -> None:
    identity = device.identify()
    device.probe_storage()
    # Earlier versions left an empty directory after a mode-detection failure.
    # Reuse only an empty directory; never replace a previous or partial backup.
    new_directory(output, allow_empty=True)
    header, partition = device.layout(output)
    image = output / "oem.img"
    device.read(partition["start_sector"], partition["sectors"], image)
    try:
        validate_ext2(image)
    except Error as exc:
        write_json(output / "read-failure.json", {"usable_backup": False, "error": str(exc)})
        raise Error(f"The read did not produce a usable OEM backup: {exc}. "
                    f"The failed read is retained in {output}.") from exc
    # A backup is useful even when the installed firmware is too old to patch.
    try:
        stock_files(image)
        supported, note = True, "Supported Pac-Man 2.0.1.6 OEM files"
    except Error as exc:
        supported, note = False, str(exc)
    record = {
        "format": BACKUP_FORMAT, "device_description": identity,
        "disk_guid": header["disk_guid"], "partition": partition,
        "supported_firmware": supported, "firmware_note": note,
        "files": {name: file_digest(output / name) for name in
                  ("gpt-header.bin", "gpt-entries.bin", "oem.img")},
    }
    write_json(output / "backup.json", record)
    print(f"Backup saved: {output}\n{note}")


def load_backup(directory: Path, *, check_files: bool = True) -> dict:
    record = json.loads((directory / "backup.json").read_text())
    if record.get("format") != BACKUP_FORMAT:
        raise Error("Unrecognized backup manifest")
    for name in ("gpt-header.bin", "gpt-entries.bin", "oem.img"):
        if file_digest(directory / name) != record["files"][name]:
            raise Error(f"Backup checksum mismatch: {name}")
    header = parse_header((directory / "gpt-header.bin").read_bytes())
    partition = parse_oem(header, (directory / "gpt-entries.bin").read_bytes())
    if partition != record["partition"] or header["disk_guid"] != record["disk_guid"]:
        raise Error("Backup metadata does not match its GPT")
    if (directory / "oem.img").stat().st_size != partition["sectors"] * SECTOR:
        raise Error("Backup is not a complete OEM partition")
    if check_files:
        if record.get("installed_payload") is not None:
            validate_payload(directory / "oem.img", record["installed_payload"])
        else:
            stock_files(directory / "oem.img")
    return record


def load_bundle(directory: Path, backup: dict) -> dict:
    record = read_bundle(directory)
    if record["source_sha256"] != backup["files"]["oem.img"]:
        raise Error("Bundle was not built from the provided backup")
    return record


def recognize_backup(directory: Path, installed_bundle: Path, original_backup: Path) -> None:
    """Accept a modified backup only against a verified previous installation."""
    original = load_backup(original_backup)
    installed = load_bundle(installed_bundle, original)
    current = load_backup(directory, check_files=False)
    if current["disk_guid"] != original["disk_guid"] or current["partition"] != original["partition"]:
        raise Error("Backups belong to different devices or layouts")
    for name in ("gpt-header.bin", "gpt-entries.bin"):
        if current["files"][name] != original["files"][name]:
            raise Error("Backup partition tables differ")
    validate_payload(directory / "oem.img", installed)
    current["installed_payload"] = installed
    current["supported_firmware"] = True
    current["firmware_note"] = f"Verified installed sideload {installed['payload_version']}; live OEM preserved"
    write_json(directory / "backup.json", current)
    print(current["firmware_note"])


def validate_failed_install(directory: Path, backup: dict, bundle: dict) -> None:
    """Allow a damaged filesystem to be restored using a pre-write receipt."""
    record = json.loads((directory / "operation.json").read_text())
    if (record.get("operation") != "install" or
            record.get("status") not in ("writing", "failed", "readback-mismatch") or
            record.get("source_sha256") != bundle["image_sha256"] or
            record.get("partition") != backup["partition"]):
        raise Error("This is not a matching interrupted installation record")
    for name in ("gpt-header.bin", "gpt-entries.bin"):
        if file_digest(directory / name) != backup["files"][name]:
            raise Error("Interrupted installation used a different partition table")
    if file_digest(directory / "before.img") != backup["files"]["oem.img"]:
        raise Error("Interrupted installation's original image differs from the backup")


def change_device(device: Device, backup_dir: Path, bundle_dir: Path,
                  record_dir: Path, *, restore: bool,
                  failed_install: Path | None = None) -> None:
    backup = load_backup(backup_dir)
    bundle = load_bundle(bundle_dir, backup)
    if failed_install is not None:
        if not restore:
            raise Error("An interrupted installation record is only used with restore")
        validate_failed_install(failed_install, backup, bundle)
    new_directory(record_dir)
    header, partition = device.layout(record_dir)
    if header["disk_guid"] != backup["disk_guid"] or partition != backup["partition"]:
        raise Error("Connected device's GPT does not match the saved backup")
    for name in ("gpt-header.bin", "gpt-entries.bin"):
        if file_digest(record_dir / name) != backup["files"][name]:
            raise Error("Device partition table changed since the backup")
    before = record_dir / "before.img"
    device.read(partition["start_sector"], partition["sectors"], before)
    if restore:
        if file_digest(before) == backup["files"]["oem.img"]:
            print("The device already contains this original backup; nothing was written.")
            return
        if failed_install is None:
            marker = json.loads(dump_file(before, "/sideload/build.json"))
            if (marker["source_sha256"] != backup["files"]["oem.img"] or
                    marker["files"] != bundle["files"]):
                raise Error("Installed payload does not belong to this backup")
        source = backup_dir / "oem.img"
        expected = backup["files"]["oem.img"]
    else:
        if file_digest(before) != backup["files"]["oem.img"]:
            raise Error("OEM changed since the backup; make a fresh backup and rebuild")
        source = bundle_dir / "oem-sideload.img"
        expected = bundle["image_sha256"]
    record = {"operation": "restore" if restore else "install", "status": "writing",
              "source": str(source), "source_sha256": expected,
              "original_backup": str(backup_dir), "partition": partition}
    if failed_install is not None:
        record["failed_install"] = str(failed_install)
    write_json(record_dir / "operation.json", record)
    # Only this line writes to flash. Everything above it is read-only.
    stage = "write"
    try:
        log = device.write_oem(source)
        (record_dir / "write.log").write_text(log)
        stage = "readback"
        device.read(partition["start_sector"], partition["sectors"], record_dir / "after.img")
    except (Error, OSError) as exc:
        record.update(status="failed", stage=stage, error=str(exc))
        write_json(record_dir / "operation.json", record)
        raise
    actual = file_digest(record_dir / "after.img")
    record.update(status="verified" if actual == expected else "readback-mismatch",
                  readback_sha256=actual)
    write_json(record_dir / "operation.json", record)
    if actual != expected:
        raise Error("Flash readback mismatch. Leave Loader mode available; preserve the backup and operation log.")
    print("OEM write verified byte for byte. Power off the console before changing USB connections.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rkdeveloptool", default="rkdeveloptool")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("boot-loader", help="Prepare a temporary RAM loader; no flash writes")
    p.add_argument("--firmware", type=Path, required=True,
                   help="Official Pac-Man 2.0.1.6 updater EXE or its Atari_Firmware.img")
    p.add_argument("--enable-reads", action="store_true",
                   help="Patch the two read-limit branches in the temporary RAM loader")
    p = sub.add_parser("backup", help="Read GPT and complete OEM partition; no device writes")
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("recognize-backup", help="Validate a modified local backup against its known previous bundle")
    p.add_argument("--backup", type=Path, required=True)
    p.add_argument("--installed-bundle", type=Path, required=True)
    p.add_argument("--original-backup", type=Path, required=True)
    for action in ("install", "restore"):
        p = sub.add_parser(action, help=f"Explicitly {action} the OEM partition and verify readback")
        p.add_argument("--backup", type=Path, required=True)
        p.add_argument("--bundle", type=Path, required=True)
        p.add_argument("--record", type=Path, required=True, help="New directory for before/after snapshots")
        if action == "restore":
            p.add_argument("--failed-install", type=Path,
                           help="Use this interrupted install record when the OEM filesystem is damaged")
    args = parser.parse_args()
    try:
        if args.action == "recognize-backup":
            require_tools("debugfs")
            recognize_backup(args.backup.resolve(), args.installed_bundle.resolve(),
                             args.original_backup.resolve())
            return 0
        device = Device(args.rkdeveloptool)
        if args.action == "boot-loader":
            device.boot_loader(args.firmware.resolve(), enable_reads=args.enable_reads)
            return 0
        require_tools("debugfs")
        if args.action == "backup":
            backup_device(device, args.output.resolve())
        else:
            change_device(device, args.backup.resolve(), args.bundle.resolve(),
                          args.record.resolve(), restore=args.action == "restore",
                          failed_install=(args.failed_install.resolve()
                                          if getattr(args, "failed_install", None) else None))
        return 0
    except (Error, OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
