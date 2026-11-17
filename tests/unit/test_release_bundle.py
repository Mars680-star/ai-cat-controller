"""Real bounded USTAR adversarial fixtures; never import a packaged tree."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import threading

import pytest

from ai_cat_controller import release_bundle as release


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    for relative in release.ALLOWED_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFFsynthetic" if relative.endswith(".wav") else
                         b"\x89PNGsynthetic" if relative.endswith(".png") else b"# synthetic public source\n")
    # These are deliberately not part of the approved source tree inventory.
    (root / ".data").mkdir()
    (root / ".data" / "private.sqlite3").write_bytes(b"never read private database")
    (root / ".env").write_text("private secret, never read", encoding="utf-8")
    return root


@pytest.fixture
def bundle(source, tmp_path):
    path = tmp_path / "candidate.tar"
    result = release.build_bundle(source, path)
    return path, result


def entries(path):
    with tarfile.open(path, mode="r:") as archive:
        return [(member.name, archive.extractfile(member).read()) for member in archive]


def archive(tmp_path, members, *, name="hostile.tar", format=tarfile.USTAR_FORMAT):
    output = tmp_path / name
    with tarfile.open(output, "w", format=format) as writer:
        for member, content in members:
            if isinstance(member, str):
                info = tarfile.TarInfo(member)
                info.mode, info.size = 0o644, len(content)
            else:
                info = member
            writer.addfile(info, io.BytesIO(content) if info.isreg() else None)
    return output


def manifest_change(tmp_path, bundle, change):
    contents = entries(bundle)
    manifest = json.loads(contents[0][1])
    change(manifest)
    contents[0] = ("manifest.json", canonical(manifest))
    return archive(tmp_path, contents)


def test_complete_candidate_is_deterministic_and_stages_exact_readonly_inventory(source, bundle, tmp_path):
    path, result = bundle
    second = tmp_path / "same-content.tar"
    assert release.build_bundle(source, second) == result
    assert second.read_bytes() == path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert result["sha256"] == digest(path)
    assert result["scope"] == "python-source-candidate"
    assert result["native_built"] is False and result["dependencies_vendored"] is False
    assert result["file_count"] == len(release.ALLOWED_PATHS)
    assert len([name for name in release.ALLOWED_PATHS if name.endswith(".wav")]) == 21
    assert release.inspect_bundle(path, result["sha256"]) == result
    names = [name for name, _ in entries(path)]
    assert names == ["manifest.json"] + ["payload/" + name for name in release.ALLOWED_PATHS]
    destination = tmp_path / "versioned-candidate"
    staged = release.stage_bundle(path, result["sha256"], destination)
    assert staged == {**result, "destination": str(destination)}
    assert json.loads((destination / "STAGED.json").read_text()) == {**result, "status": "staged"}
    assert stat.S_IMODE((destination / "STAGED.json").stat().st_mode) == 0o444
    for relative in release.ALLOWED_PATHS:
        target = destination / "payload" / relative
        assert target.read_bytes() == (source / relative).read_bytes()
        assert stat.S_IMODE(target.stat().st_mode) == 0o444
    for current, directories, _ in os.walk(destination):
        assert stat.S_IMODE(Path(current).stat().st_mode) == 0o555
    assert not (destination / "payload" / ".env").exists()


def test_packaged_source_is_never_imported_or_executed(source, tmp_path):
    (source / "src/ai_cat_controller/main.py").write_text("raise AssertionError('MUST NOT RUN')\n", encoding="utf-8")
    path = tmp_path / "candidate.tar"
    result = release.build_bundle(source, path)
    assert release.inspect_bundle(path, result["sha256"])["file_count"] == len(release.ALLOWED_PATHS)


def test_capture_new_recordings_and_private_repo_siblings_are_not_opened(source, tmp_path, monkeypatch):
    capture = source / "assets/local-speech/proud_star/phrase-02.capture.wav"
    capture.write_bytes(b"private recording")
    capture.with_name("new-recording.wav").write_bytes(b"private recording")
    hidden = source / "assets/local-speech/private-audio"
    hidden.mkdir()
    (hidden / "personal.wav").write_bytes(b"private recording")
    cache = source / "src/ai_cat_controller/__pycache__"
    cache.mkdir()
    (cache / "private.pyc").write_bytes(b"not source")
    opened = []
    original = release._stable_read

    def observed(parent, name, limit):
        opened.append(name)
        return original(parent, name, limit)

    monkeypatch.setattr(release, "_stable_read", observed)
    path = tmp_path / "candidate.tar"
    result = release.build_bundle(source, path)
    assert result["file_count"] == len(release.ALLOWED_PATHS)
    assert not {"phrase-02.capture.wav", "new-recording.wav", "personal.wav", "private.pyc", ".env", "private.sqlite3"} & set(opened)
    assert all("capture" not in name for name, _ in entries(path))


@pytest.mark.parametrize("relative", ["README.md", "src/ai_cat_controller/services/motion_service.py", "assets/local-speech/shared/touch-back.wav"])
def test_every_fixed_policy_member_is_required(source, tmp_path, relative):
    (source / relative).unlink()
    output = tmp_path / "candidate.tar"
    with pytest.raises(release.BundleError):
        release.build_bundle(source, output)
    assert not output.exists()


@pytest.mark.parametrize("name", ["credentials.py", "api-key.txt", ".env", "unexpected.png", "extra-package"])
def test_unknown_runtime_content_is_rejected_without_opening_it(source, tmp_path, name):
    unknown = source / "src/ai_cat_controller" / name
    unknown.write_bytes(b"do not read this private unknown item")
    with pytest.raises(release.BundleError):
        release.build_bundle(source, tmp_path / "candidate.tar")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_selected_input_must_be_regular_unlinked_and_never_blocks(source, tmp_path, kind):
    selected = source / "README.md"
    if kind == "hardlink":
        os.link(selected, tmp_path / "alias")
    else:
        selected.unlink()
        if kind == "symlink":
            outside = tmp_path / "private"
            outside.write_text("not selected", encoding="utf-8")
            selected.symlink_to(outside)
        else:
            os.mkfifo(selected, 0o600)
    with pytest.raises(release.BundleError):
        release.build_bundle(source, tmp_path / "candidate.tar")


def test_symlink_source_ancestor_and_selected_directory_are_rejected(source, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(release.BundleError):
        release.build_bundle(alias, tmp_path / "one.tar")
    runtime = source / "src/ai_cat_controller"
    runtime.rename(source / "retired-runtime")
    runtime.symlink_to(source / "retired-runtime", target_is_directory=True)
    with pytest.raises(release.BundleError):
        release.build_bundle(source, tmp_path / "two.tar")


def test_symlink_pycache_is_not_followed_or_accepted(source, tmp_path):
    (source / "src/ai_cat_controller/__pycache__").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(release.BundleError):
        release.build_bundle(source, tmp_path / "candidate.tar")


@pytest.mark.parametrize("content", [
    b'api_key = "0123456789abcdef0123456789abcdef"\n',
    b'"product_secret": "0123456789abcdef0123456789abcdef"\n',
    b'-----BEGIN RSA PRIVATE KEY-----\nprivate\n',
])
def test_literal_secret_heuristic_fails_without_printing_content(source, tmp_path, content):
    (source / "src/ai_cat_controller/main.py").write_bytes(content)
    with pytest.raises(release.BundleError) as error:
        release.build_bundle(source, tmp_path / "candidate.tar")
    assert "0123456789abcdef" not in str(error.value)
    assert "private" not in str(error.value)


def test_build_and_stage_never_overwrite_existing_paths_or_follow_output_symlinks(source, bundle, tmp_path):
    path, result = bundle
    before = (path.stat().st_ino, path.read_bytes())
    with pytest.raises(release.BundleError):
        release.build_bundle(source, path)
    assert before == (path.stat().st_ino, path.read_bytes())
    victim = tmp_path / "old-file"
    victim.write_bytes(b"preserve")
    alias = tmp_path / "output-link"
    alias.symlink_to(victim)
    with pytest.raises(release.BundleError):
        release.build_bundle(source, alias)
    destination = tmp_path / "already-exists"
    destination.mkdir(mode=0o700)
    old = destination / "existing-data"
    old.write_bytes(b"preserve")
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], destination)
    assert victim.read_bytes() == old.read_bytes() == b"preserve"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_stage_requires_private_parent_and_rejects_symlink_ancestors(bundle, tmp_path):
    path, result = bundle
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], public / "candidate")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], alias / "candidate")
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("bad_digest", ["", "0" * 64, "A" * 64, "1" * 64 + "\n", True, None])
def test_independently_supplied_exact_digest_is_mandatory_before_staging(bundle, tmp_path, bad_digest):
    path, _ = bundle
    destination = tmp_path / "candidate"
    with pytest.raises(release.BundleError):
        release.inspect_bundle(path, bad_digest)
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, bad_digest, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_bundle_input_is_also_nofollow_regular_single_link(bundle, tmp_path, kind):
    path, result = bundle
    suspect = tmp_path / "suspect.tar"
    if kind == "symlink":
        suspect.symlink_to(path)
    elif kind == "hardlink":
        os.link(path, suspect)
    else:
        os.mkfifo(suspect, 0o600)
    with pytest.raises(release.BundleError):
        release.inspect_bundle(suspect, result["sha256"])


def test_archive_hash_tamper_is_rejected_before_destination_creation(bundle, tmp_path):
    path, result = bundle
    data = bytearray(path.read_bytes())
    data[700] ^= 1
    path.write_bytes(data)
    destination = tmp_path / "candidate"
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], destination)
    assert not destination.exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "payload/../escape", "payload//README.md", "payload/.env", "payload/README.md/child", "payload\\README.md"])
def test_even_trusted_outer_hash_cannot_authorize_extra_or_traversing_tar_paths(bundle, tmp_path, name):
    path, _ = bundle
    malicious = archive(tmp_path, entries(path) + [(name, b"not allowed")])
    with pytest.raises(release.BundleError):
        release.inspect_bundle(malicious, digest(malicious))
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_real_tar_nonregular_members_are_rejected(bundle, tmp_path, kind):
    path, _ = bundle
    content = entries(path)
    info = tarfile.TarInfo(content[1][0])
    info.type, info.mode, info.linkname = kind, 0o644, "../../escape"
    malicious = archive(tmp_path, [content[0], (info, b"")] + content[2:])
    with pytest.raises(release.BundleError):
        release.inspect_bundle(malicious, digest(malicious))


def test_duplicate_tar_member_and_nonfirst_manifest_are_rejected(bundle, tmp_path):
    path, _ = bundle
    content = entries(path)
    for index, members in enumerate((content + [content[-1]], content[1:] + [content[0]])):
        malicious = archive(tmp_path, members, name=f"hostile-{index}.tar")
        with pytest.raises(release.BundleError):
            release.inspect_bundle(malicious, digest(malicious))


@pytest.mark.parametrize("change", [
    lambda value: value.update(schema=True),
    lambda value: value.update(native_built=True),
    lambda value: value.update(dependencies_vendored=0),
    lambda value: value.update(scope="ready"),
    lambda value: value.update(ready=True),
    lambda value: value["files"].pop(),
    lambda value: value["files"].append(value["files"][0]),
    lambda value: value["files"][0].update(size=True),
    lambda value: value["files"][0].update(sha256="0" * 64),
    lambda value: value["files"][0].update(path="../elsewhere"),
])
def test_manifest_schema_types_inventory_and_payload_hash_are_exact(bundle, tmp_path, change):
    path, _ = bundle
    malicious = manifest_change(tmp_path, path, change)
    with pytest.raises(release.BundleError):
        release.inspect_bundle(malicious, digest(malicious))


def test_duplicate_json_keys_and_missing_policy_member_are_rejected(bundle, tmp_path):
    path, _ = bundle
    content = entries(path)
    content[0] = ("manifest.json", content[0][1].replace(b'"schema":1', b'"schema":1,"schema":1'))
    malicious = archive(tmp_path, content)
    with pytest.raises(release.BundleError):
        release.inspect_bundle(malicious, digest(malicious))
    missing = archive(tmp_path, entries(path)[:-1], name="missing.tar")
    with pytest.raises(release.BundleError):
        release.inspect_bundle(missing, digest(missing))


@pytest.mark.parametrize("format", [tarfile.GNU_FORMAT, tarfile.PAX_FORMAT])
def test_gnu_and_pax_extensions_are_not_hidden_by_a_convenient_tar_parser(bundle, tmp_path, format):
    path, _ = bundle
    content = entries(path)
    info = tarfile.TarInfo("payload/" + "a" * 180)
    info.size = 1
    malicious = archive(tmp_path, [content[0], (info, b"x")] + content[1:], format=format)
    with pytest.raises(release.BundleError):
        release.inspect_bundle(malicious, digest(malicious))


@pytest.mark.parametrize("mutation", ["checksum", "padding", "trailer", "truncated", "gzip"])
def test_header_padding_trailer_truncation_and_compression_are_strict(bundle, tmp_path, mutation):
    path, _ = bundle
    data = bytearray(path.read_bytes())
    if mutation == "checksum":
        data[148] ^= 1
    elif mutation == "padding":
        size = len(entries(path)[0][1])
        data[512 + size] = 1
    elif mutation == "trailer":
        data[-1] = 1
    elif mutation == "truncated":
        data = data[:-1000]
    else:
        import gzip
        data = gzip.compress(data)
    hostile = tmp_path / "hostile.tar"
    hostile.write_bytes(data)
    with pytest.raises(release.BundleError):
        release.inspect_bundle(hostile, digest(hostile))


@pytest.mark.parametrize("limit", ["MAX_BUNDLE_BYTES", "MAX_PAYLOAD_BYTES", "MAX_FILE_BYTES", "MAX_MANIFEST_BYTES", "MAX_MEMBERS"])
def test_build_and_inspect_enforce_independent_bounded_limits(source, bundle, tmp_path, monkeypatch, limit):
    path, result = bundle
    monkeypatch.setattr(release, limit, 1)
    with pytest.raises(release.BundleError):
        release.build_bundle(source, tmp_path / "too-large.tar")
    with pytest.raises(release.BundleError):
        release.inspect_bundle(path, result["sha256"])


def test_sparse_oversized_input_is_rejected_without_reading_the_claimed_extent(source, tmp_path, monkeypatch):
    selected = source / "README.md"
    with selected.open("wb") as stream:
        stream.truncate(release.MAX_FILE_BYTES + 1)
    with pytest.raises(release.BundleError):
        release.build_bundle(source, tmp_path / "candidate.tar")


def test_output_file_is_exactly_private_even_with_restrictive_umask(source, tmp_path):
    output = tmp_path / "candidate.tar"
    previous = os.umask(0o777)
    try:
        release.build_bundle(source, output)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_deep_json_is_a_fixed_failure_before_any_stage_directory_exists(bundle, tmp_path):
    path, _ = bundle
    content = entries(path)
    content[0] = ("manifest.json", b'{"files":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}")
    hostile = archive(tmp_path, content)
    destination = tmp_path / "candidate"
    with pytest.raises(release.BundleError):
        release.stage_bundle(hostile, digest(hostile), destination)
    assert not destination.exists()


@pytest.mark.parametrize("target_kind", ["source", "bundle"])
def test_fd_content_and_named_inode_must_stay_the_same_during_read(source, bundle, tmp_path, monkeypatch, target_kind):
    path, result = bundle
    selected = source / "README.md" if target_kind == "source" else path
    selected_identity = selected.stat().st_ino
    original_read = os.read
    changed = []

    def raced_read(descriptor, length):
        data = original_read(descriptor, length)
        if not changed and os.fstat(descriptor).st_ino == selected_identity:
            selected.rename(selected.with_name(selected.name + ".retired"))
            selected.write_bytes(b"replacement must not be selected by numeric pathname")
            changed.append(True)
        return data

    monkeypatch.setattr(release.os, "read", raced_read)
    with pytest.raises(release.BundleError):
        if target_kind == "source":
            release.build_bundle(source, tmp_path / "second.tar")
        else:
            release.inspect_bundle(path, result["sha256"])
    assert changed


@pytest.mark.parametrize("at_completion", [False, True])
def test_stage_failure_always_leaves_an_empty_incomplete_seal(bundle, tmp_path, monkeypatch, at_completion):
    path, result = bundle
    destination = tmp_path / "candidate"
    original_write = release._write
    calls = []

    def failed_write(descriptor, content):
        calls.append(True)
        if (at_completion and content.endswith(b'"status":"staged"}')) or (not at_completion and len(calls) == 3):
            original_write(descriptor, content[:15])
            raise OSError("synthetic storage error")
        original_write(descriptor, content)

    monkeypatch.setattr(release, "_write", failed_write)
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], destination)
    assert not destination.exists()
    partial, = tmp_path.glob(release.STAGING_PREFIX + "*")
    assert (partial / "STAGED.json").read_bytes() == b""
    if at_completion:
        assert stat.S_IMODE(partial.stat().st_mode) == 0o555
        assert (partial / "manifest.json").exists()


def test_replaced_parent_is_not_published_into_or_touched_as_an_existing_tree(bundle, tmp_path, monkeypatch):
    path, result = bundle
    parent = tmp_path / "private-parent"
    parent.mkdir(mode=0o700)
    destination = parent / "candidate"
    moved = tmp_path / "retired-parent"
    original_write = release._write
    changed = []

    def raced_write(descriptor, content):
        original_write(descriptor, content)
        if not changed:
            parent.rename(moved)
            parent.mkdir(mode=0o700)
            (parent / "retain.txt").write_bytes(b"new existing user tree")
            changed.append(True)

    monkeypatch.setattr(release, "_write", raced_write)
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], destination)
    assert sorted(item.name for item in parent.iterdir()) == ["retain.txt"]
    assert (parent / "retain.txt").read_bytes() == b"new existing user tree"
    assert not destination.exists()
    partial, = moved.glob(release.STAGING_PREFIX + "*")
    assert (partial / "STAGED.json").read_bytes() == b""


def test_valid_outer_and_manifest_hashes_do_not_bypass_literal_secret_screen(bundle, tmp_path):
    path, _ = bundle
    content = entries(path)
    selected = "src/ai_cat_controller/main.py"
    replacement = b'api_key="0123456789abcdef0123456789abcdef"\n'
    manifest = json.loads(content[0][1])
    for entry in manifest["files"]:
        if entry["path"] == selected:
            entry.update(size=len(replacement), sha256=hashlib.sha256(replacement).hexdigest())
    content[0] = ("manifest.json", canonical(manifest))
    content = [(name, replacement if name == "payload/" + selected else data) for name, data in content]
    hostile = archive(tmp_path, content)
    with pytest.raises(release.BundleError):
        release.inspect_bundle(hostile, digest(hostile))


@pytest.mark.parametrize("replacement", ["parent", "file"])
def test_build_does_not_report_success_after_output_path_identity_changes(source, tmp_path, monkeypatch, replacement):
    parent = tmp_path / "output"
    parent.mkdir(mode=0o700)
    output = parent / "candidate.tar"
    original_write = release._write
    changed = []

    def raced_write(descriptor, content):
        original_write(descriptor, content)
        if not changed:
            if replacement == "parent":
                parent.rename(tmp_path / "retired-output")
                parent.mkdir(mode=0o700)
                (parent / "retain.txt").write_bytes(b"replacement user tree")
            else:
                output.rename(parent / "created-but-retired.tar")
                output.write_bytes(b"replacement existing file")
            changed.append(True)

    monkeypatch.setattr(release, "_write", raced_write)
    with pytest.raises(release.BundleError):
        release.build_bundle(source, output)
    assert changed
    if replacement == "parent":
        assert not output.exists()
        assert (parent / "retain.txt").read_bytes() == b"replacement user tree"
    else:
        assert output.read_bytes() == b"replacement existing file"


def test_final_name_is_only_created_by_actual_atomic_noreplace_publication(bundle, tmp_path, monkeypatch):
    path, result = bundle
    destination = tmp_path / "candidate"
    original_publish = release._rename_noreplace
    published = []

    def observed_publish(parent, hidden, final):
        assert final == destination.name and hidden.startswith(release.STAGING_PREFIX)
        assert not destination.exists()
        staging = tmp_path / hidden
        old_identity = staging.stat().st_ino
        assert stat.S_IMODE(staging.stat().st_mode) == 0o555
        assert json.loads((staging / "STAGED.json").read_text())["status"] == "staged"
        original_publish(parent, hidden, final)
        assert destination.stat().st_ino == old_identity
        assert not staging.exists()
        published.append(True)

    monkeypatch.setattr(release, "_rename_noreplace", observed_publish)
    release.stage_bundle(path, result["sha256"], destination)
    assert published == [True]


def test_stage_never_mkdirs_the_fixed_final_name_or_modifies_an_existing_empty_tree(bundle, tmp_path, monkeypatch):
    path, result = bundle
    destination = tmp_path / "candidate"
    prior = tmp_path / "preexisting-empty"
    prior.mkdir(mode=0o700)
    prior_inode = prior.stat().st_ino
    original_mkdir = os.mkdir

    def no_fixed_name(name, *args, **kwargs):
        assert name != destination.name, "The final name must not expose a partially constructed tree"
        return original_mkdir(name, *args, **kwargs)

    monkeypatch.setattr(release.os, "mkdir", no_fixed_name)
    release.stage_bundle(path, result["sha256"], destination)
    assert destination.stat().st_ino != prior_inode
    assert prior.stat().st_ino == prior_inode and list(prior.iterdir()) == []
    assert stat.S_IMODE(prior.stat().st_mode) == 0o700


def test_target_created_after_sealing_is_never_replaced_even_when_empty(bundle, tmp_path, monkeypatch):
    path, result = bundle
    destination = tmp_path / "candidate"
    original_publish = release._rename_noreplace
    existing = []

    def collided_publish(parent, hidden, final):
        destination.mkdir(mode=0o700)
        existing.append(destination.stat().st_ino)
        original_publish(parent, hidden, final)

    monkeypatch.setattr(release, "_rename_noreplace", collided_publish)
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], destination)
    assert destination.stat().st_ino == existing[0]
    assert list(destination.iterdir()) == [] and stat.S_IMODE(destination.stat().st_mode) == 0o700
    partial, = tmp_path.glob(release.STAGING_PREFIX + "*")
    assert (partial / "STAGED.json").read_bytes() == b""


def test_two_actual_parallel_publishers_have_one_winner_and_preserve_losers_hidden_tree(bundle, tmp_path, monkeypatch):
    path, result = bundle
    destination = tmp_path / "candidate"
    barrier = threading.Barrier(2)
    original_publish = release._rename_noreplace

    def synchronized_publish(parent, hidden, final):
        barrier.wait(timeout=3)
        return original_publish(parent, hidden, final)

    def stage():
        try:
            return release.stage_bundle(path, result["sha256"], destination)
        except release.BundleError:
            return None

    monkeypatch.setattr(release, "_rename_noreplace", synchronized_publish)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(stage) for _ in range(2)]
        outcomes = [future.result(timeout=5) for future in futures]
    assert sum(outcome is not None for outcome in outcomes) == 1
    assert json.loads((destination / "STAGED.json").read_text())["sha256"] == result["sha256"]
    partial, = tmp_path.glob(release.STAGING_PREFIX + "*")
    assert (partial / "STAGED.json").read_bytes() == b""


@pytest.mark.parametrize("unsupported", ["missing-libc-wrapper", "unsupported-syscall"])
def test_atomic_publication_unavailable_fails_closed_without_rename_fallback(bundle, tmp_path, monkeypatch, unsupported):
    path, result = bundle
    destination = tmp_path / "candidate"

    class Function:
        def __call__(self, *args):
            return -1

    class Library:
        renameat2 = Function()

    monkeypatch.setattr(release.ctypes, "CDLL", lambda *args, **kwargs:
                        object() if unsupported == "missing-libc-wrapper" else Library())
    monkeypatch.setattr(release.os, "rename", lambda *args, **kwargs: pytest.fail("unsafe rename fallback"))
    with pytest.raises(release.BundleError):
        release.stage_bundle(path, result["sha256"], destination)
    assert not destination.exists()
    partial, = tmp_path.glob(release.STAGING_PREFIX + "*")
    assert (partial / "STAGED.json").read_bytes() == b""
