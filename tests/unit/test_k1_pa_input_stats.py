import json
import math
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
PREFIX = "AI_CAT_PA_INPUT_STATS "


MOCK_HEADER = r'''
#ifndef MOCK_PA_H
#define MOCK_PA_H
#include <stddef.h>
typedef void PaStream;
typedef int PaError;
typedef unsigned long PaSampleFormat;
typedef unsigned long PaStreamFlags;
typedef unsigned long PaStreamCallbackFlags;
typedef struct {
    int device;
    int channelCount;
    PaSampleFormat sampleFormat;
    double suggestedLatency;
    void *hostApiSpecificStreamInfo;
} PaStreamParameters;
typedef struct {
    double inputBufferAdcTime;
    double currentTime;
    double outputBufferDacTime;
} PaStreamCallbackTimeInfo;
typedef int PaStreamCallback(const void *, void *, unsigned long,
                             const PaStreamCallbackTimeInfo *,
                             PaStreamCallbackFlags, void *);
PaError Pa_OpenStream(PaStream **, const PaStreamParameters *,
                      const PaStreamParameters *, double, unsigned long,
                      PaStreamFlags, PaStreamCallback *, void *);
PaError Pa_CloseStream(PaStream *);
int MockFire(PaStream *, const void *, void *, unsigned long,
             const PaStreamCallbackTimeInfo *, PaStreamCallbackFlags);
void MockAdvance(double);
#endif
'''


MOCK_LIBRARY = r'''
#define _GNU_SOURCE
#include "mock_pa.h"
#include <assert.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

struct mock_stream { PaStreamCallback *callback; void *user; int closed; };
static struct mock_stream streams[16];
static unsigned int allocated;
static int failed_close;
static _Atomic long long fake_ns = 1000000000000LL;

int clock_gettime(clockid_t clock, struct timespec *value) {
    if (clock == CLOCK_MONOTONIC) {
        long long now = atomic_load(&fake_ns);
        value->tv_sec = (time_t)(now / 1000000000LL);
        value->tv_nsec = (long)(now % 1000000000LL);
        return 0;
    }
    return (int)syscall(SYS_clock_gettime, clock, value);
}

void MockAdvance(double seconds) {
    atomic_fetch_add(&fake_ns, (long long)(seconds * 1000000000.0));
}

PaError Pa_OpenStream(PaStream **stream, const PaStreamParameters *input,
                     const PaStreamParameters *output, double rate,
                     unsigned long frames, PaStreamFlags flags,
                     PaStreamCallback *callback, void *user) {
    (void)input; (void)output;
    assert(rate == 4.0);
    assert(frames == 4);
    assert(flags == 0x1234UL);
    if (getenv("MOCK_OPEN_FAIL")) return -17;
    assert(allocated < 16);
    streams[allocated].callback = callback;
    streams[allocated].user = user;
    streams[allocated].closed = 0;
    *stream = &streams[allocated++];
    return 0;
}

PaError Pa_CloseStream(PaStream *opaque) {
    struct mock_stream *stream = opaque;
    assert(!stream->closed);
    if (getenv("MOCK_CLOSE_FAIL_FIRST") && !failed_close++) return -19;
    stream->closed = 1;
    return 0;
}

int MockFire(PaStream *opaque, const void *input, void *output,
             unsigned long frames, const PaStreamCallbackTimeInfo *time,
             PaStreamCallbackFlags flags) {
    struct mock_stream *stream = opaque;
    assert(!stream->closed && stream->callback);
    return stream->callback(input, output, frames, time, flags, stream->user);
}
'''


MOCK_CLIENT = r'''
#define _POSIX_C_SOURCE 200809L
#include "mock_pa.h"
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static const void *expected_input;
static void *expected_output;
static const PaStreamCallbackTimeInfo *expected_time;
static PaStreamCallbackFlags expected_flags;
static int token, calls;

static int callback(const void *input, void *output, unsigned long frames,
                    const PaStreamCallbackTimeInfo *time,
                    PaStreamCallbackFlags flags, void *user) {
    assert(input == expected_input && output == expected_output);
    assert(frames == 4 && time == expected_time && flags == expected_flags);
    assert(user == &token);
    assert(time->inputBufferAdcTime == 11.0 && time->currentTime == 12.0);
    assert(time->outputBufferDacTime == 13.0);
    calls++;
    return 7;
}

static void fire_flags(PaStream *stream, const void *input, void *output,
                       const PaStreamCallbackTimeInfo *time,
                       PaStreamCallbackFlags flags) {
    expected_input = input; expected_output = output; expected_time = time;
    expected_flags = flags;
    assert(MockFire(stream, input, output, 4, time, flags) == 7);
}

static void fire(PaStream *stream, const void *input, void *output,
                 const PaStreamCallbackTimeInfo *time) {
    fire_flags(stream, input, output, time, 0x21UL);
}

static int reuse_stderr_for_udp(void) {
    int receiver = socket(AF_INET, SOCK_DGRAM, 0);
    assert(receiver >= 0);
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    assert(bind(receiver, (struct sockaddr *)&address, sizeof(address)) == 0);
    socklen_t length = sizeof(address);
    assert(getsockname(receiver, (struct sockaddr *)&address, &length) == 0);
    assert(close(STDERR_FILENO) == 0);
    int sender = socket(AF_INET, SOCK_DGRAM, 0);
    assert(sender == STDERR_FILENO);
    assert(connect(sender, (struct sockaddr *)&address, sizeof(address)) == 0);
    return receiver;
}

static void assert_owned_stdout(int report_fd) {
    struct stat expected, actual;
    assert(fstat(report_fd, &expected) == 0);
    int found = 0;
    for (int descriptor = 64; descriptor < 256; descriptor++) {
        if (fstat(descriptor, &actual) == 0
                && actual.st_dev == expected.st_dev && actual.st_ino == expected.st_ino) {
            assert(fcntl(descriptor, F_GETFD) & FD_CLOEXEC);
            found = 1;
        }
    }
    assert(found);
}

int main(int argc, char **argv) {
    struct rlimit no_core = {0, 0};
    assert(setrlimit(RLIMIT_CORE, &no_core) == 0);
    assert(argc == 2);
    const char *scenario = argv[1];
    int report_fd = dup(STDOUT_FILENO);
    assert(report_fd >= 3);
    int udp_receiver = -1;
    if (!strcmp(scenario, "fd-stderr-udp") || !strcmp(scenario, "fd-both-reused"))
        udp_receiver = reuse_stderr_for_udp();
    if (!strcmp(scenario, "fd-dup-failure")) assert(close(STDOUT_FILENO) == 0);
    int stdout_flags = fcntl(STDOUT_FILENO, F_GETFL);
    int stderr_flags = fcntl(STDERR_FILENO, F_GETFL);
    PaStreamParameters input = {3, 2, 1UL, 0.25, &token};
    PaStreamParameters output = {7, 2, 1UL, 0.5, &token};
    PaStreamParameters *input_ptr = &input;
    PaStreamCallbackTimeInfo timing = {11.0, 12.0, 13.0};
    float values[8] = {0.0f, 1.0f, 0.5f, -0.5f, -0.5f, 0.25f, 1.0f, 0.0f};
    float output_values[8] = {91.0f, 92.0f, 93.0f, 94.0f, 95.0f, 96.0f, 97.0f, 98.0f};
    const void *data = values;
    if (!strcmp(scenario, "output-only")) { input_ptr = NULL; data = NULL; }
    if (!strcmp(scenario, "int16")) input.sampleFormat = 8UL;
    if (!strcmp(scenario, "noninterleaved")) input.sampleFormat = 0x80000001UL;
    if (!strcmp(scenario, "mono")) input.channelCount = 1;
    if (!strcmp(scenario, "three-channels")) input.channelCount = 3;
    if (!strcmp(scenario, "null-input")) data = NULL;
    if (!strcmp(scenario, "nonfinite")) {
        values[0] = NAN; values[1] = INFINITY;
        values[2] = -INFINITY; values[3] = NAN;
    }
    PaStream *stream = NULL;
    PaStreamCallback *callback_ptr = !strcmp(scenario, "missing-callback") ? NULL : callback;
    int opened = Pa_OpenStream(&stream, input_ptr, &output, 4.0, 4, 0x1234UL,
                               callback_ptr, &token);
    if (stdout_flags >= 0) assert(fcntl(STDOUT_FILENO, F_GETFL) == stdout_flags);
    assert(fcntl(STDERR_FILENO, F_GETFL) == stderr_flags);
    if (!strcmp(scenario, "fd-stderr-udp") || !strcmp(scenario, "fd-stdout-replaced")
            || !strcmp(scenario, "fd-both-reused"))
        assert_owned_stdout(report_fd);
    if (!strcmp(scenario, "fd-stdout-replaced") || !strcmp(scenario, "fd-both-reused")) {
        int sink = open("/dev/null", O_WRONLY);
        assert(sink >= 0 && dup2(sink, STDOUT_FILENO) == STDOUT_FILENO);
        assert(close(sink) == 0);
    }
    if (!strcmp(scenario, "open-failure")) {
        assert(opened == -17 && stream == NULL);
    } else if (!strcmp(scenario, "missing-callback")) {
        assert(opened == 0 && stream != NULL);
        assert(Pa_CloseStream(stream) == 0);
        assert(calls == 0);
    } else {
        assert(opened == 0 && stream != NULL);
        fire(stream, data, output_values, &timing);
        MockAdvance(1.1);
        fire(stream, data, output_values, &timing);
        if (!strcmp(scenario, "mixed-flags")) {
            MockAdvance(0.1);
            fire_flags(stream, data, output_values, &timing, 0x02UL);
            MockAdvance(1.0);
            fire_flags(stream, data, output_values, &timing, 0x04UL);
            MockAdvance(1.1);
            fire_flags(stream, data, output_values, &timing, 0UL);
        }
        if (!strcmp(scenario, "duration")) {
            MockAdvance(2.2);
            fire(stream, data, output_values, &timing);
            MockAdvance(10.0);
            fire(stream, data, output_values, &timing);
        }
        if (!strcmp(scenario, "duration-limits")) {
            MockAdvance(239.0);
            fire(stream, data, output_values, &timing);
            MockAdvance(60.0);
            fire(stream, data, output_values, &timing);
            MockAdvance(10.0);
            fire(stream, data, output_values, &timing);
        }
        if (!strcmp(scenario, "close-failure")) {
            assert(Pa_CloseStream(stream) == -19);
            MockAdvance(1.1);
            fire(stream, data, output_values, &timing);
        }
        assert(Pa_CloseStream(stream) == 0);
        assert(calls >= 2);
        assert(output_values[0] == 91.0f && output_values[7] == 98.0f);
        if (!strcmp(scenario, "stats")) {
            assert(values[0] == 0.0f && values[2] == 0.5f && values[6] == 1.0f);
        }
    }
    struct timespec drain = {0, 100000000};
    nanosleep(&drain, NULL);
    if (udp_receiver >= 0) {
        char unexpected[128];
        errno = 0;
        assert(recv(udp_receiver, unexpected, sizeof(unexpected), MSG_DONTWAIT) == -1);
        assert(errno == EAGAIN || errno == EWOULDBLOCK);
        assert(close(udp_receiver) == 0);
    }
    assert(dprintf(report_fd, "mock_client_ok calls=%d\n", calls) > 0);
    assert(close(report_fd) == 0);
    return 0;
}
'''


@pytest.fixture(scope="module")
def mock_pa_program(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("A host C compiler is required for the PortAudio shim mock tests")
    directory = tmp_path_factory.mktemp("pa-input-stats")
    (directory / "mock_pa.h").write_text(MOCK_HEADER, encoding="utf-8")
    (directory / "mock_pa.c").write_text(MOCK_LIBRARY, encoding="utf-8")
    (directory / "client.c").write_text(MOCK_CLIENT, encoding="utf-8")
    commands = [
        [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
         str(directory / "mock_pa.c"), "-I", str(directory),
         "-o", str(directory / "libmockpa.so")],
        [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(directory / "client.c"),
         "-I", str(directory), "-L", str(directory), "-lmockpa",
         "-Wl,-rpath," + str(directory), "-o", str(directory / "client")],
        [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
         str(ROOT / "tools/k1_pa_input_stats.c"), "-ldl", "-pthread", "-lm",
         "-o", str(directory / "input-stats.so")],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, text=True)
    return directory


def run_mock(directory, scenario="stats", **extra_env):
    environment = os.environ.copy()
    for name in ("AI_CAT_PA_STATS_SECONDS", "MOCK_OPEN_FAIL", "MOCK_CLOSE_FAIL_FIRST"):
        environment.pop(name, None)
    environment.update({"LD_PRELOAD": str(directory / "input-stats.so")})
    environment.update(extra_env)
    result = subprocess.run(
        [str(directory / "client"), scenario], env=environment,
        capture_output=True, text=True, timeout=5, check=True,
    )
    records = []
    assert not any(line.startswith(PREFIX) for line in result.stderr.splitlines())
    for line in result.stdout.splitlines():
        if line.startswith(PREFIX):
            records.append(json.loads(line[len(PREFIX):]))
    assert "mock_client_ok" in result.stdout
    return records


def windows(records):
    return [record for record in records if "per_channel" in record]


@pytest.mark.parametrize("scenario", ["fd-stderr-udp", "fd-stdout-replaced", "fd-both-reused"])
def test_factory_fd_reuse_keeps_statistics_on_owned_original_stdout(mock_pa_program, scenario):
    records = windows(run_mock(mock_pa_program, scenario))
    assert len(records) == 1
    assert records[0]["per_channel"][0]["n"] == 8
    assert records[0]["per_channel"][0]["sum"] == pytest.approx(2.0)
    assert records[0]["per_channel"][1]["sumsq"] == pytest.approx(2.625)


def test_stdout_dup_failure_disables_statistics_not_original_callback(mock_pa_program):
    assert run_mock(mock_pa_program, "fd-dup-failure") == []


def test_callback_passthrough_and_per_channel_statistics(mock_pa_program):
    records = windows(run_mock(mock_pa_program))
    assert records
    record = next(item for item in records if item["per_channel"][0]["n"])
    assert record["device"] == 3
    assert record["channels"] == 2
    assert record["sample_rate"] == 4
    assert record["stream_id"]
    assert record["sequence"] >= 1
    assert record["elapsed_seconds"] >= 0
    assert record["window_seconds"] > 0
    assert record["null_frames"] == 0
    assert record["callback_flags"] == 0x21
    assert record["input_underflow"] == 2
    assert record["input_overflow"] == 0
    for actual, expected in zip(record["per_channel"], [
        {"sum": 1.0, "sumsq": 1.5, "peak": 1.0, "zero": 1},
        {"sum": 0.75, "sumsq": 1.3125, "peak": 1.0, "zero": 1},
    ]):
        batches = actual["n"] / 4
        assert batches == 2
        assert actual["finite_count"] == actual["n"]
        assert actual["sum"] == pytest.approx(expected["sum"] * batches)
        assert actual["sumsq"] == pytest.approx(expected["sumsq"] * batches)
        assert actual["peak"] == pytest.approx(expected["peak"])
        assert actual["zero"] == expected["zero"] * batches


def test_callback_flags_are_aggregated_and_reset_for_each_window(mock_pa_program):
    records = windows(run_mock(mock_pa_program, "mixed-flags"))
    assert len(records) == 3
    assert len({record["stream_id"] for record in records}) == 1
    assert [record["sequence"] for record in records] == [1, 2, 3]
    assert [
        (record["callback_flags"], record["input_underflow"], record["input_overflow"])
        for record in records
    ] == [(0x21, 2, 0), (0x06, 0, 1), (0, 0, 0)]
    assert [record["per_channel"][0]["n"] for record in records] == [8, 8, 4]


@pytest.mark.parametrize("scenario, reason", [
    ("output-only", "output_only"),
    ("int16", "unsupported_sample_format"),
    ("noninterleaved", "non_interleaved"),
    ("mono", "channel_count_not_two"),
    ("three-channels", "channel_count_not_two"),
    ("missing-callback", "missing_callback"),
])
def test_unsupported_streams_are_skipped_without_affecting_callbacks(
    mock_pa_program, scenario, reason
):
    records = run_mock(mock_pa_program, scenario)
    assert not windows(records)
    assert any(record.get("event") == "skipped" and record.get("reason") == reason for record in records)


def test_open_failure_does_not_claim_recording_statistics(mock_pa_program):
    records = run_mock(mock_pa_program, "open-failure", MOCK_OPEN_FAIL="1")
    assert not windows(records)


def test_failed_close_keeps_callback_context_alive(mock_pa_program):
    records = run_mock(mock_pa_program, "close-failure", MOCK_CLOSE_FAIL_FIRST="1")
    assert windows(records)


def test_null_callback_input_is_reported_without_dereferencing(mock_pa_program):
    records = windows(run_mock(mock_pa_program, "null-input"))
    assert records
    assert sum(record["null_frames"] for record in records) > 0
    assert all(channel["finite_count"] == 0 for record in records for channel in record["per_channel"])
    for record in records:
        assert record["null_frames"] == 8
        for channel in record["per_channel"]:
            # A null buffer has no supplied samples, unlike supplied NaN/Inf.
            assert channel["n"] == 0
            assert channel["sum"] == channel["sumsq"] == channel["peak"] == channel["zero"] == 0


def test_nonfinite_values_do_not_poison_numeric_statistics(mock_pa_program):
    records = windows(run_mock(mock_pa_program, "nonfinite"))
    assert records
    for record in records:
        for channel, expected in zip(record["per_channel"], [
            {"sum": 1.0, "sumsq": 2.5, "peak": 1.0, "zero": 0},
            {"sum": 0.5, "sumsq": 0.125, "peak": 0.25, "zero": 2},
        ]):
            assert channel["n"] == 8
            assert channel["finite_count"] == 4
            assert isinstance(channel["sum"], (int, float))
            assert isinstance(channel["sumsq"], (int, float))
            assert math.isfinite(channel["sum"])
            assert math.isfinite(channel["sumsq"])
            for key, value in expected.items():
                assert channel[key] == pytest.approx(value)


def test_configured_duration_expires_without_stopping_callback_passthrough(mock_pa_program):
    records = windows(run_mock(mock_pa_program, "duration", AI_CAT_PA_STATS_SECONDS="2"))
    assert records
    assert any(not record["expired"] for record in records)
    expired = [record for record in records if record["expired"]]
    assert len(expired) == 1
    assert expired[0]["elapsed_seconds"] == pytest.approx(3.3)
    assert expired[0]["callback_flags"] == 0
    assert expired[0]["input_underflow"] == expired[0]["input_overflow"] == 0
    assert not any(record["elapsed_seconds"] > 3.3 for record in records)


def test_absent_duration_uses_240_second_default(mock_pa_program):
    records = windows(run_mock(mock_pa_program, "duration-limits"))
    expired = [record for record in records if record["expired"]]
    assert len(expired) == 1
    assert expired[0]["elapsed_seconds"] == pytest.approx(240.1)


@pytest.mark.parametrize("duration, expected_expiry", [
    ("1", 1.1),
    ("300", 300.1),
    ("0", 240.1),
    ("-1", 240.1),
    ("301", 240.1),
    ("1.5", 240.1),
    ("2junk", 240.1),
    ("999999999999999999999999999", 240.1),
    ("", 240.1),
])
def test_duration_bounds_and_invalid_values_use_safe_default(
    mock_pa_program, duration, expected_expiry
):
    records = windows(run_mock(
        mock_pa_program, "duration-limits", AI_CAT_PA_STATS_SECONDS=duration,
    ))
    expired = [record for record in records if record["expired"]]
    assert len(expired) == 1
    assert expired[0]["elapsed_seconds"] == pytest.approx(expected_expiry)
