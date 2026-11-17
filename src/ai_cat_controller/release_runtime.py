"""Standalone verified-project import foundation, not a service launcher.

Invoke the independently trusted absolute script with ``python -I -S``. This
bootstrap compiles the adjacent trusted bundle tool directly from stable source,
never imports the installed project namespace, and never consumes its pyc.
Dependencies and later filesystem resource reads remain explicit trust gaps.
"""

from __future__ import annotations

import sys

_ERROR_MESSAGE = "受控Python导入被拒绝；未启动服务，不会回退本地源码或自动重试"
if __name__ == "__main__" and (not sys.flags.isolated or not sys.flags.no_site):
    sys.stderr.write(_ERROR_MESSAGE + "\n")
    raise SystemExit(1)

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import linecache
import os
from pathlib import Path
import stat
from types import ModuleType


ROLES = {
    "controller": "ai_cat_controller.main",
    "gateway": "ai_cat_controller.mini_gateway.app",
    "autonomy": "ai_cat_controller.autonomy",
}
_PREFIX = "ai_cat_controller"
_MAX_TOOL_BYTES = 2 * 1024 * 1024


class RuntimeLoadError(ValueError):
    def __init__(self):
        super().__init__(_ERROR_MESSAGE)


class _FixedParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, str(RuntimeLoadError()) + "\n")


def _project(name):
    return isinstance(name, str) and (name == _PREFIX or name.startswith(_PREFIX + "."))


def _path(value):
    raw = os.fspath(value)
    if (not isinstance(raw, str) or not raw.startswith("/") or raw == "/" or
            any(part in {"", ".", ".."} for part in raw.split("/")[1:]) or
            any(ord(character) < 32 or ord(character) == 127 for character in raw)):
        raise RuntimeLoadError()
    return Path(raw)


def _directory(path):
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


def _read(parent, name, limit):
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1 or named.st_size > limit:
        raise RuntimeLoadError()
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        before = os.fstat(descriptor)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns, item.st_nlink)
        if signature(named) != signature(before):
            raise RuntimeLoadError()
        data = bytearray()
        while True:
            part = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not part:
                break
            data.extend(part)
            if len(data) > limit:
                raise RuntimeLoadError()
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if signature(before) != signature(after) or signature(after) != signature(named_after) or len(data) != before.st_size:
            raise RuntimeLoadError()
        return bytes(data)
    finally:
        os.close(descriptor)


def _trusted_tool():
    # This script and its sibling tool are installed trust anchors, not selected
    # from --bundle, resource_root, CWD, PYTHONPATH or sys.path.
    filename = _path(Path(__file__).absolute()).with_name("release_bundle.py")
    parent = _directory(filename.parent)
    try:
        source = _read(parent, filename.name, _MAX_TOOL_BYTES)
    finally:
        os.close(parent)
    tool = ModuleType("_ai_cat_trusted_bundle_tool")
    tool.__file__, tool.__package__ = str(filename), ""
    exec(compile(source, str(filename), "exec", dont_inherit=True, optimize=0), tool.__dict__)
    return tool


class _MemoryLoader(importlib.abc.Loader):
    def __init__(self, runtime, fullname, relative, package):
        self.runtime, self.fullname, self.relative, self.package = runtime, fullname, relative, package

    def create_module(self, spec):
        return None

    def get_source(self, fullname):
        if fullname != self.fullname:
            raise ImportError("project module is outside the verified snapshot")
        return importlib.util.decode_source(self.runtime._files[self.relative])

    def exec_module(self, module):
        runtime = self.runtime
        if runtime._closed or runtime._failed:
            raise RuntimeLoadError()
        filename = str(runtime.resource_root / self.relative)
        module.__file__, module.__cached__ = filename, None
        module.__package__ = self.fullname if self.package else self.fullname.rpartition(".")[0]
        if self.package:
            module.__path__ = [str(Path(filename).parent)]
        source = runtime._files[self.relative]
        try:
            # The only source used for execution is the verified memory bytes.
            # Not SourceFileLoader: never read/write local pyc or mutable py.
            code = compile(source, filename, "exec", dont_inherit=True, optimize=0)
            # lazycache can still prefer a real file at this filename. Pin
            # traceback/inspect text too; mtime=None prevents disk invalidation.
            linecache.cache[filename] = (len(source), None, importlib.util.decode_source(source).splitlines(keepends=True), filename)
            exec(code, module.__dict__)
        except BaseException:
            runtime._failed = True
            raise
        runtime._executed[self.fullname] = hashlib.sha256(source).hexdigest()
        runtime._objects[self.fullname] = module


class _MemoryFinder(importlib.abc.MetaPathFinder):
    def __init__(self, runtime):
        self.runtime = runtime

    def find_spec(self, fullname, path=None, target=None):
        if not _project(fullname):
            return None
        runtime = self.runtime
        if runtime._closed or runtime._failed or fullname not in runtime._modules:
            # Raising, rather than returning None, prevents a PathFinder/pyc/
            # extension/namespace fallback anywhere in the project namespace.
            raise ModuleNotFoundError("project module is outside the verified snapshot")
        relative, package = runtime._modules[fullname]
        loader = _MemoryLoader(runtime, fullname, relative, package)
        spec = importlib.util.spec_from_loader(fullname, loader,
            origin=str(runtime.resource_root / relative), is_package=package)
        spec.has_location = True
        return spec


class VerifiedRuntime:
    """One fresh-process, one-role project source snapshot.

    The API is useful to tests, but only the standalone -I -S entry enforces the
    interpreter startup flags. This is not a sandbox against approved project
    code, trusted dependency code, LD_PRELOAD, or same-UID/root administration.
    Installation/close intentionally do not restore environment, sys.path, or
    the namespace fence: use only a fresh process, never a running product app.
    """

    def __init__(self, bundle: Path, sha256: str, resource_root: Path, dependency_roots=()):
        self._installed = self._closed = self._failed = False
        self._role = None
        self._executed, self._objects = {}, {}
        try:
            self._tool = _trusted_tool()
            self._summary, manifest, files = self._tool._verified(_path(bundle), sha256)
            self._files = dict(files)
            self.resource_root = _path(resource_root)
            self._verify_resources(manifest)
            self._dependency_roots = tuple(_path(path) for path in dependency_roots)
            if len(self._dependency_roots) > 8 or len(set(self._dependency_roots)) != len(self._dependency_roots):
                raise RuntimeLoadError()
            for path in self._dependency_roots:
                descriptor = _directory(path)
                try:
                    info = os.fstat(descriptor)
                    if info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022:
                        raise RuntimeLoadError()
                finally:
                    os.close(descriptor)
            self._modules = {}
            for relative in files:
                if relative.startswith("src/ai_cat_controller/") and relative.endswith(".py"):
                    suffix = relative[len("src/"):]
                    package = suffix.endswith("/__init__.py")
                    name = suffix[:-len("/__init__.py")] if package else suffix[:-3]
                    fullname = name.replace("/", ".")
                    if fullname in self._modules:
                        raise RuntimeLoadError()
                    self._modules[fullname] = relative, package
            self._finder = _MemoryFinder(self)
        except Exception as error:
            raise RuntimeLoadError() from error

    def _verify_resources(self, manifest):
        root, parent = -1, -1
        try:
            root = _directory(self.resource_root)
            parent = _directory(self.resource_root.parent)
            root_info, parent_info = os.fstat(root), os.fstat(parent)
            root_pin = root_info.st_dev, root_info.st_ino
            parent_pin = parent_info.st_dev, parent_info.st_ino
            self._tool._check_directory(self.resource_root, root_pin)
            self._tool._check_directory(self.resource_root.parent, parent_pin)
            recorded_manifest = _read(parent, "manifest.json", self._tool.MAX_MANIFEST_BYTES)
            seal = json.loads(_read(parent, "STAGED.json", 4096).decode("ascii"), object_pairs_hook=self._tool._json_pairs)
            if recorded_manifest != manifest or seal != {**self._summary, "status": "staged"}:
                raise RuntimeLoadError()
            for relative, content in self._files.items():
                if relative.endswith(".py") or relative in {"pyproject.toml", "README.md", "LICENSE", "NOTICE"}:
                    continue
                parts = relative.split("/")
                directory = self._tool._under(root, parts[:-1])
                try:
                    actual = self._tool._stable_read(directory, parts[-1], self._tool.MAX_FILE_BYTES)
                    if actual != content:
                        raise RuntimeLoadError()
                finally:
                    os.close(directory)
            self._tool._check_directory(self.resource_root, root_pin)
            self._tool._check_directory(self.resource_root.parent, parent_pin)
        finally:
            if root >= 0:
                os.close(root)
            if parent >= 0:
                os.close(parent)

    def install(self):
        if self._installed or self._closed or self._failed or any(_project(name) for name in sys.modules):
            raise RuntimeLoadError()
        # This foundation checks imports only. Never let an ambient board/cloud
        # configuration turn the controller's import-time app into local_k1.
        for name in tuple(os.environ):
            if name.startswith("AI_CAT_"):
                os.environ.pop(name, None)
        for path in self._dependency_roots:
            sys.path.append(str(path))  # No site.addsitedir, site import or .pth.
        sys.meta_path.insert(0, self._finder)
        sys.dont_write_bytecode = True
        self._installed = True
        return self

    def load_role(self, role: str):
        if (not self._installed or self._closed or self._failed or role not in ROLES or self._role is not None):
            raise RuntimeLoadError()
        self._role = role
        try:
            module = __import__(ROLES[role], fromlist=["*"])
            self._validate_executed()
            return module
        except BaseException as error:
            self._failed = True
            raise RuntimeLoadError() from error

    def _validate_executed(self):
        if self._role is None or ROLES[self._role] not in self._executed:
            raise RuntimeLoadError()
        if any(sys.modules.get(name) is not module for name, module in self._objects.items()):
            raise RuntimeLoadError()
        for name, module in tuple(sys.modules.items()):
            if _project(name) and (name not in self._objects or self._objects[name] is not module):
                raise RuntimeLoadError()

    def identity(self) -> dict:
        if self._closed or self._failed or not self._installed:
            raise RuntimeLoadError()
        try:
            self._validate_executed()
        except BaseException as error:
            self._failed = True
            raise RuntimeLoadError() from error
        module = ROLES[self._role]
        return {"scope": "verified-python-import", "role": self._role,
                "bundle_sha256": self._summary["sha256"], "manifest_sha256": self._summary["manifest_sha256"],
                "module": module, "module_sha256": self._executed[module],
                "executed_modules": dict(sorted(self._executed.items())),
                "dependencies_verified": False, "resources_immutable": False, "production_ready": False}

    def close(self):
        # Keep the namespace fence installed: removing it while callers retain
        # modules would silently enable mutable filesystem imports after close.
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def main(argv=None) -> int:
    parser = _FixedParser(description="Verified Python imports only; never start a service", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", allow_abbrev=False)
    check.add_argument("--bundle", type=Path, required=True)
    check.add_argument("--sha256", required=True)
    check.add_argument("--role", choices=tuple(ROLES), required=True)
    check.add_argument("--resource-root", type=Path, required=True)
    check.add_argument("--dependency-root", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    if not sys.flags.isolated or not sys.flags.no_site:
        parser.exit(1, str(RuntimeLoadError()) + "\n")
    try:
        with VerifiedRuntime(args.bundle, args.sha256, args.resource_root, args.dependency_root) as runtime:
            runtime.install()
            runtime.load_role(args.role)
            result = runtime.identity()
    except BaseException:
        parser.exit(1, str(RuntimeLoadError()) + "\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
