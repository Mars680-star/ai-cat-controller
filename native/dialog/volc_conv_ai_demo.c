
#define _DEFAULT_SOURCE

/*
 * 火山引擎实时对话服务（Linux K1）。
 *
 * 核心流程：
 *   1. main() 读取 conv_ai_config.json，初始化 PulseAudio 和 SDK。
 *   2. SIGUSR1 开始一轮语音采集；SIGHUP 接收网页文字或播报请求。
 *   3. 麦克风在连续会话内按需上行，由端侧 VAD 判定每句话结束。
 *   4. 云端返回的 TTS PCM 先写入环形缓冲，再由播放线程送到扬声器。
 *   5. 云端 Function Calling 可异步执行 K1 头部动作、天气和电量查询。
 *
 * 并发模型：
 *   - 主线程：录音上传、信号状态机和 SDK 控制命令。
 *   - SDK 回调线程：连接事件、对话状态、消息和下行音频。
 *   - 播放线程：消费环形缓冲中的 TTS 音频。
 *   - 工具线程：执行可能阻塞的电机命令或网络查询，避免阻塞 SDK 回调。
 *
 * 维护约束：
 *   - 信号处理函数只设置原子状态；耗时操作必须留在主循环或工作线程。
 *   - 修改打断逻辑时必须同时清空 SDK 输出、环形缓冲和 PulseAudio 播放缓存。
 *   - Function Calling 参数必须校验后再进入固定动作或本地控制接口。
 *   - 本文件需要在 K1 上链接厂商 SDK 重新编译，不能只复制源码完成部署。
 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <ctype.h>
#include <string.h>
#include <strings.h>
#include <pthread.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>

#include "../common/ai_cat_process_gate.h"

#include <curl/curl.h>
#include <pulse/simple.h>
#include <pulse/error.h>

#include "cJSON.h"

#include "util/volc_ringbuf.h"

#include "volc_conv_ai.h"

#define AUDIO_CHANNEL_NUM 1
#define AUDIO_FRAME_PER_SECOND (32000)
#define AUDIO_PLAYBACK_CHUNK_MS 20
#define AUDIO_PLAYBACK_TARGET_MS 80
#define AUDIO_PLAYBACK_PREBUFFER_MS 20
#define AUDIO_DONE_DRAIN_GRACE_MS 60
#define AUDIO_DONE_FALLBACK_MS 1500
#define PLAYBACK_DRAIN_MAX_FAILURES 3
#define CLOUD_VAD_SILENCE_MS 900
#define CLOUD_INTERRUPT_SPEECH_MS 350
#define MAIN_LOOP_IDLE_POLL_MS 20
#define FUNCTION_NAME_MAX_LEN 64
#define FUNCTION_CALL_ID_MAX_LEN 128
#define FUNCTION_ARGUMENTS_MAX_LEN 512
#define WEATHER_LOCATION_MAX_LEN 128
#define HTTP_RESPONSE_MAX_BYTES (128 * 1024)
#define WAKE_SPEECH_TIMEOUT_MS 30000
#define DEFAULT_FOLLOW_UP_WINDOW_MS 25000
#define MIN_FOLLOW_UP_SECONDS 5
#define MAX_FOLLOW_UP_SECONDS 120
#define THINKING_TIMEOUT_MS 20000
#define CLIENT_COMMIT_ACK_TIMEOUT_MS 6000
#define CLIENT_TRANSCRIPT_TIMEOUT_MS 8000
#define LOCAL_BATTERY_CANCEL_TIMEOUT_MS 1200
#define RESPONSE_START_TIMEOUT_MS 8000
#define RESPONSE_TEXT_RECOVERY_DELAY_MS 3000
#define LOCAL_VAD_SPEECH_RMS 250
#define LOCAL_VAD_START_FRAMES 3
#define LOCAL_VAD_SHORT_SILENCE_MS 900
#define LOCAL_VAD_LONG_SILENCE_MS 650
#define LOCAL_VAD_LONG_VOICED_MS 1200
#define LOCAL_VAD_MAX_UTTERANCE_MS 15000
#define MOTOR_MOTION_COOLDOWN_MS 8000
#define PLAYBACK_CAPTURE_GUARD_MS 350
#define PLAYBACK_TOUCH_GUARD_START_MS 3500
#define PLAYBACK_TOUCH_GUARD_SETTLE_MS 1500
#define WAKE_TONE_DURATION_MS 220
#define WAKE_TONE_POST_GUARD_MS 120
#define BARGE_IN_GUARD_WINDOW_MS 10000
#define BARGE_IN_GUARD_LIMIT 3
#define DIALOG_STATUS_DIR "/run/ai-cat"
#define DIALOG_STATUS_PATH DIALOG_STATUS_DIR "/dialog-status.json"
#define DIALOG_STATUS_TMP_PATH DIALOG_STATUS_DIR "/dialog-status.json.tmp"
#define DIALOG_SESSION_MARKER DIALOG_STATUS_DIR "/dialog-session-active"
#define DIALOG_AUDIO_MARKER DIALOG_STATUS_DIR "/dialog-audio-active"
#define WAKE_PREROLL_PATH DIALOG_STATUS_DIR "/wake-preroll.pcm"
#define WAKE_PREROLL_TMP_PATH DIALOG_STATUS_DIR "/wake-preroll.pcm.tmp"
#define WAKE_PREROLL_MAX_BYTES (16000 * 2 * 30)
#define DIALOG_EVENT_DIR "/var/lib/ai-cat-controller"
#define DIALOG_EVENT_PATH DIALOG_EVENT_DIR "/dialog-events.jsonl"
#define DIALOG_CONFIG_PATH DIALOG_EVENT_DIR "/dialog-runtime-config.json"
#define DIALOG_TEXT_REQUEST_PATH DIALOG_EVENT_DIR "/dialog-text-request.json"
#define PERSONALITY_RUNTIME_PATH DIALOG_EVENT_DIR "/personality-runtime.json"
#define DIALOG_CONFIG_MAX_BYTES 4096
#define DIALOG_TEXT_REQUEST_MAX_BYTES 8192
#define DIALOG_TEXT_MAX_BYTES 4096
#define DIALOG_TEXT_REQUEST_ID_MAX_LEN 64
#define DIALOG_REQUEST_KIND_MAX_LEN 16
#define PROACTIVE_SPEECH_ACK_TIMEOUT_MS 3000
#define LOCAL_DIRECT_TTS_TIMEOUT_MS 8000
#define CLOUD_SESSION_READY_TIMEOUT_MS 15000
#define CLOUD_IDLE_TIMEOUT_MS 30000
#define PERSONALITY_RUNTIME_MAX_BYTES (32 * 1024)
#define PERSONALITY_ID_MAX_LEN 64
#define PERSONALITY_NAME_MAX_LEN 64
#define PERSONALITY_REVISION_MAX_LEN 64
#define PERSONALITY_VOICE_MAX_LEN 128
#define PERSONALITY_POLL_INTERVAL_MS 1000
#define STRINGIFY_VALUE(value) #value
#define STRINGIFY(value) STRINGIFY_VALUE(value)
#define DEVICE_SERIAL_PATH "/proc/device-tree/serial-number"
#define DEVICE_SERIAL_MAX_LEN 64
#define BATTERY_CAPACITY_PATH "/sys/class/power_supply/cw-bat/capacity"
#define BATTERY_STATUS_PATH "/sys/class/power_supply/cw-bat/status"
#define BATTERY_PRESENT_PATH "/sys/class/power_supply/cw-bat/present"
#define BATTERY_VOLTAGE_PATH "/sys/class/power_supply/cw-bat/voltage_now"
#define CHARGER_ONLINE_PATH "/sys/class/power_supply/ip2317-charger/online"

#define WS_BUFFER_CLEAR "{\"type\":\"input_audio_buffer.clear\"}"
#define WS_RESPONSE_CANCEL "{\"type\":\"response.cancel\"}"
#define DEFAULT_SYSTEM_PROMPT \
    "你叫小安，是狐狸造型的智能陪伴伙伴。不要称自己为AI猫。" \
    "直接回答，不加亲密度称呼或固定寒暄。按问题复杂度控制篇幅。" \
    "简单事实、算术、是非题只用1到2句，第一句直接给答案或结论；不要展开推导、举例、复述问题、总结或追问。" \
    "普通问题用2到4句。复杂问题，或用户明确要求解释、步骤、比较、方案、代码、风险或详细说明时，" \
    "按需要完整回答，不设句数上限，不得为缩短语音而省略关键条件、步骤、风险或结论。" \
    "人格、成长倾向和历史回答不得改变上述篇幅分级，尤其不得把简单问题扩写。" \
    "设备电量等实时状态必须调用已配置工具查询，不能猜测。"
#define WS_SESSION_UPDATE \
    "{\"event_id\":\"event_ai_cat_session_config\",\"type\":\"session.update\",\"session\":{" \
    "\"object\":\"realtime.session\",\"agent_config\":{\"WelcomeMessage\":\"\"},\"config\":{" \
    "\"ASRConfig\":{\"TurnDetectionMode\":1,\"VADConfig\":{\"SilenceTime\":" \
    STRINGIFY(CLOUD_VAD_SILENCE_MS) \
    ",\"AIVAD\":false,\"ExpireTime\":1200},\"InterruptConfig\":{" \
    "\"InterruptSpeechDuration\":" STRINGIFY(CLOUD_INTERRUPT_SPEECH_MS) \
    "}},\"LLMConfig\":{\"SystemMessages\":[\"" DEFAULT_SYSTEM_PROMPT \
    "\"]},\"SubtitleConfig\":{\"DisableRTSSubtitle\":false,\"SubtitleMode\":1}}}}"

typedef struct {
    /* 上行音频、可选视频和云端下行播放所需的运行时资源。 */
    uint8_t* audio_rec_buf;
    char* bot_id;
    int mode;
    int frame_len;
    int sample_rate;
    int audio_frame_ms;
    volc_ringbuf_t ring_buf;
    pthread_mutex_t ring_buf_mutex;
    pthread_mutex_t playback_mutex;
    /* Protected by ring_buf_mutex; independent from the touch-noise guard. */
    bool response_audio_done;
    bool response_answer_finished;
    bool response_playback_drained;
    bool response_playback_claimed;
    uint64_t response_audio_done_monotonic_ms;
    uint64_t response_answer_finished_monotonic_ms;
    uint64_t response_last_pcm_monotonic_ms;
    unsigned int response_drain_failures;
    pa_simple* p_capture;
    pa_simple* p_playback;
    pa_sample_spec format;
    pthread_t audio_playback_task;
    volc_engine_t engine;
} realtime_ws_demo_t;

typedef enum {
    LOCAL_BATTERY_ROUTE_NONE = 0,
    LOCAL_BATTERY_ROUTE_READY,
    LOCAL_BATTERY_ROUTE_CANCEL_REQUESTED,
    LOCAL_BATTERY_ROUTE_CANCEL_WAITING,
} local_battery_route_state_e;

/*
 * 跨信号/回调共享的轻量状态。信号处理函数只修改标志位，
 * 所有 SDK 调用都留在主循环执行，避免在信号上下文中做非安全操作。
 */
static volatile sig_atomic_t running = false;               /* 正在采集并上传麦克风 */
static volatile sig_atomic_t ai_playing = false;             /* 云端正在思考或播放回答 */
static volatile sig_atomic_t interrupt = false;              /* 请求打断当前回答 */
static volatile sig_atomic_t start_after_interrupt = false;  /* 打断后立即开始下一轮 */
static volatile sig_atomic_t exit_request = false;           /* 主线程和播放线程退出条件 */
static volatile sig_atomic_t clean_exit_requested = false;   /* 正常空闲退出不触发 systemd 重启 */
static volatile sig_atomic_t process_gate_closing = false;
/* Keep the lease until process exit, including every callback/child/cleanup. */
static struct ai_cat_process_gate process_gate = {.root_fd = -1, .work_fd = -1};
static volatile sig_atomic_t wake_request = false;           /* SIGUSR1 转换出的唤醒请求 */
static volatile sig_atomic_t interrupt_only_request = false; /* SIGUSR2 只打断，不开始录音 */
static volatile sig_atomic_t text_dialog_request = false;    /* SIGHUP 提交网页文字问题 */
static volatile sig_atomic_t wake_tone_after_interrupt = false;
static volatile sig_atomic_t follow_up_prepare_request = false;
static volatile sig_atomic_t echo_guard_request = false;     /* 连续插话触发回声保护 */
static volatile sig_atomic_t echo_guard_active = false;      /* 等待下一次唤醒解除保护 */
static volatile sig_atomic_t session_active = false;          /* 一次唤醒后的连续对话窗口 */
static volatile sig_atomic_t waiting_for_speech = false;      /* 等待用户开始说话 */
static volatile sig_atomic_t playback_capture_guard = false;  /* TTS 播放时暂停上行，阻断自回声 */
static volatile sig_atomic_t playback_write_active = false;   /* 播放线程已取出但尚未写完的音频块 */
static volatile sig_atomic_t discard_playback_audio = false;  /* 打断后丢弃迟到的云端 PCM */
static volatile sig_atomic_t tool_capture_guard = false;      /* 工具执行至回复结束期间暂停上行 */
static volatile sig_atomic_t client_turn_pending = false;     /* 等待云端确认端侧 commit */
static volatile sig_atomic_t input_transcript_pending = false;/* 音频提交后等待用户最终转写 */
static volatile sig_atomic_t response_create_request = false; /* commit 确认后由主线程请求回答 */
static volatile sig_atomic_t response_text_recovery_sent = false;
static volatile sig_atomic_t response_cancel_request = false;
static volatile sig_atomic_t voice_commit_acked = false;      /* 等待最终转写完成本地/云端路由 */
static volatile sig_atomic_t local_battery_reply_request = false;
static volatile sig_atomic_t local_direct_tts_active = false;
static volatile sig_atomic_t local_direct_tts_response_created = false;
static uint64_t speech_start_deadline_ms = 0;                 /* 等待首句/追问的截止时间 */
static uint64_t follow_up_deadline_ms = 0;                    /* 无需唤醒的追问窗口 */
static uint64_t thinking_deadline_ms = 0;                     /* 云端思考超时后重建连接 */
static uint64_t client_commit_ack_deadline_ms = 0;            /* 端侧提交后等待云端确认的截止时间 */
static uint64_t client_transcript_deadline_ms = 0;            /* 端侧提交后等待最终转写的截止时间 */
static uint64_t response_start_deadline_ms = 0;               /* response.create 后等待云端真正开始回答 */
static uint64_t response_text_recovery_deadline_ms = 0;       /* 回答未创建时改走显式文本输入兜底 */
static uint64_t local_direct_tts_deadline_ms = 0;
static uint64_t playback_capture_guard_until_ms = 0;
static uint64_t proactive_speech_deadline_ms = 0;             /* 低优先级播报未被云端接收时恢复 ready */
static uint64_t cloud_idle_deadline_ms = 0;                    /* 无会话时主动释放云端连接 */
static volatile sig_atomic_t proactive_speech_pending = false;
static volatile sig_atomic_t proactive_speech_active = false;
static volatile sig_atomic_t proactive_speech_finish_request = false;
static volatile sig_atomic_t cloud_session_created = false;  /* session.created 已到达 */
static volatile sig_atomic_t cloud_error_received = false;   /* 云端 error 事件要求立即重建会话 */
static volatile sig_atomic_t playback_error_received = false;/* 本地播放错误要求重建进程 */
static bool local_speech_detected = false;                    /* 本地保底 VAD 已确认人声 */
static unsigned int local_speech_frames = 0;
static uint64_t local_silence_duration_ms = 0;
static uint64_t local_voiced_duration_ms = 0;
static uint64_t local_speech_started_ms = 0;
static uint64_t barge_in_window_started_ms = 0;               /* 连续插话计数窗口起点 */
static unsigned int barge_in_count = 0;                       /* 窗口内插话次数 */
static pthread_mutex_t barge_in_guard_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t dialog_audio_guard_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t client_transcript_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t client_route_mutex = PTHREAD_MUTEX_INITIALIZER;
static char pending_user_transcript[DIALOG_TEXT_MAX_BYTES + 1];
static bool voice_response_requested = false;
static local_battery_route_state_e local_battery_route_state =
    LOCAL_BATTERY_ROUTE_NONE;
static uint64_t local_battery_cancel_deadline_ms = 0;
static bool dialog_audio_guard_enabled = false;
static uint64_t dialog_audio_guard_until_ms = 0;
extern char** environ;
static pthread_mutex_t dialog_status_mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned long dialog_status_sequence = 0;
static pthread_mutex_t dialog_event_mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned long dialog_event_sequence = 0;
static unsigned long dialog_turn_sequence = 0;
static unsigned long response_request_sequence = 0;
static unsigned long voice_recovery_sequence = 0;
static unsigned long local_direct_sequence = 0;
static uint64_t dialog_runtime_started_ms = 0;
static char device_serial[DEVICE_SERIAL_MAX_LEN + 1] = "unknown";
static pthread_mutex_t personality_config_mutex = PTHREAD_MUTEX_INITIALIZER;
static char active_personality_id[PERSONALITY_ID_MAX_LEN + 1];
static char active_personality_name[PERSONALITY_NAME_MAX_LEN + 1];
static char active_personality_revision[PERSONALITY_REVISION_MAX_LEN + 1];
static char active_voice_type[PERSONALITY_VOICE_MAX_LEN + 1];
static bool personality_allow_shake = true;
static bool personality_allow_nod = true;
static bool personality_allow_tail = false;
static time_t personality_runtime_mtime = 0;
static long personality_runtime_mtime_ns = 0;
static ino_t personality_runtime_inode = 0;
static dev_t personality_runtime_device = 0;
static off_t personality_runtime_size = -1;
static uint64_t personality_last_poll_ms = 0;

typedef struct {
    unsigned long turn;
    bool active;
    char source[16];
    uint64_t started_ms;
    uint64_t endpoint_silence_audio_ms;
    uint64_t endpoint_ms;
    uint64_t commit_ack_ms;
    uint64_t asr_final_ms;
    uint64_t response_requested_ms;
    uint64_t response_created_ms;
    uint64_t first_tts_transcript_ms;
    uint64_t last_tts_transcript_ms;
    uint64_t first_pcm_ms;
    uint64_t first_playback_write_ms;
    uint64_t tts_transcript_done_ms;
    uint64_t audio_done_ms;
    uint64_t cloud_finished_ms;
    unsigned int tts_transcript_delta_count;
    size_t first_tts_transcript_delta_bytes;
    size_t total_tts_transcript_delta_bytes;
    unsigned int pcm_chunk_count;
    size_t total_pcm_bytes;
} dialog_latency_trace_t;

typedef enum {
    DIALOG_LATENCY_COMMIT_ACK,
    DIALOG_LATENCY_ASR_FINAL,
    DIALOG_LATENCY_RESPONSE_REQUESTED,
    DIALOG_LATENCY_RESPONSE_CREATED,
    DIALOG_LATENCY_FIRST_PLAYBACK_WRITE,
    DIALOG_LATENCY_TTS_TRANSCRIPT_DONE,
    DIALOG_LATENCY_AUDIO_DONE,
    DIALOG_LATENCY_CLOUD_FINISHED,
} dialog_latency_stage_t;

static pthread_mutex_t dialog_latency_mutex = PTHREAD_MUTEX_INITIALIZER;
static dialog_latency_trace_t dialog_latency_trace;
static unsigned long dialog_latency_sequence = 0;

/* Function Calling 分两条消息到达，先暂存 name/call_id，再等待参数完成事件。 */
typedef struct {
    char name[FUNCTION_NAME_MAX_LEN];
    char call_id[FUNCTION_CALL_ID_MAX_LEN];
} pending_function_call_t;

typedef struct {
    realtime_ws_demo_t* demo;
    char call_id[FUNCTION_CALL_ID_MAX_LEN];
    char name[FUNCTION_NAME_MAX_LEN];
    char arguments[FUNCTION_ARGUMENTS_MAX_LEN];
} function_call_task_t;

static pending_function_call_t pending_function_call;
static char last_function_call_id[FUNCTION_CALL_ID_MAX_LEN];
static pthread_mutex_t function_call_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t function_call_finished = PTHREAD_COND_INITIALIZER;
static unsigned int function_call_workers = 0;
static bool function_call_shutdown = false;
static pthread_mutex_t motion_motor_mutex = PTHREAD_MUTEX_INITIALIZER;
static uint64_t motion_last_started_ms = 0;
static pthread_once_t curl_init_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t weather_cache_mutex = PTHREAD_MUTEX_INITIALIZER;
static char cached_weather_location[WEATHER_LOCATION_MAX_LEN];
static char cached_weather_place_name[WEATHER_LOCATION_MAX_LEN];
static double cached_weather_latitude;
static double cached_weather_longitude;

static bool __process_gate_allows_effect(void) {
    if (process_gate_closing || ai_cat_process_gate_check(&process_gate) != 0) {
        process_gate_closing = true;
        clean_exit_requested = true;
        exit_request = true;
        running = false;
        ai_playing = false;
        discard_playback_audio = true;
        return false;
    }
    return true;
}

static void __finish_function_call_task(function_call_task_t* task) {
    free(task);
    pthread_mutex_lock(&function_call_mutex);
    if (function_call_workers > 0) function_call_workers--;
    pthread_cond_broadcast(&function_call_finished);
    pthread_mutex_unlock(&function_call_mutex);
}

static void __drain_function_call_workers(void) {
    pthread_mutex_lock(&function_call_mutex);
    function_call_shutdown = true;
    while (function_call_workers > 0)
        pthread_cond_wait(&function_call_finished, &function_call_mutex);
    pthread_mutex_unlock(&function_call_mutex);
}

/* 调试辅助：需要观察上行帧率时可在主循环中启用。 */
static void __get_fps(void) {
    static time_t last_sec = 0;
    static int fps = 0;
    struct timespec now_time;
    fps++;
    clock_gettime(CLOCK_REALTIME, &now_time);
    if (now_time.tv_sec != last_sec) {
        last_sec = now_time.tv_sec;
        printf("send data fps: %d\n", fps);
        fps = 0;
    }
}

static uint64_t __get_time_ms(void) {
    struct timespec now_time;
    clock_gettime(CLOCK_REALTIME, &now_time);
    return now_time.tv_sec * 1000 + now_time.tv_nsec / 1000000;
}

static uint64_t __get_monotonic_ms(void) {
    struct timespec now_time;
    clock_gettime(CLOCK_MONOTONIC, &now_time);
    return now_time.tv_sec * 1000 + now_time.tv_nsec / 1000000;
}

static int64_t __latency_delta_ms(uint64_t start_ms, uint64_t end_ms) {
    if (start_ms == 0 || end_ms == 0 || end_ms < start_ms) {
        return -1;
    }
    return (int64_t)(end_ms - start_ms);
}

static void __print_dialog_latency_summary(
    const dialog_latency_trace_t* trace,
    const char* outcome,
    uint64_t finished_ms
) {
    if (trace == NULL || trace->turn == 0) {
        return;
    }
    printf(
        "[dialog-latency] runtime=%" PRIu64 " turn=%lu "
        "source=%s outcome=%s "
        "endpoint_silence_audio_ms=%" PRId64 " "
        "endpoint_to_commit_ack_ms=%" PRId64 " "
        "endpoint_to_asr_final_ms=%" PRId64 " "
        "commit_ack_to_request_ms=%" PRId64 " "
        "request_to_response_created_ms=%" PRId64 " "
        "response_created_to_first_tts_transcript_ms=%" PRId64 " "
        "response_created_to_first_pcm_ms=%" PRId64 " "
        "request_to_first_tts_transcript_ms=%" PRId64 " "
        "request_to_first_pcm_ms=%" PRId64 " "
        "request_to_first_playback_write_ms=%" PRId64 " "
        "first_tts_transcript_to_first_pcm_ms=%" PRId64 " "
        "first_pcm_to_first_playback_write_ms=%" PRId64 " "
        "tts_transcript_stream_span_ms=%" PRId64 " "
        "request_to_cloud_finish_ms=%" PRId64 " "
        "cloud_finish_to_playback_drained_ms=%" PRId64 " "
        "request_to_finish_ms=%" PRId64 " "
        "tts_transcript_delta_count=%u "
        "first_tts_transcript_delta_bytes=%zu "
        "total_tts_transcript_delta_bytes=%zu "
        "pcm_chunk_count=%u total_pcm_bytes=%zu\n",
        dialog_runtime_started_ms,
        trace->turn,
        trace->source,
        outcome != NULL ? outcome : "unknown",
        strcmp(trace->source, "voice") == 0
            ? (int64_t)trace->endpoint_silence_audio_ms
            : -1,
        __latency_delta_ms(trace->endpoint_ms, trace->commit_ack_ms),
        __latency_delta_ms(trace->endpoint_ms, trace->asr_final_ms),
        __latency_delta_ms(
            trace->commit_ack_ms,
            trace->response_requested_ms
        ),
        __latency_delta_ms(
            trace->response_requested_ms,
            trace->response_created_ms
        ),
        __latency_delta_ms(
            trace->response_created_ms,
            trace->first_tts_transcript_ms
        ),
        __latency_delta_ms(
            trace->response_created_ms,
            trace->first_pcm_ms
        ),
        __latency_delta_ms(
            trace->response_requested_ms,
            trace->first_tts_transcript_ms
        ),
        __latency_delta_ms(
            trace->response_requested_ms,
            trace->first_pcm_ms
        ),
        __latency_delta_ms(
            trace->response_requested_ms,
            trace->first_playback_write_ms
        ),
        __latency_delta_ms(
            trace->first_tts_transcript_ms,
            trace->first_pcm_ms
        ),
        __latency_delta_ms(
            trace->first_pcm_ms,
            trace->first_playback_write_ms
        ),
        __latency_delta_ms(
            trace->first_tts_transcript_ms,
            trace->last_tts_transcript_ms
        ),
        __latency_delta_ms(
            trace->response_requested_ms,
            trace->cloud_finished_ms
        ),
        outcome != NULL && strcmp(outcome, "playback_drained") == 0
            ? __latency_delta_ms(trace->cloud_finished_ms, finished_ms)
            : -1,
        __latency_delta_ms(trace->response_requested_ms, finished_ms),
        trace->tts_transcript_delta_count,
        trace->first_tts_transcript_delta_bytes,
        trace->total_tts_transcript_delta_bytes,
        trace->pcm_chunk_count,
        trace->total_pcm_bytes
    );
}

static void __finish_dialog_latency(const char* outcome) {
    dialog_latency_trace_t completed = {0};
    bool has_trace = false;
    uint64_t finished_ms = __get_monotonic_ms();

    pthread_mutex_lock(&dialog_latency_mutex);
    if (dialog_latency_trace.active) {
        completed = dialog_latency_trace;
        dialog_latency_trace.active = false;
        has_trace = true;
    }
    pthread_mutex_unlock(&dialog_latency_mutex);

    if (has_trace) {
        __print_dialog_latency_summary(&completed, outcome, finished_ms);
    }
}

static void __begin_dialog_latency(
    const char* source,
    uint64_t endpoint_silence_audio_ms
) {
    dialog_latency_trace_t replaced = {0};
    bool had_replaced_trace = false;
    bool voice_source = source != NULL && strcmp(source, "voice") == 0;
    uint64_t now_ms = __get_monotonic_ms();
    unsigned long turn;

    pthread_mutex_lock(&dialog_latency_mutex);
    if (dialog_latency_trace.active) {
        replaced = dialog_latency_trace;
        dialog_latency_trace.active = false;
        had_replaced_trace = true;
    }
    memset(&dialog_latency_trace, 0, sizeof(dialog_latency_trace));
    dialog_latency_trace.turn = ++dialog_latency_sequence;
    dialog_latency_trace.active = true;
    snprintf(
        dialog_latency_trace.source,
        sizeof(dialog_latency_trace.source),
        "%s",
        source != NULL ? source : "unknown"
    );
    dialog_latency_trace.started_ms = now_ms;
    dialog_latency_trace.endpoint_silence_audio_ms =
        endpoint_silence_audio_ms;
    if (voice_source) {
        dialog_latency_trace.endpoint_ms = now_ms;
    }
    turn = dialog_latency_trace.turn;
    pthread_mutex_unlock(&dialog_latency_mutex);

    if (had_replaced_trace) {
        __print_dialog_latency_summary(&replaced, "superseded", now_ms);
    }
    printf(
        "[dialog-latency] runtime=%" PRIu64 " turn=%lu "
        "source=%s stage=%s elapsed_ms=0\n",
        dialog_runtime_started_ms,
        turn,
        source != NULL ? source : "unknown",
        voice_source
            ? "endpoint_detected"
            : "text_submitted"
    );
}

static void __begin_voice_turn_latency(uint64_t endpoint_silence_audio_ms) {
    __begin_dialog_latency("voice", endpoint_silence_audio_ms);
}

static void __begin_text_turn_latency(void) {
    __begin_dialog_latency("text", 0);
}

static void __mark_dialog_latency_stage(dialog_latency_stage_t stage) {
    const char* stage_name = NULL;
    uint64_t* timestamp = NULL;
    uint64_t now_ms = __get_monotonic_ms();
    uint64_t elapsed_ms = 0;
    unsigned long turn = 0;
    char source[sizeof(dialog_latency_trace.source)] = {0};

    pthread_mutex_lock(&dialog_latency_mutex);
    if (!dialog_latency_trace.active) {
        pthread_mutex_unlock(&dialog_latency_mutex);
        return;
    }
    switch (stage) {
        case DIALOG_LATENCY_COMMIT_ACK:
            timestamp = &dialog_latency_trace.commit_ack_ms;
            stage_name = "commit_ack";
            break;
        case DIALOG_LATENCY_ASR_FINAL:
            timestamp = &dialog_latency_trace.asr_final_ms;
            stage_name = "asr_final";
            break;
        case DIALOG_LATENCY_RESPONSE_REQUESTED:
            timestamp = &dialog_latency_trace.response_requested_ms;
            stage_name = "response_requested";
            break;
        case DIALOG_LATENCY_RESPONSE_CREATED:
            timestamp = &dialog_latency_trace.response_created_ms;
            stage_name = "response_created";
            break;
        case DIALOG_LATENCY_FIRST_PLAYBACK_WRITE:
            timestamp = &dialog_latency_trace.first_playback_write_ms;
            stage_name = "first_playback_write";
            break;
        case DIALOG_LATENCY_TTS_TRANSCRIPT_DONE:
            timestamp = &dialog_latency_trace.tts_transcript_done_ms;
            stage_name = "tts_transcript_done";
            break;
        case DIALOG_LATENCY_AUDIO_DONE:
            timestamp = &dialog_latency_trace.audio_done_ms;
            stage_name = "audio_done";
            break;
        case DIALOG_LATENCY_CLOUD_FINISHED:
            timestamp = &dialog_latency_trace.cloud_finished_ms;
            stage_name = "cloud_finished";
            break;
    }
    if (timestamp == NULL || *timestamp != 0) {
        pthread_mutex_unlock(&dialog_latency_mutex);
        return;
    }
    *timestamp = now_ms;
    turn = dialog_latency_trace.turn;
    snprintf(source, sizeof(source), "%s", dialog_latency_trace.source);
    elapsed_ms = now_ms >= dialog_latency_trace.started_ms
        ? now_ms - dialog_latency_trace.started_ms
        : 0;
    pthread_mutex_unlock(&dialog_latency_mutex);

    printf(
        "[dialog-latency] runtime=%" PRIu64 " turn=%lu "
        "source=%s stage=%s elapsed_ms=%" PRIu64 "\n",
        dialog_runtime_started_ms,
        turn,
        source,
        stage_name,
        elapsed_ms
    );
}

static void __mark_voice_turn_asr_final(void) {
    __mark_dialog_latency_stage(DIALOG_LATENCY_ASR_FINAL);
}

static void __mark_dialog_turn_response_requested(void) {
    __mark_dialog_latency_stage(DIALOG_LATENCY_RESPONSE_REQUESTED);
}

static void __mark_dialog_turn_tts_transcript_delta(size_t delta_bytes) {
    uint64_t now_ms = __get_monotonic_ms();
    uint64_t elapsed_ms = 0;
    unsigned long turn = 0;
    char source[sizeof(dialog_latency_trace.source)] = {0};
    bool first_delta = false;

    pthread_mutex_lock(&dialog_latency_mutex);
    if (
        dialog_latency_trace.active &&
        dialog_latency_trace.response_requested_ms != 0 &&
        delta_bytes > 0
    ) {
        dialog_latency_trace.tts_transcript_delta_count++;
        dialog_latency_trace.total_tts_transcript_delta_bytes += delta_bytes;
        dialog_latency_trace.last_tts_transcript_ms = now_ms;
        if (dialog_latency_trace.first_tts_transcript_ms == 0) {
            dialog_latency_trace.first_tts_transcript_ms = now_ms;
            dialog_latency_trace.first_tts_transcript_delta_bytes = delta_bytes;
            turn = dialog_latency_trace.turn;
            snprintf(source, sizeof(source), "%s", dialog_latency_trace.source);
            elapsed_ms = now_ms >= dialog_latency_trace.started_ms
                ? now_ms - dialog_latency_trace.started_ms
                : 0;
            first_delta = true;
        }
    }
    pthread_mutex_unlock(&dialog_latency_mutex);

    if (first_delta) {
        printf(
            "[dialog-latency] runtime=%" PRIu64 " turn=%lu source=%s "
            "stage=first_tts_transcript_delta "
            "elapsed_ms=%" PRIu64 " delta_bytes=%zu\n",
            dialog_runtime_started_ms,
            turn,
            source,
            elapsed_ms,
            delta_bytes
        );
    }
}

static void __mark_dialog_turn_first_pcm(size_t pcm_bytes) {
    uint64_t now_ms = __get_monotonic_ms();
    uint64_t elapsed_ms = 0;
    unsigned long turn = 0;
    char source[sizeof(dialog_latency_trace.source)] = {0};
    bool first_pcm = false;

    pthread_mutex_lock(&dialog_latency_mutex);
    if (
        dialog_latency_trace.active &&
        dialog_latency_trace.response_requested_ms != 0
    ) {
        dialog_latency_trace.pcm_chunk_count++;
        dialog_latency_trace.total_pcm_bytes += pcm_bytes;
        if (dialog_latency_trace.first_pcm_ms == 0) {
            dialog_latency_trace.first_pcm_ms = now_ms;
            turn = dialog_latency_trace.turn;
            snprintf(source, sizeof(source), "%s", dialog_latency_trace.source);
            elapsed_ms = now_ms >= dialog_latency_trace.started_ms
                ? now_ms - dialog_latency_trace.started_ms
                : 0;
            first_pcm = true;
        }
    }
    pthread_mutex_unlock(&dialog_latency_mutex);

    if (first_pcm) {
        printf(
            "[dialog-latency] runtime=%" PRIu64 " turn=%lu source=%s "
            "stage=first_pcm "
            "elapsed_ms=%" PRIu64 " pcm_bytes=%zu\n",
            dialog_runtime_started_ms,
            turn,
            source,
            elapsed_ms,
            pcm_bytes
        );
    }
}

static void __reset_response_playback_state(realtime_ws_demo_t* demo) {
    if (demo == NULL) {
        return;
    }
    pthread_mutex_lock(&demo->ring_buf_mutex);
    demo->response_audio_done = false;
    demo->response_answer_finished = false;
    demo->response_playback_drained = false;
    demo->response_playback_claimed = false;
    demo->response_audio_done_monotonic_ms = 0;
    demo->response_answer_finished_monotonic_ms = 0;
    demo->response_last_pcm_monotonic_ms = 0;
    demo->response_drain_failures = 0;
    pthread_mutex_unlock(&demo->ring_buf_mutex);
}

static void __mark_response_audio_done(realtime_ws_demo_t* demo) {
    if (demo == NULL) {
        return;
    }
    pthread_mutex_lock(&demo->ring_buf_mutex);
    demo->response_audio_done = true;
    demo->response_audio_done_monotonic_ms = __get_monotonic_ms();
    pthread_mutex_unlock(&demo->ring_buf_mutex);
}

static void __mark_response_answer_finished(realtime_ws_demo_t* demo) {
    if (demo == NULL) {
        return;
    }
    pthread_mutex_lock(&demo->ring_buf_mutex);
    demo->response_answer_finished = true;
    demo->response_answer_finished_monotonic_ms = __get_monotonic_ms();
    pthread_mutex_unlock(&demo->ring_buf_mutex);
}

/* Caller holds ring_buf_mutex. */
static bool __response_ready_to_drain_locked(
    const realtime_ws_demo_t* demo,
    uint64_t now_monotonic_ms
) {
    bool normal_completion;
    bool missing_audio_done_fallback;

    if (
        demo == NULL ||
        !demo->response_answer_finished ||
        demo->response_playback_drained
    ) {
        return false;
    }
    normal_completion =
        demo->response_audio_done &&
        demo->response_audio_done_monotonic_ms != 0 &&
        now_monotonic_ms >=
            demo->response_audio_done_monotonic_ms +
            AUDIO_DONE_DRAIN_GRACE_MS;
    missing_audio_done_fallback =
        !demo->response_audio_done &&
        demo->response_answer_finished_monotonic_ms != 0 &&
        now_monotonic_ms >=
            demo->response_answer_finished_monotonic_ms +
            AUDIO_DONE_FALLBACK_MS &&
        (
            demo->response_last_pcm_monotonic_ms == 0 ||
            now_monotonic_ms >=
                demo->response_last_pcm_monotonic_ms +
                AUDIO_DONE_FALLBACK_MS
        );
    return normal_completion || missing_audio_done_fallback;
}

static void __write_dialog_audio_marker(bool active) {
    if (active) {
        FILE* marker;

        if (mkdir(DIALOG_STATUS_DIR, 0755) != 0 && errno != EEXIST) {
            return;
        }
        marker = fopen(DIALOG_AUDIO_MARKER, "wb");
        if (marker != NULL) {
            fclose(marker);
        }
    } else {
        unlink(DIALOG_AUDIO_MARKER);
    }
}

static void __begin_dialog_audio_guard(void) {
    bool create_marker = false;

    pthread_mutex_lock(&dialog_audio_guard_mutex);
    if (!dialog_audio_guard_enabled) {
        dialog_audio_guard_enabled = true;
        create_marker = true;
    }
    dialog_audio_guard_until_ms =
        __get_time_ms() + PLAYBACK_TOUCH_GUARD_START_MS;
    if (create_marker) {
        __write_dialog_audio_marker(true);
    }
    pthread_mutex_unlock(&dialog_audio_guard_mutex);
}

static void __mark_dialog_audio_activity(void) {
    bool create_marker = false;

    pthread_mutex_lock(&dialog_audio_guard_mutex);
    if (!dialog_audio_guard_enabled) {
        dialog_audio_guard_enabled = true;
        create_marker = true;
    }
    dialog_audio_guard_until_ms =
        __get_time_ms() + PLAYBACK_TOUCH_GUARD_SETTLE_MS;
    if (create_marker) {
        __write_dialog_audio_marker(true);
    }
    pthread_mutex_unlock(&dialog_audio_guard_mutex);
}

static bool __dialog_audio_guard_active(void) {
    bool active;

    pthread_mutex_lock(&dialog_audio_guard_mutex);
    active = dialog_audio_guard_enabled;
    pthread_mutex_unlock(&dialog_audio_guard_mutex);
    return active;
}

static bool __dialog_audio_guard_ready_to_drain(void) {
    bool ready;

    pthread_mutex_lock(&dialog_audio_guard_mutex);
    ready = dialog_audio_guard_enabled &&
        __get_time_ms() >= dialog_audio_guard_until_ms;
    pthread_mutex_unlock(&dialog_audio_guard_mutex);
    return ready;
}

static void __end_dialog_audio_guard(void) {
    pthread_mutex_lock(&dialog_audio_guard_mutex);
    dialog_audio_guard_enabled = false;
    dialog_audio_guard_until_ms = 0;
    __write_dialog_audio_marker(false);
    pthread_mutex_unlock(&dialog_audio_guard_mutex);
}

static void __cancel_cloud_idle_shutdown(void) {
    cloud_idle_deadline_ms = 0;
}

static void __schedule_cloud_idle_shutdown(void) {
    cloud_idle_deadline_ms = __get_time_ms() + CLOUD_IDLE_TIMEOUT_MS;
    printf("cloud idle shutdown scheduled in 30 seconds\n");
}

static uint64_t __get_follow_up_window_ms(void) {
    char buffer[DIALOG_CONFIG_MAX_BYTES + 1];
    FILE* file;
    size_t bytes_read;
    cJSON* root;
    cJSON* seconds_obj;
    int seconds;

    file = fopen(DIALOG_CONFIG_PATH, "r");
    if (file == NULL) {
        return DEFAULT_FOLLOW_UP_WINDOW_MS;
    }
    bytes_read = fread(buffer, 1, DIALOG_CONFIG_MAX_BYTES, file);
    if (ferror(file) || (bytes_read == DIALOG_CONFIG_MAX_BYTES && !feof(file))) {
        fclose(file);
        fprintf(stderr, "dialog config is unreadable or too large\n");
        return DEFAULT_FOLLOW_UP_WINDOW_MS;
    }
    fclose(file);
    buffer[bytes_read] = '\0';

    root = cJSON_Parse(buffer);
    seconds_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "follow_up_seconds");
    if (
        !cJSON_IsNumber(seconds_obj) ||
        seconds_obj->valuedouble != seconds_obj->valueint ||
        seconds_obj->valueint < MIN_FOLLOW_UP_SECONDS ||
        seconds_obj->valueint > MAX_FOLLOW_UP_SECONDS
    ) {
        cJSON_Delete(root);
        fprintf(stderr, "dialog config has invalid follow_up_seconds\n");
        return DEFAULT_FOLLOW_UP_WINDOW_MS;
    }
    seconds = seconds_obj->valueint;
    cJSON_Delete(root);
    printf("follow-up window configured: %d seconds\n", seconds);
    return (uint64_t)seconds * 1000;
}

/* FastAPI 原子写入单条文字请求；原生进程读取后立即删除，避免重复消费。 */
static int __read_text_dialog_request(
    char* request_id,
    size_t request_id_size,
    char* content,
    size_t content_size,
    char* request_kind,
    size_t request_kind_size
) {
    struct stat file_stat;
    FILE* file = NULL;
    char buffer[DIALOG_TEXT_REQUEST_MAX_BYTES + 1];
    size_t bytes_read;
    cJSON* root = NULL;
    cJSON* version_obj;
    cJSON* request_id_obj;
    cJSON* content_obj;
    cJSON* kind_obj;
    const char* request_id_value;
    const char* content_value;
    const char* kind_value;
    size_t index;
    bool has_visible_text = false;
    int result = -1;

    if (
        request_id == NULL || request_id_size == 0 ||
        content == NULL || content_size == 0 ||
        request_kind == NULL || request_kind_size == 0
    ) {
        return -1;
    }
    request_id[0] = '\0';
    content[0] = '\0';
    request_kind[0] = '\0';
    if (
        lstat(DIALOG_TEXT_REQUEST_PATH, &file_stat) != 0 ||
        !S_ISREG(file_stat.st_mode) ||
        file_stat.st_size <= 0 ||
        file_stat.st_size > DIALOG_TEXT_REQUEST_MAX_BYTES
    ) {
        unlink(DIALOG_TEXT_REQUEST_PATH);
        return -1;
    }
    file = fopen(DIALOG_TEXT_REQUEST_PATH, "rb");
    if (file == NULL) {
        return -1;
    }
    bytes_read = fread(buffer, 1, DIALOG_TEXT_REQUEST_MAX_BYTES, file);
    if (ferror(file) || !feof(file)) {
        fclose(file);
        unlink(DIALOG_TEXT_REQUEST_PATH);
        return -1;
    }
    fclose(file);
    unlink(DIALOG_TEXT_REQUEST_PATH);
    buffer[bytes_read] = '\0';

    root = cJSON_Parse(buffer);
    version_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "version");
    request_id_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "request_id");
    content_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "content");
    kind_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "kind");
    if (
        !cJSON_IsNumber(version_obj) || version_obj->valueint != 1 ||
        !cJSON_IsString(request_id_obj) ||
        !cJSON_IsString(content_obj)
    ) {
        goto cleanup;
    }
    request_id_value = request_id_obj->valuestring;
    content_value = content_obj->valuestring;
    kind_value = cJSON_IsString(kind_obj) ? kind_obj->valuestring : "question";
    if (
        request_id_value == NULL || content_value == NULL ||
        strlen(request_id_value) == 0 ||
        strlen(request_id_value) > DIALOG_TEXT_REQUEST_ID_MAX_LEN ||
        strlen(content_value) == 0 ||
        strlen(content_value) > DIALOG_TEXT_MAX_BYTES ||
        kind_value == NULL || strlen(kind_value) == 0 ||
        strlen(kind_value) > DIALOG_REQUEST_KIND_MAX_LEN ||
        (strcmp(kind_value, "question") != 0 && strcmp(kind_value, "speak") != 0)
    ) {
        goto cleanup;
    }
    for (index = 0; content_value[index] != '\0'; index++) {
        if (!isspace((unsigned char)content_value[index])) {
            has_visible_text = true;
            break;
        }
    }
    if (!has_visible_text) {
        goto cleanup;
    }
    snprintf(request_id, request_id_size, "%s", request_id_value);
    snprintf(content, content_size, "%s", content_value);
    snprintf(request_kind, request_kind_size, "%s", kind_value);
    result = 0;

cleanup:
    cJSON_Delete(root);
    return result;
}

static void __reset_barge_in_guard(void) {
    pthread_mutex_lock(&barge_in_guard_mutex);
    barge_in_window_started_ms = 0;
    barge_in_count = 0;
    pthread_mutex_unlock(&barge_in_guard_mutex);
}

static void __reset_local_vad(void) {
    local_speech_detected = false;
    local_speech_frames = 0;
    local_silence_duration_ms = 0;
    local_voiced_duration_ms = 0;
    local_speech_started_ms = 0;
}

static bool __local_vad_should_commit(
    const uint8_t* pcm,
    size_t pcm_len,
    int sample_rate,
    bool* speech_started
) {
    const int16_t* samples = (const int16_t*)pcm;
    size_t sample_count = pcm_len / sizeof(*samples);
    uint64_t sum_squares = 0;
    uint64_t now_ms;
    uint64_t frame_duration_ms;
    uint64_t target_silence_ms;

    *speech_started = false;
    if (sample_count == 0 || sample_rate <= 0) {
        return false;
    }
    now_ms = __get_time_ms();
    frame_duration_ms =
        ((uint64_t)sample_count * 1000 + (uint64_t)sample_rate - 1) /
        (uint64_t)sample_rate;
    for (size_t i = 0; i < sample_count; i++) {
        int32_t sample = samples[i];
        sum_squares += (uint64_t)(sample * sample);
    }

    bool has_speech =
        sum_squares >
        (uint64_t)LOCAL_VAD_SPEECH_RMS *
            LOCAL_VAD_SPEECH_RMS *
            sample_count;
    if (
        local_speech_detected &&
        now_ms - local_speech_started_ms >=
            LOCAL_VAD_MAX_UTTERANCE_MS
    ) {
        printf("local VAD fallback: maximum utterance duration reached\n");
        return true;
    }
    if (has_speech) {
        local_silence_duration_ms = 0;
        local_voiced_duration_ms += frame_duration_ms;
        if (!local_speech_detected) {
            local_speech_frames++;
            if (local_speech_frames >= LOCAL_VAD_START_FRAMES) {
                local_speech_detected = true;
                local_speech_started_ms = now_ms;
                *speech_started = true;
            }
        }
        return false;
    }

    local_speech_frames = 0;
    if (!local_speech_detected) {
        local_voiced_duration_ms = 0;
        return false;
    }
    local_silence_duration_ms += frame_duration_ms;
    target_silence_ms =
        local_voiced_duration_ms < LOCAL_VAD_LONG_VOICED_MS
            ? LOCAL_VAD_SHORT_SILENCE_MS
            : LOCAL_VAD_LONG_SILENCE_MS;
    if (local_silence_duration_ms >= target_silence_ms) {
        printf(
            "local VAD endpoint: voiced_audio_ms=%" PRIu64
            ", target_silence_ms=%" PRIu64
            ", observed_silence_ms=%" PRIu64 "\n",
            local_voiced_duration_ms,
            target_silence_ms,
            local_silence_duration_ms
        );
        return true;
    }
    return false;
}

static void __clear_wake_preroll(
    uint8_t** data,
    size_t* data_len,
    size_t* offset
) {
    free(*data);
    *data = NULL;
    *data_len = 0;
    *offset = 0;
}

/*
 * The wake-word process publishes one bounded raw PCM file with rename(2),
 * then signals this process. Read through a no-following file descriptor and
 * unlink immediately after loading so captured speech never becomes durable
 * project data. A valid zero-byte file means the immediate tone was played but
 * the cloud was already ready, so no buffered samples were needed.
 */
static bool __load_wake_preroll(uint8_t** data, size_t* data_len) {
    struct stat file_stat;
    uint8_t* buffer = NULL;
    size_t offset = 0;
    int fd = -1;

    *data = NULL;
    *data_len = 0;
    fd = open(WAKE_PREROLL_PATH, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        return false;
    }
    if (
        fstat(fd, &file_stat) != 0 ||
        !S_ISREG(file_stat.st_mode) ||
        file_stat.st_uid != 0 ||
        file_stat.st_size < 0 ||
        file_stat.st_size > WAKE_PREROLL_MAX_BYTES ||
        file_stat.st_size % (off_t)sizeof(int16_t) != 0
    ) {
        fprintf(stderr, "wake preroll rejected: invalid file metadata\n");
        close(fd);
        unlink(WAKE_PREROLL_PATH);
        return false;
    }
    if (file_stat.st_size > 0) {
        buffer = (uint8_t*)malloc((size_t)file_stat.st_size);
        if (buffer == NULL) {
            close(fd);
            unlink(WAKE_PREROLL_PATH);
            return false;
        }
        while (offset < (size_t)file_stat.st_size) {
            ssize_t read_result = read(
                fd,
                buffer + offset,
                (size_t)file_stat.st_size - offset
            );
            if (read_result < 0 && errno == EINTR) {
                continue;
            }
            if (read_result <= 0) {
                fprintf(stderr, "wake preroll read failed: %s\n", strerror(errno));
                free(buffer);
                close(fd);
                unlink(WAKE_PREROLL_PATH);
                return false;
            }
            offset += (size_t)read_result;
        }
    }
    close(fd);
    unlink(WAKE_PREROLL_PATH);
    *data = buffer;
    *data_len = (size_t)file_stat.st_size;
    return true;
}

/*
 * 正常用户偶尔插话不会触发保护；扬声器回灌通常会在数秒内重复形成
 * ANSWERING -> LISTENING 循环。达到阈值后由主线程中止云端回答和本地会话。
 */
static void __record_barge_in(uint64_t now_ms) {
    pthread_mutex_lock(&barge_in_guard_mutex);
    if (
        barge_in_window_started_ms == 0 ||
        now_ms - barge_in_window_started_ms > BARGE_IN_GUARD_WINDOW_MS
    ) {
        barge_in_window_started_ms = now_ms;
        barge_in_count = 1;
        pthread_mutex_unlock(&barge_in_guard_mutex);
        return;
    }

    barge_in_count++;
    if (barge_in_count >= BARGE_IN_GUARD_LIMIT) {
        echo_guard_request = true;
    }
    pthread_mutex_unlock(&barge_in_guard_mutex);
}

static void __load_device_serial(void) {
    FILE* fp = fopen(DEVICE_SERIAL_PATH, "rb");
    size_t len;

    if (fp == NULL) {
        return;
    }
    len = fread(device_serial, 1, DEVICE_SERIAL_MAX_LEN, fp);
    fclose(fp);
    while (
        len > 0 &&
        (device_serial[len - 1] == '\0' ||
         device_serial[len - 1] == '\n' ||
         device_serial[len - 1] == '\r')
    ) {
        len--;
    }
    device_serial[len] = '\0';
    if (len == 0) {
        snprintf(device_serial, sizeof(device_serial), "unknown");
    }
}

/*
 * Final cloud transcripts are appended as bounded JSON lines. FastAPI imports
 * them idempotently into SQLite and associates them with the bound device.
 */
static void __write_dialog_event(
    const char* role,
    const char* content,
    const char* cloud_event_id
) {
    cJSON* root = NULL;
    char* json = NULL;
    FILE* fp = NULL;
    char generated_event_id[96];
    char conversation_id[128];
    size_t content_len;

    if (!__process_gate_allows_effect() ||
        role == NULL || content == NULL || content[0] == '\0') {
        return;
    }
    content_len = strlen(content);
    if (content_len > 8192) {
        printf("dialog transcript ignored: text is too long\n");
        return;
    }

    pthread_mutex_lock(&dialog_event_mutex);
    if (strcmp(role, "user") == 0 || dialog_turn_sequence == 0) {
        dialog_turn_sequence++;
    }
    dialog_event_sequence++;
    snprintf(
        generated_event_id,
        sizeof(generated_event_id),
        "native-%" PRIu64 "-%lu",
        dialog_runtime_started_ms,
        dialog_event_sequence
    );
    snprintf(
        conversation_id,
        sizeof(conversation_id),
        "native-%s-%" PRIu64 "-%lu",
        device_serial,
        dialog_runtime_started_ms,
        dialog_turn_sequence
    );

    if (mkdir(DIALOG_EVENT_DIR, 0750) != 0 && errno != EEXIST) {
        pthread_mutex_unlock(&dialog_event_mutex);
        return;
    }
    root = cJSON_CreateObject();
    if (root == NULL) {
        pthread_mutex_unlock(&dialog_event_mutex);
        return;
    }
    cJSON_AddStringToObject(
        root,
        "event_id",
        cloud_event_id != NULL && cloud_event_id[0] != '\0'
            ? cloud_event_id
            : generated_event_id
    );
    cJSON_AddStringToObject(root, "conversation_id", conversation_id);
    cJSON_AddStringToObject(root, "device_serial", device_serial);
    cJSON_AddStringToObject(root, "role", role);
    cJSON_AddStringToObject(root, "content", content);
    cJSON_AddNumberToObject(root, "created_at_ms", (double)__get_time_ms());
    json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json != NULL) {
        fp = fopen(DIALOG_EVENT_PATH, "ab");
        if (fp != NULL) {
            fwrite(json, 1, strlen(json), fp);
            fputc('\n', fp);
            fflush(fp);
            fsync(fileno(fp));
            fclose(fp);
            printf("dialog transcript [%s]: %s\n", role, content);
        }
        free(json);
    }
    pthread_mutex_unlock(&dialog_event_mutex);
}

/*
 * 对话状态通过固定 JSON 文件提供给 FastAPI。先写临时文件再 rename，
 * 读取端永远只会看到一份完整 JSON；会话 marker 供唤醒词进程协调录音占用。
 */
static void __write_dialog_status(const char* state, const char* message) {
    cJSON* root = NULL;
    char* json = NULL;
    FILE* fp = NULL;
    char personality_id[PERSONALITY_ID_MAX_LEN + 1];
    char personality_revision[PERSONALITY_REVISION_MAX_LEN + 1];
    char voice_type[PERSONALITY_VOICE_MAX_LEN + 1];

    if (!__process_gate_allows_effect()) return;

    pthread_mutex_lock(&personality_config_mutex);
    snprintf(
        personality_id,
        sizeof(personality_id),
        "%s",
        active_personality_id
    );
    snprintf(
        personality_revision,
        sizeof(personality_revision),
        "%s",
        active_personality_revision
    );
    snprintf(voice_type, sizeof(voice_type), "%s", active_voice_type);
    pthread_mutex_unlock(&personality_config_mutex);

    pthread_mutex_lock(&dialog_status_mutex);
    if (mkdir(DIALOG_STATUS_DIR, 0755) != 0 && errno != EEXIST) {
        pthread_mutex_unlock(&dialog_status_mutex);
        return;
    }

    if (session_active) {
        FILE* marker = fopen(DIALOG_SESSION_MARKER, "wb");
        if (marker != NULL) {
            fclose(marker);
        }
    } else {
        unlink(DIALOG_SESSION_MARKER);
    }

    root = cJSON_CreateObject();
    if (root == NULL) {
        pthread_mutex_unlock(&dialog_status_mutex);
        return;
    }
    cJSON_AddStringToObject(root, "state", state);
    cJSON_AddStringToObject(root, "message", message);
    cJSON_AddBoolToObject(root, "session_active", session_active != 0);
    cJSON_AddBoolToObject(
        root,
        "can_interrupt",
        session_active != 0 && ai_playing != 0
    );
    cJSON_AddBoolToObject(root, "audio_playing", __dialog_audio_guard_active());
    cJSON_AddNumberToObject(root, "follow_up_deadline_ms", (double)follow_up_deadline_ms);
    cJSON_AddNumberToObject(root, "updated_at_ms", (double)__get_time_ms());
    cJSON_AddNumberToObject(root, "sequence", (double)++dialog_status_sequence);
    cJSON_AddNumberToObject(root, "pid", (double)getpid());
    if (personality_id[0] != '\0') {
        cJSON_AddStringToObject(root, "personality_id", personality_id);
    }
    if (personality_revision[0] != '\0') {
        cJSON_AddStringToObject(
            root,
            "personality_revision",
            personality_revision
        );
    }
    if (voice_type[0] != '\0') {
        cJSON_AddStringToObject(root, "voice_type", voice_type);
    }
    json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL) {
        pthread_mutex_unlock(&dialog_status_mutex);
        return;
    }

    fp = fopen(DIALOG_STATUS_TMP_PATH, "wb");
    if (fp != NULL) {
        fwrite(json, 1, strlen(json), fp);
        fputc('\n', fp);
        fflush(fp);
        fsync(fileno(fp));
        fclose(fp);
        rename(DIALOG_STATUS_TMP_PATH, DIALOG_STATUS_PATH);
    }
    free(json);
    pthread_mutex_unlock(&dialog_status_mutex);
}

static bool __bounded_json_string(cJSON* item, size_t maximum_length) {
    return cJSON_IsString(item) && item->valuestring != NULL &&
        item->valuestring[0] != '\0' && strlen(item->valuestring) <= maximum_length;
}

static void __remember_personality_runtime_metadata(const struct stat* file_stat) {
    personality_runtime_mtime = file_stat->st_mtime;
    personality_runtime_mtime_ns = file_stat->st_mtim.tv_nsec;
    personality_runtime_inode = file_stat->st_ino;
    personality_runtime_device = file_stat->st_dev;
    personality_runtime_size = file_stat->st_size;
}

static bool __personality_runtime_metadata_matches(const struct stat* file_stat) {
    return file_stat->st_mtime == personality_runtime_mtime &&
        file_stat->st_mtim.tv_nsec == personality_runtime_mtime_ns &&
        file_stat->st_ino == personality_runtime_inode &&
        file_stat->st_dev == personality_runtime_device &&
        file_stat->st_size == personality_runtime_size;
}

static bool __personality_runtime_revoked(void) {
    struct stat file_stat;
    /* Missing at first startup is valid. Missing after an applied profile is
     * revocation: keeping the engine alive would retain the previous owner's
     * SystemMessages even though Python has already removed its runtime file.
     * Check before queued wake/text and capture, not only on the idle timer. */
    if (personality_runtime_size < 0) {
        return false;
    }
    if (lstat(PERSONALITY_RUNTIME_PATH, &file_stat) == 0 &&
        S_ISREG(file_stat.st_mode) && file_stat.st_size > 0 &&
        file_stat.st_size <= PERSONALITY_RUNTIME_MAX_BYTES) {
        return false;
    }
    exit_request = true;
    clean_exit_requested = false;
    running = false;
    ai_playing = false;
    discard_playback_audio = true;
    return true;
}

static int __apply_personality_runtime(
    realtime_ws_demo_t* demo,
    bool force
) {
    struct stat file_stat;
    FILE* file = NULL;
    char* buffer = NULL;
    size_t bytes_read;
    cJSON* root = NULL;
    cJSON* version_obj;
    cJSON* personality_id_obj;
    cJSON* personality_name_obj;
    cJSON* revision_obj;
    cJSON* voice_type_obj;
    cJSON* allowed_functions_obj;
    cJSON* session_update_obj;
    cJSON* update_type_obj;
    cJSON* function_obj;
    char* session_update_json = NULL;
    bool allow_shake = false;
    bool allow_nod = false;
    bool allow_tail = false;
    int update_result = -1;

    if (demo == NULL || demo->engine == NULL) {
        return -1;
    }
    if (
        lstat(PERSONALITY_RUNTIME_PATH, &file_stat) != 0 ||
        !S_ISREG(file_stat.st_mode) || file_stat.st_size <= 0 ||
        file_stat.st_size > PERSONALITY_RUNTIME_MAX_BYTES
    ) {
        return -1;
    }
    if (
        !force && __personality_runtime_metadata_matches(&file_stat)
    ) {
        return 0;
    }

    buffer = calloc((size_t)file_stat.st_size + 1, 1);
    if (buffer == NULL) {
        return -1;
    }
    file = fopen(PERSONALITY_RUNTIME_PATH, "rb");
    if (file == NULL) {
        goto cleanup;
    }
    bytes_read = fread(buffer, 1, (size_t)file_stat.st_size, file);
    if (ferror(file) || bytes_read != (size_t)file_stat.st_size) {
        goto cleanup;
    }
    fclose(file);
    file = NULL;
    buffer[bytes_read] = '\0';

    root = cJSON_Parse(buffer);
    version_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "version");
    personality_id_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "personality_id");
    personality_name_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "personality_name");
    revision_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "revision");
    voice_type_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "voice_type");
    allowed_functions_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "allowed_functions");
    session_update_obj = root == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(root, "session_update");
    update_type_obj = cJSON_IsObject(session_update_obj)
        ? cJSON_GetObjectItemCaseSensitive(session_update_obj, "type")
        : NULL;
    if (
        !cJSON_IsNumber(version_obj) || version_obj->valueint != 1 ||
        !__bounded_json_string(personality_id_obj, PERSONALITY_ID_MAX_LEN) ||
        !__bounded_json_string(personality_name_obj, PERSONALITY_NAME_MAX_LEN) ||
        !__bounded_json_string(revision_obj, PERSONALITY_REVISION_MAX_LEN) ||
        !__bounded_json_string(voice_type_obj, PERSONALITY_VOICE_MAX_LEN) ||
        !cJSON_IsArray(allowed_functions_obj) ||
        !cJSON_IsObject(session_update_obj) ||
        !cJSON_IsString(update_type_obj) ||
        strcmp(update_type_obj->valuestring, "session.update") != 0
    ) {
        fprintf(stderr, "invalid personality runtime configuration\n");
        goto cleanup;
    }

    cJSON_ArrayForEach(function_obj, allowed_functions_obj) {
        if (!cJSON_IsString(function_obj) || function_obj->valuestring == NULL) {
            fprintf(stderr, "invalid personality function allowlist\n");
            goto cleanup;
        }
        if (strcmp(function_obj->valuestring, "shake_head") == 0) {
            allow_shake = true;
        } else if (strcmp(function_obj->valuestring, "nod_head") == 0) {
            allow_nod = true;
        } else if (strcmp(function_obj->valuestring, "wag_tail") == 0) {
            allow_tail = true;
        } else {
            fprintf(stderr, "unknown personality function: %s\n", function_obj->valuestring);
            goto cleanup;
        }
    }
    session_update_json = cJSON_PrintUnformatted(session_update_obj);
    if (session_update_json == NULL) {
        goto cleanup;
    }
    if (!__process_gate_allows_effect()) goto cleanup;
    update_result = volc_update(
        demo->engine,
        session_update_json,
        strlen(session_update_json)
    );
    if (update_result < 0) {
        fprintf(stderr, "personality session.update failed: %d\n", update_result);
        goto cleanup;
    }

    pthread_mutex_lock(&personality_config_mutex);
    snprintf(
        active_personality_id,
        sizeof(active_personality_id),
        "%s",
        personality_id_obj->valuestring
    );
    snprintf(
        active_personality_name,
        sizeof(active_personality_name),
        "%s",
        personality_name_obj->valuestring
    );
    snprintf(
        active_personality_revision,
        sizeof(active_personality_revision),
        "%s",
        revision_obj->valuestring
    );
    snprintf(
        active_voice_type,
        sizeof(active_voice_type),
        "%s",
        voice_type_obj->valuestring
    );
    personality_allow_shake = allow_shake;
    personality_allow_nod = allow_nod;
    personality_allow_tail = allow_tail;
    __remember_personality_runtime_metadata(&file_stat);
    pthread_mutex_unlock(&personality_config_mutex);
    printf(
        "personality applied: id=%s revision=%s voice=%s update_result=%d\n",
        personality_id_obj->valuestring,
        revision_obj->valuestring,
        voice_type_obj->valuestring,
        update_result
    );

cleanup:
    if (file != NULL) {
        fclose(file);
    }
    free(session_update_json);
    cJSON_Delete(root);
    free(buffer);
    return update_result < 0 ? -1 : 1;
}

static void __clear_pending_user_transcript(void) {
    pthread_mutex_lock(&client_transcript_mutex);
    pending_user_transcript[0] = '\0';
    pthread_mutex_unlock(&client_transcript_mutex);
}

static bool __store_pending_user_transcript(const char* transcript) {
    size_t transcript_len;

    if (transcript == NULL) {
        return false;
    }
    transcript_len = strlen(transcript);
    if (transcript_len == 0 || transcript_len > DIALOG_TEXT_MAX_BYTES) {
        printf(
            "final transcript not eligible for text recovery: bytes=%zu\n",
            transcript_len
        );
        return false;
    }
    pthread_mutex_lock(&client_transcript_mutex);
    memcpy(pending_user_transcript, transcript, transcript_len + 1);
    pthread_mutex_unlock(&client_transcript_mutex);
    return true;
}

static bool __copy_pending_user_transcript(char* output, size_t output_size) {
    bool has_transcript;

    if (output == NULL || output_size == 0) {
        return false;
    }
    pthread_mutex_lock(&client_transcript_mutex);
    snprintf(output, output_size, "%s", pending_user_transcript);
    has_transcript = pending_user_transcript[0] != '\0';
    pthread_mutex_unlock(&client_transcript_mutex);
    return has_transcript;
}

/*
 * The current CustomLLM path emits the final input transcript only after a
 * response has been requested. Keep response ownership and late local routing
 * under one lock so the callback thread and main loop cannot create two
 * responses for the same committed audio turn.
 */
static bool __queue_voice_response_if_unclaimed(void) {
    bool queued = false;

    pthread_mutex_lock(&client_route_mutex);
    if (
        voice_commit_acked &&
        !voice_response_requested &&
        !response_create_request &&
        !local_battery_reply_request &&
        local_battery_route_state == LOCAL_BATTERY_ROUTE_NONE &&
        !interrupt_only_request &&
        !response_cancel_request
    ) {
        response_create_request = true;
        queued = true;
    }
    pthread_mutex_unlock(&client_route_mutex);
    return queued;
}

static bool __claim_voice_response_request(void) {
    bool claimed = false;

    pthread_mutex_lock(&client_route_mutex);
    if (
        response_create_request &&
        !voice_response_requested &&
        !local_battery_reply_request &&
        local_battery_route_state == LOCAL_BATTERY_ROUTE_NONE &&
        !interrupt_only_request &&
        !response_cancel_request
    ) {
        response_create_request = false;
        voice_response_requested = true;
        claimed = true;
    } else if (response_create_request) {
        response_create_request = false;
    }
    pthread_mutex_unlock(&client_route_mutex);
    return claimed;
}

static bool __voice_response_was_requested(void) {
    bool requested;

    pthread_mutex_lock(&client_route_mutex);
    requested = voice_response_requested;
    pthread_mutex_unlock(&client_route_mutex);
    return requested;
}

static bool __route_local_battery_reply(void) {
    bool cancel_required;

    pthread_mutex_lock(&client_route_mutex);
    response_create_request = false;
    local_battery_reply_request = true;
    cancel_required = voice_response_requested;
    local_battery_route_state = cancel_required
        ? LOCAL_BATTERY_ROUTE_CANCEL_REQUESTED
        : LOCAL_BATTERY_ROUTE_READY;
    pthread_mutex_unlock(&client_route_mutex);
    return cancel_required;
}

static bool __claim_local_battery_cancel_request(uint64_t now_ms) {
    bool claimed = false;

    pthread_mutex_lock(&client_route_mutex);
    if (
        local_battery_reply_request &&
        local_battery_route_state == LOCAL_BATTERY_ROUTE_CANCEL_REQUESTED
    ) {
        local_battery_route_state = LOCAL_BATTERY_ROUTE_CANCEL_WAITING;
        local_battery_cancel_deadline_ms =
            now_ms + LOCAL_BATTERY_CANCEL_TIMEOUT_MS;
        claimed = true;
    }
    pthread_mutex_unlock(&client_route_mutex);
    return claimed;
}

static bool __acknowledge_local_battery_cancel(void) {
    bool acknowledged = false;

    pthread_mutex_lock(&client_route_mutex);
    if (
        local_battery_reply_request &&
        (
            local_battery_route_state == LOCAL_BATTERY_ROUTE_CANCEL_REQUESTED ||
            local_battery_route_state == LOCAL_BATTERY_ROUTE_CANCEL_WAITING
        )
    ) {
        response_create_request = false;
        voice_response_requested = false;
        local_battery_route_state = LOCAL_BATTERY_ROUTE_READY;
        local_battery_cancel_deadline_ms = 0;
        acknowledged = true;
    }
    pthread_mutex_unlock(&client_route_mutex);
    return acknowledged;
}

static bool __local_battery_override_active(void) {
    bool active;

    pthread_mutex_lock(&client_route_mutex);
    active =
        local_battery_reply_request ||
        local_battery_route_state != LOCAL_BATTERY_ROUTE_NONE;
    pthread_mutex_unlock(&client_route_mutex);
    return active;
}

static bool __claim_local_battery_reply(void) {
    bool claimed = false;

    pthread_mutex_lock(&client_route_mutex);
    if (
        local_battery_reply_request &&
        voice_commit_acked &&
        !input_transcript_pending &&
        local_battery_route_state == LOCAL_BATTERY_ROUTE_READY
    ) {
        local_battery_reply_request = false;
        local_battery_route_state = LOCAL_BATTERY_ROUTE_NONE;
        local_battery_cancel_deadline_ms = 0;
        local_direct_tts_active = true;
        local_direct_tts_response_created = false;
        claimed = true;
    }
    pthread_mutex_unlock(&client_route_mutex);
    return claimed;
}

static bool __local_battery_cancel_timed_out(uint64_t now_ms) {
    bool timed_out;

    pthread_mutex_lock(&client_route_mutex);
    timed_out =
        local_battery_route_state == LOCAL_BATTERY_ROUTE_CANCEL_WAITING &&
        local_battery_cancel_deadline_ms != 0 &&
        now_ms >= local_battery_cancel_deadline_ms;
    pthread_mutex_unlock(&client_route_mutex);
    return timed_out;
}

static void __reset_client_turn_state(void) {
    pthread_mutex_lock(&client_route_mutex);
    client_turn_pending = false;
    input_transcript_pending = false;
    response_create_request = false;
    response_text_recovery_sent = false;
    response_cancel_request = false;
    voice_commit_acked = false;
    local_battery_reply_request = false;
    voice_response_requested = false;
    local_battery_route_state = LOCAL_BATTERY_ROUTE_NONE;
    local_battery_cancel_deadline_ms = 0;
    client_commit_ack_deadline_ms = 0;
    client_transcript_deadline_ms = 0;
    response_start_deadline_ms = 0;
    response_text_recovery_deadline_ms = 0;
    pthread_mutex_unlock(&client_route_mutex);
    __clear_pending_user_transcript();
}

static void __reset_local_direct_tts_state(void) {
    pthread_mutex_lock(&client_route_mutex);
    local_battery_reply_request = false;
    pthread_mutex_unlock(&client_route_mutex);
    local_direct_tts_active = false;
    local_direct_tts_response_created = false;
    local_direct_tts_deadline_ms = 0;
}

static bool __has_non_whitespace_text(const char* text) {
    if (text == NULL) {
        return false;
    }
    while (*text != '\0') {
        if (!isspace((unsigned char)*text)) {
            return true;
        }
        text++;
    }
    return false;
}

static bool __is_named_dialog_end_request(const char* text) {
    static const char* names[] = {"小安", "晓安", "小岸", "下安"};
    static const char* commands[] = {
        "停下",
        "停止",
        "别说了",
        "安静",
        "暂停",
        "结束对话",
        "退出对话",
        "结束",
        "退出",
    };
    bool has_name = false;

    if (text == NULL) {
        return false;
    }
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        if (strstr(text, names[i]) != NULL) {
            has_name = true;
            break;
        }
    }
    if (!has_name) {
        return false;
    }
    for (size_t i = 0; i < sizeof(commands) / sizeof(commands[0]); i++) {
        if (strstr(text, commands[i]) != NULL) {
            return true;
        }
    }
    return false;
}

static void __handle_cloud_error(cJSON* root) {
    cJSON* error_obj = cJSON_GetObjectItem(root, "error");
    cJSON* code_obj = cJSON_IsObject(error_obj)
        ? cJSON_GetObjectItem(error_obj, "code")
        : NULL;
    const char* code = cJSON_GetStringValue(code_obj);

    __finish_dialog_latency("cloud_error");
    running = false;
    ai_playing = false;
    session_active = false;
    waiting_for_speech = false;
    follow_up_prepare_request = false;
    speech_start_deadline_ms = 0;
    follow_up_deadline_ms = 0;
    thinking_deadline_ms = 0;
    proactive_speech_pending = false;
    proactive_speech_active = false;
    proactive_speech_finish_request = false;
    proactive_speech_deadline_ms = 0;
    __reset_local_direct_tts_state();
    tool_capture_guard = false;
    discard_playback_audio = false;
    __end_dialog_audio_guard();
    __reset_client_turn_state();
    __reset_local_vad();
    __reset_barge_in_guard();
    cloud_error_received = true;
    __write_dialog_status(
        "recovering",
        "云端暂时无法完成回答，正在重新连接"
    );
    fprintf(
        stderr,
        "cloud error event received: code=%s; restarting cloud session\n",
        code != NULL ? code : "unknown"
    );
    exit_request = true;
}

static void __end_continuous_session(const char* message) {
    running = false;
    ai_playing = false;
    session_active = false;
    waiting_for_speech = false;
    follow_up_prepare_request = false;
    speech_start_deadline_ms = 0;
    follow_up_deadline_ms = 0;
    thinking_deadline_ms = 0;
    proactive_speech_pending = false;
    proactive_speech_active = false;
    proactive_speech_finish_request = false;
    proactive_speech_deadline_ms = 0;
    __reset_local_direct_tts_state();
    discard_playback_audio = false;
    __end_dialog_audio_guard();
    __reset_client_turn_state();
    __reset_local_vad();
    __reset_barge_in_guard();
    __schedule_cloud_idle_shutdown();
    __write_dialog_status("ready", message);
}

static void __reset_follow_up_window(bool after_interrupt) {
    char status_message[96];
    uint64_t follow_up_window_ms = __get_follow_up_window_ms();

    running = true;
    waiting_for_speech = true;
    follow_up_deadline_ms = __get_time_ms() + follow_up_window_ms;
    speech_start_deadline_ms = follow_up_deadline_ms;
    snprintf(
        status_message,
        sizeof(status_message),
        after_interrupt
            ? "回答已打断，%llu 秒内可以直接继续说"
            : "回答结束，%llu 秒内可直接继续对话",
        (unsigned long long)(follow_up_window_ms / 1000)
    );
    __write_dialog_status("followup_listening", status_message);
    printf(
        "follow-up window reset after %s: %llu seconds, deadline_ms=%" PRIu64 "\n",
        after_interrupt ? "interrupt" : "playback",
        (unsigned long long)(follow_up_window_ms / 1000),
        follow_up_deadline_ms
    );
}

/*
 * SIGHUP 提交网页文字问题；SIGUSR1 开始或继续语音；SIGUSR2 只打断回答。
 * SIGINT/SIGTERM 只请求退出，让主循环和播放线程有机会正常收尾。
 */
static void __handle_signal(int sig) {
    if (sig == SIGHUP) {
        text_dialog_request = true;
    } else if (sig == SIGUSR1) {
        wake_request = true;
    } else if (sig == SIGUSR2) {
        interrupt_only_request = true;
    } else if (sig == SIGINT || sig == SIGTERM) {
        clean_exit_requested = true;
        exit_request = true;
    }
}

static void __setup_signal_handlers(void) {
    struct sigaction sa;

    sa.sa_handler = __handle_signal;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    sigaction(SIGUSR1, &sa, NULL);
    sigaction(SIGUSR2, &sa, NULL);
}

static char* __load_config_from_file(const char* filename) {
    /* 返回以 '\0' 结尾的完整 JSON；所有权交给调用方。 */
    FILE* config_fp = fopen(filename, "rb");
    if (config_fp == NULL) {
        printf("failed to open %s for reading.\n", filename);
        return NULL;
    }
    fseek(config_fp, 0, SEEK_END);
    int config_len = ftell(config_fp);
    if (config_len < 0) {
        printf("Failed to seek to end of %s.\n", filename);
        fclose(config_fp);
        return NULL;
    }
    fseek(config_fp, 0, SEEK_SET);
    char* config_data = (char*) malloc(config_len + 1);
    if (config_data == NULL) {
        printf("Malloc config data fail\n");
        fclose(config_fp);
        return NULL;
    }
    memset(config_data, 0, config_len + 1);
    size_t read_size = fread(config_data, 1, config_len, config_fp);
    if (read_size != (size_t)config_len) {
        printf("Failed to read %s, expected %d bytes, got %zu bytes.\n", filename, config_len, read_size);
        free(config_data);
        fclose(config_fp);
        return NULL;
    }
    fclose(config_fp);
	return config_data;
}

static int __play_ready_tone(
    realtime_ws_demo_t* demo,
    int duration_ms,
    const char* description
) {
    static const int16_t wave[16] = {
        0, 3444, 6363, 8316, 9000, 8316, 6363, 3444,
        0, -3444, -6363, -8316, -9000, -8316, -6363, -3444,
    };
    int sample_count;
    int fade_samples;
    int16_t* tone;
    int error = 0;
    int ret = 0;

    if (!__process_gate_allows_effect() ||
        demo == NULL || demo->p_playback == NULL || demo->sample_rate <= 0) {
        return -1;
    }
    sample_count = demo->sample_rate * duration_ms / 1000;
    fade_samples = demo->sample_rate / 200;
    tone = calloc((size_t)sample_count, sizeof(*tone));
    if (tone == NULL) {
        return -1;
    }
    for (int i = 0; i < sample_count; i++) {
        int envelope = fade_samples;
        if (i < envelope) {
            envelope = i;
        }
        if (sample_count - i - 1 < envelope) {
            envelope = sample_count - i - 1;
        }
        tone[i] = (int16_t)(
            (int32_t)wave[i % 16] * envelope / fade_samples
        );
    }

    /*
     * 直接写播放流并 drain，保证用户先听到确认音，随后才开放麦克风。
     * playback_mutex 防止后台 TTS 线程同时操作同一个 pa_simple 句柄。
     */
    playback_capture_guard = true;
    pthread_mutex_lock(&demo->playback_mutex);
    if (!__process_gate_allows_effect()) {
        ret = -1;
    } else if (
        pa_simple_write(
            demo->p_playback,
            (char*)tone,
            sample_count * sizeof(*tone),
            &error
        ) < 0 ||
        pa_simple_drain(demo->p_playback, &error) < 0
    ) {
        printf("wake confirmation tone playback failed: %s\n", pa_strerror(error));
        ret = -1;
    } else {
        printf("%s played\n", description);
    }
    pthread_mutex_unlock(&demo->playback_mutex);
    playback_capture_guard_until_ms =
        __get_time_ms() + WAKE_TONE_POST_GUARD_MS;
    free(tone);
    return ret;
}

static int __reopen_capture_stream(realtime_ws_demo_t* demo) {
    pa_simple* capture = NULL;
    int error = 0;

    if (!__process_gate_allows_effect() || demo == NULL || demo->p_capture == NULL) {
        return -1;
    }

    /*
     * A PA_STREAM_RECORD opened during idle can retain audio that predates the
     * wake signal. pa_simple_flush() did not reliably discard that server-side
     * queue on K1, so recreate the stream after the ready tone. The first frame
     * read by the dialog then belongs to the user's actual question.
     */
    pa_simple_free(demo->p_capture);
    demo->p_capture = NULL;
    if (!__process_gate_allows_effect()) return -1;
    capture = pa_simple_new(
        NULL,
        "Capture",
        PA_STREAM_RECORD,
        NULL,
        "Capture",
        &demo->format,
        NULL,
        NULL,
        &error
    );
    if (capture == NULL) {
        fprintf(
            stderr,
            "capture stream reopen failed: %s\n",
            pa_strerror(error)
        );
        return -1;
    }
    demo->p_capture = capture;
    printf("capture stream reopened; live listening starts now\n");
    return 0;
}

static void* __audio_playback_task(void* arg) {
    /*
     * SDK 回调只负责快速写环形缓冲，本线程以 20 ms 小块持续播放，
     * 避免在回调线程里执行可能阻塞的 pa_simple_write()。
     */
    int len = 0;
    int error  = 0;
    int playback_chunk_len = 0;
    int playback_read_len = 0;
    uint8_t *buffer = NULL;
    realtime_ws_demo_t* demo = (realtime_ws_demo_t*)arg;
    playback_chunk_len = demo->sample_rate * AUDIO_CHANNEL_NUM * sizeof(int16_t) *
                         AUDIO_PLAYBACK_CHUNK_MS / 1000;
    buffer = (uint8_t *)malloc(playback_chunk_len);
    if (NULL == buffer) {
        printf("malloc buffer failed\n");
        return NULL;
    }
    while (!exit_request) {
        if (!__process_gate_allows_effect()) break;
        pthread_mutex_lock(&demo->ring_buf_mutex);
        playback_read_len = volc_ringbuf_getdatasize(demo->ring_buf);
        if (playback_read_len > playback_chunk_len) {
            playback_read_len = playback_chunk_len;
        }
        len = playback_read_len > 0
            ? volc_ringbuf_read(
                demo->ring_buf,
                (char *)buffer,
                playback_read_len
            )
            : 0;
        if (len > 0) {
            playback_write_active = true;
        }
        pthread_mutex_unlock(&demo->ring_buf_mutex);
        if (len > 0) {
            playback_capture_guard = true;
            playback_capture_guard_until_ms =
                __get_time_ms() + PLAYBACK_CAPTURE_GUARD_MS;
            pthread_mutex_lock(&demo->playback_mutex);
            if (!__process_gate_allows_effect()) {
                /* The already-popped block is discarded; cleanup still owns
                 * the process lease and serializes actual PA shutdown. */
            } else if (pa_simple_write(demo->p_playback, buffer, len, &error) < 0) {
                printf("pa_simple_write error: %s\n", pa_strerror(error));
            } else {
                __mark_dialog_latency_stage(
                    DIALOG_LATENCY_FIRST_PLAYBACK_WRITE
                );
            }
            pthread_mutex_unlock(&demo->playback_mutex);
            pthread_mutex_lock(&demo->ring_buf_mutex);
            playback_write_active = false;
            pthread_mutex_unlock(&demo->ring_buf_mutex);
        } else {
            bool response_drain_ready = false;
            bool touch_guard_ready = __dialog_audio_guard_ready_to_drain();
            int playback_pending = 0;
            uint64_t now_monotonic_ms = __get_monotonic_ms();

            pthread_mutex_lock(&demo->ring_buf_mutex);
            playback_pending =
                volc_ringbuf_getdatasize(demo->ring_buf) > 0 ||
                playback_write_active;
            response_drain_ready = __response_ready_to_drain_locked(
                demo,
                now_monotonic_ms
            );
            pthread_mutex_unlock(&demo->ring_buf_mutex);

            if (
                !playback_pending &&
                (response_drain_ready || touch_guard_ready)
            ) {
                int drain_error = 0;
                bool drain_succeeded = false;
                bool drain_failure_limit_reached = false;
                bool response_just_drained = false;

                pthread_mutex_lock(&demo->playback_mutex);
                if (pa_simple_drain(demo->p_playback, &drain_error) < 0) {
                    printf(
                        "dialog playback drain failed: %s\n",
                        pa_strerror(drain_error)
                    );
                } else {
                    drain_succeeded = true;
                }
                pthread_mutex_unlock(&demo->playback_mutex);

                if (drain_succeeded) {
                    pthread_mutex_lock(&demo->ring_buf_mutex);
                    demo->response_drain_failures = 0;
                    playback_pending =
                        volc_ringbuf_getdatasize(demo->ring_buf) > 0 ||
                        playback_write_active;
                    now_monotonic_ms = __get_monotonic_ms();
                    if (
                        !playback_pending &&
                        __response_ready_to_drain_locked(
                            demo,
                            now_monotonic_ms
                        )
                    ) {
                        demo->response_playback_drained = true;
                        response_just_drained = true;
                    }
                    pthread_mutex_unlock(&demo->ring_buf_mutex);
                    if (response_just_drained) {
                        printf(
                            "response playback drained independently of "
                            "touch guard\n"
                        );
                    }
                    if (
                        !playback_pending &&
                        touch_guard_ready &&
                        __dialog_audio_guard_ready_to_drain()
                    ) {
                        __end_dialog_audio_guard();
                        printf("dialog audio touch guard released\n");
                    }
                } else {
                    pthread_mutex_lock(&demo->ring_buf_mutex);
                    demo->response_drain_failures++;
                    drain_failure_limit_reached =
                        demo->response_drain_failures >=
                        PLAYBACK_DRAIN_MAX_FAILURES;
                    pthread_mutex_unlock(&demo->ring_buf_mutex);
                    if (drain_failure_limit_reached) {
                        __finish_dialog_latency("playback_drain_failed");
                        playback_error_received = true;
                        __write_dialog_status(
                            "recovering",
                            "本地播放排空失败，正在重建语音服务"
                        );
                        printf(
                            "playback drain failed %d times; restarting\n",
                            PLAYBACK_DRAIN_MAX_FAILURES
                        );
                        exit_request = true;
                    }
                }
            }
            if (
                playback_capture_guard &&
                __get_time_ms() >= playback_capture_guard_until_ms
            ) {
                playback_capture_guard = false;
            }
            usleep(10 * 1000);
        }
    }
    free(buffer);
    return NULL;
}

static bool is_ready = false;
static void _on_volc_event(volc_engine_t handle, volc_event_t* event, void* user_data)
{
    if (!__process_gate_allows_effect()) return;
    /*
     * 异常断线后以失败状态退出，交给 systemd 的 Restart=on-failure
     * 重建完整 SDK 会话；正常空闲退出不会自动重连。
     * 这样可以避免继续向已关闭的 WebSocket engine 发送音频。
     */
    switch (event->code) {
        case VOLC_EV_CONNECTED:
            is_ready = true;
            printf("Volc Engine connected\n");
            __write_dialog_status("connected", "云端已连接，正在完成会话配置");
            break;
        case VOLC_EV_DISCONNECTED:
            is_ready = false;
            cloud_session_created = false;
            printf("Volc Engine disconnected\n");
            session_active = false;
            discard_playback_audio = false;
            __end_dialog_audio_guard();
            __write_dialog_status("offline", "云端连接已断开，服务正在重连");
            exit_request = true;
            break;
        default:
            printf("Volc Engine event: %d\n", event->code);
            break;
    }
}

static void _on_volc_conversation_status(volc_engine_t handle, volc_conv_status_e status, void* user_data)
{
    realtime_ws_demo_t* demo = (realtime_ws_demo_t*)user_data;
    uint64_t now_ms = __get_time_ms();
    if (!__process_gate_allows_effect()) return;

    /*
     * THINKING/ANSWERING 阶段由独立离线唤醒进程识别命名停止词；本进程
     * 暂停上行，避免扬声器回声进入云端 ASR。
     */
    printf("conversation status changed: %d\n", status);
    if (echo_guard_active) {
        running = false;
        ai_playing = false;
        __write_dialog_status(
            "echo_guard",
            "检测到扬声器回声，已停止本次对话，请重新唤醒"
        );
        return;
    }
    if (
        (
            status == VOLC_CONV_STATUS_INTERRUPTED ||
            status == VOLC_CONV_STATUS_ANSWER_FINISH
        ) &&
        __acknowledge_local_battery_cancel()
    ) {
        discard_playback_audio = true;
        ai_playing = false;
        thinking_deadline_ms = 0;
        response_start_deadline_ms = 0;
        response_text_recovery_deadline_ms = 0;
        __end_dialog_audio_guard();
        __write_dialog_status(
            "processing_audio",
            "旧回答已取消，正在读取设备电量"
        );
        printf("cloud response cancellation acknowledged for local battery route\n");
        return;
    }
    if (
        local_direct_tts_active &&
        !local_direct_tts_response_created &&
        (
            status == VOLC_CONV_STATUS_INTERRUPTED ||
            status == VOLC_CONV_STATUS_ANSWER_FINISH
        )
    ) {
        discard_playback_audio = true;
        printf("stale cloud completion ignored before local TTS response boundary\n");
        return;
    }
    if (__local_battery_override_active() && !local_direct_tts_active) {
        running = false;
        waiting_for_speech = false;
        discard_playback_audio = true;
        __write_dialog_status(
            "processing_audio",
            "正在切换到本机电量播报"
        );
        printf("cloud status ignored during local battery override\n");
        return;
    }
    if (
        discard_playback_audio &&
        (
            status == VOLC_CONV_STATUS_INTERRUPTED ||
            status == VOLC_CONV_STATUS_ANSWER_FINISH
        )
    ) {
        __end_dialog_audio_guard();
        if (session_active) {
            if (response_start_deadline_ms != 0) {
                __write_dialog_status(
                    "thinking",
                    "上一段回答已停止，正在处理新问题"
                );
            } else if (
                client_turn_pending ||
                input_transcript_pending ||
                response_create_request
            ) {
                __write_dialog_status(
                    "processing_audio",
                    "上一段回答已停止，正在提交新问题"
                );
            } else {
                __write_dialog_status(
                    "listening",
                    "回答已打断，请继续说话"
                );
            }
            printf(
                "late response completion ignored while continued session stays active\n"
            );
            return;
        }
        ai_playing = false;
        thinking_deadline_ms = 0;
        __write_dialog_status("interrupted", "回答已停止，等待重新唤醒");
        printf("late response completion kept in interrupted state\n");
        return;
    }
    if (status == VOLC_CONV_STATUS_LISTENING) {
        if (local_direct_tts_active && session_active) {
            running = false;
            waiting_for_speech = false;
            ai_playing = true;
            __write_dialog_status("answering", "正在播报本机实时电量");
            printf("late listening status ignored during local direct TTS\n");
            return;
        }
        proactive_speech_pending = false;
        proactive_speech_active = false;
        proactive_speech_finish_request = false;
        proactive_speech_deadline_ms = 0;
        thinking_deadline_ms = 0;
        if (!session_active) {
            __write_dialog_status("ready", "等待口令“小安，开启对话模式”");
            return;
        }
        if (
            client_turn_pending ||
            input_transcript_pending
        ) {
            running = false;
            __write_dialog_status("processing_audio", "问题已发送，等待云端确认");
            return;
        }
        if (ai_playing && demo != NULL) {
            pthread_mutex_lock(&demo->ring_buf_mutex);
            volc_ringbuf_clear(demo->ring_buf);
            pthread_mutex_unlock(&demo->ring_buf_mutex);
            __record_barge_in(now_ms);
        }
        ai_playing = false;
        running = true;
        __write_dialog_status(
            "listening",
            waiting_for_speech
                ? "麦克风已开启，请开始说话"
                : "正在接收语音"
        );
    } else if (status == VOLC_CONV_STATUS_THINKING) {
        proactive_speech_pending = false;
        proactive_speech_active = false;
        proactive_speech_finish_request = false;
        proactive_speech_deadline_ms = 0;
        __reset_local_vad();
        running = false;
        waiting_for_speech = false;
        speech_start_deadline_ms = 0;
        follow_up_deadline_ms = 0;
        thinking_deadline_ms = now_ms + THINKING_TIMEOUT_MS;
        ai_playing = true;
        __write_dialog_status("thinking", "问题已收到，正在思考");
    } else if (status == VOLC_CONV_STATUS_ANSWERING) {
        proactive_speech_pending = false;
        proactive_speech_active = false;
        proactive_speech_finish_request = false;
        proactive_speech_deadline_ms = 0;
        __reset_local_vad();
        client_turn_pending = false;
        client_commit_ack_deadline_ms = 0;
        running = false;
        waiting_for_speech = false;
        speech_start_deadline_ms = 0;
        follow_up_deadline_ms = 0;
        thinking_deadline_ms = 0;
        ai_playing = true;
        __begin_dialog_audio_guard();
        __write_dialog_status("answering", "正在回答");
    } else if (
        status == VOLC_CONV_STATUS_INTERRUPTED ||
        status == VOLC_CONV_STATUS_ANSWER_FINISH
    ) {
        if (
            status == VOLC_CONV_STATUS_INTERRUPTED &&
            local_direct_tts_active
        ) {
            __reset_local_direct_tts_state();
        }
        if (status == VOLC_CONV_STATUS_INTERRUPTED) {
            __finish_dialog_latency("cloud_interrupted");
        } else {
            /*
             * 云端完成不等于扬声器已经播放完。先记录云端完成点，连续
             * 对话则等本地播放队列排空后再结束本轮时延统计。
            */
            __mark_dialog_latency_stage(DIALOG_LATENCY_CLOUD_FINISHED);
        }
        proactive_speech_pending = false;
        proactive_speech_active = false;
        proactive_speech_finish_request = false;
        proactive_speech_deadline_ms = 0;
        __reset_local_vad();
        __reset_client_turn_state();
        tool_capture_guard = false;
        thinking_deadline_ms = 0;
        if (status == VOLC_CONV_STATUS_ANSWER_FINISH) {
            ai_playing = true;
            if (!__dialog_audio_guard_active()) {
                __begin_dialog_audio_guard();
            }
            __reset_barge_in_guard();
        } else {
            ai_playing = false;
        }
        if (session_active) {
            if (status == VOLC_CONV_STATUS_ANSWER_FINISH) {
                __mark_response_answer_finished(demo);
                /*
                 * SDK 的完成事件可能早于扬声器真正播放完毕。主线程等待播放
                 * 队列排空后再清缓冲并开放追问，避免尾音被当作用户输入。
                 */
                running = false;
                waiting_for_speech = false;
                follow_up_deadline_ms = 0;
                speech_start_deadline_ms = 0;
                playback_capture_guard = true;
                follow_up_prepare_request = true;
                __write_dialog_status(
                    "followup_preparing",
                    "回答播放完毕后将自然继续聆听"
                );
            } else {
                __reset_follow_up_window(true);
            }
        } else {
            running = false;
            if (status == VOLC_CONV_STATUS_ANSWER_FINISH) {
                __finish_dialog_latency("answer_finish");
            }
            __write_dialog_status(
                status == VOLC_CONV_STATUS_INTERRUPTED ? "interrupted" : "ready",
                status == VOLC_CONV_STATUS_INTERRUPTED
                    ? "回答已打断"
                    : "等待口令“小安，开启对话模式”"
            );
        }
    }
}

static void _on_volc_audio_data(volc_engine_t handle, const void* data_ptr, size_t data_len, volc_audio_frame_info_t* info_ptr, void* user_data)
{
    if (!__process_gate_allows_effect()) return;
    /* SDK 下发的是待播放 PCM；此处只入队，播放工作由独立线程完成。 */
		realtime_ws_demo_t* demo = (realtime_ws_demo_t*)user_data;
		bool proactive_audio = false;
		bool first_session_audio = false;
		int written = 0;
	if (demo == NULL) {
		printf("demo is NULL\n");
		return;
	}
    if (data_ptr == NULL || data_len == 0) {
        return;
    }
    proactive_audio =
        proactive_speech_pending || proactive_speech_active ||
        proactive_speech_finish_request;
    if (discard_playback_audio && !proactive_audio) {
        return;
    }

    /*
     * Serialize the final response-completion claim with PCM admission. If
     * this callback wins the lock, the ring is non-empty and the main loop
     * cannot open follow-up capture yet. If the main loop wins, this is stale
     * PCM from the completed response and must not mutate dialog state.
     */
    pthread_mutex_lock(&demo->ring_buf_mutex);
    if (!proactive_audio && demo->response_playback_claimed) {
        pthread_mutex_unlock(&demo->ring_buf_mutex);
        printf("late response PCM dropped after playback completion claim\n");
        return;
    }
    if (!proactive_audio) {
        /* Any admitted late PCM invalidates an earlier drain decision/done. */
        demo->response_playback_drained = false;
        demo->response_audio_done = false;
        demo->response_audio_done_monotonic_ms = 0;
        demo->response_last_pcm_monotonic_ms = __get_monotonic_ms();
    }
    written = volc_ringbuf_write(demo->ring_buf, (char *)data_ptr, data_len);
    pthread_mutex_unlock(&demo->ring_buf_mutex);
    if (written < 0 || (size_t)written != data_len) {
        printf("write audio data to ring buf fail!!!!!!!!\n");
    }

    __mark_dialog_audio_activity();
    if (!proactive_audio && session_active) {
        __mark_dialog_turn_first_pcm(data_len);
        /*
         * The low-load WebSocket transport can deliver response PCM before it
         * emits VOLC_CONV_STATUS_ANSWERING. Publish the answering state from
         * the first PCM frame so the independent local recognizer starts
         * listening for the named stop command immediately.
         */
        first_session_audio = thinking_deadline_ms != 0;
        thinking_deadline_ms = 0;
        ai_playing = true;
        if (first_session_audio) {
            __write_dialog_status("answering", "正在回答，可说“小安停下”打断");
            printf("response PCM started; local interruption enabled\n");
        }
    }
    if (proactive_audio) {
        discard_playback_audio = false;
        if (
            !proactive_speech_active &&
            !proactive_speech_finish_request
        ) {
            proactive_speech_pending = false;
            proactive_speech_active = true;
            proactive_speech_deadline_ms = 0;
            ai_playing = true;
            __write_dialog_status("proactive_speaking", "正在自主播报");
            printf("proactive input_tts PCM playback started\n");
        }
    }
}

static void _on_volc_video_data(volc_engine_t handle, const void* data_ptr, size_t data_len, volc_video_frame_info_t* info_ptr, void* user_data)
{
    /* 当前 K1 验证未启用视频下行，保留回调接口便于后续扩展。 */
}

static void __on_subtitle_message_received(cJSON* root) {
    /* RTC 模式字幕解析；当前 K1 低负载方案使用 WS 模式。 */
    if (NULL == root) {
        return;
    }
    cJSON * type_obj = cJSON_GetObjectItem(root, "type");
    if (type_obj != NULL && strcmp("subtitle", cJSON_GetStringValue(type_obj)) == 0) {
        cJSON* data_obj_arr = cJSON_GetObjectItem(root, "data");
        cJSON* obji = NULL;
        cJSON_ArrayForEach(obji, data_obj_arr) {
            cJSON* user_id_obj = cJSON_GetObjectItem(obji, "userId");
            cJSON* text_obj = cJSON_GetObjectItem(obji, "text");
            if (user_id_obj && text_obj) {
                printf("subtitle:%s:%s\n", cJSON_GetStringValue(user_id_obj), cJSON_GetStringValue(text_obj));
            }
        }
    }
}

static int __send_dialog_text_item(
    realtime_ws_demo_t* demo,
    const char* event_id,
    const char* text,
    const char* content_type,
    int interrupt_mode
) {
    volc_message_info_t msg_info = {0};
    cJSON* root = cJSON_CreateObject();
    cJSON* item = cJSON_CreateObject();
    cJSON* content = cJSON_CreateArray();
    cJSON* content_item = cJSON_CreateObject();
    char* json_str = NULL;
    int ret = -1;

    if (
        !__process_gate_allows_effect() ||
        demo == NULL || event_id == NULL || text == NULL ||
        content_type == NULL ||
        root == NULL || item == NULL || content == NULL || content_item == NULL
    ) {
        cJSON_Delete(root);
        cJSON_Delete(item);
        cJSON_Delete(content);
        cJSON_Delete(content_item);
        return -1;
    }
    cJSON_AddStringToObject(root, "event_id", event_id);
    cJSON_AddStringToObject(root, "type", "conversation.item.create");
    cJSON_AddItemToObject(root, "item", item);
    cJSON_AddStringToObject(item, "type", "message");
    cJSON_AddStringToObject(item, "role", "user");
    cJSON_AddNumberToObject(item, "interrupt_mode", interrupt_mode);
    cJSON_AddItemToObject(item, "content", content);
    cJSON_AddStringToObject(content_item, "type", content_type);
    cJSON_AddStringToObject(content_item, "text", text);
    cJSON_AddItemToArray(content, content_item);

    json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json_str == NULL) {
        return -1;
    }
    msg_info.is_binary = true;
    ret = __process_gate_allows_effect()
        ? volc_send_message(demo->engine, json_str, strlen(json_str), &msg_info)
        : -1;
    printf(
        "dialog text item sent, type=%s, event_id=%s, ret=%d\n",
        content_type,
        event_id,
        ret
    );
    free(json_str);
    return ret;
}

static int __send_text_dialog_item(
    realtime_ws_demo_t* demo,
    const char* event_id,
    const char* text
) {
    return __send_dialog_text_item(demo, event_id, text, "input_text", 1);
}

static int __send_proactive_speech_item(
    realtime_ws_demo_t* demo,
    const char* event_id,
    const char* text
) {
    /* 低优先级在用户已经开始交互时由云端丢弃，不抢占真实对话。 */
    return __send_dialog_text_item(demo, event_id, text, "input_tts", 3);
}

static int __send_direct_speech_item(
    realtime_ws_demo_t* demo,
    const char* event_id,
    const char* text
) {
    /* 用户明确查询的本地结果属于当前轮次，使用高优先级直接进入 TTS。 */
    return __send_dialog_text_item(demo, event_id, text, "input_tts", 1);
}

static int __send_function_call_output(realtime_ws_demo_t* demo, const char* call_id, const char* output) {
    /* 将设备侧工具执行结果关联到原 call_id 并发送回云端。 */
    volc_message_info_t msg_info = { 0 };
    cJSON* root = cJSON_CreateObject();
    cJSON* item = cJSON_CreateObject();
    char event_id[FUNCTION_CALL_ID_MAX_LEN + 16];
    char* json_str;
    int ret;

    if (demo == NULL || call_id == NULL || output == NULL || root == NULL || item == NULL) {
        cJSON_Delete(root);
        cJSON_Delete(item);
        return -1;
    }

    snprintf(event_id, sizeof(event_id), "event_%s", call_id);

    cJSON_AddStringToObject(root, "event_id", event_id);
    cJSON_AddStringToObject(root, "type", "conversation.item.create");
    cJSON_AddItemToObject(root, "item", item);
    cJSON_AddStringToObject(item, "type", "function_call_output");
    cJSON_AddStringToObject(item, "call_id", call_id);
    cJSON_AddStringToObject(item, "output", output);

    json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json_str == NULL) {
        printf("cJSON_Print failed\n");
        return -1;
    }
    printf("function call output: %s\n", json_str);
    msg_info.is_binary = true;
    ret = __process_gate_allows_effect()
        ? volc_send_message(demo->engine, json_str, strlen(json_str), &msg_info)
        : -1;
    free(json_str);
    return ret;
}

typedef struct {
    char* data;
    size_t size;
} http_response_t;

static void __initialize_curl(void) {
    curl_global_init(CURL_GLOBAL_DEFAULT);
}

static size_t __write_http_response(
    void* contents,
    size_t element_size,
    size_t element_count,
    void* user_data
) {
    size_t incoming_size = element_size * element_count;
    http_response_t* response = (http_response_t*)user_data;
    char* resized;

    if (
        response == NULL ||
        incoming_size > HTTP_RESPONSE_MAX_BYTES ||
        response->size > HTTP_RESPONSE_MAX_BYTES - incoming_size
    ) {
        return 0;
    }
    resized = realloc(response->data, response->size + incoming_size + 1);
    if (resized == NULL) {
        return 0;
    }
    response->data = resized;
    memcpy(response->data + response->size, contents, incoming_size);
    response->size += incoming_size;
    response->data[response->size] = '\0';
    return incoming_size;
}

static cJSON* __http_get_json(const char* url) {
    http_response_t response = {0};
    CURL* curl;
    CURLcode result;
    long status_code = 0;
    cJSON* json = NULL;

    if (!__process_gate_allows_effect()) return NULL;
    pthread_once(&curl_init_once, __initialize_curl);
    curl = curl_easy_init();
    if (curl == NULL) {
        return NULL;
    }
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 3000L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 7000L);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "ai-cat-controller/0.1");
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, __write_http_response);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    result = curl_easy_perform(curl);
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status_code);
    curl_easy_cleanup(curl);

    if (result == CURLE_OK && status_code >= 200 && status_code < 300) {
        json = cJSON_Parse(response.data);
    } else {
        fprintf(
            stderr,
            "weather HTTP request failed: curl=%d, status=%ld\n",
            (int)result,
            status_code
        );
    }
    free(response.data);
    return json;
}

static bool __is_weather_function(const char* name) {
    return name != NULL && (
        strcmp(name, "get_weather") == 0 ||
        strcmp(name, "get_current_weather") == 0 ||
        strcmp(name, "check_weather") == 0
    );
}

static bool __is_battery_function(const char* name) {
    return name != NULL && (
        strcmp(name, "get_battery_status") == 0 ||
        strcmp(name, "query_battery_status") == 0 ||
        strcmp(name, "check_battery") == 0
    );
}

static bool __text_contains_any(
    const char* text,
    const char* const* terms,
    size_t term_count
) {
    if (text == NULL) {
        return false;
    }
    for (size_t i = 0; i < term_count; i++) {
        if (strstr(text, terms[i]) != NULL) {
            return true;
        }
    }
    return false;
}

/*
 * Third-party CustomLLM models do not all preserve Volcengine Function
 * Calling. Route only unambiguous queries about this device's live battery
 * state locally; explanatory questions still go to the LLM.
 */
static bool __is_local_battery_status_request(const char* text) {
    static const char* excluded_devices[] = {
        "手机", "电脑", "平板", "汽车", "耳机", "手表", "遥控器"
    };
    static const char* explanatory_terms[] = {
        "为什么", "怎么保养", "如何保养", "原理", "寿命", "更换电池",
        "哪种电池", "什么电池"
    };
    static const char* current_terms[] = {
        "当前", "现在", "还有", "还剩", "剩余", "多少", "百分",
        "状态", "查看", "查询", "读取", "告诉", "报一下", "是不是",
        "是否", "有没有", "连接", "正在"
    };
    bool has_battery_subject;
    bool has_current_cue;

    if (text == NULL || text[0] == '\0') {
        return false;
    }
    if (
        __text_contains_any(
            text,
            excluded_devices,
            sizeof(excluded_devices) / sizeof(excluded_devices[0])
        ) ||
        __text_contains_any(
            text,
            explanatory_terms,
            sizeof(explanatory_terms) / sizeof(explanatory_terms[0])
        )
    ) {
        return false;
    }
    if (strstr(text, "电池电压") != NULL) {
        return true;
    }
    has_battery_subject =
        strstr(text, "电量") != NULL ||
        strstr(text, "电池") != NULL ||
        strstr(text, "充电") != NULL ||
        strstr(text, "多少电") != NULL ||
        strstr(text, "有电吗") != NULL;
    if (!has_battery_subject) {
        return false;
    }
    has_current_cue = __text_contains_any(
        text,
        current_terms,
        sizeof(current_terms) / sizeof(current_terms[0])
    );
    if (has_current_cue || strstr(text, "在充电") != NULL) {
        return true;
    }
    return strstr(text, "电量") != NULL && strlen(text) <= 18;
}

static int __read_sysfs_text(const char* path, char* output, size_t output_size) {
    FILE* file;
    size_t length;

    if (path == NULL || output == NULL || output_size < 2) {
        return -1;
    }
    file = fopen(path, "r");
    if (file == NULL) {
        return -1;
    }
    if (fgets(output, (int)output_size, file) == NULL) {
        fclose(file);
        return -1;
    }
    fclose(file);
    length = strcspn(output, "\r\n");
    output[length] = '\0';
    return length == 0 ? -1 : 0;
}

static int __read_sysfs_int(
    const char* path,
    int minimum,
    int maximum,
    int* output
) {
    char text[64];
    char* end;
    long value;

    if (output == NULL || __read_sysfs_text(path, text, sizeof(text)) != 0) {
        return -1;
    }
    errno = 0;
    value = strtol(text, &end, 10);
    while (*end == ' ' || *end == '\t') {
        end++;
    }
    if (errno != 0 || end == text || *end != '\0' || value < minimum || value > maximum) {
        return -1;
    }
    *output = (int)value;
    return 0;
}

static int __query_battery_status(char* output, size_t output_size) {
    char reported_status[64] = {0};
    const char* state_text;
    int capacity;
    int present;
    int voltage_uv;
    int charger_online;
    bool has_voltage;
    bool has_charger;

    if (output == NULL || output_size == 0) {
        return -1;
    }
    if (
        __read_sysfs_int(BATTERY_CAPACITY_PATH, 0, 100, &capacity) != 0 ||
        __read_sysfs_int(BATTERY_PRESENT_PATH, 0, 1, &present) != 0
    ) {
        snprintf(output, output_size, "暂时无法读取设备电量，请稍后再试。");
        return -1;
    }
    if (present == 0) {
        snprintf(output, output_size, "当前未检测到设备电池。");
        return -1;
    }

    has_voltage = __read_sysfs_int(
        BATTERY_VOLTAGE_PATH,
        0,
        20000000,
        &voltage_uv
    ) == 0;
    has_charger = __read_sysfs_int(
        CHARGER_ONLINE_PATH,
        0,
        1,
        &charger_online
    ) == 0;
    if (__read_sysfs_text(BATTERY_STATUS_PATH, reported_status, sizeof(reported_status)) != 0) {
        reported_status[0] = '\0';
    }

    if (has_charger && charger_online == 0) {
        state_text = "正在使用电池，充电器未连接";
    } else if (has_charger && charger_online == 1) {
        if (capacity == 100 || strcasecmp(reported_status, "Full") == 0) {
            state_text = "电池已充满，充电器已连接";
        } else if (strcasecmp(reported_status, "Charging") == 0) {
            state_text = "正在充电，充电器已连接";
        } else {
            state_text = "充电器已连接，但当前未充电";
        }
    } else if (strcasecmp(reported_status, "Charging") == 0) {
        state_text = "正在充电，充电器状态未知";
    } else if (strcasecmp(reported_status, "Full") == 0) {
        state_text = "电池已充满，充电器状态未知";
    } else {
        state_text = "正在使用电池，充电器状态未知";
    }

    if (has_voltage) {
        snprintf(
            output,
            output_size,
            "当前电量%d%%，%s，电池电压%.2f伏。",
            capacity,
            state_text,
            voltage_uv / 1000000.0
        );
    } else {
        snprintf(output, output_size, "当前电量%d%%，%s。", capacity, state_text);
    }
    printf(
        "battery query capacity=%d, status=%s, charger_online=%d, voltage_uv=%d\n",
        capacity,
        reported_status[0] == '\0' ? "unknown" : reported_status,
        has_charger ? charger_online : -1,
        has_voltage ? voltage_uv : -1
    );
    return 0;
}

static int __start_local_battery_reply(
    realtime_ws_demo_t* demo,
    const char* event_id
) {
    char reply[512];
    int query_result;
    int send_result;

    if (demo == NULL || event_id == NULL || event_id[0] == '\0') {
        return -1;
    }
    query_result = __query_battery_status(reply, sizeof(reply));
    __reset_client_turn_state();
    __reset_response_playback_state(demo);
    /*
     * Keep old response PCM blocked until input_tts emits its own
     * response.created boundary. This is required when a battery intent is
     * learned only after a speculative CustomLLM response was requested.
     */
    discard_playback_audio = true;
    running = false;
    waiting_for_speech = false;
    speech_start_deadline_ms = 0;
    follow_up_deadline_ms = 0;
    follow_up_prepare_request = false;
    local_direct_tts_active = true;
    local_direct_tts_response_created = false;
    local_direct_tts_deadline_ms =
        __get_time_ms() + LOCAL_DIRECT_TTS_TIMEOUT_MS;
    ai_playing = true;
    thinking_deadline_ms = local_direct_tts_deadline_ms;
    __begin_dialog_audio_guard();
    __mark_dialog_turn_response_requested();
    __write_dialog_status("answering", "正在播报本机实时电量");
    send_result = __send_direct_speech_item(demo, event_id, reply);
    if (send_result < 0) {
        __reset_local_direct_tts_state();
        thinking_deadline_ms = 0;
        ai_playing = false;
        __end_dialog_audio_guard();
        return send_result;
    }
    __write_dialog_event("assistant", reply, event_id);
    printf(
        "local battery reply sent to TTS: query_ret=%d, event_id=%s\n",
        query_result,
        event_id
    );
    return send_result;
}

static const char* __weather_description(int code) {
    if (code == 0) {
        return "晴";
    }
    if (code == 1 || code == 2) {
        return "少云";
    }
    if (code == 3) {
        return "阴";
    }
    if (code == 45 || code == 48) {
        return "有雾";
    }
    if (code >= 51 && code <= 57) {
        return "毛毛雨";
    }
    if (code >= 61 && code <= 67) {
        return "有雨";
    }
    if (code >= 71 && code <= 77) {
        return "有雪";
    }
    if (code >= 80 && code <= 82) {
        return "有阵雨";
    }
    if (code >= 85 && code <= 86) {
        return "有阵雪";
    }
    if (code >= 95 && code <= 99) {
        return "有雷暴";
    }
    return "天气状况未知";
}

static cJSON* __first_array_item(cJSON* object, const char* name) {
    cJSON* array = cJSON_GetObjectItemCaseSensitive(object, name);

    if (!cJSON_IsArray(array) || cJSON_GetArraySize(array) == 0) {
        return NULL;
    }
    return cJSON_GetArrayItem(array, 0);
}

static int __query_weather(
    const char* requested_location,
    char* output,
    size_t output_size
) {
    const char* default_location;
    const char* location = requested_location;
    char geocoding_url[1024];
    char forecast_url[1024];
    char* escaped_location;
    cJSON* geocoding = NULL;
    cJSON* forecast = NULL;
    cJSON* results;
    cJSON* place;
    cJSON* place_name;
    cJSON* latitude;
    cJSON* longitude;
    cJSON* current;
    cJSON* daily;
    cJSON* temperature;
    cJSON* apparent_temperature;
    cJSON* humidity;
    cJSON* wind_speed;
    cJSON* weather_code;
    cJSON* maximum_temperature;
    cJSON* minimum_temperature;
    cJSON* precipitation_probability;
    CURL* curl;
    char resolved_place_name[WEATHER_LOCATION_MAX_LEN] = {0};
    double resolved_latitude = 0;
    double resolved_longitude = 0;
    bool location_cache_hit = false;
    int ret = -1;

    if (output == NULL || output_size == 0) {
        return -1;
    }
    output[0] = '\0';
    if (
        location == NULL ||
        location[0] == '\0' ||
        strstr(location, "当前") != NULL ||
        strcmp(location, "本地") == 0 ||
        strcmp(location, "这里") == 0
    ) {
        default_location = getenv("AI_CAT_DEFAULT_CITY");
        if (default_location == NULL || default_location[0] == '\0') {
            snprintf(
                output,
                output_size,
                "设备尚未设置所在城市，请询问用户要查询哪个城市的天气。"
            );
            return 0;
        }
        location = default_location;
    }

    pthread_mutex_lock(&weather_cache_mutex);
    if (strcmp(cached_weather_location, location) == 0) {
        snprintf(
            resolved_place_name,
            sizeof(resolved_place_name),
            "%s",
            cached_weather_place_name
        );
        resolved_latitude = cached_weather_latitude;
        resolved_longitude = cached_weather_longitude;
        location_cache_hit = true;
    }
    pthread_mutex_unlock(&weather_cache_mutex);

    if (!location_cache_hit) {
        pthread_once(&curl_init_once, __initialize_curl);
        curl = curl_easy_init();
        if (curl == NULL) {
            snprintf(output, output_size, "天气服务暂时不可用，请稍后再试。");
            return -1;
        }
        escaped_location = curl_easy_escape(curl, location, 0);
        if (escaped_location == NULL) {
            curl_easy_cleanup(curl);
            snprintf(output, output_size, "无法解析城市名称，请换一个城市名称再试。");
            return -1;
        }
        snprintf(
            geocoding_url,
            sizeof(geocoding_url),
            "https://geocoding-api.open-meteo.com/v1/search"
            "?name=%s&count=1&language=zh&format=json",
            escaped_location
        );
        curl_free(escaped_location);
        curl_easy_cleanup(curl);

        geocoding = __http_get_json(geocoding_url);
        results = geocoding == NULL
            ? NULL
            : cJSON_GetObjectItemCaseSensitive(geocoding, "results");
        place = cJSON_IsArray(results) ? cJSON_GetArrayItem(results, 0) : NULL;
        place_name = cJSON_GetObjectItemCaseSensitive(place, "name");
        latitude = cJSON_GetObjectItemCaseSensitive(place, "latitude");
        longitude = cJSON_GetObjectItemCaseSensitive(place, "longitude");
        if (
            !cJSON_IsString(place_name) ||
            !cJSON_IsNumber(latitude) ||
            !cJSON_IsNumber(longitude)
        ) {
            snprintf(
                output,
                output_size,
                "没有找到“%s”的天气信息，请确认城市名称。",
                location
            );
            goto cleanup;
        }
        snprintf(
            resolved_place_name,
            sizeof(resolved_place_name),
            "%s",
            place_name->valuestring
        );
        resolved_latitude = latitude->valuedouble;
        resolved_longitude = longitude->valuedouble;
        pthread_mutex_lock(&weather_cache_mutex);
        snprintf(
            cached_weather_location,
            sizeof(cached_weather_location),
            "%s",
            location
        );
        snprintf(
            cached_weather_place_name,
            sizeof(cached_weather_place_name),
            "%s",
            resolved_place_name
        );
        cached_weather_latitude = resolved_latitude;
        cached_weather_longitude = resolved_longitude;
        pthread_mutex_unlock(&weather_cache_mutex);
    }

    snprintf(
        forecast_url,
        sizeof(forecast_url),
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=%.6f&longitude=%.6f"
        "&current=temperature_2m,apparent_temperature,"
        "relative_humidity_2m,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max"
        "&timezone=auto&forecast_days=1",
        resolved_latitude,
        resolved_longitude
    );
    forecast = __http_get_json(forecast_url);
    current = forecast == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(forecast, "current");
    daily = forecast == NULL
        ? NULL
        : cJSON_GetObjectItemCaseSensitive(forecast, "daily");
    temperature = cJSON_GetObjectItemCaseSensitive(current, "temperature_2m");
    apparent_temperature =
        cJSON_GetObjectItemCaseSensitive(current, "apparent_temperature");
    humidity =
        cJSON_GetObjectItemCaseSensitive(current, "relative_humidity_2m");
    wind_speed = cJSON_GetObjectItemCaseSensitive(current, "wind_speed_10m");
    weather_code = cJSON_GetObjectItemCaseSensitive(current, "weather_code");
    maximum_temperature = __first_array_item(daily, "temperature_2m_max");
    minimum_temperature = __first_array_item(daily, "temperature_2m_min");
    precipitation_probability =
        __first_array_item(daily, "precipitation_probability_max");
    if (
        !cJSON_IsNumber(temperature) ||
        !cJSON_IsNumber(apparent_temperature) ||
        !cJSON_IsNumber(humidity) ||
        !cJSON_IsNumber(wind_speed) ||
        !cJSON_IsNumber(weather_code) ||
        !cJSON_IsNumber(maximum_temperature) ||
        !cJSON_IsNumber(minimum_temperature)
    ) {
        snprintf(output, output_size, "天气服务返回数据不完整，请稍后再试。");
        goto cleanup;
    }

    snprintf(
        output,
        output_size,
        "%s现在%s，%.0f度；今天最低%.0f度、最高%.0f度%s。",
        resolved_place_name,
        __weather_description(weather_code->valueint),
        temperature->valuedouble,
        minimum_temperature->valuedouble,
        maximum_temperature->valuedouble,
        cJSON_IsNumber(precipitation_probability)
            ? precipitation_probability->valuedouble >= 40
                ? "，有较高降雨概率"
                : "，降雨概率较低"
            : ""
    );
    printf(
        "weather query source=Open-Meteo, location=%s, cache_hit=%d, "
        "apparent=%.1f, humidity=%.0f, wind=%.1f\n",
        resolved_place_name,
        location_cache_hit,
        apparent_temperature->valuedouble,
        humidity->valuedouble,
        wind_speed->valuedouble
    );
    ret = 0;

cleanup:
    cJSON_Delete(geocoding);
    cJSON_Delete(forecast);
    return ret;
}

/* Self-contained because prepare_sdk.sh copies this source without new headers.
 * Match Settings: no trimming, no truthiness, invalid explicit values reject. */
static int __motion_env_flag(const char* name, bool fallback, bool* result) {
    const char* value = getenv(name);
    if (value == NULL) {
        *result = fallback;
        return 0;
    }
    if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
        strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
        *result = true;
        return 0;
    }
    if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
        strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
        *result = false;
        return 0;
    }
    fprintf(stderr, "invalid motion boolean: %s\n", name);
    return -1;
}

static const char* __motion_rejection_reason(bool requires_tail) {
    bool enabled, has_tail, tail_enabled;
    if (__motion_env_flag("AI_CAT_ENABLE_MOTION", true, &enabled) != 0 ||
        __motion_env_flag("AI_CAT_HAS_TAIL", true, &has_tail) != 0 ||
        __motion_env_flag("AI_CAT_ENABLE_TAIL_MOTION", false, &tail_enabled) != 0) {
        return "动作配置无效，保持禁用";
    }
    if (requires_tail && !has_tail) return "当前设备没有尾部机构";
    if (!enabled) return "动作已暂停";
    if (requires_tail && !tail_enabled) {
        return "尾部动作尚未通过维修后验收，当前保持禁用";
    }
    return NULL;
}

static bool __vendor_smooth_motion_enabled(void) {
    const char* value = getenv("AI_CAT_MOTION_PROFILE");

    return value != NULL && strcmp(value, "k1_vendor_smooth") == 0;
}

static const char* __motor_motion_speed(const char* actuator) {
    if (actuator == NULL) {
        return NULL;
    }
    if (__vendor_smooth_motion_enabled()) {
        return (
            strcmp(actuator, "head_lr") == 0 ||
            strcmp(actuator, "head_ud") == 0 ||
            strcmp(actuator, "tail_lr") == 0
        ) ? "3" : NULL;
    }
    if (strcmp(actuator, "head_ud") == 0) {
        return "2";
    }
    return (
        strcmp(actuator, "head_lr") == 0 ||
        strcmp(actuator, "tail_lr") == 0
    ) ? "1" : NULL;
}

static bool __is_motion_function(const char* name) {
    return name != NULL && (
        strcmp(name, "shake_head") == 0 ||
        strcmp(name, "nod_head") == 0 ||
        strcmp(name, "wag_tail") == 0
    );
}

static bool __personality_allows_motion(
    const char* name,
    char* personality_name,
    size_t personality_name_size
) {
    bool allowed = false;

    pthread_mutex_lock(&personality_config_mutex);
    if (strcmp(name, "shake_head") == 0) {
        allowed = personality_allow_shake;
    } else if (strcmp(name, "nod_head") == 0) {
        allowed = personality_allow_nod;
    } else if (strcmp(name, "wag_tail") == 0) {
        allowed = personality_allow_tail;
    }
    if (personality_name != NULL && personality_name_size > 0) {
        snprintf(
            personality_name,
            personality_name_size,
            "%s",
            active_personality_name[0] == '\0'
                ? "当前性格"
                : active_personality_name
        );
    }
    pthread_mutex_unlock(&personality_config_mutex);
    return allowed;
}

static int __run_motor_motion(const char* actuator, const char* speed) {
    /*
     * 使用固定可执行文件和固定参数，不经 shell 拼接用户输入，
     * 避免命令注入，并通过退出码判断电机动作是否成功。
     */
    const char* executable = "/usr/bin/ai-toy_app";
    char* const argv[] = {
        (char*)executable,
        "motor",
        (char*)actuator,
        (char*)speed,
        NULL,
    };
    pid_t pid;
    int status;
    int ret;
    const char* expected_speed = __motor_motion_speed(actuator);
    const char* rejection = __motion_rejection_reason(
        actuator != NULL && strcmp(actuator, "tail_lr") == 0
    );

    if (rejection != NULL || speed == NULL || expected_speed == NULL || strcmp(speed, expected_speed) != 0) {
        return -1;
    }
    if (!__process_gate_allows_effect()) return -1;
    ret = posix_spawn(&pid, executable, NULL, NULL, argv, environ);
    if (ret != 0) {
        fprintf(stderr, "failed to start head motor command: %s\n", strerror(ret));
        return -1;
    }
    do {
        ret = waitpid(pid, &status, 0);
    } while (ret < 0 && errno == EINTR);

    if (ret < 0 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        fprintf(stderr, "head motor command failed\n");
        return -1;
    }
    return 0;
}

static void* __run_function_call(void* arg) {
    /* 电机、网络和 sysfs 查询均放在工具线程，避免阻塞 SDK 消息回调。 */
    function_call_task_t* task = (function_call_task_t*)arg;
    char output[768];
    cJSON* arguments;
    cJSON* location_obj;
    const char* location = NULL;
    int ret = -1;

    if (task == NULL) {
        return NULL;
    }
    if (!__process_gate_allows_effect()) {
        __finish_function_call_task(task);
        return NULL;
    }
    if (
        __is_motion_function(task->name)
    ) {
        bool is_nod = strcmp(task->name, "nod_head") == 0;
        bool is_tail = strcmp(task->name, "wag_tail") == 0;
        const char* action_name = is_tail ? "摇尾" : (is_nod ? "点头" : "摇头");
        const char* actuator = is_tail ? "tail_lr" : (is_nod ? "head_ud" : "head_lr");
        const char* speed = __motor_motion_speed(actuator);
        char personality_name[PERSONALITY_NAME_MAX_LEN + 1];
        uint64_t now_ms;
        const char* rejection = __motion_rejection_reason(is_tail);
        if (rejection != NULL) {
            ret = 0;
            snprintf(output, sizeof(output), "%s", rejection);
            printf("%s rejected by device motion gates\n", task->name);
        } else if (!__personality_allows_motion(
            task->name,
            personality_name,
            sizeof(personality_name)
        )) {
            ret = 0;
            snprintf(
                output,
                sizeof(output),
                "%s不执行%s动作",
                personality_name,
                action_name
            );
            printf(
                "%s rejected by personality action rules, call_id=%s\n",
                task->name,
                task->call_id
            );
        } else if (pthread_mutex_trylock(&motion_motor_mutex) != 0) {
            ret = 0;
            snprintf(
                output,
                sizeof(output),
                "电机动作正在执行，本次重复请求已忽略"
            );
            printf(
                "duplicate %s ignored while motor is busy, call_id=%s\n",
                task->name,
                task->call_id
            );
        } else {
            now_ms = __get_time_ms();
            if (
                motion_last_started_ms != 0 &&
                now_ms - motion_last_started_ms < MOTOR_MOTION_COOLDOWN_MS
            ) {
                ret = 0;
                snprintf(
                    output,
                    sizeof(output),
                    "刚刚已经完成电机动作，本次重复请求已忽略"
                );
                printf(
                    "duplicate %s ignored during cooldown, call_id=%s\n",
                    task->name,
                    task->call_id
                );
            } else {
                motion_last_started_ms = now_ms;
                printf("executing %s, call_id=%s\n", task->name, task->call_id);
                ret = __run_motor_motion(actuator, speed);
                snprintf(
                    output,
                    sizeof(output),
                    "%s动作%s",
                    action_name,
                    ret == 0 ? "已完成" : "执行失败"
                );
            }
            pthread_mutex_unlock(&motion_motor_mutex);
        }
    } else if (__is_battery_function(task->name)) {
        printf("executing %s, call_id=%s\n", task->name, task->call_id);
        __write_dialog_status("thinking", "正在读取设备电量");
        ret = __query_battery_status(output, sizeof(output));
    } else if (__is_weather_function(task->name)) {
        arguments = cJSON_Parse(task->arguments);
        location_obj = arguments == NULL
            ? NULL
            : cJSON_GetObjectItemCaseSensitive(arguments, "location");
        if (!cJSON_IsString(location_obj) && arguments != NULL) {
            location_obj = cJSON_GetObjectItemCaseSensitive(arguments, "city");
        }
        if (cJSON_IsString(location_obj)) {
            location = location_obj->valuestring;
        }
        printf(
            "executing %s, location=%s, call_id=%s\n",
            task->name,
            location == NULL ? "(not provided)" : location,
            task->call_id
        );
        if (location != NULL && strstr(location, "当前") == NULL) {
            char status_message[WEATHER_LOCATION_MAX_LEN + 32];
            snprintf(
                status_message,
                sizeof(status_message),
                "正在查询%s天气",
                location
            );
            __write_dialog_status("thinking", status_message);
        } else {
            __write_dialog_status("thinking", "正在确认天气查询城市");
        }
        ret = __query_weather(location, output, sizeof(output));
        cJSON_Delete(arguments);
    } else {
        snprintf(output, sizeof(output), "设备端未配置此工具，无法执行。");
    }
    __send_function_call_output(
        task->demo,
        task->call_id,
        output
    );
    printf(
        "function call completed: name=%s, call_id=%s, ret=%d\n",
        task->name,
        task->call_id,
        ret
    );
    __finish_function_call_task(task);
    return NULL;
}

static void __handle_ws_conversation_item_created_call(realtime_ws_demo_t* demo, cJSON* root) {
    /*
     * Function Calling 第一步：conversation.item.created 给出工具名和
     * call_id。这里只记录元数据，等待 arguments.done 后再真正执行。
     */
    cJSON* item_obj;
    cJSON* call_id_obj;
    cJSON* type_obj;
    cJSON* name_obj;
    const char* name;
    const char* call_id;

    if (!__process_gate_allows_effect()) return;
    (void)demo;

    item_obj = cJSON_GetObjectItem(root, "item");
    if (!cJSON_IsObject(item_obj)) {
        return;
    }
    type_obj = cJSON_GetObjectItem(item_obj, "type");
    call_id_obj = cJSON_GetObjectItem(item_obj, "call_id");
    name_obj = cJSON_GetObjectItem(item_obj, "name");
    if (!cJSON_IsString(type_obj) || strcmp(type_obj->valuestring, "function_call") != 0) {
        return;
    }
    if (!cJSON_IsString(call_id_obj) || !cJSON_IsString(name_obj)) {
        printf("invalid function call notification\n");
        return;
    }
    name = name_obj->valuestring;
    call_id = call_id_obj->valuestring;
    if (
        !__is_motion_function(name) &&
        !__is_weather_function(name) &&
        !__is_battery_function(name)
    ) {
        printf("unknown function call name: %s\n", name);
        return;
    }

    pthread_mutex_lock(&function_call_mutex);
    snprintf(pending_function_call.name, sizeof(pending_function_call.name), "%s", name);
    snprintf(pending_function_call.call_id, sizeof(pending_function_call.call_id), "%s", call_id);
    pthread_mutex_unlock(&function_call_mutex);
    printf("%s function call received, call_id=%s\n", name, call_id);
}

static void __handle_function_call_arguments_done(realtime_ws_demo_t* demo, cJSON* root) {
    /*
     * Function Calling 第二步：参数接收完成。豆包会先发送
     * conversation.item.created，而部分 OpenAI 兼容 CustomLLM 只发送这条
     * done 事件并在其中携带 name。两种顺序都在这里归一化，SDK 回调线程
     * 只创建任务，绝不在回调内同步回传消息，避免传输层锁重入。
     */
    pthread_t msg_thread;
    cJSON* call_id_obj;
    cJSON* name_obj;
    cJSON* arguments_obj;
    function_call_task_t* task;
    const char* event_name;
    const char* arguments;
    bool recovered_from_done = false;
    int ret;

    if (!__process_gate_allows_effect() || root == NULL) {
        return;
    }
    call_id_obj = cJSON_GetObjectItem(root, "call_id");
    if (!cJSON_IsString(call_id_obj)) {
        printf("function call arguments missing call_id\n");
        return;
    }
    if (strlen(call_id_obj->valuestring) >= FUNCTION_CALL_ID_MAX_LEN) {
        printf("function call id is too long\n");
        return;
    }
    name_obj = cJSON_GetObjectItem(root, "name");
    event_name = cJSON_IsString(name_obj) ? name_obj->valuestring : NULL;
    if (event_name != NULL && strlen(event_name) >= FUNCTION_NAME_MAX_LEN) {
        event_name = NULL;
    }
    arguments_obj = cJSON_GetObjectItem(root, "arguments");
    arguments =
        cJSON_IsString(arguments_obj) && arguments_obj->valuestring[0] != '\0'
            ? arguments_obj->valuestring
            : "{}";

    task = calloc(1, sizeof(*task));
    if (task == NULL) {
        tool_capture_guard = false;
        cloud_error_received = true;
        exit_request = true;
        fprintf(stderr, "failed to allocate function call task; reconnecting\n");
        return;
    }

    pthread_mutex_lock(&function_call_mutex);
    if (function_call_shutdown || !__process_gate_allows_effect()) {
        pthread_mutex_unlock(&function_call_mutex);
        free(task);
        return;
    }
    if (strcmp(last_function_call_id, call_id_obj->valuestring) == 0) {
        pthread_mutex_unlock(&function_call_mutex);
        free(task);
        printf("duplicate function call ignored: call_id=%s\n", call_id_obj->valuestring);
        return;
    }
    task->demo = demo;
    snprintf(task->call_id, sizeof(task->call_id), "%s", call_id_obj->valuestring);
    if (
        pending_function_call.name[0] != '\0' &&
        strcmp(pending_function_call.call_id, call_id_obj->valuestring) == 0
    ) {
        snprintf(task->name, sizeof(task->name), "%s", pending_function_call.name);
        pending_function_call.name[0] = '\0';
        pending_function_call.call_id[0] = '\0';
    } else if (event_name != NULL) {
        snprintf(task->name, sizeof(task->name), "%s", event_name);
        recovered_from_done = true;
    }
    snprintf(task->arguments, sizeof(task->arguments), "%s", arguments);
    snprintf(
        last_function_call_id,
        sizeof(last_function_call_id),
        "%s",
        call_id_obj->valuestring
    );
    tool_capture_guard = true;
    /* Reserve before pthread_create: cleanup must see even a not-yet-started
     * worker. This count, not the speech guard, proves actual tool completion. */
    function_call_workers++;
    pthread_mutex_unlock(&function_call_mutex);

    if (recovered_from_done) {
        printf(
            "function call recovered from arguments.done: name=%s, call_id=%s\n",
            task->name,
            task->call_id
        );
    } else if (task->name[0] == '\0') {
        printf("function call name unavailable: call_id=%s\n", task->call_id);
    }

    ret = pthread_create(&msg_thread, NULL, __run_function_call, task);
    if (ret != 0) {
        tool_capture_guard = false;
        fprintf(stderr, "failed to create function call thread: %s\n", strerror(ret));
        cloud_error_received = true;
        exit_request = true;
        __finish_function_call_task(task);
        return;
    }
    pthread_detach(msg_thread);
}

static void __handle_ws_message(realtime_ws_demo_t* demo, const void* message, size_t size) {
    /* WS JSON 消息路由：工具调用单独处理，其余事件原样打印便于调试。 */
    cJSON* root = NULL;
    cJSON* type_obj = NULL;
    cJSON* transcript_obj = NULL;
    cJSON* event_id_obj = NULL;
    const char* type = NULL;
    bool transcript_cached = false;
    if (!__process_gate_allows_effect()) return;
    root = cJSON_Parse((const char*)message);
    if (NULL == root) {
        printf("parse json buffer failed\n");
        return;
    }
    printf("ws message size:%zu data:%s\n", size, (char*)message);
    type_obj = cJSON_GetObjectItem(root, "type");
    type = cJSON_GetStringValue(type_obj);
    if (
        type != NULL &&
        strcmp(type, "session.created") == 0
    ) {
        cloud_session_created = true;
        printf("cloud session created; requests may proceed\n");
    } else if (
        type != NULL &&
        strcmp(type, "response.created") == 0
    ) {
        if (local_direct_tts_active) {
            __reset_response_playback_state(demo);
            __mark_dialog_latency_stage(DIALOG_LATENCY_RESPONSE_CREATED);
            local_direct_tts_response_created = true;
            discard_playback_audio = false;
            printf(
                "response.created established local direct TTS audio boundary\n"
            );
        } else if (__local_battery_override_active()) {
            discard_playback_audio = true;
            printf("stale response.created ignored during local battery override\n");
        } else if (session_active && response_start_deadline_ms != 0) {
            /*
             * Keep the completed-response PCM barrier through user capture,
             * commit and model startup. response.created is the ordered
             * boundary at which subsequent PCM belongs to the new answer.
             */
            __reset_response_playback_state(demo);
            __mark_dialog_latency_stage(DIALOG_LATENCY_RESPONSE_CREATED);
            if (discard_playback_audio) {
                printf(
                    "new cloud response created; late playback discard guard cleared\n"
                );
            }
            discard_playback_audio = false;
        }
        if (
            !local_direct_tts_active &&
            !__local_battery_override_active()
        ) {
            response_start_deadline_ms = 0;
            response_text_recovery_deadline_ms = 0;
            __clear_pending_user_transcript();
        }
        printf("cloud response created\n");
    } else if (
        type != NULL &&
        strcmp(type, "error") == 0
    ) {
        __handle_cloud_error(root);
    } else if (
        type != NULL &&
        strcmp(type, "conversation.item.input_audio_transcription.completed") == 0
    ) {
        transcript_obj = cJSON_GetObjectItem(root, "transcript");
        event_id_obj = cJSON_GetObjectItem(root, "event_id");
        if (cJSON_IsString(transcript_obj)) {
            if (__has_non_whitespace_text(transcript_obj->valuestring)) {
                if (input_transcript_pending) {
                    __mark_voice_turn_asr_final();
                    __write_dialog_event(
                        "user",
                        transcript_obj->valuestring,
                        cJSON_IsString(event_id_obj) ? event_id_obj->valuestring : NULL
                    );
                    input_transcript_pending = false;
                    client_transcript_deadline_ms = 0;
                    if (__is_named_dialog_end_request(transcript_obj->valuestring)) {
                        pthread_mutex_lock(&client_route_mutex);
                        response_create_request = false;
                        pthread_mutex_unlock(&client_route_mutex);
                        response_text_recovery_deadline_ms = 0;
                        __clear_pending_user_transcript();
                        discard_playback_audio = true;
                        interrupt_only_request = true;
                        printf("named end command received in dialog transcript\n");
                    } else if (
                        __is_local_battery_status_request(
                            transcript_obj->valuestring
                        )
                    ) {
                        bool cancel_required =
                            __route_local_battery_reply();
                        response_text_recovery_deadline_ms = 0;
                        __clear_pending_user_transcript();
                        if (cancel_required) {
                            discard_playback_audio = true;
                        }
                        printf(
                            cancel_required
                                ? "late local battery intent recognized; "
                                  "queueing speculative cloud cancellation\n"
                                : "local battery intent recognized; waiting for "
                                  "commit acknowledgement\n"
                        );
                    } else {
                        transcript_cached = __store_pending_user_transcript(
                            transcript_obj->valuestring
                        );
                        __queue_voice_response_if_unclaimed();
                        if (
                            transcript_cached &&
                            response_start_deadline_ms != 0 &&
                            !response_text_recovery_sent
                        ) {
                            response_text_recovery_deadline_ms =
                                __get_time_ms() +
                                RESPONSE_TEXT_RECOVERY_DELAY_MS;
                            printf(
                                "final transcript received; arming explicit text "
                                "recovery\n"
                            );
                        }
                    }
                } else {
                    printf("stale final transcript ignored\n");
                }
            } else if (input_transcript_pending) {
                input_transcript_pending = false;
                pthread_mutex_lock(&client_route_mutex);
                response_cancel_request = true;
                response_create_request = false;
                pthread_mutex_unlock(&client_route_mutex);
                client_transcript_deadline_ms = 0;
                response_text_recovery_deadline_ms = 0;
                __clear_pending_user_transcript();
                printf("empty final transcript; queueing response.cancel\n");
            }
        }
    } else if (
        type != NULL &&
        strcmp(type, "input_audio_buffer.committed") == 0
    ) {
        if (client_turn_pending && session_active) {
            __mark_dialog_latency_stage(DIALOG_LATENCY_COMMIT_ACK);
            client_turn_pending = false;
            voice_commit_acked = true;
            client_commit_ack_deadline_ms = 0;
            __queue_voice_response_if_unclaimed();
            printf(
                "client audio commit acknowledged; response routing released\n"
            );
        } else {
            printf("audio commit acknowledged without pending client turn\n");
        }
    } else if (
        type != NULL &&
        strcmp(type, "response.audio_transcript.delta") == 0
    ) {
        cJSON* delta_obj = cJSON_GetObjectItem(root, "delta");
        const char* delta = cJSON_GetStringValue(delta_obj);
        bool proactive =
            proactive_speech_pending || proactive_speech_active ||
            proactive_speech_finish_request;

        if (!proactive && session_active) {
            __mark_dialog_turn_tts_transcript_delta(
                delta != NULL ? strlen(delta) : 0
            );
        }
        if (proactive) {
            proactive_speech_pending = false;
            proactive_speech_deadline_ms = 0;
            ai_playing = true;
            __mark_dialog_audio_activity();
            if (!proactive_speech_finish_request) {
                proactive_speech_active = true;
                __write_dialog_status("proactive_speaking", "正在自主播报");
                printf("proactive input_tts playback started\n");
            } else {
                printf("late proactive transcript received while draining\n");
            }
        }
    } else if (
        type != NULL &&
        strcmp(type, "response.audio.done") == 0
    ) {
        if (proactive_speech_pending || proactive_speech_active) {
            proactive_speech_pending = false;
            proactive_speech_active = false;
            proactive_speech_finish_request = true;
            proactive_speech_deadline_ms = 0;
            __write_dialog_status("proactive_finishing", "自主短语正在播放完毕");
            printf("proactive input_tts audio completed\n");
        } else if (session_active) {
            if (
                local_direct_tts_active &&
                !local_direct_tts_response_created
            ) {
                printf(
                    "stale response.audio.done ignored before local TTS "
                    "response boundary\n"
                );
            } else {
                __mark_response_audio_done(demo);
                __mark_dialog_latency_stage(DIALOG_LATENCY_AUDIO_DONE);
                if (local_direct_tts_active) {
                    local_direct_tts_deadline_ms = 0;
                    thinking_deadline_ms = 0;
                    __mark_response_answer_finished(demo);
                    __mark_dialog_latency_stage(DIALOG_LATENCY_CLOUD_FINISHED);
                    running = false;
                    waiting_for_speech = false;
                    follow_up_deadline_ms = 0;
                    speech_start_deadline_ms = 0;
                    playback_capture_guard = true;
                    follow_up_prepare_request = true;
                    __write_dialog_status(
                        "followup_preparing",
                        "电量播报完成，播放完毕后继续聆听"
                    );
                    printf("local direct TTS audio completed\n");
                }
            }
        }
    } else if (
        type != NULL &&
        strcmp(type, "response.audio_transcript.done") == 0
    ) {
        __mark_dialog_latency_stage(DIALOG_LATENCY_TTS_TRANSCRIPT_DONE);
        transcript_obj = cJSON_GetObjectItem(root, "transcript");
        event_id_obj = cJSON_GetObjectItem(root, "event_id");
        if (cJSON_IsString(transcript_obj)) {
            if (local_direct_tts_active) {
                printf("local direct TTS transcript already recorded\n");
            } else {
                __write_dialog_event(
                    "assistant",
                    transcript_obj->valuestring,
                    cJSON_IsString(event_id_obj) ? event_id_obj->valuestring : NULL
                );
            }
        }
    } else if (type != NULL && strcmp("conversation.item.created", type) == 0) {
        __handle_ws_conversation_item_created_call(demo, root);
    } else if(type != NULL && strcmp("response.function_call_arguments.done", type) == 0) {
        __handle_function_call_arguments_done(demo, root);
    } else {
        printf("%s\n", (char*)message);
    }

    if (root) {
        cJSON_Delete(root);
    }
}

static void __handle_rtc_message(realtime_ws_demo_t* demo, const void* message, size_t size) {
    /* RTC 二进制消息路由；K1 当前构建关闭 RTC，仅保留原 demo 兼容代码。 */
    cJSON* root = NULL;
    if (((uint8_t*)message)[0] == 's' && ((uint8_t*)message)[1] == 'u' && ((uint8_t*)message)[2] == 'b' && ((uint8_t*)message)[3] == 'v') {
        // root = cJSON_Parse(((uint8_t *)message) + 8);
        root = cJSON_Parse((const char *)(((uint8_t *)message) + 8));
        __on_subtitle_message_received(root);
    } else {
        printf("message size: %d data: %s\n", (int)size, (char*) message);
    }
    if (root) {
        cJSON_Delete(root);
    }
}

static void _on_volc_message_data(volc_engine_t handle, const void* message, size_t size, volc_message_info_t* info_ptr, void* user_data)
{
    /* 根据配置的传输模式，将 SDK 消息交给 RTC 或 WebSocket 解析器。 */
    realtime_ws_demo_t* demo = (realtime_ws_demo_t*)user_data;
    static int cnt = 0;
    printf("--------------%d----------------\r\n", cnt++);
    switch(demo->mode) {
        case VOLC_MODE_RTC:
            __handle_rtc_message(demo, message, size);
            break;
        case VOLC_MODE_WS:
            __handle_ws_message(demo, message, size);
            break;
        default:
            break;
    }
    printf("------------------------------\r\n");
}

static int __parse_demo_config(const char* config, realtime_ws_demo_t* handle) {
    /*
     * Demo 只消费 bot_id 和 mode；IoT 鉴权等完整配置由 volc_create()
     * 再次解析。WS 使用 16 kHz/100 ms 帧，RTC 保留原 8 kHz 配置。
     */
    cJSON* root = NULL;
    cJSON* obj_item = NULL;
    if (NULL == config) {
        printf("invalid input args\n");
        return -1;
    }
    root = cJSON_Parse(config);
    if (NULL == root) {
        printf("parse json buffer failed\n");
        return -1;
    }
    obj_item = cJSON_GetObjectItem(root, "bot_id");
    if (NULL == obj_item) {
        printf("parse bot_id failed\n");
        goto err_out_label;
    }
    handle->bot_id = strdup(cJSON_GetStringValue(obj_item));

    obj_item = cJSON_GetObjectItem(root, "mode");
    if (NULL == obj_item) {
        printf("parse mode failed\n");
        goto err_out_label;
    }
    handle->mode = (int)cJSON_GetNumberValue(obj_item);
    if (handle->mode == 0) {
        // rtc
        handle->frame_len = 320;
        handle->sample_rate = 8000;
    } else {
        handle->frame_len = 3200;
        handle->sample_rate = 16000;
    }
    handle->audio_frame_ms = (1000 / (AUDIO_FRAME_PER_SECOND / handle->frame_len));

    printf("get bot id from config file: %s, mode: %d\n", handle->bot_id, handle->mode);
    cJSON_Delete(root);
    return 0;
err_out_label:
    cJSON_Delete(root);
    return -1;
}

static int _build_ws_message(const char* msg, uint8_t** out_buf, size_t* out_len) {
    size_t msg_len = strlen(msg) + 1;
    *out_len = msg_len - 1;
    *out_buf = (uint8_t*)malloc(msg_len);
    if (!*out_buf) {
        printf("malloc failed\n");
        return -1;
    }
    memcpy(*out_buf, msg, msg_len);
    (*out_buf)[msg_len - 1] = 0;
    return 0;
}

static int _ws_clear_buffer(realtime_ws_demo_t* demo) {
    /* 清除云端尚未提交的输入音频，供交互调试按键使用。 */
    uint8_t* clear = NULL;
    size_t clear_len = 0;
    int ret = _build_ws_message(WS_BUFFER_CLEAR, &clear, &clear_len);
    if (ret != 0) {
        printf("build clear message failed");
        return ret;
    }
    ret = __process_gate_allows_effect()
        ? volc_send_message(demo->engine, clear, clear_len, NULL) : -1;
    if (clear) {
        free(clear);
    }
    return ret;
}

static int _ws_response_create(realtime_ws_demo_t* demo) {
    /* 客户端判停模式下，在音频 commit 后按同一 WebSocket 顺序请求回答。 */
    char event_id[128];
    char* message = NULL;
    cJSON* root = NULL;
    int ret = -1;

    snprintf(
        event_id,
        sizeof(event_id),
        "event_response_%" PRIu64 "_%lu",
        dialog_runtime_started_ms,
        ++response_request_sequence
    );
    root = cJSON_CreateObject();
    if (root == NULL) {
        return -1;
    }
    cJSON_AddStringToObject(root, "event_id", event_id);
    cJSON_AddStringToObject(root, "type", "response.create");
    message = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (message == NULL) {
        printf("build response.create message failed\n");
        return -1;
    }

    ret = __process_gate_allows_effect()
        ? volc_send_message(demo->engine, message, strlen(message), NULL) : -1;
    free(message);
    printf("response.create submitted: event_id=%s, ret=%d\n", event_id, ret);
    return ret;
}

static int _ws_response_cancel(realtime_ws_demo_t* demo) {
    uint8_t* message = NULL;
    size_t message_len = 0;
    int ret = _build_ws_message(
        WS_RESPONSE_CANCEL,
        &message,
        &message_len
    );
    if (ret != 0) {
        printf("build response.cancel message failed\n");
        return ret;
    }
    ret = volc_send_message(demo->engine, message, message_len, NULL);
    free(message);
    return ret;
}

int main(void){
    char* config_data = NULL;
    uint8_t* wake_preroll_data = NULL;
    size_t wake_preroll_len = 0;
    size_t wake_preroll_offset = 0;
    int error = 0;
    uint64_t last_time_ms = 0;
    uint64_t diff_time_ms = 0;
	uint64_t cloud_session_ready_deadline_ms = 0;
	realtime_ws_demo_t demo = {0};
    pa_buffer_attr playback_buffer_attr;
    int gate_result;
    bool playback_thread_started = false;

    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);
    __setup_signal_handlers();
    ai_cat_process_gate_init(&process_gate);
    /* A healthy closing coordinator is a parked worker, not a failure/restart
     * loop. Invalid configuration is explicit failure, never a legacy fallback.
     * No runtime files, PulseAudio or cloud calls precede admission. */
    while ((gate_result = ai_cat_process_gate_enter(&process_gate)) == 1 && !exit_request)
        usleep(100 * 1000);
    if (exit_request) return EXIT_SUCCESS;
    if (gate_result < 0) {
        fprintf(stderr, "invalid device process gate; refusing dialog startup\n");
        return EXIT_FAILURE;
    }
    if (!__process_gate_allows_effect()) return EXIT_SUCCESS;
    /* Signals received while parked were never admitted requests. Do not
     * replay them in the newly opened epoch; senders must wait for fresh ready. */
    wake_request = false;
    text_dialog_request = false;
    interrupt_only_request = false;
    dialog_runtime_started_ms = __get_time_ms();
    __load_device_serial();
    session_active = false;
    unlink(DIALOG_SESSION_MARKER);
    unlink(DIALOG_AUDIO_MARKER);
    unlink(DIALOG_TEXT_REQUEST_PATH);
    unlink(WAKE_PREROLL_PATH);
    unlink(WAKE_PREROLL_TMP_PATH);
    __write_dialog_status("starting", "语音服务正在启动");

    /* 阶段 1：读取配置并根据传输模式确定音频采样率和帧大小。 */
	if ((config_data = __load_config_from_file("conv_ai_config.json")) == NULL) {
		printf("load config from file fail\n");
		return -1;
	}

    if (__parse_demo_config(config_data, &demo) != 0) {
        printf("parse demo config failed");
        return -1;
    }

	demo.audio_rec_buf = (uint8_t*)malloc(demo.frame_len);
	if (demo.audio_rec_buf == NULL) {
		printf("malloc audio rec buf fail\n");
		return -1;
	}
    demo.ring_buf = volc_ringbuf_create(demo.frame_len * 1000);
    if (demo.ring_buf == NULL) {
        printf("create ring buf fail\n");
        goto err_out_label;
    }
    pthread_mutex_init(&demo.ring_buf_mutex, NULL);
    pthread_mutex_init(&demo.playback_mutex, NULL);

    /*
     * 阶段 2：创建一条单声道 PulseAudio 录音流和一条播放流。
     * K1 上由 systemd 保证 PulseAudio socket 已就绪后再启动本程序。
     */
    demo.format.format = PA_SAMPLE_S16NE;
    demo.format.rate = demo.sample_rate;
    demo.format.channels = AUDIO_CHANNEL_NUM;
    memset(&playback_buffer_attr, 0xFF, sizeof(playback_buffer_attr));
    playback_buffer_attr.tlength = (uint32_t)pa_usec_to_bytes(
        (pa_usec_t)AUDIO_PLAYBACK_TARGET_MS * 1000,
        &demo.format
    );
    playback_buffer_attr.prebuf = (uint32_t)pa_usec_to_bytes(
        (pa_usec_t)AUDIO_PLAYBACK_PREBUFFER_MS * 1000,
        &demo.format
    );
    playback_buffer_attr.minreq = (uint32_t)pa_usec_to_bytes(
        (pa_usec_t)AUDIO_PLAYBACK_CHUNK_MS * 1000,
        &demo.format
    );
    if (!__process_gate_allows_effect()) goto err_out_label;
    demo.p_capture = pa_simple_new(NULL, "Capture", PA_STREAM_RECORD, NULL, "Capture", &demo.format, NULL, NULL, &error);
    if (NULL == demo.p_capture) {
        printf("capture pa_simple_new failed: %s\n", pa_strerror(error));
        goto err_out_label;
    }
    demo.p_playback = pa_simple_new(
        NULL,
        "Playback",
        PA_STREAM_PLAYBACK,
        NULL,
        "Playback",
        &demo.format,
        NULL,
        &playback_buffer_attr,
        &error
    );
    if (NULL == demo.p_playback) {
        printf("playback pa_simple_new failed: %s\n", pa_strerror(error));
        goto err_out_label;
    }
    printf(
        "playback buffer requested: chunk_ms=%d target_ms=%d prebuffer_ms=%d\n",
        AUDIO_PLAYBACK_CHUNK_MS,
        AUDIO_PLAYBACK_TARGET_MS,
        AUDIO_PLAYBACK_PREBUFFER_MS
    );

    if (!__process_gate_allows_effect()) goto err_out_label;
    volc_event_handler_t volc_event_handler = {.on_volc_event = _on_volc_event,
                                            .on_volc_conversation_status = _on_volc_conversation_status,
                                            .on_volc_audio_data = _on_volc_audio_data,
                                            .on_volc_video_data = _on_volc_video_data,
                                            .on_volc_message_data = _on_volc_message_data};
    error = volc_create(&demo.engine, config_data, &volc_event_handler, &demo);

    free(config_data);
    if (error != 0) {
        printf("volc_create failed: %d\n", error);
        goto err_out_label;
    }
    volc_opt_t opt = {0};
    opt.mode = demo.mode;
    opt.bot_id = demo.bot_id;
    __write_dialog_status("connecting", "正在连接火山引擎");
    cloud_session_created = false;
    cloud_session_ready_deadline_ms =
        __get_time_ms() + CLOUD_SESSION_READY_TIMEOUT_MS;
    if (!__process_gate_allows_effect()) goto err_out_label;
    volc_start(demo.engine, &opt);

    /*
     * 阶段 3：WebSocket 连接成功不等于 realtime session 已创建。必须等到
     * session.created 后再发布 ready，避免首轮 response.create 被欢迎语抢占。
     */
    while ((!is_ready || !cloud_session_created) && !exit_request) {
        if (!__process_gate_allows_effect()) break;
        if (__get_time_ms() >= cloud_session_ready_deadline_ms) {
            __write_dialog_status("offline", "云端会话创建超时，服务正在重连");
            printf("cloud session creation timeout\n");
            exit_request = true;
            break;
        }
        usleep(100000);
    }
    if (exit_request) {
        printf("Volc Engine connection failed; exiting for service restart.\n");
        goto err_out_label;
    }
    printf("volc realtime is ready.\n");
    if (!__process_gate_allows_effect()) goto err_out_label;
    error = volc_update(
        demo.engine,
        WS_SESSION_UPDATE,
        strlen(WS_SESSION_UPDATE)
    );
    printf("session update result: %d\n", error);
    error = __apply_personality_runtime(&demo, true);
    if (error < 0) {
        fprintf(stderr, "personality runtime was not applied at startup\n");
    }
    __setup_signal_handlers();
    __schedule_cloud_idle_shutdown();
    __write_dialog_status("ready", "等待口令“小安，开启对话模式”");
    printf("dialog signal loop ready\n");
    error = pthread_create(&demo.audio_playback_task, NULL, __audio_playback_task, &demo);
    if (error != 0) goto err_out_label;
    playback_thread_started = true;

    volc_audio_frame_info_t info = {0};
    info.data_type = VOLC_AUDIO_DATA_TYPE_PCM;

    /*
     * 阶段 4：主状态机。
     * running=true 时阻塞读取一帧麦克风 PCM 并持续上传。本地能量 VAD
     * 判定句尾并提交缓冲区，随后立即按同一 WebSocket 顺序请求模型回答。
     */
    while (!exit_request) {
        uint64_t now_ms;

        if (!__process_gate_allows_effect()) break;
        if (__personality_runtime_revoked()) {
            fprintf(stderr, "personality runtime revoked; closing cached session\n");
            break;
        }
        now_ms = __get_time_ms();
        if (
            wake_request || text_dialog_request ||
            now_ms - personality_last_poll_ms >=
                PERSONALITY_POLL_INTERVAL_MS
        ) {
            personality_last_poll_ms = now_ms;
            if (
                !session_active && !running && !ai_playing &&
                !proactive_speech_pending && !proactive_speech_active &&
                !tool_capture_guard && !client_turn_pending
            ) {
                int personality_result =
                    __apply_personality_runtime(&demo, false);
                if (personality_result < 0 && personality_runtime_size >= 0) {
                    /* Never start a pending request with an obsolete owner's
                     * cached configuration when its replacement is invalid. */
                    exit_request = true;
                    clean_exit_requested = false;
                    running = false;
                    ai_playing = false;
                    discard_playback_audio = true;
                    fprintf(stderr, "personality replacement unavailable; closing cached session\n");
                    break;
                }
                if (personality_result > 0) {
                    __write_dialog_status(
                        "ready",
                        "性格配置已同步，等待口令“小安，开启对话模式”"
                    );
                }
            }
        }

        if (
            cloud_idle_deadline_ms != 0 &&
            now_ms >= cloud_idle_deadline_ms &&
            !session_active && !running && !ai_playing &&
            !wake_request && !text_dialog_request &&
            !interrupt_only_request && !follow_up_prepare_request &&
            !proactive_speech_pending && !proactive_speech_active &&
            !proactive_speech_finish_request &&
            !tool_capture_guard && !client_turn_pending &&
            !playback_write_active
        ) {
            clean_exit_requested = true;
            __write_dialog_status("stopping", "云端会话空闲，正在主动断开");
            printf("cloud idle timeout reached; disconnecting normally\n");
            exit_request = true;
            continue;
        }

        if (text_dialog_request) {
            char text_request_id[DIALOG_TEXT_REQUEST_ID_MAX_LEN + 1];
            char text_content[DIALOG_TEXT_MAX_BYTES + 1];
            char text_request_kind[DIALOG_REQUEST_KIND_MAX_LEN + 1];
            char text_event_id[DIALOG_TEXT_REQUEST_ID_MAX_LEN + 16];
            bool local_battery_text = false;
            int text_result;

            text_dialog_request = false;
            text_result = __read_text_dialog_request(
                text_request_id,
                sizeof(text_request_id),
                text_content,
                sizeof(text_content),
                text_request_kind,
                sizeof(text_request_kind)
            );
            if (text_result != 0) {
                __write_dialog_status("ready", "文字或播报请求无效，请重新提交");
                printf("invalid dialog text request\n");
            } else if (strcmp(text_request_kind, "speak") == 0) {
                /*
                 * 主动短语不建立连续会话、不清录音缓冲，也不覆盖待处理的
                 * 唤醒信号。若用户同时开始交互，本地跳过；云端低优先级仍
                 * 提供第二层竞争保护。
                 */
                if (
                    wake_request || session_active || running || ai_playing ||
                    proactive_speech_pending || proactive_speech_active ||
                    proactive_speech_finish_request
                ) {
                    printf(
                        "proactive speech dropped because dialog is busy, "
                        "event_id=%s\n",
                        text_request_id
                    );
                } else {
                    __cancel_cloud_idle_shutdown();
                    discard_playback_audio = false;
                    __begin_dialog_audio_guard();
                    snprintf(
                        text_event_id,
                        sizeof(text_event_id),
                        "event_%s",
                        text_request_id
                    );
                    __write_dialog_status(
                        "proactive_queued",
                        "自主短语已提交，等待播报"
                    );
                    proactive_speech_pending = true;
                    proactive_speech_active = false;
                    proactive_speech_finish_request = false;
                    proactive_speech_deadline_ms =
                        __get_time_ms() + PROACTIVE_SPEECH_ACK_TIMEOUT_MS;
                    ai_playing = true;
                    text_result = __send_proactive_speech_item(
                        &demo,
                        text_event_id,
                        text_content
                    );
                    if (text_result < 0) {
                        proactive_speech_pending = false;
                        proactive_speech_deadline_ms = 0;
                        ai_playing = false;
                        __write_dialog_status("ready", "自主短语发送失败，等待唤醒");
                    } else {
                        printf("proactive input_tts item sent\n");
                    }
                }
            } else {
                __cancel_cloud_idle_shutdown();
                discard_playback_audio = false;
                wake_request = false;
                interrupt_only_request = false;
                follow_up_prepare_request = false;
                echo_guard_active = false;
                echo_guard_request = false;
                session_active = true;
                waiting_for_speech = false;
                running = false;
                ai_playing = false;
                speech_start_deadline_ms = 0;
                follow_up_deadline_ms = 0;
                thinking_deadline_ms = 0;
                tool_capture_guard = false;
                __reset_local_direct_tts_state();
                __reset_client_turn_state();
                __reset_local_vad();
                __reset_barge_in_guard();
                _ws_clear_buffer(&demo);
                snprintf(
                    text_event_id,
                    sizeof(text_event_id),
                    "event_%s",
                    text_request_id
                );
                __begin_text_turn_latency();
                __write_dialog_status("submitting_text", "正在提交文字问题");
                __write_dialog_event("user", text_content, text_event_id);
                local_battery_text =
                    __is_local_battery_status_request(text_content);
                if (local_battery_text) {
                    snprintf(
                        text_event_id,
                        sizeof(text_event_id),
                        "event_%s_battery",
                        text_request_id
                    );
                    text_result = __start_local_battery_reply(
                        &demo,
                        text_event_id
                    );
                } else {
                    text_result = __send_text_dialog_item(
                        &demo,
                        text_event_id,
                        text_content
                    );
                    if (text_result >= 0) {
                        response_start_deadline_ms =
                            __get_time_ms() + RESPONSE_START_TIMEOUT_MS;
                        __mark_dialog_turn_response_requested();
                        text_result = _ws_response_create(&demo);
                    }
                }
                if (text_result < 0) {
                    session_active = false;
                    ai_playing = false;
                    __write_dialog_status(
                        "recovering",
                        "文字问题发送失败，正在重新连接"
                    );
                    printf("text dialog request failed; restarting cloud session\n");
                    exit_request = true;
                } else if (!local_battery_text) {
                    ai_playing = true;
                    thinking_deadline_ms =
                        __get_time_ms() + THINKING_TIMEOUT_MS;
                    __write_dialog_status("thinking", "文字问题已收到，正在思考");
                    printf("text dialog response.create sent\n");
                } else {
                    printf("text dialog answered through local battery TTS\n");
                }
            }
        }

        if (
            proactive_speech_pending && proactive_speech_deadline_ms > 0 &&
            __get_time_ms() >= proactive_speech_deadline_ms
        ) {
            proactive_speech_pending = false;
            proactive_speech_deadline_ms = 0;
            if (!proactive_speech_active && !proactive_speech_finish_request) {
                ai_playing = false;
                __schedule_cloud_idle_shutdown();
                __write_dialog_status(
                    "ready",
                    "自主短语未播放或已被用户交互抢占"
                );
                printf("proactive input_tts acknowledgement timeout\n");
            }
        }
        if (
            local_direct_tts_active &&
            local_direct_tts_deadline_ms != 0 &&
            __get_time_ms() >= local_direct_tts_deadline_ms
        ) {
            __finish_dialog_latency("local_direct_tts_timeout");
            __reset_local_direct_tts_state();
            session_active = false;
            waiting_for_speech = false;
            running = false;
            ai_playing = false;
            thinking_deadline_ms = 0;
            __end_dialog_audio_guard();
            __write_dialog_status(
                "recovering",
                "本地电量播报超时，正在重新连接"
            );
            printf("local direct TTS timeout: restarting cloud session\n");
            exit_request = true;
            continue;
        }

        /*
         * SIGUSR1 启动连续会话。本地 VAD 负责判停；长回答仍可再次说
         * 唤醒词，由独立唤醒进程发送 SIGUSR1 来打断并开始下一轮。
         */
        if (wake_request) {
            bool wake_preroll_handoff;

            wake_request = false;
            __clear_wake_preroll(
                &wake_preroll_data,
                &wake_preroll_len,
                &wake_preroll_offset
            );
            wake_preroll_handoff = __load_wake_preroll(
                &wake_preroll_data,
                &wake_preroll_len
            );
            if (wake_preroll_handoff) {
                printf("replaying wake preroll: bytes=%zu\n", wake_preroll_len);
            }
            __cancel_cloud_idle_shutdown();
            proactive_speech_pending = false;
            proactive_speech_active = false;
            proactive_speech_finish_request = false;
            proactive_speech_deadline_ms = 0;
            __reset_local_direct_tts_state();
            follow_up_prepare_request = false;
            echo_guard_active = false;
            echo_guard_request = false;
            __reset_barge_in_guard();
            session_active = true;
            waiting_for_speech = true;
            speech_start_deadline_ms = __get_time_ms() + WAKE_SPEECH_TIMEOUT_MS;
            follow_up_deadline_ms = 0;
            thinking_deadline_ms = 0;
            __reset_client_turn_state();
            tool_capture_guard = false;
            playback_capture_guard = false;
            playback_capture_guard_until_ms = 0;
            __reset_local_vad();
            __write_dialog_status("wake_detected", "已唤醒，请在提示音后说话");
            if (ai_playing) {
                __finish_dialog_latency("replaced_by_wake");
                discard_playback_audio = true;
                interrupt = true;
                start_after_interrupt = true;
                wake_tone_after_interrupt = !wake_preroll_handoff;
                ai_playing = false;
                printf("wakeup signal: interrupting current response\n");
            } else {
                /*
                 * A completed stop deliberately leaves late PCM blocked.
                 * Keep that barrier across this wake; response.created is the
                 * ordered boundary that proves subsequent PCM belongs to the
                 * newly requested answer.
                 */
                running = false;
                _ws_clear_buffer(&demo);
                if (!wake_preroll_handoff) {
                    __play_ready_tone(
                        &demo,
                        WAKE_TONE_DURATION_MS,
                        "wake confirmation tone"
                    );
                }
                __reopen_capture_stream(&demo);
                speech_start_deadline_ms =
                    __get_time_ms() + WAKE_SPEECH_TIMEOUT_MS;
                running = true;
                printf("wakeup signal: listening\n");
            }
        }
        if (interrupt_only_request) {
            interrupt_only_request = false;
            discard_playback_audio = true;
            proactive_speech_pending = false;
            proactive_speech_active = false;
            proactive_speech_finish_request = false;
            proactive_speech_deadline_ms = 0;
            follow_up_prepare_request = false;
            echo_guard_active = false;
            echo_guard_request = false;
            __reset_barge_in_guard();
            session_active = false;
            waiting_for_speech = false;
            speech_start_deadline_ms = 0;
            follow_up_deadline_ms = 0;
            thinking_deadline_ms = 0;
            __reset_local_direct_tts_state();
            __reset_client_turn_state();
            tool_capture_guard = false;
            __reset_local_vad();
            running = false;
            ai_playing = false;
            start_after_interrupt = false;
            interrupt = true;
            __schedule_cloud_idle_shutdown();
            __write_dialog_status("interrupted", "已请求打断当前回答");
        }
        if (echo_guard_request) {
            echo_guard_request = false;
            __finish_dialog_latency("echo_guard");
            discard_playback_audio = true;
            proactive_speech_pending = false;
            proactive_speech_active = false;
            proactive_speech_finish_request = false;
            proactive_speech_deadline_ms = 0;
            follow_up_prepare_request = false;
            echo_guard_active = true;
            session_active = false;
            waiting_for_speech = false;
            speech_start_deadline_ms = 0;
            follow_up_deadline_ms = 0;
            thinking_deadline_ms = 0;
            __reset_local_direct_tts_state();
            __reset_client_turn_state();
            tool_capture_guard = false;
            __reset_local_vad();
            running = false;
            ai_playing = false;
            start_after_interrupt = false;
            interrupt = true;
            __schedule_cloud_idle_shutdown();
            __write_dialog_status(
                "echo_guard",
                "检测到扬声器回声，已停止本次对话，请重新唤醒"
            );
            printf("echo guard: repeated barge-in loop stopped\n");
        }
        if (follow_up_prepare_request && session_active) {
            int playback_pending = 0;
            bool playback_completion_claimed = false;

            pthread_mutex_lock(&demo.ring_buf_mutex);
            playback_pending =
                volc_ringbuf_getdatasize(demo.ring_buf) > 0 ||
                playback_write_active ||
                !demo.response_playback_drained;
            if (!playback_pending) {
                demo.response_playback_claimed = true;
                playback_completion_claimed = true;
            }
            pthread_mutex_unlock(&demo.ring_buf_mutex);
            if (playback_completion_claimed) {
                /*
                 * 等待 PulseAudio 中排队的 TTS 真正播放完，再静默复位
                 * 云端和本地录音缓冲并开放下一轮，不在连续对话中插入提示音。
                 */
                __finish_dialog_latency("playback_drained");
                follow_up_prepare_request = false;
                __reset_local_direct_tts_state();
                _ws_clear_buffer(&demo);
                __reopen_capture_stream(&demo);
                __reset_local_vad();
                ai_playing = false;
                playback_capture_guard = false;
                __reset_follow_up_window(false);
                printf("follow-up capture ready without tone\n");
            }
        }
        if (proactive_speech_finish_request && !session_active) {
            int playback_pending = 0;
            int drain_error = 0;
            bool touch_guard_active = __dialog_audio_guard_active();

            pthread_mutex_lock(&demo.ring_buf_mutex);
            playback_pending =
                volc_ringbuf_getdatasize(demo.ring_buf) > 0 ||
                playback_write_active ||
                touch_guard_active;
            pthread_mutex_unlock(&demo.ring_buf_mutex);
            if (!playback_pending) {
                pthread_mutex_lock(&demo.playback_mutex);
                if (pa_simple_drain(demo.p_playback, &drain_error) < 0) {
                    printf(
                        "proactive playback drain failed: %s\n",
                        pa_strerror(drain_error)
                    );
                }
                pthread_mutex_unlock(&demo.playback_mutex);
                proactive_speech_finish_request = false;
                ai_playing = false;
                playback_capture_guard = false;
                __schedule_cloud_idle_shutdown();
                __write_dialog_status("ready", "自主动作完成，等待口令“小安，开启对话模式”");
                printf("proactive input_tts playback drained\n");
            }
        }
        // printf("volc....................\n");
        if (running) {
            bool local_speech_started_now = false;
            bool local_endpoint_detected = false;
            bool replaying_wake_preroll = false;
            bool suppress_capture = false;
            size_t capture_len = (size_t)demo.frame_len;
            int send_result = 0;

            if (!__process_gate_allows_effect()) break;
            if (demo.p_capture == NULL) {
                exit_request = true;
                break;
            }
            last_time_ms = __get_time_ms();
            // __get_fps();
            if (wake_preroll_offset < wake_preroll_len) {
                size_t remaining = wake_preroll_len - wake_preroll_offset;
                capture_len = remaining < (size_t)demo.frame_len
                    ? remaining
                    : (size_t)demo.frame_len;
                memcpy(
                    demo.audio_rec_buf,
                    wake_preroll_data + wake_preroll_offset,
                    capture_len
                );
                wake_preroll_offset += capture_len;
                replaying_wake_preroll = true;
            } else if (
                pa_simple_read(
                    demo.p_capture,
                    demo.audio_rec_buf,
                    (size_t)demo.frame_len,
                    &error
                ) < 0
            ) {
                fprintf(stderr, "录音失败: %s\n", pa_strerror(error));
                exit_request = true;
                break;
            }
            /* A blocking capture read may have crossed closing/epoch change. */
            if (!__process_gate_allows_effect()) break;
            suppress_capture =
                !replaying_wake_preroll &&
                (tool_capture_guard ||
                (
                    playback_capture_guard &&
                    __get_time_ms() < playback_capture_guard_until_ms
                ));
            if (suppress_capture) {
                __reset_local_vad();
            } else if (waiting_for_speech) {
                local_endpoint_detected = __local_vad_should_commit(
                    demo.audio_rec_buf,
                    capture_len,
                    demo.sample_rate,
                    &local_speech_started_now
                );
                if (local_speech_started_now) {
                    speech_start_deadline_ms = 0;
                    __write_dialog_status(
                        "listening",
                        "已检测到语音，等待说完"
                    );
                }
            }
            if (!suppress_capture) {
                bool should_commit =
                    local_endpoint_detected && waiting_for_speech;

                /*
                 * TurnDetectionMode=1 由端侧决定句尾。SDK 补丁只发送
                 * append+commit；收到 input_audio_buffer.committed 后再由
                 * 主线程发送 response.create，避免连续轮次中请求早于云端
                 * 完成本轮音频入队而被静默忽略。
                */
                info.commit = should_commit;
                if (should_commit) {
                    __begin_voice_turn_latency(local_silence_duration_ms);
                    __clear_pending_user_transcript();
                    pthread_mutex_lock(&client_route_mutex);
                    voice_commit_acked = false;
                    voice_response_requested = false;
                    local_battery_reply_request = false;
                    local_battery_route_state = LOCAL_BATTERY_ROUTE_NONE;
                    local_battery_cancel_deadline_ms = 0;
                    response_create_request = false;
                    response_cancel_request = false;
                    client_turn_pending = true;
                    input_transcript_pending = true;
                    client_commit_ack_deadline_ms =
                        __get_time_ms() + CLIENT_COMMIT_ACK_TIMEOUT_MS;
                    client_transcript_deadline_ms =
                        __get_time_ms() + CLIENT_TRANSCRIPT_TIMEOUT_MS;
                    pthread_mutex_unlock(&client_route_mutex);
                    running = false;
                }
                if (!__process_gate_allows_effect()) break;
                send_result = volc_send_audio_data(
                    demo.engine,
                    demo.audio_rec_buf,
                    (int)capture_len,
                    &info
                );
                if (send_result < 0) {
                    __finish_dialog_latency("audio_upload_failed");
                    __reset_client_turn_state();
                    running = true;
                    fprintf(stderr, "audio upload failed, ret=%d\n", send_result);
                } else if (should_commit) {
                    __clear_wake_preroll(
                        &wake_preroll_data,
                        &wake_preroll_len,
                        &wake_preroll_offset
                    );
                    __reset_local_vad();
                    waiting_for_speech = false;
                    speech_start_deadline_ms = 0;
                    follow_up_deadline_ms = 0;
                    __write_dialog_status(
                        "processing_audio",
                        "问题已发送，等待云端确认"
                    );
                    printf("local VAD: utterance committed; waiting for acknowledgement\n");
                }
            }
            if (
                wake_preroll_len > 0 &&
                wake_preroll_offset >= wake_preroll_len
            ) {
                __clear_wake_preroll(
                    &wake_preroll_data,
                    &wake_preroll_len,
                    &wake_preroll_offset
                );
            }
            if (
                waiting_for_speech &&
                speech_start_deadline_ms != 0 &&
                __get_time_ms() >= speech_start_deadline_ms
            ) {
                _ws_clear_buffer(&demo);
                __end_continuous_session(
                    follow_up_deadline_ms != 0
                        ? "连续对话等待超时，对话模式已结束"
                        : "没有听到问题，请重新说“小安，开启对话模式”"
                );
            }
            diff_time_ms = __get_time_ms() - last_time_ms;
            if (
                !replaying_wake_preroll &&
                diff_time_ms < (uint64_t)demo.audio_frame_ms
            ) {
                usleep(
                    (useconds_t)(
                        ((uint64_t)demo.audio_frame_ms - diff_time_ms) * 1000
                    )
                );
            }
        } else {
            usleep(MAIN_LOOP_IDLE_POLL_MS * 1000);
        }
        if (__claim_local_battery_cancel_request(__get_time_ms())) {
            int cancel_result;
            int flush_error = 0;

            discard_playback_audio = true;
            running = false;
            waiting_for_speech = false;
            ai_playing = true;
            thinking_deadline_ms = 0;
            response_start_deadline_ms = 0;
            response_text_recovery_deadline_ms = 0;
            pthread_mutex_lock(&demo.ring_buf_mutex);
            volc_ringbuf_clear(demo.ring_buf);
            pthread_mutex_unlock(&demo.ring_buf_mutex);
            pthread_mutex_lock(&demo.playback_mutex);
            if (pa_simple_flush(demo.p_playback, &flush_error) < 0) {
                printf(
                    "local battery override playback flush failed: %s\n",
                    pa_strerror(flush_error)
                );
            }
            pthread_mutex_unlock(&demo.playback_mutex);
            cancel_result = _ws_response_cancel(&demo);
            volc_interrupt(demo.engine);
            __write_dialog_status(
                "processing_audio",
                "正在停止旧回答并读取设备电量"
            );
            printf(
                "speculative cloud response cancelled for local battery route: "
                "ret=%d\n",
                cancel_result
            );
            if (cancel_result < 0) {
                __finish_dialog_latency("local_battery_cancel_failed");
                __reset_local_direct_tts_state();
                __reset_client_turn_state();
                session_active = false;
                ai_playing = false;
                __write_dialog_status(
                    "recovering",
                    "无法安全切换电量播报，正在重新连接"
                );
                exit_request = true;
            }
        }
        if (__claim_local_battery_reply()) {
            char local_event_id[128];
            int local_result;

            snprintf(
                local_event_id,
                sizeof(local_event_id),
                "event_local_battery_%" PRIu64 "_%lu",
                dialog_runtime_started_ms,
                ++local_direct_sequence
            );
            local_result = __start_local_battery_reply(
                &demo,
                local_event_id
            );
            if (local_result < 0) {
                __finish_dialog_latency("local_battery_tts_failed");
                session_active = false;
                running = false;
                ai_playing = false;
                __write_dialog_status(
                    "recovering",
                    "电量播报发送失败，正在重新连接"
                );
                printf("local battery TTS failed; restarting cloud session\n");
                exit_request = true;
            }
        }
        if (response_cancel_request) {
            response_cancel_request = false;
            __finish_dialog_latency("empty_transcript");
            if (__voice_response_was_requested()) {
                _ws_response_cancel(&demo);
            }
            _ws_clear_buffer(&demo);
            __end_continuous_session(
                "没有识别到有效问题，请重新说“小安，开启对话模式”"
            );
        }
        if (__claim_voice_response_request()) {
            int response_result;
            char transcript[DIALOG_TEXT_MAX_BYTES + 1];
            uint64_t now_ms = __get_time_ms();

            response_text_recovery_sent = false;
            response_text_recovery_deadline_ms = 0;
            response_start_deadline_ms = now_ms + RESPONSE_START_TIMEOUT_MS;
            if (__copy_pending_user_transcript(transcript, sizeof(transcript))) {
                response_text_recovery_deadline_ms =
                    now_ms + RESPONSE_TEXT_RECOVERY_DELAY_MS;
            }
            ai_playing = true;
            thinking_deadline_ms = now_ms + THINKING_TIMEOUT_MS;
            __write_dialog_status("thinking", "问题已收到，正在思考");
            __mark_dialog_turn_response_requested();
            response_result = _ws_response_create(&demo);
            if (response_result < 0) {
                __finish_dialog_latency("response_request_failed");
                __reset_client_turn_state();
                session_active = false;
                ai_playing = false;
                __write_dialog_status(
                    "recovering",
                    "请求回答失败，正在重新连接"
                );
                printf("response.create failed: restarting cloud session\n");
                exit_request = true;
            } else {
                printf("response.create sent after commit acknowledgement\n");
            }
        }
        if (
            client_commit_ack_deadline_ms != 0 &&
            __get_time_ms() >= client_commit_ack_deadline_ms
        ) {
            __finish_dialog_latency("commit_timeout");
            client_commit_ack_deadline_ms = 0;
            __reset_client_turn_state();
            session_active = false;
            waiting_for_speech = false;
            running = false;
            ai_playing = false;
            __write_dialog_status(
                "recovering",
                "本轮语音未确认，正在清理并重连"
            );
            printf("client audio commit timeout: restarting cloud session\n");
            exit_request = true;
        }
        if (
            input_transcript_pending &&
            client_transcript_deadline_ms != 0 &&
            __get_time_ms() >= client_transcript_deadline_ms
        ) {
            input_transcript_pending = false;
            client_transcript_deadline_ms = 0;
            if (voice_commit_acked) {
                __queue_voice_response_if_unclaimed();
            }
            printf(
                "final transcript timeout: history unavailable; response "
                "routing remains non-blocking\n"
            );
        }
        if (__local_battery_cancel_timed_out(__get_time_ms())) {
            __finish_dialog_latency("local_battery_cancel_timeout");
            __reset_local_direct_tts_state();
            __reset_client_turn_state();
            session_active = false;
            waiting_for_speech = false;
            running = false;
            ai_playing = false;
            thinking_deadline_ms = 0;
            __write_dialog_status(
                "recovering",
                "旧回答未确认停止，正在重新连接"
            );
            printf(
                "local battery override cancel timeout: restarting cloud session\n"
            );
            exit_request = true;
        }
        if (
            response_text_recovery_deadline_ms != 0 &&
            __get_time_ms() >= response_text_recovery_deadline_ms
        ) {
            char recovery_event_id[128];
            char recovery_text[DIALOG_TEXT_MAX_BYTES + 1];
            int recovery_result = -1;

            response_text_recovery_deadline_ms = 0;
            if (
                response_start_deadline_ms != 0 &&
                !response_text_recovery_sent &&
                __copy_pending_user_transcript(
                    recovery_text,
                    sizeof(recovery_text)
                )
            ) {
                response_text_recovery_sent = true;
                snprintf(
                    recovery_event_id,
                    sizeof(recovery_event_id),
                    "event_voice_recovery_%" PRIu64 "_%lu",
                    dialog_runtime_started_ms,
                    ++voice_recovery_sequence
                );
                __write_dialog_status(
                    "thinking",
                    "语音已识别，正在恢复回答"
                );
                recovery_result = __send_text_dialog_item(
                    &demo,
                    recovery_event_id,
                    recovery_text
                );
                if (recovery_result >= 0) {
                    recovery_result = _ws_response_create(&demo);
                }
                if (recovery_result < 0) {
                    __reset_client_turn_state();
                    session_active = false;
                    running = false;
                    ai_playing = false;
                    __write_dialog_status(
                        "recovering",
                        "文字兜底请求失败，正在重新连接"
                    );
                    printf(
                        "explicit text recovery failed: restarting cloud session\n"
                    );
                    exit_request = true;
                } else {
                    printf(
                        "explicit text recovery submitted: event_id=%s\n",
                        recovery_event_id
                    );
                }
            }
        }
        if (
            response_start_deadline_ms != 0 &&
            __get_time_ms() >= response_start_deadline_ms
        ) {
            __finish_dialog_latency("response_start_timeout");
            response_start_deadline_ms = 0;
            _ws_response_cancel(&demo);
            _ws_clear_buffer(&demo);
            session_active = false;
            waiting_for_speech = false;
            running = false;
            ai_playing = false;
            __write_dialog_status(
                "recovering",
                "云端未开始回答，正在重新连接"
            );
            printf("response start timeout: restarting cloud session\n");
            exit_request = true;
        }
        if (
            thinking_deadline_ms != 0 &&
            __get_time_ms() >= thinking_deadline_ms
        ) {
            __finish_dialog_latency("thinking_timeout");
            session_active = false;
            waiting_for_speech = false;
            speech_start_deadline_ms = 0;
            follow_up_deadline_ms = 0;
            thinking_deadline_ms = 0;
            __reset_local_vad();
            running = false;
            ai_playing = false;
            __write_dialog_status(
                "recovering",
                "云端响应超时，正在重新连接"
            );
            printf("thinking timeout: restarting cloud session\n");
            exit_request = true;
        }
        if (interrupt) {
            /* 同时清空应用和 PulseAudio 缓冲，避免信号命中后仍延迟播完。 */
            int flush_error = 0;
            bool flush_succeeded = true;
            interrupt = false;
            pthread_mutex_lock(&demo.ring_buf_mutex);
            volc_ringbuf_clear(demo.ring_buf);
            pthread_mutex_unlock(&demo.ring_buf_mutex);
            pthread_mutex_lock(&demo.playback_mutex);
            if (pa_simple_flush(demo.p_playback, &flush_error) < 0) {
                flush_succeeded = false;
                printf(
                    "interrupt playback flush failed: %s\n",
                    pa_strerror(flush_error)
                );
            }
            pthread_mutex_unlock(&demo.playback_mutex);
            __finish_dialog_latency(
                flush_succeeded
                    ? "local_interrupt_playback_flushed"
                    : "local_interrupt_flush_failed"
            );
            printf(
                "response.cancel after local interrupt: ret=%d\n",
                _ws_response_cancel(&demo)
            );
            volc_interrupt(demo.engine);
            if (start_after_interrupt) {
                start_after_interrupt = false;
                session_active = true;
                waiting_for_speech = true;
                _ws_clear_buffer(&demo);
                if (wake_tone_after_interrupt) {
                    wake_tone_after_interrupt = false;
                    __play_ready_tone(
                        &demo,
                        WAKE_TONE_DURATION_MS,
                        "wake confirmation tone"
                    );
                }
                __reopen_capture_stream(&demo);
                speech_start_deadline_ms = __get_time_ms() + WAKE_SPEECH_TIMEOUT_MS;
                running = true;
                __write_dialog_status("listening", "回答已打断，请继续说话");
                printf("状态: 打断后继续聆听\n");
            }
        }
    }

    /* 阶段 5：通知播放线程退出并释放本进程持有的音频和缓冲资源。 */
err_out_label:
    exit_request = true;
    /* Required cleanup must not reacquire admission. SDK callbacks can still
     * arrive until destroy, but no new worker can pass the synchronized stop.
     * A motor waitpid / SDK send already in flight keeps this process lease. */
    __drain_function_call_workers();
    if (playback_thread_started) pthread_join(demo.audio_playback_task, NULL);
    __finish_dialog_latency("process_exit");
    session_active = false;
    __reset_local_direct_tts_state();
    unlink(DIALOG_SESSION_MARKER);
    unlink(WAKE_PREROLL_PATH);
    unlink(WAKE_PREROLL_TMP_PATH);
    __clear_wake_preroll(
        &wake_preroll_data,
        &wake_preroll_len,
        &wake_preroll_offset
    );
    discard_playback_audio = false;
    __end_dialog_audio_guard();
    if (playback_error_received) {
        __write_dialog_status(
            "recovering",
            "本地播放服务异常，正在重新连接"
        );
    } else if (cloud_error_received) {
        __write_dialog_status(
            "recovering",
            "云端暂时无法完成回答，正在重新连接"
        );
    } else {
        __write_dialog_status("offline", "语音服务已停止");
    }
	if (demo.engine) {
		volc_stop(demo.engine);
		volc_destroy(demo.engine);
		demo.engine = NULL;
	}
	if (demo.audio_rec_buf) {
		free(demo.audio_rec_buf);
	}
	if (demo.ring_buf) {
        pthread_mutex_destroy(&demo.ring_buf_mutex);
        pthread_mutex_destroy(&demo.playback_mutex);
        volc_ringbuf_destroy(demo.ring_buf);
    }
	if (demo.p_capture) {
		pa_simple_free(demo.p_capture);
	}
	if (demo.p_playback) {
		if (process_gate_closing) pa_simple_flush(demo.p_playback, &error);
		else pa_simple_drain(demo.p_playback, &error);
		pa_simple_free(demo.p_playback);
	}
	return clean_exit_requested ? EXIT_SUCCESS : EXIT_FAILURE;
}
