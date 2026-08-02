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

    assert "value < 1 || value > 2" in source
    assert 'strcmp(cursor, "motor") != 0' in source
    assert "pid_is_motor_process(pid)" in source
