/* Pure guard logic for the device-scoped native diagnostic; no hardware I/O. */
#ifndef K1_HEAD_PROBE_GUARD_H
#define K1_HEAD_PROBE_GUARD_H

#include <stdbool.h>

#define HEAD_PROBE_PULSES_PER_LEG 215U
#define HEAD_PROBE_TOTAL_PULSES 430U

struct head_probe_guard {
    unsigned int leg;
    unsigned int pulses[2];
    int direction[2];
    bool failed;
};

static inline struct head_probe_guard head_probe_guard_init(void)
{
    struct head_probe_guard guard = {0, {0, 0}, {-1, -1}, false};
    return guard;
}

static inline bool head_probe_begin_leg(struct head_probe_guard *guard,
    unsigned int leg, int limit, bool stopped)
{
    if (guard->failed || stopped || limit != 1 || leg > 1 ||
        guard->pulses[leg] != 0 ||
        (leg == 1 && guard->pulses[0] != HEAD_PROBE_PULSES_PER_LEG)) {
        guard->failed = true;
        return false;
    }
    guard->leg = leg;
    return true;
}

static inline bool head_probe_direction(struct head_probe_guard *guard, int value)
{
    if (guard->failed || guard->leg > 1 || (value != 0 && value != 1) ||
        (guard->direction[guard->leg] != -1 && guard->direction[guard->leg] != value) ||
        (guard->leg == 1 && value != 1 - guard->direction[0])) {
        guard->failed = true;
        return false;
    }
    guard->direction[guard->leg] = value;
    return true;
}

/* Counts permitted high-write attempts, not verified physical motor steps. */
static inline bool head_probe_step(struct head_probe_guard *guard,
    int limit, bool stopped)
{
    if (guard->failed || stopped || limit != 1 || guard->leg > 1 ||
        guard->direction[guard->leg] < 0 ||
        guard->pulses[guard->leg] >= HEAD_PROBE_PULSES_PER_LEG ||
        guard->pulses[0] + guard->pulses[1] >= HEAD_PROBE_TOTAL_PULSES) {
        guard->failed = true;
        return false;
    }
    guard->pulses[guard->leg]++;
    return true;
}

#endif
