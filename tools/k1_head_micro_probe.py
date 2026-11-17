"""Explicit, device-scoped head diagnostics; never an API or automatic action.

Usage on device 7c2b63fd4a120:
    python3 k1_head_micro_probe.py head_lr|head_ud|head_lr_100
The explicitly approved head_lr_100 mode is left/right only. Default probes
remain 40 pulses; no numeric parameters or larger/up-down modes are accepted.
Requires operator consent/observation, disabled controller motion, and factory
motor service stopped. Does not trust or write cached positions/calibration.
"""

from __future__ import annotations

import ctypes
import fcntl
import json
from pathlib import Path
import signal
from statistics import median
import subprocess
import sys
import time

DEVICE_SERIAL = "7c2b63fd4a120"
PULSES_PER_LEG = 40
PULSE_SECONDS = 0.001
AXES = {
    "head_lr": {"step": 46, "direction": 43, "enable": 42, "limit": 83, "adc": 2},
    "head_ud": {"step": 34, "direction": 35, "enable": 36, "limit": 82, "adc": 5},
}
PROBE_MODES = {
    "head_lr": ("head_lr", PULSES_PER_LEG),
    "head_ud": ("head_ud", PULSES_PER_LEG),
    "head_lr_100": ("head_lr", 100),
}


def resolve_probe_mode(argv):
    if len(argv) != 1 or argv[0] not in PROBE_MODES:
        raise RuntimeError("Only fixed head probe modes are accepted; no tunable parameters")
    return PROBE_MODES[argv[0]]


def probe_roundtrip(io, *, stopped, mode="head_lr", pause=time.sleep, observe=lambda stage: None):
    """Fail closed; no limit escape, calibration, full sweep or center seeking."""
    _, pulses_per_leg = resolve_probe_mode([mode])
    try:
        io.set("enable", 1)
        if io.get("limit") != 1 or stopped():
            raise RuntimeError("Limit active/unreadable or stop requested before probe")
        observe("before")
        for direction, stage in ((1, "outward"), (0, "returned")):
            if io.get("limit") != 1 or stopped():
                raise RuntimeError("Probe stopped before enabling next leg")
            io.set("direction", direction)
            io.set("enable", 0)
            pause(0.02)
            if stage == "outward":
                observe("energized_before")
            for _ in range(pulses_per_leg):
                if stopped() or io.get("limit") != 1:
                    raise RuntimeError("Probe stopped: signal or active/unreadable limit")
                io.set("step", 1)
                pause(PULSE_SECONDS)
                io.set("step", 0)
                pause(PULSE_SECONDS)
            observe(stage)
            io.set("enable", 1)
            pause(0.1)
    finally:
        try:
            io.set("enable", 1)
        finally:
            io.set("step", 0)


class GpioV1:
    """Request enable disabled first; release every owned line on failure."""

    def __init__(self, axis):
        self.lib = ctypes.CDLL("libgpiod.so.2", use_errno=True)
        signatures = {
            "gpiod_chip_open_by_name": ([ctypes.c_char_p], ctypes.c_void_p),
            "gpiod_chip_get_line": ([ctypes.c_void_p, ctypes.c_uint], ctypes.c_void_p),
            "gpiod_line_request_input": ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_int),
            "gpiod_line_request_output": (
                [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int,
            ),
            "gpiod_line_get_value": ([ctypes.c_void_p], ctypes.c_int),
            "gpiod_line_set_value": ([ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            "gpiod_line_release": ([ctypes.c_void_p], None),
            "gpiod_chip_close": ([ctypes.c_void_p], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = arguments, result
        self.chip = self.lib.gpiod_chip_open_by_name(b"gpiochip0")
        self.lines = {}
        if not self.chip:
            raise RuntimeError("Cannot open confirmed GPIO chip")
        try:
            for name, initial in (("limit", None), ("enable", 1), ("step", 0), ("direction", 1)):
                line = self.lib.gpiod_chip_get_line(self.chip, axis[name])
                if not line:
                    raise RuntimeError(f"Cannot obtain {name} GPIO")
                if initial is None:
                    result = self.lib.gpiod_line_request_input(line, b"ai-cat-micro-probe")
                else:
                    result = self.lib.gpiod_line_request_output(line, b"ai-cat-micro-probe", initial)
                if result != 0:
                    raise RuntimeError(f"Cannot exclusively request {name} GPIO")
                self.lines[name] = line
        except BaseException:
            self.close()
            raise

    def get(self, name):
        value = self.lib.gpiod_line_get_value(self.lines[name])
        if value not in (0, 1):
            raise RuntimeError(f"Cannot read {name} GPIO")
        return value

    def set(self, name, value):
        if self.lib.gpiod_line_set_value(self.lines[name], value) != 0:
            raise RuntimeError(f"Cannot set {name} GPIO")

    def close(self):
        try:
            if "enable" in self.lines:
                self.set("enable", 1)
        finally:
            for line in reversed(list(self.lines.values())):
                self.lib.gpiod_line_release(line)
            self.lines.clear()
            if self.chip:
                self.lib.gpiod_chip_close(self.chip)
                self.chip = None


def main(argv):
    axis_name, pulses_per_leg = resolve_probe_mode(argv)
    serial = Path("/proc/device-tree/serial-number").read_bytes().rstrip(b"\0").decode()
    if serial != DEVICE_SERIAL:
        raise RuntimeError("Wrong device; do not reuse another device's acceptance")
    env = Path("/etc/ai-cat-controller.env").read_text().splitlines()
    if [line for line in env if line.startswith("AI_CAT_ENABLE_MOTION=")] != ["AI_CAT_ENABLE_MOTION=false"]:
        raise RuntimeError("Controller motion must remain disabled")
    motor_state = subprocess.run(
        ["systemctl", "is-active", "toy_motor.service"],
        capture_output=True, text=True, check=False, timeout=5,
    )
    if motor_state.returncode != 3 or motor_state.stdout.strip() != "inactive":
        raise RuntimeError("Factory/autonomous motor consumer must be stopped")

    stop = False

    def request_stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    axis = AXES[axis_name]
    adc_path = Path(f"/sys/bus/iio/devices/iio:device1/in_voltage{axis['adc']}_raw")

    def observe(stage):
        values = []
        for _ in range(9):
            value = int(adc_path.read_text())
            if not 0 <= value <= 4095:
                raise RuntimeError("Position readout unavailable")
            values.append(value)
            time.sleep(0.01)
        print(json.dumps({
            "axis": axis_name, "stage": stage, "adc_median": median(values),
            "adc_min": min(values), "adc_max": max(values),
            "pulses_per_leg": pulses_per_leg,
            "probe_mode": argv[0],
            "calibration_used": False,
        }), flush=True)

    with Path("/run/ai-cat/motor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        io = GpioV1(axis)
        try:
            probe_roundtrip(io, stopped=lambda: stop, mode=argv[0], observe=observe)
        finally:
            io.close()
    print("probe_finished; motor_disabled=true; calibration_unchanged=true", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
