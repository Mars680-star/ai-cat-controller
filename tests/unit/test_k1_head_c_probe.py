import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def guard_binary(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("A C compiler is required for native diagnostic guard tests")
    directory = tmp_path_factory.mktemp("head-c-guard")
    source = directory / "guard.c"
    source.write_text(r'''
#include <assert.h>
#include <string.h>
#include "k1_head_probe_guard.h"
int main(int argc, char **argv) {
    assert(argc == 2);
    struct head_probe_guard guard = head_probe_guard_init();
    if (strcmp(argv[1], "premature-return") == 0) {
        assert(!head_probe_begin_leg(&guard, 1, 1, false));
        return 0;
    }
    assert(head_probe_begin_leg(&guard, 0, 1, false));
    if (strcmp(argv[1], "missing-direction") == 0) {
        assert(!head_probe_step(&guard, 1, false));
        return 0;
    }
    assert(head_probe_direction(&guard, 0));
    if (strcmp(argv[1], "limit") == 0) {
        assert(!head_probe_step(&guard, 0, false));
    } else if (strcmp(argv[1], "unreadable") == 0) {
        assert(!head_probe_step(&guard, -1, false));
    } else if (strcmp(argv[1], "stop") == 0) {
        assert(!head_probe_step(&guard, 1, true));
    } else if (strcmp(argv[1], "invalid-leg") == 0) {
        guard.leg = 2;
        assert(!head_probe_step(&guard, 1, false));
    } else {
        for (unsigned int i = 0; i < 215; i++)
            assert(head_probe_step(&guard, 1, false));
        if (strcmp(argv[1], "budget") == 0) {
            assert(!head_probe_step(&guard, 1, false));
        } else {
            assert(head_probe_begin_leg(&guard, 1, 1, false));
            if (strcmp(argv[1], "same-direction") == 0) {
                assert(!head_probe_direction(&guard, 0));
            } else {
                assert(head_probe_direction(&guard, 1));
                for (unsigned int i = 0; i < 215; i++)
                    assert(head_probe_step(&guard, 1, false));
                assert(guard.pulses[0] + guard.pulses[1] == 430);
                assert(!head_probe_step(&guard, 1, false));
            }
        }
    }
    assert(guard.failed);
    assert(!head_probe_step(&guard, 1, false));
    assert(guard.pulses[0] <= 215 && guard.pulses[1] <= 215);
    return 0;
}
''', encoding="utf-8")
    binary = directory / "guard"
    subprocess.run([
        compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "tools"), str(source), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("case", [
    "roundtrip", "budget", "limit", "unreadable", "stop", "invalid-leg",
    "missing-direction", "premature-return", "same-direction",
])
def test_native_guard_rejects_unsafe_steps(guard_binary, case):
    subprocess.run([str(guard_binary), case], check=True, timeout=5)


def test_native_probe_is_isolated_and_preserves_calibration():
    source = (ROOT / "tools/k1_head_c_probe.c").read_text()
    assert '#define PROBE_SERIAL "7c2b63fd4a120"' in source
    assert 'if (plan)\n        return 0;' in source
    assert 'motor_alloc_pwm("pwm_RoHS", 0, &info)' in source
    assert 'leg == 0 ? 91.0f : 90.0f' in source
    assert 'head_probe_step(&guard, current_limit(), stopped != 0)' in source
    assert 'head_probe_begin_leg(&guard, leg, current_limit(), stopped != 0)' in source
    assert 'flock(lock, LOCK_EX | LOCK_NB)' in source
    assert 'O_EXCL | O_NOFOLLOW' in source
    assert '__wrap_fopen' in source
    assert '__wrap_fclose' in source
    assert 'cache_writing ? sizeof(cache_buffer) : strlen(cache_buffer)' in source
    assert 'read_disk_int(paths[i], &value)' in source
    assert '__wrap_gpiod_line_release' in source
    assert '__wrap_gpiod_chip_close' in source
    assert 'alarm(5)' in source
    assert 'AI_CAT_ENABLE_MOTION=false' in source
    assert 'motor preset' not in source
    assert '!wrappers_linked()' in source


@pytest.fixture(scope="module")
def wrapper_binary(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("A C compiler is required for native diagnostic wrapper tests")
    directory = tmp_path_factory.mktemp("head-c-wrappers")
    (directory / "gpiod.h").write_text(r'''
#ifndef FAKE_GPIOD_H
#define FAKE_GPIOD_H
struct gpiod_line { unsigned int offset; int value; };
struct gpiod_chip { int unused; };
unsigned int gpiod_line_offset(struct gpiod_line *);
int gpiod_line_get_value(struct gpiod_line *);
struct gpiod_chip *gpiod_chip_open_by_name(const char *);
int gpiod_line_request_output(struct gpiod_line *, const char *, int);
int gpiod_line_request_input(struct gpiod_line *, const char *);
int gpiod_line_set_value(struct gpiod_line *, int);
void gpiod_line_release(struct gpiod_line *);
void gpiod_chip_close(struct gpiod_chip *);
#endif
''')
    (directory / "motor.h").write_text(r'''
#ifndef FAKE_MOTOR_H
#define FAKE_MOTOR_H
#include <stdint.h>
struct motor_dev { int unused; };
enum { MOTOR_MODE_IDLE = 0, MOTOR_MODE_POS = 1 };
struct motor_cmd { uint32_t mode; float pos_des, vel_des, trq_des, kp, kd; };
struct motor_dev *motor_alloc_pwm(const char *, uint32_t, void *);
int motor_init_one(struct motor_dev *);
int motor_set_cmd_one(struct motor_dev *, const struct motor_cmd *);
void motor_free(struct motor_dev **, uint32_t);
#endif
''')
    source = directory / "wrappers.c"
    source.write_text(r'''
#define main diagnostic_main
#include "k1_head_c_probe.c"
#undef main
#include <assert.h>
static unsigned int actual_highs, requests, file_opens;
static int fail_request_offset = -1;
static struct gpiod_chip fake_chip;
struct gpiod_chip *gpiod_chip_open_by_name(const char *name) { (void)name; return NULL; }
int gpiod_line_request_output(struct gpiod_line *line, const char *name, int value) {
    (void)line; (void)name; (void)value; return -1;
}
int gpiod_line_request_input(struct gpiod_line *line, const char *name) {
    (void)line; (void)name; return -1;
}
int gpiod_line_set_value(struct gpiod_line *line, int value) { (void)line; (void)value; return -1; }
void gpiod_line_release(struct gpiod_line *line) { (void)line; }
void gpiod_chip_close(struct gpiod_chip *chip) { (void)chip; }
unsigned int gpiod_line_offset(struct gpiod_line *line) { return line->offset; }
int gpiod_line_get_value(struct gpiod_line *line) { return line->value; }
int __real_gpiod_line_set_value(struct gpiod_line *line, int value) {
    line->value = value;
    if (line->offset == 34 && value == 1) actual_highs++;
    return 0;
}
struct gpiod_chip *__real_gpiod_chip_open_by_name(const char *name) {
    (void)name; return &fake_chip;
}
int __real_gpiod_line_request_output(struct gpiod_line *line, const char *name, int value) {
    (void)name; requests++;
    if ((int)line->offset == fail_request_offset) return -1;
    line->value = value; return 0;
}
int __real_gpiod_line_request_input(struct gpiod_line *line, const char *name) {
    (void)line; (void)name; requests++; return 0;
}
void __real_gpiod_line_release(struct gpiod_line *line) { (void)line; }
void __real_gpiod_chip_close(struct gpiod_chip *chip) { (void)chip; }
int __real_usleep(useconds_t duration) { (void)duration; return 0; }
FILE *__real_fopen(const char *path, const char *mode) { file_opens++; return fopen(path, mode); }
int __real_fclose(FILE *file) { return fclose(file); }
struct motor_dev *motor_alloc_pwm(const char *name, uint32_t channel, void *info) {
    (void)name; (void)channel; (void)info; return NULL;
}
int motor_init_one(struct motor_dev *motor) { (void)motor; return -1; }
int motor_set_cmd_one(struct motor_dev *motor, const struct motor_cmd *command) {
    (void)motor; (void)command; return -1;
}
void motor_free(struct motor_dev **motor, uint32_t count) { (void)motor; (void)count; }
int main(int argc, char **argv) {
    assert(argc == 2);
    if (strcmp(argv[1], "missing-link-flags") == 0) {
        assert(!wrappers_linked());
        return 0;
    }
    guard = head_probe_guard_init();
    struct gpiod_line step = {34, 0}, direction = {35, 0}, enable = {36, 1}, limit = {82, 1};
    if (strcmp(argv[1], "wrong-pin") == 0) {
        struct gpiod_line foreign = {46, 0};
        assert(__wrap_gpiod_line_request_output(&foreign, "test", 0) == -1);
        assert(requests == 0 && actual_highs == 0);
        return 0;
    }
    if (strcmp(argv[1], "cache") == 0 || strcmp(argv[1], "bad-cache") == 0) {
        FILE *file = __wrap_fopen(ANGLE_CACHE, "r");
        assert(file != NULL);
        int value;
        assert(fscanf(file, "%d", &value) == 1 && value == 90);
        assert(__wrap_fclose(file) == 0);
        file = __wrap_fopen(ANGLE_CACHE, "w");
        assert(file != NULL);
        fprintf(file, "%d", strcmp(argv[1], "cache") == 0 ? 91 : 100);
        assert(__wrap_fclose(file) == 0);
        assert(file_opens == 0);
        assert(virtual_angle == (guard.failed ? 90 : 91));
        return 0;
    }
    if (strcmp(argv[1], "forbidden-write") == 0) {
        assert(__wrap_fopen("/root/.motor_2_max_steps", "w") == NULL);
        assert(file_opens == 0 && guard.failed);
        return 0;
    }
    assert(__wrap_gpiod_line_request_output(&step, "test", 0) == 0);
    if (strcmp(argv[1], "partial-init") == 0) {
        fail_request_offset = 36;
        assert(__wrap_gpiod_line_request_output(&enable, "test", 0) == -1);
        __wrap_gpiod_chip_close(&fake_chip);
        assert(step_line == NULL && enable_line == NULL && actual_highs == 0);
        return 0;
    }
    assert(__wrap_gpiod_line_request_output(&enable, "test", 0) == 0);
    assert(enable.value == 1); /* init cannot energize */
    assert(__wrap_gpiod_line_request_input(&limit, "test") == 0);
    if (strcmp(argv[1], "release") == 0) {
        __wrap_gpiod_line_release(&enable);
        __wrap_gpiod_line_release(&step);
        __wrap_gpiod_line_release(&limit);
        assert(enable_line == NULL && step_line == NULL && limit_line == NULL);
        return 0;
    }
    if (strcmp(argv[1], "stop") == 0) stopped = 1;
    if (strcmp(argv[1], "limit") == 0) limit.value = 0;
    if (stopped || limit.value == 0) {
        assert(__wrap_gpiod_line_set_value(&enable, 0) == -1);
        assert(enable.value == 1 && step.value == 0 && actual_highs == 0);
        return 0;
    }
    for (unsigned int leg = 0; leg < 2; leg++) {
        assert(head_probe_begin_leg(&guard, leg, 1, false));
        assert(__wrap_gpiod_line_set_value(&enable, 0) == 0);
        assert(__wrap_gpiod_line_set_value(&direction, (int)leg) == 0);
        for (unsigned int pulse = 0; pulse < 215; pulse++) {
            assert(__wrap_gpiod_line_set_value(&step, 1) == 0);
            assert(__wrap_gpiod_line_set_value(&step, 0) == 0);
        }
        if (strcmp(argv[1], "overbudget") == 0) {
            assert(__wrap_gpiod_line_set_value(&step, 1) == -1);
            assert(actual_highs == 215 && enable.value == 1 && step.value == 0);
            return 0;
        }
        assert(__wrap_gpiod_line_set_value(&enable, 1) == 0);
    }
    assert(actual_highs == 430 && !guard.failed && enable.value == 1 && step.value == 0);
    return 0;
}
''', encoding="utf-8")
    binary = directory / "wrappers"
    subprocess.run([
        compiler, "-std=c11", "-O0", "-Wall", "-Wextra", "-Werror",
        "-I", str(directory), "-I", str(ROOT / "tools"), str(source), "-o", str(binary),
    ], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("case", [
    "normal", "overbudget", "limit", "stop", "wrong-pin", "partial-init",
    "release", "cache", "forbidden-write", "bad-cache", "missing-link-flags",
])
def test_native_wrappers_fail_closed_without_hardware(wrapper_binary, case):
    subprocess.run([str(wrapper_binary), case], check=True, timeout=5)
