#!/usr/bin/env python3
"""Back up the console, replace its internal game catalog, and verify the write.

This command writes the OEM partition. Run with the console in system update
mode. First-run paths are remembered only after a successful installation.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sys
import uuid

from device import Device, backup_device, change_device, load_backup, recognize_backup
from sideload import Error, build, prepare_catalog, require_tools, write_json

HERE = Path(__file__).resolve().parent
STATE_FORMAT = "ATARI_GAME_UPDATER_V1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=HERE / "local",
                        help="Private folder for settings, backups, bundles and receipts")
    parser.add_argument("--firmware", type=Path, help="Official Pac-Man 2.0.1.6 updater")
    parser.add_argument("--games", type=Path, help="Folder containing the complete desired game list")
    parser.add_argument("--menu", type=Path, help="ARM executable from build_menu.py")
    parser.add_argument("--installed-bundle", type=Path,
                        help="Existing installation's bundle when adopting a previous setup")
    parser.add_argument("--original-backup", type=Path,
                        help="Backup used to build --installed-bundle; provide both together")
    parser.add_argument("--rkdeveloptool", default="rkdeveloptool")
    args = parser.parse_args()
    run = None
    try:
        require_tools("debugfs", "e2fsck", args.rkdeveloptool)
        state = args.state.resolve()
        state.mkdir(parents=True, exist_ok=True)
        # Keep the lock open through the write and state update.
        with (state / "update.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Error("Another update is already using this state folder") from None
            current_path = state / "current.json"
            current = json.loads(current_path.read_text()) if current_path.exists() else {}
            if current and current.get("format") != STATE_FORMAT:
                raise Error("Unrecognized updater settings")
            selected = {}
            for name in ("firmware", "games", "menu"):
                value = getattr(args, name) or current.get(name)
                if not value:
                    raise Error(f"First run requires --{name}; see docs/ADDING_GAMES.md")
                selected[name] = Path(value).resolve()
                if not selected[name].exists():
                    raise Error(f"Missing {name}: {selected[name]}")
            if bool(args.installed_bundle) != bool(args.original_backup):
                raise Error("Provide --installed-bundle and --original-backup together")
            previous_bundle = args.installed_bundle or current.get("installed_bundle")
            previous_backup = args.original_backup or current.get("original_backup")
            if bool(previous_bundle) != bool(previous_backup):
                raise Error("Incomplete previous-installation settings")
            if previous_bundle:
                previous_bundle = Path(previous_bundle).resolve()
                previous_backup = Path(previous_backup).resolve()
            menu = selected["menu"].read_bytes()
            if len(menu) < 52 or menu[:6] != b"\x7fELF\x01\x01" or menu[18:20] != b"\x28\x00":
                raise Error("Menu must be the compiled little-endian ARM ELF32 executable")
            _, games = prepare_catalog(selected["games"], "auto")
            print(f"Installing {len(games)} games from {selected['games']}:", flush=True)
            for game in games:
                print(f"  {game['title']} (Atari {game['system']})", flush=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run = state / "runs" / f"{stamp}-{uuid.uuid4().hex[:8]}"
            run.mkdir(parents=True)
            backup, bundle, receipt = run / "backup", run / "bundle", run / "install"
            plan = {"format": STATE_FORMAT, **{k: str(v) for k, v in selected.items()},
                    "backup": str(backup), "bundle": str(bundle), "record": str(receipt),
                    "previous_settings": current,
                    "previous_bundle": str(previous_bundle) if previous_bundle else None,
                    "previous_backup": str(previous_backup) if previous_backup else None}
            write_json(run / "plan.json", plan)
            print(f"Backup and recovery records: {run}", flush=True)
            device = Device(args.rkdeveloptool)
            device.boot_loader(selected["firmware"], enable_reads=True)
            backup_device(device, backup)
            if previous_bundle:
                recognize_backup(backup, previous_bundle, previous_backup)
            else:
                load_backup(backup)  # Require supported stock firmware on a first install.
            build(backup / "oem.img", bundle, games=selected["games"],
                  menu=selected["menu"], installed_bundle=previous_bundle)
            change_device(device, backup, bundle, receipt, restore=False)
            new_state = {"format": STATE_FORMAT, **{k: str(v) for k, v in selected.items()},
                         "installed_bundle": str(bundle), "original_backup": str(backup),
                         "last_run": str(run)}
            write_json(current_path, new_state)
            print(f"Game update complete. Settings saved in {current_path}")
            print("Keep this entire state folder and all earlier backups for recovery.")
        return 0
    except (Error, OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if run:
            print(f"Keep {run}. See docs/RECOVERY.md before retrying a failed write.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
