"""Fresh isolated interpreter proof, never production startup or hardware use."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import sysconfig

import pytest

from ai_cat_controller import release_bundle as release


RUNTIME = Path(__file__).parents[2] / "src/ai_cat_controller/release_runtime.py"
ROLES = {
    "controller": "ai_cat_controller.main",
    "gateway": "ai_cat_controller.mini_gateway.app",
    "autonomy": "ai_cat_controller.autonomy",
}
IDENTITY_KEYS = {
    "scope", "role", "bundle_sha256", "manifest_sha256", "module", "module_sha256",
    "executed_modules", "dependencies_verified", "resources_immutable", "production_ready",
}


def module_path(name):
    return "src/" + name.replace(".", "/") + ".py"


def write_source(source, relative, data):
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def code(value):
    return value.encode("utf-8")


@pytest.fixture
def candidate(tmp_path):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    for relative in release.ALLOWED_PATHS:
        write_source(source, relative,
            b"RIFFsynthetic" if relative.endswith(".wav") else
            b"\x89PNGsynthetic" if relative.endswith(".png") else b"# synthetic public candidate\n")
    write_source(source, "src/ai_cat_controller/domain/actions.py", b"IDENTITY = 'verified-memory-source'\n")
    marker = tmp_path / "executed-entry.json"
    for role, module in ROLES.items():
        write_source(source, module_path(module), code(
            "from pathlib import Path\n"
            "import json, os\n"
            "from ai_cat_controller.domain.actions import IDENTITY\n"
            "assert IDENTITY == 'verified-memory-source'\n"
            "assert not any(key.startswith('AI_CAT_') for key in os.environ)\n"
            f"Path({str(marker)!r}).write_text(json.dumps({{'module': {module!r}, 'identity': IDENTITY}}))\n"
        ))
    return {"source": source, "marker": marker, "root": tmp_path}


def prepare(candidate):
    root = candidate["root"]
    bundle = root / "candidate.tar"
    summary = release.build_bundle(candidate["source"], bundle)
    staged = root / "staged"
    release.stage_bundle(bundle, summary["sha256"], staged)
    return bundle, summary, staged / "payload"


def arguments(bundle, summary, resource, role="controller", dependencies=()):
    result = ["check", "--bundle", str(bundle), "--sha256", summary["sha256"],
              "--role", role, "--resource-root", str(resource)]
    for dependency in dependencies:
        result.extend(("--dependency-root", str(dependency)))
    return result


def invoke(candidate, args, *, environ=None, prefix=None):
    # No real credentials, device configuration or linker injection is inherited.
    environment = {"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"}
    environment.update(environ or {})
    return subprocess.run(
        [sys.executable, "-I", "-S", *(prefix or [str(RUNTIME)]), *args],
        cwd=candidate["root"], env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10,
    )


def identity(result, candidate, summary, role):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    response = json.loads(result.stdout)
    assert set(response) == IDENTITY_KEYS
    assert response["scope"] == "verified-python-import"
    assert response["role"] == role
    assert response["bundle_sha256"] == summary["sha256"]
    assert response["manifest_sha256"] == summary["manifest_sha256"]
    assert response["module"] == ROLES[role]
    assert response["dependencies_verified"] is False
    assert response["resources_immutable"] is False
    assert response["production_ready"] is False
    modules = {"ai_cat_controller": "src/ai_cat_controller/__init__.py",
               "ai_cat_controller.domain": "src/ai_cat_controller/domain/__init__.py",
               "ai_cat_controller.domain.actions": "src/ai_cat_controller/domain/actions.py",
               ROLES[role]: module_path(ROLES[role])}
    if role == "gateway":
        modules["ai_cat_controller.mini_gateway"] = "src/ai_cat_controller/mini_gateway/__init__.py"
    expected = {name: hashlib.sha256((candidate["source"] / path).read_bytes()).hexdigest()
                for name, path in modules.items()}
    assert response["executed_modules"] == expected
    assert response["module_sha256"] == expected[ROLES[role]]
    assert json.loads(candidate["marker"].read_text()) == {
        "module": ROLES[role], "identity": "verified-memory-source"}
    return response


def rejected(result, candidate):
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip()
    assert "Traceback" not in result.stderr
    assert not candidate["marker"].exists()


@pytest.mark.parametrize("role", list(ROLES))
def test_actual_isolated_child_executes_exact_selected_module_bytes(candidate, role):
    bundle, summary, resource = prepare(candidate)
    identity(invoke(candidate, arguments(bundle, summary, resource, role)), candidate, summary, role)


@pytest.mark.parametrize("error", ["wrong-sha", "unknown-role"])
def test_sha_and_role_fail_before_any_candidate_code_executes(candidate, error):
    bundle, summary, resource = prepare(candidate)
    args = arguments(bundle, summary, resource)
    if error == "wrong-sha":
        args[args.index("--sha256") + 1] = "0" * 64
    else:
        args[args.index("--role") + 1] = "arbitrary.module:run"
    rejected(invoke(candidate, args), candidate)


def test_ambient_cwd_pythonpath_user_site_and_pth_cannot_execute(candidate):
    bundle, summary, resource = prepare(candidate)
    root = candidate["root"]
    poison = root / "ambient-executed"
    malicious = code(f"from pathlib import Path\nPath({str(poison)!r}).write_text('must not execute')\nraise RuntimeError('ambient injection')\n")
    for name in ("json.py", "hashlib.py", "argparse.py", "sitecustomize.py", "ai_cat_controller.py"):
        (root / name).write_bytes(malicious)
    dependencies = root / "dependencies"
    dependencies.mkdir(mode=0o700)
    (dependencies / "unsafe.pth").write_text(
        f"import pathlib; pathlib.Path({str(poison)!r}).write_text('pth executed')\n")
    (dependencies / "sitecustomize.py").write_bytes(malicious)
    result = invoke(candidate, arguments(bundle, summary, resource, dependencies=(dependencies,)), environ={
        "PYTHONPATH": str(root), "PYTHONUSERBASE": str(root),
        "PYTHONSTARTUP": str(root / "sitecustomize.py"),
        "AI_CAT_HARDWARE_DRIVER": "local_k1", "AI_CAT_AUTONOMY_IN_PROCESS_ENABLED": "true",
        "AI_CAT_API_KEY": "synthetic-do-not-use", "AI_CAT_DATA_PATH": str(root / "forbidden.sqlite3"),
    })
    identity(result, candidate, summary, "controller")
    assert not poison.exists()
    assert not (root / "forbidden.sqlite3").exists()


def test_unknown_project_namespace_never_falls_back_to_a_dependency_tree(candidate):
    root = candidate["root"]
    write_source(candidate["source"], module_path(ROLES["controller"]), code(
        "import ai_cat_controller.not_in_policy\n"
        f"open({str(candidate['marker'])!r}, 'w').write('must not execute')\n"))
    dependencies = root / "dependencies"
    ambient = dependencies / "ai_cat_controller"
    ambient.mkdir(parents=True)
    poison = root / "namespace-fallback"
    (ambient / "__init__.py").write_text("# outside the verified project\n")
    (ambient / "not_in_policy.py").write_text(
        f"open({str(poison)!r}, 'w').write('filesystem fallback')\n")
    bundle, summary, resource = prepare(candidate)
    staged_package = resource / "src/ai_cat_controller"
    staged_package.chmod(0o755)
    (staged_package / "not_in_policy.py").write_text(
        f"open({str(poison)!r}, 'w').write('staged filesystem fallback')\n")
    rejected(invoke(candidate, arguments(bundle, summary, resource, dependencies=(dependencies,))), candidate)
    assert not poison.exists()


def test_staged_python_and_matching_pyc_are_never_loaded_instead_of_memory(candidate):
    bundle, summary, resource = prepare(candidate)
    disk_source = resource / module_path(ROLES["controller"])
    package = disk_source.parent
    package.chmod(0o755)
    disk_source.chmod(0o644)
    poison = candidate["root"] / "disk-python-executed"
    disk_source.write_text(f"open({str(poison)!r}, 'w').write('disk source or pyc executed')\n")
    cache = Path(importlib.util.cache_from_source(str(disk_source)))
    cache.parent.mkdir(mode=0o700)
    py_compile.compile(str(disk_source), cfile=str(cache), doraise=True)
    identity(invoke(candidate, arguments(bundle, summary, resource)), candidate, summary, "controller")
    assert not poison.exists()


def test_preloaded_project_module_is_rejected_in_a_real_isolated_interpreter(candidate):
    bundle, summary, resource = prepare(candidate)
    wrapper = (
        "import sys, types, runpy; "
        "sys.modules['ai_cat_controller'] = types.ModuleType('ai_cat_controller'); "
        f"sys.argv = [{str(RUNTIME)!r}] + sys.argv[1:]; "
        f"runpy.run_path({str(RUNTIME)!r}, run_name='__main__')"
    )
    rejected(invoke(candidate, arguments(bundle, summary, resource), prefix=["-c", wrapper]), candidate)


def test_archive_path_changed_by_early_module_does_not_replace_later_memory_imports(candidate):
    bundle_path = candidate["root"] / "candidate.tar"
    write_source(candidate["source"], "src/ai_cat_controller/__init__.py", code(
        "from pathlib import Path\n"
        f"Path({str(bundle_path)!r}).write_bytes(b'changed after verified snapshot')\n"))
    bundle, summary, resource = prepare(candidate)
    identity(invoke(candidate, arguments(bundle, summary, resource)), candidate, summary, "controller")
    assert bundle.read_bytes() == b"changed after verified snapshot"


@pytest.mark.parametrize("change", ["tamper", "missing", "symlink"])
def test_resource_identity_is_checked_before_executing_project_python(candidate, change):
    bundle, summary, resource = prepare(candidate)
    target = resource / "src/ai_cat_controller/static/control.css"
    target.parent.chmod(0o755)
    if change == "tamper":
        target.chmod(0o644)
        target.write_bytes(b"changed resource")
    else:
        original = target.read_bytes()
        target.unlink()
        if change == "symlink":
            outside = candidate["root"] / "same-bytes.css"
            outside.write_bytes(original)
            target.symlink_to(outside)
    rejected(invoke(candidate, arguments(bundle, summary, resource)), candidate)


@pytest.fixture
def real_candidate(tmp_path):
    # Fixed approved runtime inputs only: build_bundle never traverses private
    # repository siblings, and this does not import the candidate in pytest.
    source = Path(__file__).parents[2]
    bundle = tmp_path / "real-candidate.tar"
    summary = release.build_bundle(source, bundle)
    stage = tmp_path / "real-stage"
    release.stage_bundle(bundle, summary["sha256"], stage)
    return {"root": tmp_path, "bundle": bundle, "summary": summary,
            "resource": stage / "payload"}


@pytest.mark.parametrize("role", list(ROLES))
def test_real_current_candidate_imports_in_isolated_child_without_hardware_or_private_config(real_candidate, role):
    fixture = real_candidate
    dependencies = tuple(dict.fromkeys(Path(sysconfig.get_path(name)) for name in ("purelib", "platlib")))
    forbidden = fixture["root"] / "must-not-open-private-config"
    forbidden.write_bytes(b"synthetic forbidden credential input")
    result = invoke(fixture, arguments(fixture["bundle"], fixture["summary"], fixture["resource"],
        role=role, dependencies=dependencies), environ={
            "AI_CAT_HARDWARE_DRIVER": "local_k1", "AI_CAT_API_KEY_ENABLED": "true",
            "AI_CAT_API_KEY": "synthetic-never-send", "AI_CAT_ENABLE_MOTION": "true",
            "AI_CAT_AUTONOMY_IN_PROCESS_ENABLED": "true", "AI_CAT_MINI_APP_SECRET_FILE": str(forbidden),
            "AI_CAT_DATA_PATH": str(fixture["root"] / "forbidden.sqlite3"),
            "AI_CAT_DIALOG_TEXT_REQUEST_PATH": str(fixture["root"] / "forbidden-request.json"),
        })
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    response = json.loads(result.stdout)
    assert set(response) == IDENTITY_KEYS
    assert response["scope"] == "verified-python-import"
    assert response["role"] == role and response["module"] == ROLES[role]
    assert response["bundle_sha256"] == fixture["summary"]["sha256"]
    assert response["manifest_sha256"] == fixture["summary"]["manifest_sha256"]
    assert response["dependencies_verified"] is False
    assert response["resources_immutable"] is False
    assert response["production_ready"] is False
    _, _, verified_files = release._verified(fixture["bundle"], fixture["summary"]["sha256"])
    executed = response["executed_modules"]
    assert isinstance(executed, dict) and "ai_cat_controller" in executed and ROLES[role] in executed
    for module, actual_hash in executed.items():
        ordinary = module_path(module)
        package = ordinary[:-3] + "/__init__.py"
        selected = ordinary if ordinary in verified_files else package
        assert selected in verified_files
        assert actual_hash == hashlib.sha256(verified_files[selected]).hexdigest()
    assert response["module_sha256"] == executed[ROLES[role]]
    assert forbidden.read_bytes() == b"synthetic forbidden credential input"
    assert not (fixture["root"] / "forbidden.sqlite3").exists()
    assert not (fixture["root"] / "forbidden-request.json").exists()


def test_real_verified_controller_serves_health_and_web_with_only_temporary_mock_state(real_candidate):
    fixture = real_candidate
    dependencies = tuple(dict.fromkeys(Path(sysconfig.get_path(name)) for name in ("purelib", "platlib")))
    state_root = fixture["root"] / "mock-state"
    harness = f"""
import hashlib, json, runpy
from pathlib import Path
bootstrap = runpy.run_path({str(RUNTIME)!r})
with bootstrap['VerifiedRuntime'](Path({str(fixture['bundle'])!r}), {fixture['summary']['sha256']!r},
        Path({str(fixture['resource'])!r}), tuple(Path(path) for path in {tuple(str(path) for path in dependencies)!r})).install() as runtime:
    main = runtime.load_role('controller')
    from ai_cat_controller.core.config import Settings
    from fastapi.testclient import TestClient
    root = Path({str(state_root)!r})
    root.mkdir(mode=0o700)
    defaults = Settings()
    overrides = {{'hardware_driver': 'mock', 'log_level': 'CRITICAL'}}
    for name in Settings.model_fields:
        value = getattr(defaults, name)
        if isinstance(value, Path) and not name.endswith(('_binary', '_player_path')):
            overrides[name] = root / name
    application = main.create_app(Settings(**overrides))
    with TestClient(application) as client:
        assert application.state.services.adapter.mode == 'mock'
        health = client.get('/health')
        assert health.status_code == 200
        page = client.get('/')
        assert page.status_code == 200 and 'text/html' in page.headers.get('content-type', '')
        css = client.get('/static/control.css')
        assert css.status_code == 200
        assert css.content == (Path({str(fixture['resource'])!r}) / 'src/ai_cat_controller/static/control.css').read_bytes()
    result = runtime.identity()
    assert set(result['executed_modules']) == {{name for name in __import__('sys').modules if name == 'ai_cat_controller' or name.startswith('ai_cat_controller.')}}
    print(json.dumps({{'identity': result, 'mode': 'mock', 'health': health.status_code,
        'page': page.status_code, 'css_sha256': hashlib.sha256(css.content).hexdigest()}}))
"""
    result = invoke(fixture, [], prefix=["-c", harness], environ={
        "AI_CAT_HARDWARE_DRIVER": "local_k1", "AI_CAT_API_KEY_ENABLED": "true",
        "AI_CAT_API_KEY": "synthetic-never-send", "AI_CAT_AUTONOMY_IN_PROCESS_ENABLED": "true",
    })
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert set(response) == {"identity", "mode", "health", "page", "css_sha256"}
    assert response["mode"] == "mock" and response["health"] == response["page"] == 200
    assert set(response["identity"]) == IDENTITY_KEYS
    assert response["identity"]["bundle_sha256"] == fixture["summary"]["sha256"]
    assert response["identity"]["production_ready"] is False
    expected_css = fixture["resource"] / "src/ai_cat_controller/static/control.css"
    assert response["css_sha256"] == hashlib.sha256(expected_css.read_bytes()).hexdigest()
    assert (state_root / "data_path").is_file()
    assert not (fixture["root"] / ".data").exists()
