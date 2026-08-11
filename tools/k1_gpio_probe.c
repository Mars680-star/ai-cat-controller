#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <gpiod.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_LINES 16

static volatile sig_atomic_t stop_requested;

static void handle_signal(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

static long long monotonic_milliseconds(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return -1;
    return (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int parse_positive_int(const char *text, int fallback)
{
    char *end = NULL;
    long value;

    if (text == NULL)
        return fallback;
    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value <= 0 ||
        value > INT_MAX)
        return fallback;
    return (int)value;
}

int main(int argc, char **argv)
{
    struct gpiod_chip *chip;
    struct gpiod_line *lines[MAX_LINES] = {0};
    unsigned int offsets[MAX_LINES] = {0};
    int values[MAX_LINES] = {0};
    int duration_seconds = parse_positive_int(argc > 1 ? argv[1] : NULL, 30);
    int line_count = argc > 2 ? argc - 2 : 2;
    int active_lines = 0;
    long long started_at;
    const struct timespec poll_delay = {.tv_sec = 0, .tv_nsec = 10000000};

    if (line_count > MAX_LINES) {
        fprintf(stderr, "at most %d GPIO lines are supported\n", MAX_LINES);
        return 2;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    chip = gpiod_chip_open_by_name("gpiochip0");
    if (chip == NULL) {
        fprintf(stderr, "cannot open gpiochip0: %s\n", strerror(errno));
        return 1;
    }

    for (int index = 0; index < line_count; ++index) {
        int offset = argc > 2 ? parse_positive_int(argv[index + 2], -1)
                              : (index == 0 ? 113 : 49);
        if (offset < 0) {
            fprintf(stderr, "invalid GPIO offset: %s\n", argv[index + 2]);
            continue;
        }
        offsets[index] = (unsigned int)offset;
        lines[index] = gpiod_chip_get_line(chip, offsets[index]);
        if (lines[index] == NULL ||
            gpiod_line_request_input(lines[index], "ai-cat-gpio-probe") != 0) {
            fprintf(stderr, "cannot read GPIO%u: %s\n", offsets[index],
                    strerror(errno));
            lines[index] = NULL;
            continue;
        }
        values[index] = gpiod_line_get_value(lines[index]);
        if (values[index] < 0) {
            fprintf(stderr, "initial read failed for GPIO%u: %s\n",
                    offsets[index], strerror(errno));
            gpiod_line_release(lines[index]);
            lines[index] = NULL;
            continue;
        }
        printf("GPIO%u initial=%d\n", offsets[index], values[index]);
        active_lines++;
    }
    fflush(stdout);

    if (active_lines == 0) {
        gpiod_chip_close(chip);
        return 1;
    }

    started_at = monotonic_milliseconds();
    while (!stop_requested &&
           monotonic_milliseconds() - started_at < duration_seconds * 1000LL) {
        for (int index = 0; index < line_count; ++index) {
            int current;
            long long elapsed;

            if (lines[index] == NULL)
                continue;
            current = gpiod_line_get_value(lines[index]);
            if (current < 0 || current == values[index])
                continue;
            elapsed = monotonic_milliseconds() - started_at;
            printf("GPIO%u value=%d elapsed_ms=%lld\n", offsets[index], current,
                   elapsed);
            fflush(stdout);
            values[index] = current;
        }
        nanosleep(&poll_delay, NULL);
    }

    for (int index = 0; index < line_count; ++index) {
        if (lines[index] != NULL)
            gpiod_line_release(lines[index]);
    }
    gpiod_chip_close(chip);
    return 0;
}
