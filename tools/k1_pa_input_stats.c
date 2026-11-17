/*
 * LD_PRELOAD-only PortAudio input diagnostics.  Never opens a PCM device,
 * modifies samples, or retains raw audio.  Intended for process-lifetime
 * preload, not dlclose(): the detached logger owns no stream/context pointers.
 *
 * cc -shared -fPIC -O2 -Wall -Wextra -o k1_pa_input_stats.so \
 *    k1_pa_input_stats.c -ldl -lm -pthread
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <locale.h>
#include <math.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#if defined(__has_include)
# if __has_include(<portaudio.h>)
#  include <portaudio.h>
#  define HAVE_PORTAUDIO_HEADER 1
# endif
#endif
#ifndef HAVE_PORTAUDIO_HEADER
/* Exact public PortAudio v19 ABI subset; no private PortAudio structures. */
typedef int PaError;
typedef int PaDeviceIndex;
typedef double PaTime;
typedef unsigned long PaSampleFormat;
typedef unsigned long PaStreamFlags;
typedef unsigned long PaStreamCallbackFlags;
typedef void PaStream;
typedef struct PaStreamParameters {
    PaDeviceIndex device;
    int channelCount;
    PaSampleFormat sampleFormat;
    PaTime suggestedLatency;
    void *hostApiSpecificStreamInfo;
} PaStreamParameters;
typedef struct PaStreamCallbackTimeInfo {
    PaTime inputBufferAdcTime;
    PaTime currentTime;
    PaTime outputBufferDacTime;
} PaStreamCallbackTimeInfo;
typedef int PaStreamCallback(const void *, void *, unsigned long,
                            const PaStreamCallbackTimeInfo *,
                            PaStreamCallbackFlags, void *);
# define paFloat32 ((PaSampleFormat)0x00000001)
# define paNonInterleaved ((PaSampleFormat)0x80000000)
# define paInputUnderflow ((PaStreamCallbackFlags)0x00000001)
# define paInputOverflow ((PaStreamCallbackFlags)0x00000002)
# define paUnanticipatedHostError (-9999)
#endif

/* Isolate each atomic record from partially written factory stdout lines. */
#define LOG_PREFIX "\nAI_CAT_PA_INPUT_STATS "
#define CHANNELS 2
#define QUEUE_CAPACITY 64u
#define DEFAULT_SECONDS 240u

typedef PaError (*open_fn)(PaStream **, const PaStreamParameters *,
                          const PaStreamParameters *, double, unsigned long,
                          PaStreamFlags, PaStreamCallback *, void *);
typedef PaError (*close_fn)(PaStream *);

struct channel_stats {
    uint64_t n, finite_count, zero;
    double sum, sumsq, peak;
};

enum event_kind { WINDOW, SKIPPED, OPEN_FAILED, INITIALIZED };
enum skip_reason {
    NO_REASON, OUTPUT_ONLY, MISSING_CALLBACK, NON_INTERLEAVED,
    UNSUPPORTED_FORMAT, NOT_STEREO, INVALID_RATE, MISSING_DESTINATION,
    ALLOCATION_FAILED, CLOCK_UNAVAILABLE, INVALID_FRAME_COUNT,
    CLOSE_UNAVAILABLE
};

/* Only scalar metadata and aggregates cross the queue; never audio pointers. */
struct record {
    enum event_kind event;
    enum skip_reason reason;
    uint64_t stream_id, sequence, null_frames;
    uint64_t callback_flags, input_underflow, input_overflow;
    int device, channels, error;
    double rate, elapsed, window;
    bool expired;
    struct channel_stats channel[CHANNELS];
};

struct slot {
    _Atomic size_t sequence;
    struct record value;
};

struct context {
    PaStream *stream;
    PaStreamCallback *callback;
    void *user_data;
    struct context *next;
    struct record current;
    unsigned seconds;
    bool started, disabled;
    double began, window_began;
};

static open_fn real_open;
static close_fn real_close;
static pthread_once_t symbols_once = PTHREAD_ONCE_INIT;
static pthread_once_t logger_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t contexts_lock = PTHREAD_MUTEX_INITIALIZER;
static struct context *contexts;
static _Atomic uint64_t next_stream_id = 1;
static struct slot queue[QUEUE_CAPACITY];
static _Atomic size_t enqueue_position;
static bool logger_ready;
static unsigned stats_seconds = DEFAULT_SECONDS;
static locale_t numeric_locale;
/* Process-owned stdout duplicate, never the factory's reusable standard fds. */
static int logger_fd = -1;

static void resolve_symbols(void)
{
    real_open = (open_fn)dlsym(RTLD_NEXT, "Pa_OpenStream");
    real_close = (close_fn)dlsym(RTLD_NEXT, "Pa_CloseStream");
}

/* One bounded reservation attempt: contention/full queue only drops a log. */
static void publish(const struct record *record)
{
    size_t position = atomic_load_explicit(&enqueue_position,
                                           memory_order_relaxed);
    struct slot *slot = &queue[position % QUEUE_CAPACITY];
    if (atomic_load_explicit(&slot->sequence, memory_order_acquire) != position)
        return;
    if (!atomic_compare_exchange_strong_explicit(&enqueue_position, &position,
                                                 position + 1,
                                                 memory_order_relaxed,
                                                 memory_order_relaxed))
        return;
    slot->value = *record;
    atomic_store_explicit(&slot->sequence, position + 1, memory_order_release);
}

static const char *reason_name(enum skip_reason reason)
{
    switch (reason) {
    case OUTPUT_ONLY: return "output_only";
    case MISSING_CALLBACK: return "missing_callback";
    case NON_INTERLEAVED: return "non_interleaved";
    case UNSUPPORTED_FORMAT: return "unsupported_sample_format";
    case NOT_STEREO: return "channel_count_not_two";
    case INVALID_RATE: return "invalid_sample_rate";
    case MISSING_DESTINATION: return "missing_stream_destination";
    case ALLOCATION_FAILED: return "allocation_failed";
    case CLOCK_UNAVAILABLE: return "clock_unavailable";
    case INVALID_FRAME_COUNT: return "invalid_frame_count";
    case CLOSE_UNAVAILABLE: return "close_symbol_unavailable";
    default: return "none";
    }
}

static void log_record(const struct record *r)
{
    char line[2048], rate[48];
    int length;
    if (isfinite(r->rate))
        (void)snprintf(rate, sizeof(rate), "%.17g", r->rate);
    else
        (void)snprintf(rate, sizeof(rate), "null");
    if (r->event == INITIALIZED) {
        length = snprintf(line, sizeof(line), LOG_PREFIX
                          "{\"event\":\"initialized\",\"seconds\":%d}\n", r->error);
    } else if (r->event != WINDOW) {
        length = snprintf(line, sizeof(line), LOG_PREFIX
                          "{\"event\":\"%s\",\"stream_id\":%" PRIu64
                          ",\"device\":%d,\"channels\":%d,\"sample_rate\":%s,"
                          "\"reason\":\"%s\",\"error\":%d}\n",
                          r->event == OPEN_FAILED ? "open_failed" : "skipped",
                          r->stream_id, r->device, r->channels, rate,
                          reason_name(r->reason), r->error);
    } else {
        const struct channel_stats *a = &r->channel[0], *b = &r->channel[1];
        length = snprintf(line, sizeof(line), LOG_PREFIX
            "{\"event\":\"window\",\"stream_id\":%" PRIu64
            ",\"device\":%d,\"channels\":%d,\"sample_rate\":%s,"
            "\"sequence\":%" PRIu64 ",\"elapsed_seconds\":%.9g,"
            "\"window_seconds\":%.9g,\"expired\":%s,\"null_frames\":%" PRIu64
            ",\"callback_flags\":%" PRIu64 ",\"input_underflow\":%" PRIu64
            ",\"input_overflow\":%" PRIu64
            ",\"per_channel\":["
            "{\"n\":%" PRIu64 ",\"sum\":%.17g,\"sumsq\":%.17g,\"peak\":%.17g,"
            "\"zero\":%" PRIu64 ",\"finite_count\":%" PRIu64 "},"
            "{\"n\":%" PRIu64 ",\"sum\":%.17g,\"sumsq\":%.17g,\"peak\":%.17g,"
            "\"zero\":%" PRIu64 ",\"finite_count\":%" PRIu64 "}]}\n",
            r->stream_id, r->device, r->channels, rate, r->sequence,
            r->elapsed, r->window, r->expired ? "true" : "false", r->null_frames,
            r->callback_flags, r->input_underflow, r->input_overflow,
            a->n, a->sum, a->sumsq, a->peak, a->zero, a->finite_count,
            b->n, b->sum, b->sumsq, b->peak, b->zero, b->finite_count);
    }
    /* Logger-thread-only I/O.  Failure/short write never reaches audio code. */
    if (length > 0 && (size_t)length < sizeof(line)) {
        ssize_t written = write(logger_fd, line, (size_t)length);
        if (written < 0)
            return;
    }
}

static void *logger_main(void *unused)
{
    (void)unused;
    sigset_t blocked;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGPIPE);
    /* A closed diagnostic pipe must not kill the original process. */
    bool can_log = pthread_sigmask(SIG_BLOCK, &blocked, NULL) == 0;
    if (uselocale(numeric_locale) == (locale_t)0)
        can_log = false;
    size_t position = 0;
    const struct timespec delay = { .tv_sec = 0, .tv_nsec = 20000000 };
    for (;;) {
        struct slot *slot = &queue[position % QUEUE_CAPACITY];
        if (atomic_load_explicit(&slot->sequence, memory_order_acquire)
                != position + 1) {
            (void)nanosleep(&delay, NULL);
            continue;
        }
        struct record record = slot->value;
        atomic_store_explicit(&slot->sequence, position + QUEUE_CAPACITY,
                              memory_order_release);
        ++position;
        if (can_log)
            log_record(&record);
    }
    return NULL;
}

static unsigned configured_seconds(void)
{
    const char *s = getenv("AI_CAT_PA_STATS_SECONDS");
    unsigned value = 0;
    if (s == NULL || *s == '\0')
        return DEFAULT_SECONDS;
    for (; *s != '\0'; ++s) {
        if (*s < '0' || *s > '9' || value > 300)
            return DEFAULT_SECONDS;
        value = value * 10 + (unsigned)(*s - '0');
    }
    return value >= 1 && value <= 300 ? value : DEFAULT_SECONDS;
}

static void initialize_logger(void)
{
    stats_seconds = configured_seconds();
    for (size_t i = 0; i < QUEUE_CAPACITY; ++i)
        atomic_init(&queue[i].sequence, i);
    atomic_init(&enqueue_position, 0);
    if (!atomic_is_lock_free(&enqueue_position)
            || !atomic_is_lock_free(&queue[0].sequence))
        return;
    numeric_locale = newlocale(LC_NUMERIC_MASK, "C", (locale_t)0);
    if (numeric_locale == (locale_t)0)
        return;
    /* The factory reuses stderr for DDS.  Capture stdout once, outside every
     * audio callback; later close/dup2 of fd 1 cannot redirect diagnostics.
     * F_DUPFD_CLOEXEC changes only our owned descriptor, not stdout's flags. */
    logger_fd = fcntl(STDOUT_FILENO, F_DUPFD_CLOEXEC, 64);
    if (logger_fd < 0) {
        freelocale(numeric_locale);
        return;
    }
    pthread_attr_t attributes;
    if (pthread_attr_init(&attributes) != 0) {
        (void)close(logger_fd);
        logger_fd = -1;
        freelocale(numeric_locale);
        return;
    }
    pthread_t thread;
    int error = pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED);
    if (error == 0)
        error = pthread_create(&thread, &attributes, logger_main, NULL);
    (void)pthread_attr_destroy(&attributes);
    if (error != 0) {
        (void)close(logger_fd);
        logger_fd = -1;
        freelocale(numeric_locale);
        return;
    }
    logger_ready = true;
    const struct record initialized = { .event = INITIALIZED, .error = (int)stats_seconds };
    publish(&initialized);
}

static void emit_window(struct context *ctx, double now, bool expired)
{
    ctx->current.event = WINDOW;
    ++ctx->current.sequence;
    ctx->current.elapsed = now - ctx->began;
    ctx->current.window = now - ctx->window_began;
    ctx->current.expired = expired;
    publish(&ctx->current);
    memset(ctx->current.channel, 0, sizeof(ctx->current.channel));
    ctx->current.null_frames = 0;
    ctx->current.callback_flags = 0;
    ctx->current.input_underflow = 0;
    ctx->current.input_overflow = 0;
    ctx->window_began = now;
}

static void collect(struct context *ctx, const void *input, unsigned long frames,
                    PaStreamCallbackFlags flags)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        ctx->current.event = SKIPPED;
        ctx->current.reason = CLOCK_UNAVAILABLE;
        publish(&ctx->current);
        ctx->disabled = true;
        return;
    }
    double now = (double)ts.tv_sec + (double)ts.tv_nsec / 1000000000.0;
    if (!ctx->started) {
        ctx->started = true;
        ctx->began = ctx->window_began = now;
    }
    if (now < ctx->window_began || now - ctx->began >= ctx->seconds) {
        emit_window(ctx, now, true);
        ctx->disabled = true;
        return;
    }
    if (frames > SIZE_MAX / (CHANNELS * sizeof(float))) {
        ctx->current.event = SKIPPED;
        ctx->current.reason = INVALID_FRAME_COUNT;
        publish(&ctx->current);
        ctx->disabled = true;
        return;
    }
    ctx->current.callback_flags |= (uint64_t)flags;
    ctx->current.input_underflow += (flags & paInputUnderflow) != 0;
    ctx->current.input_overflow += (flags & paInputOverflow) != 0;
    if (input == NULL) {
        ctx->current.null_frames += frames;
    } else {
        const float *samples = input;
        for (unsigned long frame = 0; frame < frames; ++frame) {
            for (unsigned channel = 0; channel < CHANNELS; ++channel) {
                struct channel_stats *s = &ctx->current.channel[channel];
                double value = samples[(size_t)frame * CHANNELS + channel];
                ++s->n;
                if (!isfinite(value))
                    continue;
                ++s->finite_count;
                s->zero += value == 0.0;
                s->sum += value;
                s->sumsq += value * value;
                double magnitude = fabs(value);
                if (magnitude > s->peak)
                    s->peak = magnitude;
            }
        }
    }
    if (now - ctx->window_began >= 1.0)
        emit_window(ctx, now, false);
}

static int input_callback(const void *input, void *output, unsigned long frames,
                          const PaStreamCallbackTimeInfo *time_info,
                          PaStreamCallbackFlags flags, void *user_data)
{
    struct context *ctx = user_data;
    int saved_errno = errno;
    if (!ctx->disabled)
        collect(ctx, input, frames, flags);
    errno = saved_errno;
    return ctx->callback(input, output, frames, time_info, flags, ctx->user_data);
}

PaError Pa_OpenStream(PaStream **stream, const PaStreamParameters *input,
                      const PaStreamParameters *output, double rate,
                      unsigned long frames, PaStreamFlags flags,
                      PaStreamCallback *callback, void *user_data)
{
    int saved_errno = errno;
    (void)pthread_once(&symbols_once, resolve_symbols);
    if (real_open == NULL) {
        errno = saved_errno;
        return paUnanticipatedHostError;
    }
    (void)pthread_once(&logger_once, initialize_logger);
    struct record record = { .event = SKIPPED, .device = input ? input->device : -1,
        .channels = input ? input->channelCount : 0, .rate = rate };
    record.stream_id = atomic_fetch_add_explicit(&next_stream_id, 1,
                                                memory_order_relaxed);
    if (input == NULL) record.reason = OUTPUT_ONLY;
    else if (callback == NULL) record.reason = MISSING_CALLBACK;
    else if (input->sampleFormat & paNonInterleaved) record.reason = NON_INTERLEAVED;
    else if (input->sampleFormat != paFloat32) record.reason = UNSUPPORTED_FORMAT;
    else if (input->channelCount != CHANNELS) record.reason = NOT_STEREO;
    else if (!isfinite(rate) || rate <= 0) record.reason = INVALID_RATE;
    else if (stream == NULL) record.reason = MISSING_DESTINATION;
    else if (real_close == NULL) record.reason = CLOSE_UNAVAILABLE;
    struct context *ctx = NULL;
    /* Need CloseStream for a safe release; unavailable diagnostics just bypass. */
    if (record.reason == NO_REASON && logger_ready && real_close != NULL) {
        ctx = calloc(1, sizeof(*ctx));
        if (ctx != NULL) {
            ctx->callback = callback;
            ctx->user_data = user_data;
            ctx->seconds = stats_seconds;
            ctx->current = record;
        } else {
            record.reason = ALLOCATION_FAILED;
        }
    }
    errno = saved_errno;
    PaError error = real_open(stream, input, output, rate, frames, flags,
                              ctx ? input_callback : callback,
                              ctx ? ctx : user_data);
    saved_errno = errno;
    if (ctx != NULL && error == 0) {
        ctx->stream = *stream;
        (void)pthread_mutex_lock(&contexts_lock);
        ctx->next = contexts;
        contexts = ctx;
        (void)pthread_mutex_unlock(&contexts_lock);
    } else {
        free(ctx);
        if (logger_ready) {
            record.error = error;
            if (error != 0) record.event = OPEN_FAILED;
            publish(&record);
        }
    }
    errno = saved_errno;
    return error;
}

PaError Pa_CloseStream(PaStream *stream)
{
    int saved_errno = errno;
    (void)pthread_once(&symbols_once, resolve_symbols);
    if (real_close == NULL) {
        errno = saved_errno;
        return paUnanticipatedHostError;
    }
    errno = saved_errno;
    /* PortAudio Close succeeds only after its callbacks have ceased. */
    PaError error = real_close(stream);
    saved_errno = errno;
    if (error == 0) {
        (void)pthread_mutex_lock(&contexts_lock);
        struct context **link = &contexts;
        while (*link != NULL && (*link)->stream != stream)
            link = &(*link)->next;
        if (*link != NULL) {
            struct context *ctx = *link;
            *link = ctx->next;
            free(ctx);
        }
        (void)pthread_mutex_unlock(&contexts_lock);
    }
    errno = saved_errno;
    return error;
}
