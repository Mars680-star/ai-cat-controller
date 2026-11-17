"""Compile actual native privacy guards with inert globals; no SDK or K1."""

from pathlib import Path
import shutil
import subprocess

import pytest

from test_native_motion_gate_runtime import function


SOURCE = Path(__file__).parents[2] / "native/dialog/volc_conv_ai_demo.c"


@pytest.fixture(scope="module")
def native_privacy_guard(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-personality-inert")
    compiler = shutil.which("cc")
    assert compiler, "A C compiler is required for the native privacy regression"
    source = SOURCE.read_text()
    prefix = r'''
#define _DEFAULT_SOURCE
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
static const char *runtime_path;
#define PERSONALITY_RUNTIME_PATH runtime_path
#define PERSONALITY_RUNTIME_MAX_BYTES (32 * 1024)
static off_t personality_runtime_size = -1;
static time_t personality_runtime_mtime;
static long personality_runtime_mtime_ns;
static ino_t personality_runtime_inode;
static dev_t personality_runtime_device;
static bool exit_request, clean_exit_requested, running, ai_playing, discard_playback_audio;
'''
    main = r'''
int main(int argc, char **argv) {
    struct stat saved, changed;
    if (argc != 3) return 2;
    runtime_path = argv[1];
    if (argv[2][0] == 'm') {
        if (lstat(runtime_path, &saved)) return 3;
        __remember_personality_runtime_metadata(&saved);
        printf("same=%d ", __personality_runtime_metadata_matches(&saved));
        changed = saved; changed.st_ino++;
        printf("inode=%d ", __personality_runtime_metadata_matches(&changed));
        changed = saved; changed.st_dev++;
        printf("device=%d ", __personality_runtime_metadata_matches(&changed));
        changed = saved; changed.st_mtim.tv_nsec++;
        printf("nanos=%d ", __personality_runtime_metadata_matches(&changed));
        changed = saved; changed.st_mtime++;
        printf("seconds=%d ", __personality_runtime_metadata_matches(&changed));
        changed = saved; changed.st_size++;
        printf("size=%d\n", __personality_runtime_metadata_matches(&changed));
    } else {
        personality_runtime_size = atoi(argv[2]);
        clean_exit_requested = running = ai_playing = true;
        bool revoked = __personality_runtime_revoked();
        printf("revoked=%d exit=%d clean=%d running=%d playing=%d discard=%d\n",
            revoked, exit_request, clean_exit_requested, running, ai_playing, discard_playback_audio);
    }
    return 0;
}
'''
    harness = directory / "privacy.c"
    harness.write_text(prefix + "\n".join(function(source, name) for name in (
        "__remember_personality_runtime_metadata", "__personality_runtime_metadata_matches",
        "__personality_runtime_revoked",
    )) + main)
    executable = directory / "privacy"
    result = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(executable)],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return executable


def _run(executable: Path, path: Path, mode: str) -> str:
    result = subprocess.run([str(executable), str(path), mode], capture_output=True, text=True, timeout=2)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("kind", ["missing", "empty", "directory", "symlink", "oversized"])
def test_applied_runtime_revocation_stops_before_new_input(native_privacy_guard, tmp_path, kind):
    path = tmp_path / "runtime"
    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "other"
        target.write_bytes(b"{}")
        path.symlink_to(target)
    elif kind == "oversized":
        path.write_bytes(b"x" * (32 * 1024 + 1))
    assert _run(native_privacy_guard, path, "100") == "revoked=1 exit=1 clean=0 running=0 playing=0 discard=1"


def test_unconfigured_startup_does_not_exit_when_runtime_is_absent(native_privacy_guard, tmp_path):
    assert _run(native_privacy_guard, tmp_path / "absent", "-1") == "revoked=0 exit=0 clean=1 running=1 playing=1 discard=0"


def test_present_regular_runtime_does_not_revoke_cached_profile(native_privacy_guard, tmp_path):
    path = tmp_path / "runtime"
    path.write_text("{}")
    assert _run(native_privacy_guard, path, "100") == "revoked=0 exit=0 clean=1 running=1 playing=1 discard=0"


def test_profile_cache_includes_inode_device_and_subsecond_mtime(native_privacy_guard, tmp_path):
    path = tmp_path / "runtime"
    path.write_text("{}")
    assert _run(native_privacy_guard, path, "metadata") == "same=1 inode=0 device=0 nanos=0 seconds=0 size=0"


def test_main_checks_revocation_before_poll_and_pending_input():
    source = SOURCE.read_text()
    loop = source[source.index("while (!exit_request) {", source.index("/*\n     * 阶段 4：")) :]
    assert loop.index("__personality_runtime_revoked()") < loop.index("personality_last_poll_ms")
    assert loop.index("__personality_runtime_revoked()") < loop.index("if (text_dialog_request)")
    gate = loop[loop.index("!session_active && !running && !ai_playing") : loop.index("__apply_personality_runtime(&demo, false)")]
    assert "!wake_request" not in gate and "!text_dialog_request" not in gate
    assert "wake_request || text_dialog_request" in loop[: loop.index("__apply_personality_runtime(&demo, false)")]
    assert "personality_result < 0 && personality_runtime_size >= 0" in loop

