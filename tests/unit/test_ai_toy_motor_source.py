from pathlib import Path


SOURCE_PATH = Path(__file__).parents[2] / "native/ai-toy-app/src/main.c"


def test_motor_source_has_serial_lock_and_graceful_stop() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert 'MOTOR_RUNTIME_DIR "/run/ai-cat"' in source
    assert 'MOTOR_LOCK_PATH MOTOR_RUNTIME_DIR "/motor.lock"' in source
    assert 'MOTOR_PID_PATH MOTOR_RUNTIME_DIR "/motor.pid"' in source
    assert "lstat(MOTOR_RUNTIME_DIR, &info)" in source
    assert "flock(lock_fd, LOCK_EX | LOCK_NB)" in source
    assert "sigaction(SIGTERM, &action, NULL)" in source
    assert "cmd.mode = MOTOR_MODE_IDLE" in source
    assert 'strcmp(which, "stop") == 0' in source


def test_motor_source_restricts_speed_and_validates_stop_pid() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert "value < 1 || value > 3" in source
    assert 'strcmp(cursor, "motor") != 0' in source
    assert "pid_is_motor_process(pid)" in source


def test_motor_source_uses_validated_head_and_provisional_tail_mappings() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    head_ud = source.split('"head_ud"', maxsplit=1)[1].split("},\n    },", maxsplit=1)[0]
    assert ".step_gpio = 34" in head_ud
    assert ".motor_index = 2" in head_ud
    assert ".dir_gpio = 35" in head_ud
    assert ".enable_gpio = 36" in head_ud
    assert ".stop_gpio = 82" in head_ud
    assert ".constant_range = 20" in head_ud
    tail_lr = source.split('"tail_lr"', maxsplit=1)[1].split("},\n    },", maxsplit=1)[0]
    assert ".step_gpio = 37" in tail_lr
    assert ".motor_index = 3" in tail_lr
    assert ".dir_gpio = 38" in tail_lr
    assert ".enable_gpio = 39" in tail_lr
    assert ".stop_gpio = 61" in tail_lr
    assert ".constant_range = 50" in tail_lr

    head_lr = source.split('"head_lr"', maxsplit=1)[1].split("},\n    },", maxsplit=1)[0]
    assert ".constant_range = 30" in head_lr
    assert "{180.0f, 0.0f, 90.0f}" in source
    assert "speed >= 3.0f ? 100000U : 500000U" in source
    assert "75.0f" not in source
    assert "105.0f" not in source
