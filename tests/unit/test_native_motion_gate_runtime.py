"""Compile actual native guard/dispatch functions with inert SDK/spawn stubs.

The tests never execute a device binary, touch GPIO, spawn a motor process or use
real configuration. Runtime locks/PID files are confined to pytest's directory.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[2]
TOY = ROOT / "native/ai-toy-app/src/main.c"
VOICE = ROOT / "native/dialog/volc_conv_ai_demo.c"
FLAGS = ("AI_CAT_ENABLE_MOTION", "AI_CAT_HAS_TAIL", "AI_CAT_ENABLE_TAIL_MOTION")
TRUE_VALUES = ("1", "true", "TRUE", "TrUe", "yes", "YES", "on", "ON")
FALSE_VALUES = ("0", "false", "FALSE", "FaLsE", "no", "NO", "off", "OFF")
INVALID_VALUES = ("", " ", " true", "true ", "\ttrue", "yes\n", "enabled", "disabled", "t", "f", "y", "n", "2", "-1", "none")


def function(source: str, name: str) -> str:
    """Extract the actual static function, respecting C strings and comments."""
    match = re.search(r"^static [\w\s*]+\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{", source, re.MULTILINE)
    assert match, name
    opening = source.index("{", match.start())
    depth, position, state = 1, opening + 1, "code"
    while position < len(source):
        char, pair = source[position], source[position : position + 2]
        if state in {"string", "character"}:
            if char == "\\":
                position += 2
                continue
            if char == ('"' if state == "string" else "'"):
                state = "code"
        elif state == "line":
            if char == "\n":
                state = "code"
        elif state == "comment":
            if pair == "*/":
                state = "code"
                position += 2
                continue
        elif pair in {"/*", "//"}:
            state = "comment" if pair == "/*" else "line"
            position += 2
            continue
        elif char in {'"', "'"}:
            state = "string" if char == '"' else "character"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : position + 1]
        position += 1
    raise AssertionError("Unterminated C function: " + name)


INCLUDES = r"""
#define _DEFAULT_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
"""
INCLUDES += '#include "' + str(ROOT / "native/common/ai_cat_process_gate.h") + '"\n'


@pytest.fixture(scope="module")
def native_harnesses(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-motion-inert")
    compiler = shutil.which("cc")
    assert compiler, "A local C compiler is required for native safety regression"
    toy, voice = TOY.read_text(), VOICE.read_text()
    structures = toy[toy.index("static volatile sig_atomic_t motor_stop_requested;") : toy.index("/* Keep this self-contained:")]
    array_start = toy.index("static const struct fixed_rohs_motor k_rohs_motors[]")
    array = toy[array_start : toy.index("\n};", array_start) + 3]
    stubs = r"""
struct motor_dev { int unused; };
struct motor_cmd { int mode; float pos_des; float vel_des; };
struct motor_state { float pos; float vel; float trq; };
enum { MOTOR_MODE_POS, MOTOR_MODE_IDLE };
static int allocations, initializations, commands;
static struct motor_dev inert_motor;
static struct motor_dev *motor_alloc_pwm(const char *name, int channel, void *info) {
    (void)name; (void)channel; (void)info; allocations++; return &inert_motor;
}
static int motor_init_one(struct motor_dev *motor) { (void)motor; initializations++; return 0; }
static void motor_free(struct motor_dev **motor, int count) { (void)motor; (void)count; }
static void motor_set_cmd_one(struct motor_dev *motor, struct motor_cmd *cmd) { (void)motor; (void)cmd; commands++; }
static void motor_get_state_one(struct motor_dev *motor, struct motor_state *state) { (void)motor; memset(state, 0, sizeof(*state)); }
static int inert_usleep(useconds_t delay) { (void)delay; return 0; }
#define usleep inert_usleep
"""
    names = ("motion_env_flag", "motion_rejection_reason", "run_rohs_motor_waypoints", "run_one_rohs_motor", "find_rohs_motor", "known_motor_preset", "motor_preset_requires_tail", "run_proud_pose", "run_motor_preset", "motor_signal_handler", "known_motor_name", "ensure_motor_runtime_directory", "parse_motor_speed", "pid_is_motor_process", "request_motor_stop", "run_motor")
    toy_main = r"""
int main(int argc, char **argv) {
    int result;
    if (argc > 2 && strcmp(argv[1], "driver") == 0)
        result = run_one_rohs_motor(find_rohs_motor(argv[2]), 1.0f);
    else if (argc > 2 && strcmp(argv[1], "direct-preset") == 0)
        result = run_motor_preset(argv[2]);
    else if (argc > 1 && strcmp(argv[1], "direct-proud") == 0)
        result = run_proud_pose(find_rohs_motor("head_lr"), find_rohs_motor("tail_lr"));
    else result = run_motor(argc - 1, argv + 1);
    printf("HARNESS rc=%d alloc=%d init=%d commands=%d\n", result, allocations, initializations, commands);
    return 0;
}
"""
    toy_path = directory / "toy.c"
    toy_path.write_text(INCLUDES + '#define MOTOR_RUNTIME_DIR "' + str(directory / "runtime") + '"\n#define MOTOR_LOCK_PATH MOTOR_RUNTIME_DIR "/motor.lock"\n#define MOTOR_PID_PATH MOTOR_RUNTIME_DIR "/motor.pid"\n' + structures + array + stubs + "\n".join(function(toy, name) for name in names) + toy_main)
    voice_stubs = r"""
#include <spawn.h>
#include <sys/wait.h>
extern char **environ;
static int spawns;
static bool process_gate_closing, clean_exit_requested, exit_request, running, ai_playing, discard_playback_audio;
static struct ai_cat_process_gate process_gate = {.root_fd = -1, .work_fd = -1};
static int inert_spawn(pid_t *pid, const char *path, const posix_spawn_file_actions_t *actions,
    const posix_spawnattr_t *attributes, char *const argv[], char *const env[]) {
    (void)actions; (void)attributes; (void)env;
    if (strcmp(path, "/usr/bin/ai-toy_app") || strcmp(argv[1], "motor")) abort();
    *pid = 123; spawns++; return 0;
}
static pid_t inert_waitpid(pid_t pid, int *status, int options) { (void)options; *status = 0; return pid; }
#define posix_spawn inert_spawn
#define waitpid inert_waitpid
"""
    voice_path = directory / "voice.c"
    voice_path.write_text(INCLUDES + voice_stubs + "\n".join(function(voice, name) for name in ("__process_gate_allows_effect", "__motion_env_flag", "__motion_rejection_reason", "__vendor_smooth_motion_enabled", "__motor_motion_speed", "__run_motor_motion")) + r"""
int main(int argc, char **argv) {
    const char *actuator = argc > 1 ? argv[1] : "head_lr";
    int result = ai_cat_process_gate_enter(&process_gate);
    if (argc > 2 && strcmp(argv[2], "hold") == 0 && result == 0) {
        printf("READY\n"); fflush(stdout); (void)getchar();
    }
    if (result == 0) result = __run_motor_motion(actuator, __motor_motion_speed(actuator));
    printf("HARNESS rc=%d spawn=%d\n", result, spawns);
    return 0;
}
""")
    executables = {}
    for kind, source in (("toy", toy_path), ("voice", voice_path)):
        executable = directory / kind
        built = subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(executable)], capture_output=True, text=True, timeout=15)
        assert built.returncode == 0, built.stderr
        executables[kind] = executable
    return executables


def run(harnesses, kind, args, flags=None):
    environment = {key: value for key, value in os.environ.items() if key not in FLAGS and key not in {"AI_CAT_MOTION_PROFILE", "AI_CAT_PROCESS_GATE_PATH"}}
    environment.update(flags or {})
    result = subprocess.run([str(harnesses[kind]), *args], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    marker = result.stdout.rsplit("HARNESS ", 1)[1]
    return {key: int(value) for key, value in re.findall(r"(\w+)=(-?\d+)", marker)}


@pytest.mark.parametrize("kind", ["toy", "voice"])
@pytest.mark.parametrize("value", TRUE_VALUES)
def test_native_true_synonyms_preserve_existing_head_and_tail_dispatch(native_harnesses, kind, value):
    flags = {key: value for key in FLAGS}
    for actuator in ("head_lr", "head_ud", "tail_lr"):
        result = run(native_harnesses, kind, [actuator], flags)
        assert result["rc"] == 0
        assert result["alloc" if kind == "toy" else "spawn"] == 1


@pytest.mark.parametrize("kind", ["toy", "voice"])
@pytest.mark.parametrize("value", FALSE_VALUES)
def test_native_false_motion_synonyms_never_initialize_or_spawn(native_harnesses, kind, value):
    for actuator in ("head_lr", "head_ud", "tail_lr"):
        result = run(native_harnesses, kind, [actuator], {"AI_CAT_ENABLE_MOTION": value, "AI_CAT_ENABLE_TAIL_MOTION": "true"})
        assert result["rc"] != 0
        assert result["alloc" if kind == "toy" else "spawn"] == 0


@pytest.mark.parametrize("kind", ["toy", "voice"])
@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("value", INVALID_VALUES)
def test_native_invalid_explicit_flag_fails_closed_even_for_head(native_harnesses, kind, flag, value):
    flags = {key: "true" for key in FLAGS}
    flags[flag] = value
    result = run(native_harnesses, kind, ["head_lr"], flags)
    assert result["rc"] != 0
    assert result["alloc" if kind == "toy" else "spawn"] == 0


@pytest.mark.parametrize("kind", ["toy", "voice"])
def test_native_missing_defaults_keep_head_but_not_tail(native_harnesses, kind):
    head = run(native_harnesses, kind, ["head_lr"])
    tail = run(native_harnesses, kind, ["tail_lr"])
    assert head["rc"] == 0 and head["alloc" if kind == "toy" else "spawn"] == 1
    assert tail["rc"] != 0 and tail["alloc" if kind == "toy" else "spawn"] == 0


@pytest.mark.parametrize("kind", ["toy", "voice"])
@pytest.mark.parametrize("value", FALSE_VALUES)
def test_native_no_tail_does_not_disable_head_but_cannot_wag(native_harnesses, kind, value):
    flags = {"AI_CAT_HAS_TAIL": value, "AI_CAT_ENABLE_TAIL_MOTION": "true"}
    head = run(native_harnesses, kind, ["head_ud"], flags)
    tail = run(native_harnesses, kind, ["tail_lr"], flags)
    assert head["rc"] == 0
    assert tail["rc"] != 0 and tail["alloc" if kind == "toy" else "spawn"] == 0


@pytest.mark.parametrize("kind", ["toy", "voice"])
@pytest.mark.parametrize("value", FALSE_VALUES)
def test_native_original_tail_enable_gate_still_applies(native_harnesses, kind, value):
    flags = {"AI_CAT_ENABLE_MOTION": "true", "AI_CAT_HAS_TAIL": "true", "AI_CAT_ENABLE_TAIL_MOTION": value}
    head = run(native_harnesses, kind, ["head_lr"], flags)
    tail = run(native_harnesses, kind, ["tail_lr"], flags)
    assert head["rc"] == 0
    assert tail["rc"] != 0 and tail["alloc" if kind == "toy" else "spawn"] == 0


@pytest.mark.parametrize("args", [["all"], ["preset", "proud_pose"], ["preset", "greeting_combo"], ["preset", "celebration_combo"], ["direct-preset", "proud_pose"], ["direct-preset", "greeting_combo"], ["direct-preset", "celebration_combo"], ["direct-proud"], ["driver", "tail_lr"]])
def test_no_tail_rejects_entire_combo_before_any_head_or_tail_initialization(native_harnesses, args):
    result = run(native_harnesses, "toy", args, {"AI_CAT_HAS_TAIL": "false", "AI_CAT_ENABLE_TAIL_MOTION": "true"})
    assert result["rc"] != 0 and result["alloc"] == 0 and result["init"] == 0 and result["commands"] == 0


@pytest.mark.parametrize("args", [["all"], ["head_lr"], ["head_ud"], ["tail_lr"], ["preset", "conversation_tilt"], ["preset", "conversation_nod"], ["preset", "quiet_companion"], ["preset", "proud_pose"], ["preset", "greeting_combo"], ["preset", "celebration_combo"], ["driver", "head_lr"], ["direct-preset", "quiet_companion"], ["direct-proud"]])
def test_disabled_motion_blocks_native_entrypoints_before_driver_init(native_harnesses, args):
    result = run(native_harnesses, "toy", args, {"AI_CAT_ENABLE_MOTION": "false", "AI_CAT_ENABLE_TAIL_MOTION": "true"})
    assert result["rc"] != 0 and result["alloc"] == 0 and result["init"] == 0 and result["commands"] == 0


@pytest.mark.parametrize("args,expected_motors,expected_commands", [
    (["all"], 3, 12), (["preset", "conversation_tilt"], 1, 3),
    (["preset", "conversation_nod"], 1, 3), (["preset", "quiet_companion"], 1, 3),
    (["preset", "proud_pose"], 2, 7), (["preset", "greeting_combo"], 2, 8),
    (["preset", "celebration_combo"], 3, 12),
])
def test_enabled_tail_devices_keep_existing_fixed_sequence_dispatch(native_harnesses, args, expected_motors, expected_commands):
    result = run(native_harnesses, "toy", args, {key: "true" for key in FLAGS})
    assert result["rc"] == 0 and result["alloc"] == expected_motors
    assert result["init"] == expected_motors and result["commands"] == expected_commands


@pytest.mark.parametrize("flags", [{"AI_CAT_ENABLE_MOTION": "false"}, {"AI_CAT_HAS_TAIL": "false"}, {key: "invalid" for key in FLAGS}, {}])
def test_native_stop_remains_available_even_with_invalid_or_disabled_configuration(native_harnesses, flags):
    result = run(native_harnesses, "toy", ["stop"], flags)
    assert result["rc"] == 0 and result["alloc"] == 0


def test_voice_tool_gate_precedes_personality_lock_cooldown_and_motor_dispatch():
    worker = function(VOICE.read_text(), "__run_function_call")
    assert worker.index("__motion_rejection_reason(is_tail)") < worker.index("__personality_allows_motion(")
    assert worker.index("__motion_rejection_reason(is_tail)") < worker.index("pthread_mutex_trylock(")
    assert worker.index("__motion_rejection_reason(is_tail)") < worker.index("__run_motor_motion(actuator, speed)")
