import runpy
from pathlib import Path

import pytest

PROBE = runpy.run_path(str(Path(__file__).parents[2] / "tools/k1_head_micro_probe.py"))


class FakeGpio:
    def __init__(self, limit=1):
        self.limit = limit
        self.values = {}
        self.calls = []

    def get(self, name):
        assert name == "limit"
        return self.limit

    def set(self, name, value):
        self.values[name] = value
        self.calls.append((name, value))


def test_probe_has_fixed_tiny_roundtrip_and_disables_motor():
    io = FakeGpio()
    stages = []
    PROBE["probe_roundtrip"](
        io, stopped=lambda: False, pause=lambda _: None, observe=stages.append,
    )
    assert stages == ["before", "energized_before", "outward", "returned"]
    assert io.calls.count(("step", 1)) == 80
    assert [value for name, value in io.calls if name == "direction"] == [1, 0]
    assert io.values["enable"] == 1
    assert io.values["step"] == 0


@pytest.mark.parametrize("limit", [0, -1])
def test_probe_never_moves_at_active_or_unreadable_limit(limit):
    io = FakeGpio(limit)
    with pytest.raises(RuntimeError, match="before probe"):
        PROBE["probe_roundtrip"](io, stopped=lambda: False, pause=lambda _: None)
    assert ("step", 1) not in io.calls
    assert ("enable", 0) not in io.calls
    assert io.values["enable"] == 1


def test_probe_stops_at_first_pulse_after_limit_changes():
    io = FakeGpio()

    def pause(_):
        if ("step", 1) in io.calls:
            io.limit = 0

    with pytest.raises(RuntimeError, match="Probe stopped"):
        PROBE["probe_roundtrip"](io, stopped=lambda: False, pause=pause)
    assert io.calls.count(("step", 1)) == 1
    assert io.values["enable"] == 1
    assert io.values["step"] == 0


def test_probe_disables_motor_on_stop_or_observation_failure():
    io = FakeGpio()

    def fail(stage):
        if stage == "outward":
            raise OSError("ADC unavailable")

    with pytest.raises(OSError):
        PROBE["probe_roundtrip"](
            io, stopped=lambda: False, pause=lambda _: None, observe=fail,
        )
    assert io.calls.count(("step", 1)) == 40
    assert io.values["enable"] == 1
    assert io.values["step"] == 0
    io = FakeGpio()
    with pytest.raises(RuntimeError):
        PROBE["probe_roundtrip"](io, stopped=lambda: True, pause=lambda _: None)
    assert ("step", 1) not in io.calls


def test_approved_left_right_mode_is_bounded_to_100_pulses_per_leg():
    assert PROBE["resolve_probe_mode"](["head_lr_100"]) == ("head_lr", 100)
    assert PROBE["resolve_probe_mode"](["head_ud"]) == ("head_ud", 40)
    io = FakeGpio()
    PROBE["probe_roundtrip"](
        io, stopped=lambda: False, mode="head_lr_100", pause=lambda _: None,
    )
    assert io.calls.count(("step", 1)) == 200
    assert [value for name, value in io.calls if name == "direction"] == [1, 0]
    assert io.values["enable"] == 1
    assert io.values["step"] == 0


@pytest.mark.parametrize("argv", [
    [], ["head_ud_100"], ["tail_lr"], ["head_lr_100", "200"], ["200"],
])
def test_probe_rejects_unapproved_axes_counts_and_extra_arguments(argv):
    with pytest.raises(RuntimeError, match="fixed head probe modes"):
        PROBE["resolve_probe_mode"](argv)


def test_invalid_probe_mode_does_not_request_motion():
    io = FakeGpio()
    with pytest.raises(RuntimeError, match="fixed head probe modes"):
        PROBE["probe_roundtrip"](io, stopped=lambda: False, mode="head_ud_100")
    assert io.calls == []


@pytest.mark.parametrize("stop_kind", ["limit", "signal"])
def test_100_pulse_mode_stops_without_returning_after_stop_request(stop_kind):
    io = FakeGpio()
    stop = False

    def pause(_):
        nonlocal stop
        if io.calls.count(("step", 1)) == 3:
            if stop_kind == "limit":
                io.limit = 0
            else:
                stop = True

    with pytest.raises(RuntimeError, match="Probe stopped"):
        PROBE["probe_roundtrip"](
            io, stopped=lambda: stop, mode="head_lr_100", pause=pause,
        )
    assert io.calls.count(("step", 1)) == 3
    assert [value for name, value in io.calls if name == "direction"] == [1]
    assert io.values["enable"] == 1
    assert io.values["step"] == 0


def test_100_pulse_mode_disables_motor_after_observation_failure():
    io = FakeGpio()

    def fail(stage):
        if stage == "outward":
            raise OSError("ADC unavailable")

    with pytest.raises(OSError):
        PROBE["probe_roundtrip"](
            io, stopped=lambda: False, mode="head_lr_100", pause=lambda _: None,
            observe=fail,
        )
    assert io.calls.count(("step", 1)) == 100
    assert io.values["enable"] == 1
    assert io.values["step"] == 0
