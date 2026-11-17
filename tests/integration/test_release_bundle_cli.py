"""Exercise the real offline CLI against the complete current public runtime.

No package installation, import of the candidate, service or device operation.
The source inventory is the fixed release policy, not the whole worktree.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).parents[2]


def command(*arguments):
    return subprocess.run([sys.executable, "-I", "-m", "ai_cat_controller.release_bundle", *map(str, arguments)],
        cwd=ROOT, capture_output=True, text=True, timeout=20)


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    directory = tmp_path_factory.mktemp("release-cli")
    first, second = directory / "first.tar", directory / "second.tar"
    summary = None
    for output in (first, second):
        result = command("build", "--source", ROOT, "--output", output)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["scope"] == "python-source-candidate"
        assert data["native_built"] is False and data["dependencies_vendored"] is False
        assert data["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        if summary is not None:
            assert summary == data
        summary = data
    assert first.read_bytes() == second.read_bytes()
    return directory, first, summary


def test_complete_current_runtime_cli_is_deterministic_and_stages_readonly(candidate):
    directory, archive, summary = candidate
    inspected = command("inspect", "--bundle", archive, "--sha256", summary["sha256"])
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == summary
    destination = directory / "readonly-candidate"
    staged = command("stage", "--bundle", archive, "--sha256", summary["sha256"], "--destination", destination)
    assert staged.returncode == 0, staged.stderr
    assert json.loads(staged.stdout)["destination"] == str(destination)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o555
    assert (destination / "manifest.json").is_file()
    completed = json.loads((destination / "STAGED.json").read_bytes())
    assert completed == {**summary, "status": "staged"}
    assert stat.S_IMODE((destination / "STAGED.json").stat().st_mode) == 0o444
    for relative in ("src/ai_cat_controller/main.py", "src/ai_cat_controller/core/operation_gate.py",
            "src/ai_cat_controller/release_bundle.py", "src/ai_cat_controller/mini_gateway/app.py",
            "src/ai_cat_controller/templates/control.html", "src/ai_cat_controller/static/control.js",
            "assets/local-speech/shared/event-level_up.wav"):
        target = destination / "payload" / relative
        assert target.read_bytes() == (ROOT / relative).read_bytes()
        assert stat.S_IMODE(target.stat().st_mode) == 0o444
    # These are real current release files, not a sparse synthetic manifest.
    payload_files = [path for path in (destination / "payload").rglob("*") if path.is_file()]
    assert len(payload_files) == summary["file_count"]
    assert summary["file_count"] > 90
    assert not any(path.name.endswith(".capture.wav") for path in payload_files)
    for target in payload_files:
        relative = target.relative_to(destination / "payload")
        assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256((ROOT / relative).read_bytes()).digest(), str(relative)
        assert stat.S_IMODE(target.stat().st_mode) == 0o444


def test_cli_refuses_existing_build_and_stage_targets_without_changing_them(candidate):
    directory, archive, summary = candidate
    before = archive.read_bytes()
    result = command("build", "--source", ROOT, "--output", archive)
    assert result.returncode != 0 and "Traceback" not in result.stderr
    assert archive.read_bytes() == before
    destination = directory / "occupied"
    destination.mkdir(mode=0o700)
    sentinel = destination / "retain.txt"
    sentinel.write_bytes(b"existing user data")
    result = command("stage", "--bundle", archive, "--sha256", summary["sha256"], "--destination", destination)
    assert result.returncode != 0 and "Traceback" not in result.stderr
    assert sentinel.read_bytes() == b"existing user data"
    assert sorted(path.name for path in destination.iterdir()) == ["retain.txt"]


def test_cli_requires_independent_digest_and_validates_before_creating_destination(candidate):
    directory, archive, summary = candidate
    missing = command("inspect", "--bundle", archive)
    assert missing.returncode != 0 and "Traceback" not in missing.stderr
    destination = directory / "must-not-be-created"
    result = command("stage", "--bundle", archive, "--sha256", "0" * 64, "--destination", destination)
    assert result.returncode != 0 and "Traceback" not in result.stderr
    assert not destination.exists()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == summary["sha256"]


def test_trusted_isolated_cli_does_not_import_package_from_received_cwd_or_pythonpath(candidate, tmp_path):
    _, archive, summary = candidate
    untrusted = tmp_path / "ai_cat_controller"
    untrusted.mkdir()
    (untrusted / "__init__.py").write_text("raise SystemExit('UNTRUSTED_PACKAGE_WAS_IMPORTED')\n", encoding="ascii")
    result = subprocess.run([sys.executable, "-I", "-m", "ai_cat_controller.release_bundle", "inspect",
        "--bundle", str(archive), "--sha256", summary["sha256"]], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == summary
    assert "UNTRUSTED_PACKAGE_WAS_IMPORTED" not in result.stdout + result.stderr


def test_cli_rejects_traversal_even_with_correct_external_archive_digest(candidate):
    directory, archive, _ = candidate
    malformed = directory / "traversal.tar"
    with tarfile.open(archive, "r:") as source, tarfile.open(malformed, "w", format=tarfile.USTAR_FORMAT) as target:
        changed = False
        for member in source:
            data = source.extractfile(member).read()
            if member.name == "payload/src/ai_cat_controller/main.py":
                member.name = "../escaped.py"
                changed = True
            target.addfile(member, io.BytesIO(data))
        assert changed
    digest = hashlib.sha256(malformed.read_bytes()).hexdigest()
    destination = directory / "malformed-stage"
    result = command("stage", "--bundle", malformed, "--sha256", digest, "--destination", destination)
    assert result.returncode != 0 and "Traceback" not in result.stderr
    assert not destination.exists() and not (directory.parent / "escaped.py").exists()
