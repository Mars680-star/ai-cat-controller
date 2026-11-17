#!/usr/bin/env python3
"""Read-only, device-scoped file backup; never flash or restore a partition."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from urllib.parse import quote


SERIAL = "7c2b63fd4a120"
DATABASE = Path("/var/lib/ai-cat-controller/product-data.db")
BACKUP_ROOT = Path("/root/ai-cat-backups")
ROOT_ENTRIES = ("etc", "usr", "var", "opt", "root", "home", "boot", "bin", "sbin", "lib", "srv")
SERVICES = (
    "ai-cat-controller.service", "toy_ui.service", "toy_light.service",
    "toy_main.service", "toy_voice.service", "toy_motor.service",
    "volc-pulseaudio.service", "volc-k1-wake-word.service", "volc-conv-ai.service",
)


def validate_identity(expected, actual):
    if expected != SERIAL or actual != expected:
        raise RuntimeError("Wrong device: this backup is restricted to 7c2b63fd4a120")


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sqlite_report(path):
    connection = sqlite3.connect("file:" + quote(str(Path(path).resolve()), safe="/") + "?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise RuntimeError("SQLite integrity check failed")
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        counts = {}
        for (name,) in tables:
            identifier = '"' + name.replace('"', '""') + '"'
            counts[name] = connection.execute("SELECT count(*) FROM " + identifier).fetchone()[0]
        return {
            "integrity": "ok",
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
            "table_counts": counts,
        }
    finally:
        connection.close()


def snapshot_sqlite(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file() or destination.exists():
        raise RuntimeError("SQLite source missing or destination already exists")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    original = copied = None
    try:
        original = sqlite3.connect("file:" + quote(str(source.resolve()), safe="/") + "?mode=ro", uri=True)
        copied = sqlite3.connect(destination)
        original.backup(copied)
        copied.execute("PRAGMA journal_mode=DELETE")
        copied.close()
        copied = None
        original.close()
        original = None
        result = sqlite_report(destination)
        if result["journal_mode"] != "delete" or any(
            Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")
        ):
            raise RuntimeError("SQLite snapshot is not standalone")
        return result
    except sqlite3.Error as error:
        raise RuntimeError("SQLite snapshot failed") from error
    finally:
        if copied is not None:
            copied.close()
        if original is not None:
            original.close()


def build_tar_command(backup_dir):
    backup_dir = Path(backup_dir)
    if (not backup_dir.is_absolute() or backup_dir.parent != BACKUP_ROOT
            or not backup_dir.name.startswith("before-firmware-")
            or ".." in backup_dir.parts):
        raise RuntimeError("Backup output must use a dedicated before-firmware directory")
    relative = backup_dir.relative_to("/").as_posix()
    return [
        "tar", "--create", "--file=" + str(Path(backup_dir) / "device-files.tar.gz"),
        "--use-compress-program=gzip -1", "--numeric-owner", "--acls", "--xattrs",
        "--sparse", "--one-file-system", "--exclude=" + relative,
        "--exclude=var/lib/ai-cat-controller/product-data.db*", "-C", "/",
        *[name for name in ROOT_ENTRIES if Path("/", name).exists()],
    ]


def capture_command(directory, name, arguments, required=True):
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    (directory / name).write_bytes(result.stdout + result.stderr)
    if required and result.returncode:
        raise RuntimeError("Read-only inventory command failed: " + arguments[0])


def critical_hashes():
    paths = [Path("/etc/ai-cat-controller.env"), Path("/usr/bin/ai-toy_app"), Path("/usr/bin/toy_ui"),
             Path("/usr/bin/toy_light"), Path("/usr/bin/toy_main"), Path("/usr/bin/toy_audio")]
    paths += list(Path("/root").glob(".motor_*")) + list(Path("/root").glob(".adc_*"))
    paths += [Path("/root/.motor_type"), Path("/root/.motor_limit_switch_exist")]
    paths += list(Path("/etc/NetworkManager/system-connections").glob("*"))
    paths += list(Path("/etc/ssh").glob("ssh_host_*_key"))
    return {path.relative_to("/").as_posix(): digest_file(path)
            for path in paths if path.is_file() and not path.is_symlink()}


def verify_backup(directory):
    directory = Path(directory)
    metadata = json.loads((directory / "metadata.json").read_text())
    validate_identity(SERIAL, metadata["serial"])
    checksummed = set()
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ", 1)
        if (name in checksummed or len(expected) != 64 or Path(name).name != name
                or not (directory / name).is_file() or (directory / name).is_symlink()):
            raise RuntimeError("Unsafe or missing checksum entry")
        if digest_file(directory / name) != expected:
            raise RuntimeError("Backup checksum mismatch: " + name)
        checksummed.add(name)
    payloads = {path.name for path in directory.iterdir()
                if path.is_file() and path.name not in ("SHA256SUMS", ".complete")}
    if checksummed != payloads:
        raise RuntimeError("Checksum manifest does not cover every payload")
    archive = directory / "device-files.tar.gz"
    subprocess.run(["gzip", "-t", str(archive)], check=True)
    critical = metadata["critical_files_sha256"]
    seen = set()
    names = set()
    with tarfile.open(archive, "r|gz") as stream:
        for member in stream:
            name = member.name.removeprefix("./").rstrip("/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise RuntimeError("Unsafe archive member")
            names.add(name)
            if name in critical:
                if not member.isfile():
                    raise RuntimeError("Critical backup member is not a file")
                digest = hashlib.sha256()
                with stream.extractfile(member) as payload:
                    for block in iter(lambda: payload.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != critical[name]:
                    raise RuntimeError("Critical archive content mismatch: " + name)
                seen.add(name)
    if seen != set(critical):
        raise RuntimeError("Critical files missing from archive")
    required = ("etc/ai-cat-controller.env", "opt/ai-cat-controller/src/ai_cat_controller/main.py",
                "root/.cache/sensevoice", "boot/vmlinuz-6.6.63", "root/ai-cat-backups")
    if any(name not in names for name in required):
        raise RuntimeError("Required recovery content missing")
    if any(name.startswith("var/lib/ai-cat-controller/product-data.db") for name in names):
        raise RuntimeError("Live SQLite database unexpectedly archived")
    if sqlite_report(directory / "product-data-standalone.db") != metadata["sqlite"]:
        raise RuntimeError("SQLite snapshot report mismatch")
    return {"serial": SERIAL, "checksums": "ok", "archive": "ok", "sqlite": "ok", "members": len(names)}


def create_backup(serial):
    os.umask(0o077)
    validate_identity(serial, Path("/proc/device-tree/serial-number").read_bytes().rstrip(b"\0").decode())
    if os.geteuid() != 0:
        raise RuntimeError("Device backup requires root")
    safety = Path("/etc/ai-cat-controller.env").read_text().splitlines()
    if "AI_CAT_ENABLE_MOTION=false" not in safety or "AI_CAT_HAS_TAIL=false" not in safety:
        raise RuntimeError("Motion/no-tail safety settings must remain in place")
    if shutil.disk_usage("/root").free < 5 * 1024**3:
        raise RuntimeError("Insufficient free space for file backup")
    BACKUP_ROOT.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="before-firmware-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-", dir=BACKUP_ROOT))
    if directory.is_symlink() or directory.resolve().parent != BACKUP_ROOT.resolve():
        raise RuntimeError("Unexpected backup directory symlink")
    print("backup_directory=" + str(directory), flush=True)
    print("stage=sqlite_snapshot", flush=True)
    sqlite = snapshot_sqlite(DATABASE, directory / "product-data-standalone.db")
    critical = critical_hashes()
    capture_command(directory, "services-before.txt", ["systemctl", "show", *SERVICES, "-p", "Id", "-p", "ActiveState", "-p", "MainPID", "-p", "UnitFileState"])
    capture_command(directory, "partitions.txt", ["lsblk", "-o", "NAME,SIZE,FSTYPE,UUID,MOUNTPOINTS"])
    capture_command(directory, "partition-table.txt", ["sfdisk", "--dump", "/dev/mmcblk2"])
    capture_command(directory, "mounts.txt", ["findmnt"])
    capture_command(directory, "packages.txt", ["dpkg-query", "-W"])
    capture_command(directory, "kernel-log.txt", ["journalctl", "-b", "-k", "-n", "250", "--no-pager"])
    for name, source in (("cmdline.txt", "/proc/cmdline"), ("device-tree.dtb", "/sys/firmware/fdt")):
        (directory / name).write_bytes(Path(source).read_bytes())
    # Preserve small boot/loader areas as reference data, not a flashable full-disk image.
    for name in ("mmcblk2p1", "mmcblk2p2", "mmcblk2p3", "mmcblk2p4", "mmcblk2boot0", "mmcblk2boot1"):
        source = Path("/dev") / name
        size = int((Path("/sys/class/block") / name / "size").read_text()) * 512
        if not 0 < size <= 8 * 1024**2:
            raise RuntimeError("Unexpected boot area size")
        with source.open("rb") as original, (directory / (name + ".img")).open("xb") as copied:
            shutil.copyfileobj(original, copied, 1024 * 1024)
        if (directory / (name + ".img")).stat().st_size != size:
            raise RuntimeError("Incomplete boot area backup")
    print("stage=device_file_archive", flush=True)
    with (directory / "archive-warnings.txt").open("wb") as warnings:
        result = subprocess.run(["nice", "-n", "10", *build_tar_command(directory)],
                                stdout=subprocess.DEVNULL, stderr=warnings, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError("File archive failed; backup is incomplete")
    if critical_hashes() != critical:
        raise RuntimeError("Critical configuration changed during backup")
    capture_command(directory, "services-after.txt", ["systemctl", "show", *SERVICES, "-p", "Id", "-p", "ActiveState", "-p", "MainPID", "-p", "UnitFileState"])
    if (directory / "services-before.txt").read_bytes() != (directory / "services-after.txt").read_bytes():
        raise RuntimeError("Service state changed during backup; inspect before flashing")
    metadata = {
        "format": "ai-cat-pre-flash-files-v1", "serial": serial,
        "created_at": datetime.now(timezone.utc).isoformat(), "sqlite": sqlite,
        "critical_files_sha256": critical, "tar_exit_code": result.returncode,
        "scope": "Live file-level backup, standalone product SQLite, boot files and small boot-area references; NOT a complete flashable firmware image.",
        "service_mutations": False, "motion_or_partition_writes": False,
        "restore_policy": "Same original device only. Do not restore old boot/DTB/loader/config wholesale onto new firmware. Review hardware calibration and keep motion disabled.",
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (directory / "BACKUP_README.txt").write_text(
        "Contains passwords, API/cloud credentials, SSH identity and private user data. Keep offline and private.\n"
        "Not a full-disk/flashable firmware image. The live archive is not an atomic OS snapshot.\n"
        "Product SQLite is excluded from device-files.tar.gz; use product-data-standalone.db, never old WAL backups.\n"
        "Existing factory backups are inside root/ai-cat-backups in device-files.tar.gz.\n"
        "Do not automatically restore the whole archive after flashing; new-firmware drivers and hardware config must be reviewed.\n"
    )
    (directory / "SHA256SUMS").write_text("".join(
        digest_file(path) + "  " + path.name + "\n"
        for path in sorted(directory.iterdir()) if path.is_file()
    ))
    print("stage=backup_verification", flush=True)
    verification = verify_backup(directory)
    (directory / ".complete").write_text(json.dumps(verification) + "\n")
    print(json.dumps(verification), flush=True)
    print("verified_backup=" + str(directory), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", choices=[SERIAL])
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        print(json.dumps(verify_backup(args.verify)))
    elif args.serial:
        create_backup(args.serial)
    else:
        parser.error("Supply --serial for device backup or --verify for offline validation")
