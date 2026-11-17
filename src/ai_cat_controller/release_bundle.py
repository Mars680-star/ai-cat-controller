"""Bounded offline Python-source candidates, never installation or readiness.

The policy is an audited inventory, not discovery by extension or Git status.
Changing the runtime inventory requires an explicit policy review. No bundled
code is imported and no SDK, dependency, service, data or environment is read.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tarfile


MAX_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_PAYLOAD_BYTES = 96 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_MEMBERS = 256
SCOPE = "python-source-candidate"
STAGING_PREFIX = ".ai-cat-stage-"

_PYTHON_FILES = """
__init__.py
adapters/__init__.py
adapters/base.py
adapters/command_runner.py
adapters/factory.py
adapters/local_k1.py
adapters/mock.py
api/__init__.py
api/dependencies.py
api/device.py
api/dialog.py
api/error_handlers.py
api/health.py
api/local_gateway.py
api/mijia.py
api/motion.py
api/personality.py
api/product.py
api/router.py
api/services.py
autonomy.py
cli.py
core/__init__.py
core/config.py
core/errors.py
core/logging.py
core/operation_gate.py
core/process_gate.py
core/security.py
core/state.py
domain/__init__.py
domain/actions.py
domain/dialog_memory.py
domain/growth.py
domain/intimacy.py
domain/personalities.py
local_speech.py
local_gateway/__init__.py
local_gateway/app.py
local_gateway/bootstrap.py
local_gateway/cli.py
local_gateway/commissioning.py
local_gateway/configuration.py
local_gateway/delegation.py
local_gateway/discovery.py
local_gateway/network_cues.py
local_gateway/protocol.py
local_gateway/provisioning.py
local_gateway/provision_reviews.py
local_gateway/status_reader.py
local_gateway/store.py
main.py
mini_gateway/__init__.py
mini_gateway/app.py
mini_gateway/auth.py
mini_gateway/cli.py
mini_gateway/config.py
mini_gateway/dialog.py
mini_gateway/dialog_control.py
mini_gateway/dialog_control_models.py
mini_gateway/dialog_models.py
mini_gateway/lifecycle.py
mini_gateway/lifecycle_models.py
mini_gateway/motion.py
mini_gateway/preferences.py
mini_gateway/preferences_models.py
mini_gateway/product.py
mini_gateway/product_models.py
mini_gateway/registration.py
mini_gateway/status_reader.py
mini_gateway/store.py
mini_gateway/wechat.py
persistence/__init__.py
persistence/sqlite_repository.py
provisioning/__init__.py
provisioning/cli.py
provisioning/nm_backend.py
provisioning/protocol.py
provisioning/server.py
provisioning/window.py
release_bundle.py
release_runtime.py
schemas/__init__.py
schemas/common.py
schemas/device.py
schemas/dialog.py
schemas/mijia.py
schemas/motion.py
schemas/product.py
services/__init__.py
services/autonomy_service.py
services/behavior_profile_service.py
services/device_service.py
services/dialog_service.py
services/growth_engine.py
services/growth_service.py
services/interaction_classifier.py
services/mijia_gateway_service.py
services/motion_service.py
services/personality_service.py
services/product_mock_service.py
services/tag_engine.py
services/touch_event_monitor.py
web/__init__.py
web/router.py
""".split()

_PERSONALITIES = ("calm_guardian", "curious_scholar", "gentle_companion", "proud_star", "sunny_explorer")
_SHARED_WAV = ("event-level_up", "touch-back", "touch-head", "touch-left_foot", "touch-nose", "touch-right_foot")
ALLOWED_PATHS = tuple(sorted(
    ["pyproject.toml", "README.md", "LICENSE", "NOTICE"]
    + ["src/ai_cat_controller/" + name for name in _PYTHON_FILES]
    + ["src/ai_cat_controller/" + name for name in (
        "static/ai-cat-avatar.png", "static/control.css", "static/control.js", "templates/control.html")]
    + [f"assets/local-speech/{name}/phrase-{number:02d}.wav" for name in _PERSONALITIES for number in (1, 2, 3)]
    + [f"assets/local-speech/shared/{name}.wav" for name in _SHARED_WAV]
))
_ALLOWED = frozenset(ALLOWED_PATHS)
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LITERAL = re.compile(
    r"(?i)(?:product_secret|device_secret|app_secret|appsecret|api_key|access_token|session_token)"
    r"[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_.:/+\-]{16,})[\"']"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----")


class BundleError(ValueError):
    """Fixed public failure without source contents or OS/private details."""

    def __init__(self):
        super().__init__("离线源码候选包校验或创建失败；不会覆盖、安装或自动重试")


def _public(method):
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except BundleError:
            raise
        except (OSError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError, tarfile.TarError) as error:
            raise BundleError() from error
    return guarded


def _path(value) -> Path:
    raw = os.fspath(value)
    if (not isinstance(raw, str) or not raw.startswith("/") or raw == "/" or
            any(part in {"", ".", ".."} for part in raw.split("/")[1:]) or
            any(ord(char) < 32 or ord(char) == 127 for char in raw)):
        raise BundleError()
    return Path(raw)


def _directory(path: Path) -> int:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _under(root: int, parts) -> int:
    descriptor = os.dup(root)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _stable_read(parent: int, name: str, limit: int) -> bytes:
    named_before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(named_before.st_mode) or named_before.st_nlink != 1:
        raise BundleError()
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise BundleError()
        chunks, count = [], 0
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
            if count > limit:
                raise BundleError()
        after = os.fstat(descriptor)
        signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
        named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (signature(named_before) != signature(before) or signature(before) != signature(after) or
                signature(after) != signature(named_after) or count != before.st_size):
            raise BundleError()
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _inventory(root: int, relative: str):
    """Visit only approved runtime tree; never traverse private repo siblings."""
    descriptor = _under(root, relative.split("/"))
    try:
        for name in os.listdir(descriptor):
            path = relative + "/" + name
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if name == "__pycache__" and stat.S_ISDIR(info.st_mode):
                continue
            if relative.startswith("assets/local-speech") and path not in _ALLOWED and not any(
                    item.startswith(path + "/") for item in ALLOWED_PATHS):
                # Unselected capture/new recordings are not opened or traversed.
                continue
            if path in _ALLOWED:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise BundleError()
            elif any(item.startswith(path + "/") for item in ALLOWED_PATHS):
                if not stat.S_ISDIR(info.st_mode):
                    raise BundleError()
                _inventory(root, path)
            else:
                raise BundleError()
    finally:
        os.close(descriptor)


def _check_literal(path: str, content: bytes):
    if path.endswith((".wav", ".png")):
        return
    value = content.decode("utf-8")
    if _PRIVATE_KEY.search(value):
        raise BundleError()
    for match in _LITERAL.finditer(value):
        token = match.group(1).upper()
        if not token.startswith(("REPLACE_", "EXAMPLE", "SYNTHETIC", "TEST_")):
            raise BundleError()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _header(name: str, size: int) -> bytes:
    member = tarfile.TarInfo(name)
    member.size, member.mode = size, 0o644
    member.uid = member.gid = member.mtime = 0
    member.uname = member.gname = ""
    return member.tobuf(format=tarfile.USTAR_FORMAT, encoding="ascii", errors="strict")


def _record(name: str, content: bytes) -> bytes:
    return _header(name, len(content)) + content + b"\0" * (-len(content) % 512)


def _manifest(files) -> dict:
    return {"schema": 1, "scope": SCOPE, "native_built": False, "dependencies_vendored": False,
            "files": [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for path, data in files.items()]}


def _digest(value) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise BundleError()
    return value


def _summary(data: bytes, manifest_bytes: bytes, files) -> dict:
    return {"scope": SCOPE, "sha256": hashlib.sha256(data).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "file_count": len(files), "payload_bytes": sum(len(value) for value in files.values()),
            "native_built": False, "dependencies_vendored": False}


def _write(descriptor: int, content: bytes):
    offset = 0
    while offset < len(content):
        count = os.write(descriptor, content[offset:])
        if count <= 0:
            raise BundleError()
        offset += count
    os.fsync(descriptor)


def _identity(info):
    return info.st_dev, info.st_ino


def _check_directory(path: Path, expected, *, private=False):
    descriptor = _directory(path)
    try:
        info = os.fstat(descriptor)
        if (_identity(info) != expected or private and
                (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700)):
            raise BundleError()
    finally:
        os.close(descriptor)


def _check_named(parent: int, name: str, descriptor: int, *, directory=False, size=None, mode=None):
    held, named = os.fstat(descriptor), os.stat(name, dir_fd=parent, follow_symlinks=False)
    check_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not check_type(held.st_mode) or not check_type(named.st_mode) or
            _identity(held) != _identity(named) or not directory and held.st_nlink != 1 or
            size is not None and held.st_size != size or
            mode is not None and stat.S_IMODE(held.st_mode) != mode):
        raise BundleError()


def _rename_noreplace(parent: int, staging_name: str, destination_name: str):
    """Linux atomic new-name publication, with no unsafe rename fallback."""
    try:
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renameat2
    except (AttributeError, OSError) as error:
        raise BundleError() from error
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(staging_name), parent, os.fsencode(destination_name), 1) != 0:
        raise BundleError()


def _source_directories(root: int):
    relatives = {"/".join(path.split("/")[:count]) for path in ALLOWED_PATHS
                 for count in range(1, len(path.split("/")))}
    result = {}
    for relative in sorted(relatives):
        descriptor = _under(root, relative.split("/"))
        try:
            result[relative] = _identity(os.fstat(descriptor))
        finally:
            os.close(descriptor)
    return result


@_public
def build_bundle(source: Path, output: Path) -> dict:
    source, output = _path(source), _path(output)
    root = _directory(source)
    try:
        root_identity = _identity(os.fstat(root))
        _inventory(root, "src/ai_cat_controller")
        _inventory(root, "assets/local-speech")
        directory_pins = _source_directories(root)
        files, total = {}, 0
        for path in ALLOWED_PATHS:
            parts = path.split("/")
            parent = _under(root, parts[:-1])
            try:
                content = _stable_read(parent, parts[-1], MAX_FILE_BYTES)
            finally:
                os.close(parent)
            _check_literal(path, content)
            total += len(content)
            if total > MAX_PAYLOAD_BYTES:
                raise BundleError()
            files[path] = content
        if _source_directories(root) != directory_pins:
            raise BundleError()
        _check_directory(source, root_identity)
    finally:
        os.close(root)
    manifest_bytes = _canonical(_manifest(files))
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise BundleError()
    data = _record("manifest.json", manifest_bytes) + b"".join(
        _record("payload/" + path, content) for path, content in files.items()) + b"\0" * 1024
    data += b"\0" * (-len(data) % tarfile.RECORDSIZE)
    if len(data) > MAX_BUNDLE_BYTES or len(files) + 1 > MAX_MEMBERS:
        raise BundleError()
    parent = _directory(output.parent)
    try:
        parent_identity = _identity(os.fstat(parent))
        _check_directory(output.parent, parent_identity)
        descriptor = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=parent)
        try:
            os.fchmod(descriptor, 0o600)
            _write(descriptor, data)
            _check_named(parent, output.name, descriptor, size=len(data), mode=0o600)
            os.fsync(parent)
            _check_directory(output.parent, parent_identity)
            _check_named(parent, output.name, descriptor, size=len(data), mode=0o600)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)
    return _summary(data, manifest_bytes, files)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError()
        result[key] = value
    return result


def _field(value: bytes) -> str:
    raw, _, padding = value.partition(b"\0")
    if any(padding):
        raise BundleError()
    return raw.decode("ascii")


def _members(data: bytes) -> dict:
    if len(data) % 512 or len(data) < 1024:
        raise BundleError()
    members, offset = {}, 0
    while offset + 512 <= len(data):
        header = data[offset:offset + 512]
        if header == b"\0" * 512:
            if len(data) - offset < 1024 or any(data[offset:]):
                raise BundleError()
            return members
        name, prefix = _field(header[:100]), _field(header[345:500])
        name = prefix + "/" + name if prefix else name
        if name in members or not name or len(members) >= MAX_MEMBERS:
            raise BundleError()
        if (not members and name != "manifest.json" or members and
                (not name.startswith("payload/") or name[8:] not in _ALLOWED)):
            raise BundleError()
        size_raw = header[124:136].rstrip(b"\0 ")
        if not size_raw or any(value < 48 or value > 55 for value in size_raw):
            raise BundleError()
        size = int(size_raw, 8)
        limit = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_FILE_BYTES
        end = offset + 512 + size
        padded = end + (-size % 512)
        if size > limit or padded > len(data) or header != _header(name, size) or any(data[end:padded]):
            raise BundleError()
        members[name] = data[offset + 512:end]
        offset = padded
    raise BundleError()


def _verified(bundle: Path, expected_sha256: str):
    expected_sha256 = _digest(expected_sha256)
    bundle = _path(bundle)
    parent = _directory(bundle.parent)
    try:
        # Hash and parse the same stable bounded memory snapshot, never reopen.
        data = _stable_read(parent, bundle.name, MAX_BUNDLE_BYTES)
    finally:
        os.close(parent)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise BundleError()
    members = _members(data)
    if set(members) != {"manifest.json", *("payload/" + item for item in ALLOWED_PATHS)}:
        raise BundleError()
    manifest_bytes = members["manifest.json"]
    manifest = json.loads(manifest_bytes.decode("ascii"), object_pairs_hook=_json_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(BundleError()))
    if (not isinstance(manifest, dict) or set(manifest) != {
            "schema", "scope", "native_built", "dependencies_vendored", "files"} or
            type(manifest["schema"]) is not int or manifest["schema"] != 1 or
            manifest["scope"] != SCOPE or manifest["native_built"] is not False or
            manifest["dependencies_vendored"] is not False or
            not isinstance(manifest["files"], list) or len(manifest["files"]) != len(ALLOWED_PATHS)):
        raise BundleError()
    files, total = {}, 0
    for expected, entry in zip(ALLOWED_PATHS, manifest["files"]):
        if (not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"} or
                entry["path"] != expected or type(entry["size"]) is not int or
                not 0 <= entry["size"] <= MAX_FILE_BYTES):
            raise BundleError()
        content = members["payload/" + expected]
        if len(content) != entry["size"] or hashlib.sha256(content).hexdigest() != _digest(entry["sha256"]):
            raise BundleError()
        _check_literal(expected, content)
        total += len(content)
        if total > MAX_PAYLOAD_BYTES:
            raise BundleError()
        files[expected] = content
    if manifest_bytes != _canonical(manifest):
        raise BundleError()
    return _summary(data, manifest_bytes, files), manifest_bytes, files


@_public
def inspect_bundle(bundle: Path, expected_sha256: str) -> dict:
    """The expected digest must arrive through an independently trusted channel."""
    return _verified(bundle, expected_sha256)[0]


@_public
def stage_bundle(bundle: Path, expected_sha256: str, destination: Path) -> dict:
    """Atomically publish a new inactive candidate, never replace an old tree.

    Unpublished hidden trees are never complete destinations, even if sealed.
    A failure leaves its new hidden tree for inspection and clears its seal.
    Publication requires Linux renameat2(RENAME_NOREPLACE); no active link,
    configuration or existing destination is ever changed.
    """
    summary, manifest, files = _verified(bundle, expected_sha256)
    destination = _path(destination)
    if destination.name.startswith(STAGING_PREFIX):
        raise BundleError()
    parent = _directory(destination.parent)
    root, marker = -1, -1
    try:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise BundleError()
        parent_identity = _identity(info)
        _check_directory(destination.parent, parent_identity, private=True)
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BundleError()
        staging_name = STAGING_PREFIX + secrets.token_hex(16)
        os.mkdir(staging_name, mode=0o700, dir_fd=parent)
        initial_identity = _identity(os.stat(staging_name, dir_fd=parent, follow_symlinks=False))
        root = os.open(staging_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        root_identity = _identity(os.fstat(root))
        if root_identity != initial_identity:
            raise BundleError()
        _check_named(parent, staging_name, root, directory=True, mode=0o700)
        marker = os.open("STAGED.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=root)
        os.fchmod(marker, 0o444)
        os.fsync(marker)
        os.fsync(root)
        directories = {""}
        for path, content in {"manifest.json": manifest, **{"payload/" + key: value for key, value in files.items()}}.items():
            parts = path.split("/")
            for count in range(1, len(parts)):
                relative = "/".join(parts[:count])
                if relative not in directories:
                    holder = _under(root, parts[:count - 1])
                    try:
                        os.mkdir(parts[count - 1], 0o700, dir_fd=holder)
                    finally:
                        os.close(holder)
                    directories.add(relative)
            holder = _under(root, parts[:-1])
            try:
                descriptor = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     0o600, dir_fd=holder)
                try:
                    _write(descriptor, content)
                    os.fchmod(descriptor, 0o444)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                os.close(holder)
        for relative in sorted(directories, key=lambda item: item.count("/") + bool(item), reverse=True):
            descriptor = _under(root, relative.split("/") if relative else [])
            try:
                os.fchmod(descriptor, 0o555)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.fsync(parent)
        _check_directory(destination.parent, parent_identity, private=True)
        _check_named(parent, staging_name, root, directory=True, mode=0o555)
        # All content/modes and directory entries are durable before completion
        # is written through the still-owned FD. Nothing from the bundle runs.
        _write(marker, _canonical({**summary, "status": "staged"}))
        _check_directory(destination.parent, parent_identity, private=True)
        _check_named(parent, staging_name, root, directory=True, mode=0o555)
        _rename_noreplace(parent, staging_name, destination.name)
        os.fsync(parent)
        _check_directory(destination.parent, parent_identity, private=True)
        _check_named(parent, destination.name, root, directory=True, mode=0o555)
    except BaseException:
        if marker >= 0:
            try:
                os.ftruncate(marker, 0)
                os.fsync(marker)
            except OSError:
                pass  # Storage failure is never a successful stage response.
        raise
    finally:
        if marker >= 0:
            os.close(marker)
        if root >= 0:
            os.close(root)
        os.close(parent)
    return {**summary, "destination": str(destination)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline Python source candidate; no install, activation or readiness")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    for name in ("inspect", "stage"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", type=Path, required=True)
        command.add_argument("--sha256", required=True)
        if name == "stage":
            command.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "build":
            result = build_bundle(arguments.source, arguments.output)
        elif arguments.command == "inspect":
            result = inspect_bundle(arguments.bundle, arguments.sha256)
        else:
            result = stage_bundle(arguments.bundle, arguments.sha256, arguments.destination)
    except BundleError:
        parser.exit(1, str(BundleError()) + "\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
