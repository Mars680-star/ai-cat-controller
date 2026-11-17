#define _DEFAULT_SOURCE
/*
 * Device .120, diagnostic only: reuse the web's unchanged pwm_RoHS library.
 * Build with the gpiod/file helper link wrappers listed in the motion document.
 * --plan never initializes a motor. Actual movement needs separate consent.
 * No production installation, raw arguments, calibration or absolute homing.
 */
#include <errno.h>
#include <fcntl.h>
#include <gpiod.h>
#include <motor.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include "k1_head_probe_guard.h"

#define PROBE_SERIAL "7c2b63fd4a120"
#define ANGLE_CACHE "/root/.motor_2_cur_angle"
#define MOTOR_LOCK "/run/ai-cat/motor.lock"
#define MOTOR_PID "/run/ai-cat/motor.pid"

/* Verified against this board's driver ABI. This is NOT physical feedback. */
struct rohs_motor_info {
    unsigned char motor_index, step_gpio, dir_gpio, enable_gpio, stop_gpio;
    int current_position, constant_range, gpio_max_steps;
    bool enable_gpio_level, dir_gpio_left_level, stop_gpio_active_level;
    int range_steps;
};
_Static_assert(offsetof(struct rohs_motor_info, enable_gpio_level) == 20, "RoHS ABI mismatch");
_Static_assert(sizeof(struct rohs_motor_info) == 28, "RoHS ABI mismatch");

static struct head_probe_guard guard;
static volatile sig_atomic_t stopped;
static struct gpiod_line *step_line, *enable_line, *limit_line;
static int virtual_angle = 90;
static bool cleanup_failed;
static FILE *cache_stream;
static bool cache_writing;
static char cache_buffer[32];

int __real_gpiod_line_set_value(struct gpiod_line *, int);
struct gpiod_chip *__real_gpiod_chip_open_by_name(const char *);
int __real_gpiod_line_request_output(struct gpiod_line *, const char *, int);
int __real_gpiod_line_request_input(struct gpiod_line *, const char *);
int __real_usleep(useconds_t);
void __real_gpiod_line_release(struct gpiod_line *);
void __real_gpiod_chip_close(struct gpiod_chip *);
FILE *__real_fopen(const char *, const char *);
int __real_fclose(FILE *);

static int fail_closed(void)
{
    guard.failed = true;
    if (enable_line && __real_gpiod_line_set_value(enable_line, 1) != 0)
        cleanup_failed = true;
    if (step_line && __real_gpiod_line_set_value(step_line, 0) != 0)
        cleanup_failed = true;
    return -1;
}

static int current_limit(void)
{
    return limit_line ? gpiod_line_get_value(limit_line) : -1;
}

struct gpiod_chip *__wrap_gpiod_chip_open_by_name(const char *name)
{
    if (strcmp(name, "gpiochip0") != 0) {
        guard.failed = true;
        return NULL;
    }
    return __real_gpiod_chip_open_by_name(name);
}

int __wrap_gpiod_line_request_output(struct gpiod_line *line,
    const char *consumer, int value)
{
    unsigned int offset = gpiod_line_offset(line);
    if (guard.failed || (offset != 34 && offset != 35 && offset != 36))
        return fail_closed();
    /* Never energize as a side effect of driver initialization. */
    if (offset == 36)
        value = 1;
    if (offset == 34)
        value = 0;
    int result = __real_gpiod_line_request_output(line, consumer, value);
    if (result != 0)
        return fail_closed();
    if (offset == 34)
        step_line = line;
    if (offset == 36)
        enable_line = line;
    return 0;
}

int __wrap_gpiod_line_request_input(struct gpiod_line *line, const char *consumer)
{
    if (guard.failed || gpiod_line_offset(line) != 82)
        return fail_closed();
    if (__real_gpiod_line_request_input(line, consumer) != 0)
        return fail_closed();
    limit_line = line;
    return 0;
}

void __wrap_gpiod_line_release(struct gpiod_line *line)
{
    if (line == enable_line) {
        if (__real_gpiod_line_set_value(line, 1) != 0)
            cleanup_failed = true;
        enable_line = NULL;
    }
    if (line == step_line) {
        if (__real_gpiod_line_set_value(line, 0) != 0)
            cleanup_failed = true;
        step_line = NULL;
    }
    if (line == limit_line)
        limit_line = NULL;
    __real_gpiod_line_release(line);
}

void __wrap_gpiod_chip_close(struct gpiod_chip *chip)
{
    /* Driver init may close its chip on failure; never keep dangling lines. */
    if (enable_line && __real_gpiod_line_set_value(enable_line, 1) != 0)
        cleanup_failed = true;
    if (step_line && __real_gpiod_line_set_value(step_line, 0) != 0)
        cleanup_failed = true;
    enable_line = step_line = limit_line = NULL;
    __real_gpiod_chip_close(chip);
}

int __wrap_gpiod_line_set_value(struct gpiod_line *line, int value)
{
    unsigned int offset = gpiod_line_offset(line);
    if (value != 0 && value != 1)
        return fail_closed();
    if (offset == 36) {
        if (value == 0 && (guard.failed || stopped || current_limit() != 1))
            return fail_closed();
    } else if (offset == 35) {
        if (stopped || !head_probe_direction(&guard, value))
            return fail_closed();
    } else if (offset == 34) {
        if (value == 1 && !head_probe_step(&guard, current_limit(), stopped != 0))
            return fail_closed();
    } else {
        return fail_closed();
    }
    if (__real_gpiod_line_set_value(line, value) != 0)
        return fail_closed();
    return 0;
}

int __wrap_usleep(useconds_t duration)
{
    if (guard.failed || stopped)
        return 0;
    return __real_usleep(duration);
}

FILE *__wrap_fopen(const char *path, const char *mode)
{
    if (strcmp(path, ANGLE_CACHE) != 0) {
        if (strcmp(mode, "r") != 0) {
            fail_closed();
            return NULL; /* Reject every other attempted driver file write. */
        }
        return __real_fopen(path, mode);
    }
    if (cache_stream || (strcmp(mode, "r") != 0 && strcmp(mode, "w") != 0) ||
        (strcmp(mode, "w") == 0 && (guard.failed || stopped))) {
        fail_closed();
        return NULL;
    }
    cache_writing = strcmp(mode, "w") == 0;
    memset(cache_buffer, 0, sizeof(cache_buffer));
    if (!cache_writing)
        snprintf(cache_buffer, sizeof(cache_buffer), "%d\n", virtual_angle);
    cache_stream = fmemopen(cache_buffer,
        cache_writing ? sizeof(cache_buffer) : strlen(cache_buffer), mode);
    if (!cache_stream)
        fail_closed();
    return cache_stream;
}

int __wrap_fclose(FILE *file)
{
    if (file != cache_stream)
        return __real_fclose(file);
    if (cache_writing) {
        char *end = NULL;
        if (fflush(file) != 0)
            fail_closed();
        long value = strtol(cache_buffer, &end, 10);
        if (end == cache_buffer || (*end != '\0' && strcmp(end, "\n") != 0) ||
            (value != 90 && value != 91) || guard.failed || stopped)
            fail_closed();
        else
            virtual_angle = (int)value;
    }
    cache_stream = NULL;
    return __real_fclose(file);
}

static int read_int_stream(FILE *file, int *value)
{
    char extra;
    if (!file)
        return -1;
    int count = fscanf(file, "%d %c", value, &extra);
    fclose(file);
    return count == 1 ? 0 : -1;
}

static int read_int(const char *path, int *value)
{
    return read_int_stream(fopen(path, "r"), value);
}

static int read_disk_int(const char *path, int *value)
{
    return read_int_stream(__real_fopen(path, "r"), value);
}

static bool motion_disabled(void)
{
    FILE *file = fopen("/etc/ai-cat-controller.env", "r");
    char line[4096];
    unsigned int count = 0;
    bool disabled = false;
    if (!file)
        return false;
    while (fgets(line, sizeof(line), file)) {
        if (strncmp(line, "AI_CAT_ENABLE_MOTION=", 21) == 0) {
            count++;
            disabled = strcmp(line, "AI_CAT_ENABLE_MOTION=false\n") == 0 ||
                strcmp(line, "AI_CAT_ENABLE_MOTION=false") == 0;
        }
    }
    fclose(file);
    return count == 1 && disabled;
}

static bool motor_service_inactive(void)
{
    pid_t pid = fork();
    if (pid < 0)
        return false;
    if (pid == 0) {
        int null_fd = open("/dev/null", O_WRONLY);
        if (null_fd < 0)
            _exit(127);
        dup2(null_fd, STDOUT_FILENO);
        dup2(null_fd, STDERR_FILENO);
        close(null_fd);
        execl("/usr/bin/systemctl", "systemctl", "is-active", "--quiet", "toy_motor.service", (char *)NULL);
        _exit(127);
    }
    int status;
    if (waitpid(pid, &status, 0) != pid)
        return false;
    return WIFEXITED(status) && WEXITSTATUS(status) == 3;
}

static bool preflight(void)
{
    char serial[64] = {0};
    int descriptor = open("/proc/device-tree/serial-number", O_RDONLY);
    if (descriptor < 0)
        return false;
    ssize_t size = read(descriptor, serial, sizeof(serial) - 1);
    close(descriptor);
    if (size <= 0 || strcmp(serial, PROBE_SERIAL) != 0 || !motion_disabled())
        return false;
    const char *paths[] = {
        "/root/.motor_type", "/root/.motor_limit_switch_exist",
        "/root/.motor_2_max_steps", "/root/.motor_2_range_angle", ANGLE_CACHE,
        "/root/.motor_2_step_gpio", "/root/.motor_2_dir_gpio",
        "/root/.motor_2_sleep_gpio", "/root/.motor_2_stop_gpio",
    };
    const int expected[] = {2, 1, 4300, 20, 90, 34, 35, 36, 82};
    for (size_t i = 0; i < sizeof(paths) / sizeof(paths[0]); i++) {
        int value;
        if (read_disk_int(paths[i], &value) != 0 || value != expected[i]) {
            fprintf(stderr, "Preflight configuration mismatch at item %zu\n", i);
            return false;
        }
    }
    return true;
}

static void request_stop(int number)
{
    (void)number;
    stopped = 1;
}

static void observe(const char *stage)
{
    int adc = -1;
    read_int("/sys/bus/iio/devices/iio:device1/in_voltage5_raw", &adc);
    printf("{\"stage\":\"%s\",\"adc_raw\":%d,\"pulse_high_attempts\":[%u,%u],\"physical_position_verified\":false}\n",
        stage, adc, guard.pulses[0], guard.pulses[1]);
    fflush(stdout);
}

static bool wrappers_linked(void)
{
    /* Refuse a manually rebuilt binary missing any protective linker flag. */
    /* Volatile loads are essential: compilers may fold named functions unequal
     * before the linker redirects their references with --wrap. */
#define CHECK_WRAPPER(name) do { \
    __typeof__(&name) volatile routed = name; \
    __typeof__(&name) volatile expected = __wrap_##name; \
    if (routed != expected) return false; \
} while (0)
    CHECK_WRAPPER(fopen);
    CHECK_WRAPPER(fclose);
    CHECK_WRAPPER(gpiod_chip_open_by_name);
    CHECK_WRAPPER(gpiod_line_request_output);
    CHECK_WRAPPER(gpiod_line_request_input);
    CHECK_WRAPPER(gpiod_line_set_value);
    CHECK_WRAPPER(gpiod_line_release);
    CHECK_WRAPPER(gpiod_chip_close);
    CHECK_WRAPPER(usleep);
#undef CHECK_WRAPPER
    return true;
}

int main(int argc, char **argv)
{
    bool plan = argc == 2 && strcmp(argv[1], "--plan") == 0;
    bool isolation_check = argc == 2 && strcmp(argv[1], "--check-isolation") == 0;
    bool run = argc == 3 && strcmp(argv[1], "motor") == 0 && strcmp(argv[2], "head_ud") == 0;
    if ((!plan && !run && !isolation_check) || !wrappers_linked() || !preflight()) {
        fprintf(stderr, "Refused: fixed device, safety configuration or arguments do not match\n");
        return 1;
    }
    guard = head_probe_guard_init();
    if (isolation_check) {
        /* Check virtual file routing without opening a GPIO chip or motor. */
        FILE *memory_file = fopen(ANGLE_CACHE, "w");
        if (!memory_file)
            return 1;
        fprintf(memory_file, "91");
        if (fclose(memory_file) != 0 || virtual_angle != 91 || guard.failed)
            return 1;
        int memory_value;
        if (read_int(ANGLE_CACHE, &memory_value) != 0 || memory_value != 91)
            return 1;
        FILE *disk = __real_fopen(ANGLE_CACHE, "r");
        if (!disk)
            return 1;
        int disk_value;
        int count = fscanf(disk, "%d", &disk_value);
        __real_fclose(disk);
        if (count != 1 || disk_value != 90)
            return 1;
        puts("isolation_check_passed; disk_cache_unchanged=true; gpio_requests=0");
        return 0;
    }
    printf("{\"axis\":\"head_ud\",\"driver\":\"pwm_RoHS\",\"speed\":2,\"logical_targets\":[91,90],\"max_pulses_per_leg\":215,\"max_total_pulses\":430,\"calibration_writes\":false,\"physical_degrees_verified\":false}\n");
    fflush(stdout);
    if (plan)
        return 0; /* No chip request, driver init, PID write or movement. */
    if (geteuid() != 0 || !motor_service_inactive())
        return 1;
    int lock = open(MOTOR_LOCK, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (lock < 0)
        return 1;
    if (flock(lock, LOCK_EX | LOCK_NB) != 0) {
        close(lock);
        return 1;
    }
    struct sigaction action = {0};
    action.sa_handler = request_stop;
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGALRM, &action, NULL);
    int pid_fd = open(MOTOR_PID, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (pid_fd < 0) {
        close(lock);
        return 1;
    }
    if (dprintf(pid_fd, "%ld\n", (long)getpid()) <= 0) {
        close(pid_fd);
        unlink(MOTOR_PID);
        close(lock);
        return 1;
    }
    close(pid_fd);
    alarm(5);
    struct rohs_motor_info info = {
        .motor_index = 2, .step_gpio = 34, .dir_gpio = 35, .enable_gpio = 36, .stop_gpio = 82,
        .current_position = -1, .constant_range = 20, .gpio_max_steps = -1,
        .enable_gpio_level = false, .dir_gpio_left_level = false, .stop_gpio_active_level = false,
        .range_steps = 0,
    };
    struct motor_dev *motor = motor_alloc_pwm("pwm_RoHS", 0, &info);
    int result = 1;
    if (!motor || motor_init_one(motor) != 0 || guard.failed || stopped)
        goto cleanup;
    observe("before");
    for (unsigned int leg = 0; leg < 2; leg++) {
        if (!head_probe_begin_leg(&guard, leg, current_limit(), stopped != 0))
            goto cleanup;
        struct motor_cmd command = {
            .mode = MOTOR_MODE_POS, .pos_des = leg == 0 ? 91.0f : 90.0f, .vel_des = 2.0f,
        };
        if (motor_set_cmd_one(motor, &command) != 0 || guard.failed || stopped ||
            guard.pulses[leg] != HEAD_PROBE_PULSES_PER_LEG)
            goto cleanup;
        observe(leg == 0 ? "outward" : "returned");
        usleep(250000);
    }
    result = 0;
cleanup:
    /* Disable before library handles are freed, including partial-init failures. */
    if (enable_line && __real_gpiod_line_set_value(enable_line, 1) != 0)
        cleanup_failed = true;
    if (step_line && __real_gpiod_line_set_value(step_line, 0) != 0)
        cleanup_failed = true;
    if (motor)
        motor_free(&motor, 1);
    step_line = enable_line = limit_line = NULL;
    alarm(0);
    unlink(MOTOR_PID);
    close(lock);
    if (cleanup_failed || stopped || guard.failed)
        result = 1;
    printf("{\"finished\":true,\"returncode\":%d,\"disable_write_ok\":%s,\"guard_failed\":%s,\"cache_persistent_write\":false}\n",
        result, cleanup_failed ? "false" : "true", guard.failed ? "true" : "false");
    return result;
}
