"""Verified imports execute synthetic bytes in fresh isolated child processes."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest

from ai_cat_controller import release_bundle, release_runtime


SCRIPT = Path(release_runtime.__file__).absolute()


@pytest.fixture
def candidate(tmp_path):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    for relative in release_bundle.ALLOWED_PATHS:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"" if relative.endswith(".py") else b"synthetic resource")
    sources = {
        "src/ai_cat_controller/__init__.py": "ROOT_VALUE = 'verified-root'\n",
        "src/ai_cat_controller/domain/actions.py": "VALUE = 'snapshot-original'\n",
    }
    for role, module in release_runtime.ROLES.items():
        relative = "src/" + module.replace(".", "/") + ".py"
        sources[relative] = "from ai_cat_controller.domain.actions import VALUE\nROLE_VALUE = VALUE\n"
    for relative, content in sources.items():
        (source / relative).write_text(content, encoding="utf-8")
    archive = tmp_path / "candidate.tar"
    summary = release_bundle.build_bundle(source, archive)
    staged = tmp_path / "stage"
    release_bundle.stage_bundle(archive, summary["sha256"], staged)
    return {"source": source, "bundle": archive, "sha": summary["sha256"], "resources": staged / "payload",
            "summary": summary, "sources": sources}


def child(candidate, tmp_path, body, *, dependency_roots=(), environment=None):
    code = """
import hashlib, importlib, json, os, runpy, sys
from pathlib import Path
namespace = runpy.run_path(sys.argv[1], run_name='_trusted_bootstrap_unit')
VerifiedRuntime, RuntimeLoadError = namespace['VerifiedRuntime'], namespace['RuntimeLoadError']
runtime = VerifiedRuntime(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), [Path(p) for p in json.loads(sys.argv[5])])
""" + body
    return subprocess.run([sys.executable, "-I", "-S", "-c", code, str(SCRIPT), str(candidate["bundle"]),
                           candidate["sha"], str(candidate["resources"]), json.dumps(list(map(str, dependency_roots)))],
        cwd=tmp_path, env=environment or {}, capture_output=True, text=True, timeout=10)


def successful(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("role", ["controller", "gateway", "autonomy"])
def test_actual_import_executes_only_verified_project_graph_and_records_every_module(candidate, tmp_path, role):
    result = child(candidate, tmp_path, f"""
runtime.install()
module = runtime.load_role({role!r})
assert module.ROLE_VALUE == 'snapshot-original'
assert module.__cached__ is None
identity = runtime.identity()
assert module.__file__ == str(runtime.resource_root / ('src/' + identity['module'].replace('.', '/') + '.py'))
print(json.dumps(identity))
""")
    identity = successful(result)
    entry = release_runtime.ROLES[role]
    names = {"ai_cat_controller", "ai_cat_controller.domain", "ai_cat_controller.domain.actions", entry}
    if role == "gateway":
        names.add("ai_cat_controller.mini_gateway")
    assert set(identity["executed_modules"]) == names
    for name, sha in identity["executed_modules"].items():
        relative = "src/" + name.replace(".", "/")
        path = candidate["source"] / (relative + ".py")
        if not path.exists():
            path = candidate["source"] / relative / "__init__.py"
        assert sha == hashlib.sha256(path.read_bytes()).hexdigest()
    assert identity["module_sha256"] == identity["executed_modules"][entry]
    assert identity["bundle_sha256"] == candidate["sha"]
    assert identity["scope"] == "verified-python-import"
    assert identity["production_ready"] is identity["dependencies_verified"] is identity["resources_immutable"] is False


def test_mutable_filesystem_python_and_real_unchecked_pyc_are_never_loaded(candidate, tmp_path):
    path = candidate["resources"] / "src/ai_cat_controller/domain/actions.py"
    path.chmod(0o644)
    path.write_text("raise RuntimeError('MUTABLE SOURCE MUST NOT EXECUTE')\n", encoding="utf-8")
    path.parent.chmod(0o755)
    py_compile.compile(str(path), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    path.parent.chmod(0o555)
    identity = successful(child(candidate, tmp_path, """
runtime.install()
module = runtime.load_role('controller')
assert module.ROLE_VALUE == 'snapshot-original'
print(json.dumps(runtime.identity()))
"""))
    assert identity["executed_modules"]["ai_cat_controller.domain.actions"] == hashlib.sha256(
        candidate["sources"]["src/ai_cat_controller/domain/actions.py"].encode()).hexdigest()


def test_unknown_project_module_does_not_fall_through_to_real_filesystem(candidate, tmp_path):
    root = candidate["resources"] / "src/ai_cat_controller"
    root.chmod(0o755)
    (root / "rogue.py").write_text("raise AssertionError('FILESYSTEM FALLBACK')\n", encoding="utf-8")
    root.chmod(0o555)
    successful(child(candidate, tmp_path, """
runtime.install()
runtime.load_role('controller')
try:
    importlib.import_module('ai_cat_controller.rogue')
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('unknown project import was allowed')
print(json.dumps(runtime.identity()))
"""))


def test_traceback_source_uses_memory_loader_not_mutable_file(candidate, tmp_path):
    path = candidate["resources"] / "src/ai_cat_controller/main.py"
    path.chmod(0o644)
    path.write_text("MUTATED_DISK_SOURCE\n", encoding="utf-8")
    successful(child(candidate, tmp_path, """
import linecache
runtime.install()
module = runtime.load_role('controller')
source = ''.join(linecache.getlines(module.__file__))
assert 'ROLE_VALUE = VALUE' in source
assert 'MUTATED_DISK_SOURCE' not in source
print(json.dumps(runtime.identity()))
"""))


def test_utf8_bom_source_debug_text_matches_python_compilation(candidate, tmp_path):
    source = candidate['source'] / 'src/ai_cat_controller/main.py'
    source.write_bytes(b'\xef\xbb\xbfROLE_VALUE = "bom-original"\r\n')
    bundle = tmp_path / 'bom.tar'
    summary = release_bundle.build_bundle(candidate['source'], bundle)
    staged = tmp_path / 'bom-stage'
    release_bundle.stage_bundle(bundle, summary['sha256'], staged)
    changed = {**candidate, 'bundle': bundle, 'sha': summary['sha256'], 'resources': staged / 'payload'}
    successful(child(changed, tmp_path, """
import linecache
runtime.install()
module = runtime.load_role('controller')
assert module.ROLE_VALUE == 'bom-original'
assert module.__loader__.get_source(module.__name__) == 'ROLE_VALUE = "bom-original"\\n'
assert ''.join(linecache.getlines(module.__file__)) == 'ROLE_VALUE = "bom-original"\\n'
print(json.dumps(runtime.identity()))
"""))


def test_close_keeps_namespace_fence_and_never_restores_mutable_imports(candidate, tmp_path):
    successful(child(candidate, tmp_path, """
runtime.install()
runtime.load_role('controller')
runtime.close()
try:
    importlib.import_module('ai_cat_controller.services.motion_service')
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('close enabled fallback')
try:
    runtime.identity()
except RuntimeLoadError:
    pass
else:
    raise AssertionError('closed runtime still claimed success')
print(json.dumps({'closed':True}))
"""))


def test_installer_refuses_preloaded_project_namespace_even_none(candidate, tmp_path):
    successful(child(candidate, tmp_path, """
sys.modules['ai_cat_controller.untrusted'] = None
try:
    runtime.install()
except RuntimeLoadError:
    pass
else:
    raise AssertionError('preloaded namespace was accepted')
print(json.dumps({'refused':True}))
"""))


def test_unknown_none_module_poisoning_permanently_invalidates_identity(candidate, tmp_path):
    successful(child(candidate, tmp_path, """
runtime.install()
runtime.load_role('controller')
sys.modules['ai_cat_controller.unknown'] = None
for attempt in range(2):
    try:
        runtime.identity()
    except RuntimeLoadError:
        pass
    else:
        raise AssertionError('poisoned identity was accepted')
    sys.modules.pop('ai_cat_controller.unknown', None)
print(json.dumps({'refused':True}))
"""))


@pytest.mark.parametrize("change", ["delete", "none", "replace"])
def test_removed_or_replaced_current_entry_permanently_invalidates_identity(candidate, tmp_path, change):
    successful(child(candidate, tmp_path, f"""
from types import ModuleType
runtime.install()
module = runtime.load_role('controller')
if {change!r} == 'delete':
    sys.modules.pop('ai_cat_controller.main')
elif {change!r} == 'none':
    sys.modules['ai_cat_controller.main'] = None
else:
    sys.modules['ai_cat_controller.main'] = ModuleType('ai_cat_controller.main')
try:
    runtime.identity()
except RuntimeLoadError:
    pass
else:
    raise AssertionError('missing/replaced live entry accepted')
sys.modules['ai_cat_controller.main'] = module
try:
    runtime.identity()
except RuntimeLoadError:
    pass
else:
    raise AssertionError('poisoned identity silently recovered')
print(json.dumps({{'refused':True}}))
"""))


def test_empty_or_no_entry_execution_cannot_claim_verified_import(candidate, tmp_path):
    successful(child(candidate, tmp_path, """
runtime.install()
try:
    runtime.identity()
except RuntimeLoadError:
    pass
else:
    raise AssertionError('no entry executed')
print(json.dumps({'refused':True}))
"""))


def test_failed_role_execution_never_emits_success_and_cannot_retry(candidate, tmp_path):
    # New trusted candidate intentionally fails in its fixed entry module.
    (candidate['source'] / 'src/ai_cat_controller/main.py').write_text("raise RuntimeError('PRIVATE FAILURE BODY')\n")
    bundle = tmp_path / "failed.tar"
    summary = release_bundle.build_bundle(candidate['source'], bundle)
    staged = tmp_path / "failed-stage"
    release_bundle.stage_bundle(bundle, summary['sha256'], staged)
    changed = {**candidate, "bundle": bundle, "sha": summary['sha256'], "resources": staged / 'payload'}
    successful(child(changed, tmp_path, """
runtime.install()
for operation in (lambda: runtime.load_role('controller'), runtime.identity, lambda: runtime.load_role('gateway')):
    try:
        operation()
    except RuntimeLoadError:
        pass
    else:
        raise AssertionError('failed runtime recovered implicitly')
print(json.dumps({'failed':True}))
"""))


def test_environment_is_sanitized_before_import_and_explicit_deps_never_run_pth(candidate, tmp_path):
    dependencies = tmp_path / "trusted-dependencies"
    dependencies.mkdir()
    marker = tmp_path / "pth-must-not-run"
    (dependencies / "malicious.pth").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).write_text('wrong')\n")
    successful(child(candidate, tmp_path, """
runtime.install()
assert not any(key.startswith('AI_CAT_') for key in os.environ)
runtime.load_role('controller')
print(json.dumps(runtime.identity()))
""", dependency_roots=[dependencies], environment={"AI_CAT_HARDWARE_DRIVER": "local_k1", "AI_CAT_API_KEY": "PRIVATE AMBIENT"}))
    assert not marker.exists()


@pytest.mark.parametrize("resource", ["src/ai_cat_controller/static/control.js", "src/ai_cat_controller/templates/control.html", "assets/local-speech/shared/touch-back.wav"])
def test_resource_mismatch_is_rejected_before_any_project_execution(candidate, resource):
    selected = candidate["resources"] / resource
    selected.chmod(0o644)
    selected.write_bytes(b"changed resource")
    with pytest.raises(release_runtime.RuntimeLoadError):
        release_runtime.VerifiedRuntime(candidate["bundle"], candidate["sha"], candidate["resources"])


@pytest.mark.parametrize("kind", ["missing", "empty", "wrong-sha"])
def test_resource_stage_seal_is_required(candidate, kind):
    seal = candidate["resources"].parent / "STAGED.json"
    if kind == "missing":
        seal.parent.chmod(0o755)
        seal.unlink()
    else:
        seal.chmod(0o644)
        if kind == "empty":
            seal.write_bytes(b"")
        else:
            value = json.loads(seal.read_text())
            value["sha256"] = "0" * 64
            seal.write_text(json.dumps(value))
    with pytest.raises(release_runtime.RuntimeLoadError):
        release_runtime.VerifiedRuntime(candidate["bundle"], candidate["sha"], candidate["resources"])


def test_resource_root_rename_during_verification_is_not_accepted(candidate, tmp_path, monkeypatch):
    original = release_runtime._read
    changed = []
    resource = candidate["resources"]
    resource.parent.chmod(0o755)

    def raced_read(parent, name, limit):
        content = original(parent, name, limit)
        if not changed and name == "manifest.json":
            resource.rename(resource.parent / "retired-payload")
            resource.mkdir(mode=0o700)
            changed.append(True)
        return content

    monkeypatch.setattr(release_runtime, "_read", raced_read)
    with pytest.raises(release_runtime.RuntimeLoadError):
        release_runtime.VerifiedRuntime(candidate["bundle"], candidate["sha"], resource)
    assert changed


def test_parent_open_failure_closes_resource_root_fd(candidate, monkeypatch):
    original = release_runtime._directory
    descriptors = []

    def failed_directory(path):
        if path == candidate["resources"]:
            descriptor = original(path)
            descriptors.append(descriptor)
            return descriptor
        if path == candidate["resources"].parent:
            raise OSError("synthetic parent replacement")
        return original(path)

    monkeypatch.setattr(release_runtime, "_directory", failed_directory)
    with pytest.raises(release_runtime.RuntimeLoadError):
        release_runtime.VerifiedRuntime(candidate["bundle"], candidate["sha"], candidate["resources"])
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("bad", ["", "0" * 64, "A" * 64, "1" * 64 + "\n", True])
def test_untrusted_digest_is_rejected_by_verified_snapshot(candidate, bad):
    with pytest.raises(release_runtime.RuntimeLoadError):
        release_runtime.VerifiedRuntime(candidate["bundle"], bad, candidate["resources"])


def test_unexpected_role_never_dispatches_arbitrary_module(candidate, tmp_path):
    successful(child(candidate, tmp_path, """
runtime.install()
try:
    runtime.load_role('os')
except RuntimeLoadError:
    pass
else:
    raise AssertionError('arbitrary role was accepted')
print(json.dumps({'refused':True}))
"""))


def test_api_refuses_installation_into_already_running_application_namespace(candidate):
    runtime = release_runtime.VerifiedRuntime(candidate["bundle"], candidate["sha"], candidate["resources"])
    with pytest.raises(release_runtime.RuntimeLoadError):
        runtime.install()


@pytest.mark.parametrize("arguments", [["--private-flag", "DO_NOT_ECHO_PRIVATE"],
                                       ["check", "--role", "DO_NOT_ECHO_PRIVATE"], ["check"]])
def test_parser_failures_do_not_echo_untrusted_arguments_or_tracebacks(arguments, tmp_path):
    result = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), *arguments], cwd=tmp_path,
                            env={}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "DO_NOT_ECHO_PRIVATE" not in result.stderr and "Traceback" not in result.stderr
    assert result.stderr.strip() == str(release_runtime.RuntimeLoadError())
