
/*
 * 火山引擎实时对话 Demo（macOS / Linux K1 共用）。
 *
 * 核心流程：
 *   1. main() 读取 conv_ai_config.json，初始化 PulseAudio 和 SDK。
 *   2. 空格键或 SIGUSR1 将 running 置为 true，主循环开始上传麦克风 PCM。
 *   3. 麦克风在连续会话内持续上行，由服务端 VAD 自动判定每句话结束。
 *   4. 云端返回的 TTS PCM 先写入环形缓冲，再由播放线程送到扬声器。
 *   5. 云端 Function Calling 可异步执行 K1 摇头命令并回传结果。
 *
 * 并发模型：
 *   - 主线程：录音上传、键盘/信号状态机和 SDK 控制命令。
 *   - SDK 回调线程：连接事件、对话状态、消息和下行音频。
 *   - 播放线程：消费环形缓冲中的 TTS 音频。
 *   - 工具线程：执行可能阻塞的电机命令，避免阻塞 SDK 回调。
 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <termios.h>
#include <errno.h>
#include <stdbool.h>
#include <string.h>
#include <pthread.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>

#include <pulse/simple.h>
#include <pulse/error.h>

#include "cJSON.h"

#include "util/volc_ringbuf.h"

#include "volc_conv_ai.h"

#define AUDIO_CHANNEL_NUM 1
#define AUDIO_FRAME_PER_SECOND (32000)
#define AUDIO_FRAME_MS (1000 / (AUDIO_FRAME_PER_SECOND / AUDIO_FRAME_LEN))
#define AUDIO_PLAYBACK_CHUNK_MS 20
#define FUNCTION_NAME_MAX_LEN 64
#define FUNCTION_CALL_ID_MAX_LEN 128
#define WAKE_SPEECH_TIMEOUT_MS 15000
#define FOLLOW_UP_WINDOW_MS 15000
#define DIALOG_STATUS_DIR "/run/ai-cat"
#define DIALOG_STATUS_PATH DIALOG_STATUS_DIR "/dialog-status.json"
#define DIALOG_STATUS_TMP_PATH DIALOG_STATUS_DIR "/dialog-status.json.tmp"
#define DIALOG_SESSION_MARKER DIALOG_STATUS_DIR "/dialog-session-active"

#define WS_BUFFER_CLEAR "{\"type\":\"input_audio_buffer.clear\"}"
#define WS_SESSION_UPDATE "{\"event_id\":\"event_OgjwihjHg\",\"type\":\"session.update\",\"session\":{\"object\":\"realtime.session\",\"model\":\"\",\"config\":{\"ASRConfig\":{\"TurnDetectionMode\":0}},\"agent_config\":{\"WelcomeMessage\":\"这是一个覆盖智能体上的欢迎语.\"}}}"

typedef struct {
    /* 上行音频、可选视频和云端下行播放所需的运行时资源。 */
    uint8_t* audio_rec_buf;
    char* bot_id;
    char* video_rec_buf;
    int video_frame_len;
    int mode;
    int frame_len;
    int sample_rate;
    int audio_frame_ms;
    volc_ringbuf_t ring_buf;
	    pthread_mutex_t ring_buf_mutex;
	pa_simple* p_capture;
	pa_simple* p_playback;
    pa_sample_spec format;
    pthread_t audio_playback_task;
    volc_engine_t engine;
} realtime_ws_demo_t;

/*
 * 跨信号/回调共享的轻量状态。信号处理函数只修改标志位，
 * 所有 SDK 调用都留在主循环执行，避免在信号上下文中做非安全操作。
 */
static volatile sig_atomic_t running = false;               /* 正在采集并上传麦克风 */
static volatile sig_atomic_t ai_playing = false;             /* 云端正在思考或播放回答 */
static volatile sig_atomic_t interrupt = false;              /* 请求打断当前回答 */
static volatile sig_atomic_t start_after_interrupt = false;  /* 打断后立即开始下一轮 */
static volatile sig_atomic_t start = false;
static volatile sig_atomic_t stop = false;
static volatile sig_atomic_t destory = false;
static volatile sig_atomic_t clear = false;
static volatile sig_atomic_t video_upload = false;
static volatile sig_atomic_t exit_request = false;           /* 主线程和播放线程退出条件 */
static volatile sig_atomic_t session_update = false;
static volatile sig_atomic_t wake_request = false;           /* SIGUSR1 转换出的唤醒请求 */
static volatile sig_atomic_t interrupt_only_request = false; /* SIGUSR2 只打断，不开始录音 */
static volatile sig_atomic_t session_active = false;          /* 一次唤醒后的连续对话窗口 */
static volatile sig_atomic_t waiting_for_speech = false;      /* 等待用户开始说话 */
static uint64_t speech_start_deadline_ms = 0;                 /* 等待首句/追问的截止时间 */
static uint64_t follow_up_deadline_ms = 0;                    /* 无需唤醒的追问窗口 */
static struct termios original_term;
static bool terminal_configured = false;
static int tick = 0;
extern char** environ;
static pthread_mutex_t dialog_status_mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned long dialog_status_sequence = 0;

/* Function Calling 分两条消息到达，先暂存 name/call_id，再等待参数完成事件。 */
typedef struct {
    char name[FUNCTION_NAME_MAX_LEN];
    char call_id[FUNCTION_CALL_ID_MAX_LEN];
} pending_function_call_t;

typedef struct {
    realtime_ws_demo_t* demo;
    char call_id[FUNCTION_CALL_ID_MAX_LEN];
} function_call_task_t;

static pending_function_call_t pending_function_call;
static pthread_mutex_t function_call_mutex = PTHREAD_MUTEX_INITIALIZER;

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

/*
 * 对话状态通过固定 JSON 文件提供给 FastAPI。先写临时文件再 rename，
 * 读取端永远只会看到一份完整 JSON；会话 marker 供唤醒词进程协调录音占用。
 */
static void __write_dialog_status(const char* state, const char* message) {
    cJSON* root = NULL;
    char* json = NULL;
    FILE* fp = NULL;

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
    cJSON_AddBoolToObject(root, "can_interrupt", ai_playing != 0);
    cJSON_AddNumberToObject(root, "follow_up_deadline_ms", (double)follow_up_deadline_ms);
    cJSON_AddNumberToObject(root, "updated_at_ms", (double)__get_time_ms());
    cJSON_AddNumberToObject(root, "sequence", (double)++dialog_status_sequence);
    cJSON_AddNumberToObject(root, "pid", (double)getpid());
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

static void __end_continuous_session(const char* message) {
    running = false;
    session_active = false;
    waiting_for_speech = false;
    speech_start_deadline_ms = 0;
    follow_up_deadline_ms = 0;
    __write_dialog_status("ready", message);
}

/* 仅交互运行时恢复终端；systemd 模式没有 TTY，不执行任何操作。 */
static void __restore_terminal(void) {
    if (terminal_configured) {
        tcsetattr(STDIN_FILENO, TCSANOW, &original_term);
    }
}

static void __handle_key(char key) {
    /*
     * 按键只改变状态，实际 start/stop/interrupt/update 等调用统一由
     * 主循环处理，避免键盘处理路径与音频路径并发操作 engine。
     */
    switch (key) {
        case ' ':
            if (session_active) {
                printf("\n状态: 打断并结束连续对话\n");
                interrupt_only_request = true;
            } else {
                printf("\n状态: 开始连续对话\n");
                wake_request = true;
            }
            break;
        case 'i':
            printf("\n状态: 打断并结束连续对话\n");
            interrupt_only_request = true;
            break;
        case 'o':
            printf("\n状态: stop\n");
            stop = true;
            break;
        case 'a':
            printf("\n状态: start\n");
            start = true;
            break;
        case 'd':
            printf("\n状态: destory\n");
            destory = true;
            break;
        case 'c':
            printf("\n状态: 清除\n");
            clear = true;
            break;
        case 'v':
            printf("\n状态: 视频上传\n");
            video_upload = true;
            break;
        case 'u':
            printf("\n状态: session update\n");
            session_update = true;
            break;
        default:
            break;
    }
}

static void __poll_keyboard(void) {
    char buf[16];
    ssize_t len;

    while ((len = read(STDIN_FILENO, buf, sizeof(buf))) > 0) {
        for (ssize_t i = 0; i < len; i++) {
            __handle_key(buf[i]);
        }
    }
}

/*
 * SIGUSR1 开始或继续一轮对话；SIGUSR2 只打断当前回答并结束连续会话。
 * SIGINT/SIGTERM 只请求退出，让主循环和播放线程有机会正常收尾。
 */
static void __handle_signal(int sig) {
    if (sig == SIGUSR1) {
        wake_request = true;
    } else if (sig == SIGUSR2) {
        interrupt_only_request = true;
    } else if (sig == SIGINT || sig == SIGTERM) {
        exit_request = true;
    }
}

/* 同时配置守护进程信号和交互终端的非阻塞按键输入。 */
static void __setup_async_io(void) {
    struct sigaction sa;
    int flags;

    // 配置信号处理
    sa.sa_handler = __handle_signal;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGUSR1, &sa, NULL);
    sigaction(SIGUSR2, &sa, NULL);

    if (!isatty(STDIN_FILENO)) {
        return;
    }

    // 保存原始终端设置
    if (tcgetattr(STDIN_FILENO, &original_term) != 0) {
        return;
    }
    terminal_configured = true;
    atexit(__restore_terminal);

    // 键盘输入在主循环中轮询，避免SIGIO丢失连续按键
    flags = fcntl(STDIN_FILENO, F_GETFL);
    fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);

    // 修改终端设置：禁用行缓冲和回显
    struct termios term = original_term;
    term.c_lflag &= ~(ICANON | ECHO);
    tcsetattr(STDIN_FILENO, TCSANOW, &term);
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

static int __load_video_file(realtime_ws_demo_t* demo) {
    /* 视频链路的可选测试数据；默认启动流程没有调用此函数。 */
    FILE* video_fp = fopen("send_video.h264", "rb");
    if (video_fp == NULL) {
        printf("failed to open send_video.h264 for reading.\n");
        return -1;
    }
    fseek(video_fp, 0, SEEK_END);
    demo->video_frame_len = ftell(video_fp);
    if (demo->video_frame_len < 0) {
        printf("Failed to seek to end of send_video.h264.\n");
        fclose(video_fp);
        return -1;
    }
    fseek(video_fp, 0, SEEK_SET);
    demo->video_rec_buf = (char*) realloc(demo->video_rec_buf, demo->video_frame_len + 1);
    if (demo->video_rec_buf == NULL) {
        printf("Realloc video_rec_buf failed\n");
        fclose(video_fp);
        return -1;
    }
    memset(demo->video_rec_buf, 0, demo->video_frame_len + 1);
    size_t read_size = fread(demo->video_rec_buf, 1, demo->video_frame_len, video_fp);
    if (read_size != (size_t)demo->video_frame_len) {
        printf("Failed to read send_video.h264, expected %d bytes, got %zu bytes.\n", demo->video_frame_len, read_size);
        free(demo->video_rec_buf);
        fclose(video_fp);
        return -1;
    }
    fclose(video_fp);
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
        pthread_mutex_lock(&demo->ring_buf_mutex);
        len = volc_ringbuf_read(demo->ring_buf, (char *)buffer, playback_chunk_len);
        pthread_mutex_unlock(&demo->ring_buf_mutex);
        if (len > 0) {
            if (pa_simple_write(demo->p_playback, buffer, len, &error) < 0) {
                printf("pa_simple_write error: %s\n", pa_strerror(error));
            }
        } else {
            usleep(10 * 1000);
        }
    }
    free(buffer);
    return NULL;
}

static bool is_ready = false;
static void _on_volc_event(volc_engine_t handle, volc_event_t* event, void* user_data)
{
    /*
     * 断线后退出进程，交给 systemd 的 Restart=always 重建完整 SDK 会话。
     * 这样可以避免继续向已关闭的 WebSocket engine 发送音频。
     */
    switch (event->code) {
        case VOLC_EV_CONNECTED:
            is_ready = true;
            printf("Volc Engine connected\n");
            __write_dialog_status("ready", "等待唤醒词“小安小安”");
            break;
        case VOLC_EV_DISCONNECTED:
            is_ready = false;
            printf("Volc Engine disconnected\n");
            session_active = false;
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

    /*
     * 连续对话期间不能在 THINKING/ANSWERING 停止上行，否则云端永远听不到
     * 用户插话，InterruptMode=0 也无法生效。LISTENING 可能发生在 AI 回答时，
     * 此时立即清空本地 TTS 缓冲，避免残余语音继续播放。
     */
    printf("conversation status changed: %d\n", status);
    if (status == VOLC_CONV_STATUS_LISTENING) {
        if (!session_active) {
            __write_dialog_status("ready", "等待唤醒词“小安小安”");
            return;
        }
        if (ai_playing && demo != NULL) {
            pthread_mutex_lock(&demo->ring_buf_mutex);
            volc_ringbuf_clear(demo->ring_buf);
            pthread_mutex_unlock(&demo->ring_buf_mutex);
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
        running = session_active;
        waiting_for_speech = false;
        speech_start_deadline_ms = 0;
        follow_up_deadline_ms = 0;
        ai_playing = true;
        __write_dialog_status("thinking", "问题已收到，正在思考");
    } else if (status == VOLC_CONV_STATUS_ANSWERING) {
        running = session_active;
        waiting_for_speech = false;
        speech_start_deadline_ms = 0;
        follow_up_deadline_ms = 0;
        ai_playing = true;
        __write_dialog_status("answering", "正在回答，可以直接说话打断");
    } else if (
        status == VOLC_CONV_STATUS_INTERRUPTED ||
        status == VOLC_CONV_STATUS_ANSWER_FINISH
    ) {
        ai_playing = false;
        if (session_active) {
            running = true;
            waiting_for_speech = true;
            follow_up_deadline_ms = now_ms + FOLLOW_UP_WINDOW_MS;
            speech_start_deadline_ms = follow_up_deadline_ms;
            __write_dialog_status(
                "followup_listening",
                status == VOLC_CONV_STATUS_INTERRUPTED
                    ? "回答已打断，可以直接继续说"
                    : "回答结束，15 秒内可以直接追问"
            );
        } else {
            running = false;
            __write_dialog_status(
                status == VOLC_CONV_STATUS_INTERRUPTED ? "interrupted" : "ready",
                status == VOLC_CONV_STATUS_INTERRUPTED
                    ? "回答已打断"
                    : "等待唤醒词“小安小安”"
            );
        }
    }
}

static void _on_volc_audio_data(volc_engine_t handle, const void* data_ptr, size_t data_len, volc_audio_frame_info_t* info_ptr, void* user_data)
{
    /* SDK 下发的是待播放 PCM；此处只入队，播放工作由独立线程完成。 */
	realtime_ws_demo_t* demo = (realtime_ws_demo_t*)user_data;
	if (demo == NULL) {
		printf("demo is NULL\n");
		return;
	}
    pthread_mutex_lock(&demo->ring_buf_mutex);
    int written = volc_ringbuf_write(demo->ring_buf, (char *)data_ptr, data_len);
    pthread_mutex_unlock(&demo->ring_buf_mutex);
    if (written < 0 || (size_t)written != data_len) {
        printf("write audio data to ring buf fail!!!!!!!!\n");
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
    ret = volc_send_message(demo->engine, json_str, strlen(json_str), &msg_info);
    free(json_str);
    return ret;
}

static int __run_head_shake(void) {
    /*
     * 使用固定可执行文件和固定参数，不经 shell 拼接用户输入，
     * 避免命令注入，并通过退出码判断电机动作是否成功。
     */
    const char* executable = "/usr/bin/ai-toy_app";
    char* const argv[] = {
        (char*)executable,
        "motor",
        "head_lr",
        "2",
        NULL,
    };
    pid_t pid;
    int status;
    int ret;

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
    /* 电机动作可能持续数秒，必须放到工作线程，不能阻塞 SDK 消息回调。 */
    function_call_task_t* task = (function_call_task_t*)arg;
    int ret;

    if (task == NULL) {
        return NULL;
    }
    printf("executing shake_head, call_id=%s\n", task->call_id);
    ret = __run_head_shake();
    __send_function_call_output(
        task->demo,
        task->call_id,
        ret == 0 ? "摇头动作已完成" : "摇头动作执行失败");
    free(task);
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
    if (strcmp(name, "shake_head") != 0) {
        printf("unknown function call name: %s\n", name);
        return;
    }

    pthread_mutex_lock(&function_call_mutex);
    snprintf(pending_function_call.name, sizeof(pending_function_call.name), "%s", name);
    snprintf(pending_function_call.call_id, sizeof(pending_function_call.call_id), "%s", call_id);
    pthread_mutex_unlock(&function_call_mutex);
    printf("shake_head function call received, call_id=%s\n", call_id);
}

static void __handle_function_call_arguments_done(realtime_ws_demo_t* demo, cJSON* root) {
    /*
     * Function Calling 第二步：参数接收完成。核对 call_id 后启动工作线程，
     * 防止错配并避免重复执行上一条工具调用。
     */
    pthread_t msg_thread;
    cJSON* call_id_obj;
    function_call_task_t* task;
    int ret;

    if (root == NULL) {
        return;
    }
    call_id_obj = cJSON_GetObjectItem(root, "call_id");
    if (!cJSON_IsString(call_id_obj)) {
        printf("function call arguments missing call_id\n");
        return;
    }

    task = calloc(1, sizeof(*task));
    if (task == NULL) {
        __send_function_call_output(demo, call_id_obj->valuestring, "摇头动作执行失败");
        return;
    }

    pthread_mutex_lock(&function_call_mutex);
    if (strcmp(pending_function_call.name, "shake_head") != 0 ||
        strcmp(pending_function_call.call_id, call_id_obj->valuestring) != 0) {
        pthread_mutex_unlock(&function_call_mutex);
        free(task);
        printf("no matching function call for call_id=%s\n", call_id_obj->valuestring);
        return;
    }
    task->demo = demo;
    snprintf(task->call_id, sizeof(task->call_id), "%s", pending_function_call.call_id);
    pending_function_call.name[0] = '\0';
    pending_function_call.call_id[0] = '\0';
    pthread_mutex_unlock(&function_call_mutex);

    ret = pthread_create(&msg_thread, NULL, __run_function_call, task);
    if (ret != 0) {
        fprintf(stderr, "failed to create function call thread: %s\n", strerror(ret));
        __send_function_call_output(demo, task->call_id, "摇头动作执行失败");
        free(task);
        return;
    }
    pthread_detach(msg_thread);
}

static void __handle_ws_message(realtime_ws_demo_t* demo, const void* message, size_t size) {
    /* WS JSON 消息路由：工具调用单独处理，其余事件原样打印便于调试。 */
    cJSON* root = NULL;
    cJSON* type_obj = NULL;
    root = cJSON_Parse((const char*)message);
    if (NULL == root) {
        printf("parse json buffer failed\n");
        return;
    }
    printf("ws message size:%zu data:%s\n", size, (char*)message);
    type_obj = cJSON_GetObjectItem(root, "type");
    if (type_obj != NULL && strcmp("conversation.item.created", cJSON_GetStringValue(type_obj)) == 0) {
        __handle_ws_conversation_item_created_call(demo, root);
    } else if(type_obj != NULL && strcmp("response.function_call_arguments.done", cJSON_GetStringValue(type_obj)) == 0) {
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
    ret = volc_send_message(demo->engine, clear, clear_len, NULL);
    if (clear) {
        free(clear);
    }
    return ret;
}

int main(int argc, const char* argv[]){
	char* config_data = NULL;
    int error = 0;
    uint64_t last_time_ms = 0;
    uint64_t diff_time_ms = 0;
	realtime_ws_demo_t demo = {0};

    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);
    session_active = false;
    unlink(DIALOG_SESSION_MARKER);
    __write_dialog_status("starting", "语音服务正在启动");

    /* 阶段 1：读取配置并根据传输模式确定音频采样率和帧大小。 */
	if ((config_data = __load_config_from_file("conv_ai_config.json")) == NULL) {
		printf("load config from file fail\n");
		return -1;
	}

    // if (__load_video_file(&demo) != 0) {
    //     printf("load video file failed\n");
    //     return -1;
    // }

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

    /*
     * 阶段 2：创建一条单声道 PulseAudio 录音流和一条播放流。
     * K1 上由 systemd 保证 PulseAudio socket 已就绪后再启动本程序。
     */
    demo.format.format = PA_SAMPLE_S16NE;
    demo.format.rate = demo.sample_rate;
    demo.format.channels = AUDIO_CHANNEL_NUM;
    demo.p_capture = pa_simple_new(NULL, "Capture", PA_STREAM_RECORD, NULL, "Capture", &demo.format, NULL, NULL, &error);
    if (NULL == demo.p_capture) {
        printf("capture pa_simple_new failed: %s\n", pa_strerror(error));
        goto err_out_label;
    }
    demo.p_playback = pa_simple_new(NULL, "Playback", PA_STREAM_PLAYBACK, NULL, "Playback", &demo.format, NULL, NULL, &error);
    if (NULL == demo.p_playback) {
        printf("playback pa_simple_new failed: %s\n", pa_strerror(error));
        goto err_out_label;
    }

    volc_event_handler_t volc_event_handler = {.on_volc_event = _on_volc_event,
                                            .on_volc_conversation_status = _on_volc_conversation_status,
                                            .on_volc_audio_data = _on_volc_audio_data,
                                            .on_volc_video_data = _on_volc_video_data,
                                            .on_volc_message_data = _on_volc_message_data};
    printf("demo.engine : %p\n",demo.engine);
    printf("volc_create event_handler 为NULL");
    error = volc_create(&demo.engine, config_data, &volc_event_handler, &demo);

    free(config_data);
    if (error != 0) {
        printf("volc_create failed: %d\n", error);
        goto err_out_label;
    }
    printf("demo.engine : %p\n",demo.engine);
    volc_opt_t opt = {0};
    opt.mode = demo.mode;

    /*
     * 以下注释代码是 SDK API 的手工实验入口，不属于正常运行路径。
     * 需要专项验证 create/start/stop/destroy 时再单独启用。
     */
    // // create后直接destroy
    // printf("创建后直接销毁\n");
    // volc_destroy(demo.engine);
    // printf("销毁成功...\n");
    // return 0;

    // 验证create后直接destroy 程序不崩溃
    // printf("创建后直接销毁\n");
    // volc_destroy(demo.engine);
    // printf("销毁成功...\n");
    // return 0;


    // 验证引擎为NULL 调用start、stop、destroy 程序不崩溃
    // volc_start(demo.engine, &opt);
    // demo.engine = NULL
    // volc_start(NULL, &opt);
    // volc_stop(NULL);
    // volc_destroy(NULL);
    // return 0;

    // 验证重复多次调用start
    // printf("第一次调用start....");
    // volc_start(demo.engine,&opt);
    // printf("第二次调用start....");
    // volc_start(demo.engine,&opt);
    // printf("第三次调用start....");
    // volc_start(demo.engine,&opt);
    // return 0;


    opt.bot_id = demo.bot_id;
    __write_dialog_status("connecting", "正在连接火山引擎");
    volc_start(demo.engine, &opt);

    /* 阶段 3：等待云端 session 就绪后再接受唤醒，避免丢失首轮音频。 */
    while (!is_ready && !exit_request) {
        sleep(1);
        printf("waiting for volc realtime to be ready...\n");
    }
    if (exit_request) {
        printf("Volc Engine connection failed; exiting for service restart.\n");
        goto err_out_label;
    }
    printf("volc realtime is ready.\n");
    __write_dialog_status("ready", "等待唤醒词“小安小安”");


    __setup_async_io();
    printf("键盘监听程序已启动\n");
    printf("按空格键开始/停止, 按i键打断, 按c键清除, 按v键开启视频上传, 按s键stop/start, 按d键 按Ctrl+C退出\n");
    printf("当前状态: 已停止\n");
    pthread_create(&demo.audio_playback_task, NULL, __audio_playback_task, &demo);

    volc_audio_frame_info_t info = {0};
    info.data_type = VOLC_AUDIO_DATA_TYPE_PCM;
    volc_video_frame_info_t video_info = {0};

    /*
     * 阶段 4：主状态机。
     * running=true 时阻塞读取一帧麦克风 PCM 并持续上传。服务端 VAD
     * 根据静音自动判定句尾，客户端不对正常语句主动发送 commit。
     */
    while (!exit_request) {
        __poll_keyboard();

        /*
         * SIGUSR1 只负责启动连续会话。会话激活后麦克风持续上传，
         * 云端 VAD 负责判停，InterruptMode=0 负责在用户插话时打断 AI。
         */
        if (wake_request) {
            wake_request = false;
            session_active = true;
            waiting_for_speech = true;
            speech_start_deadline_ms = __get_time_ms() + WAKE_SPEECH_TIMEOUT_MS;
            follow_up_deadline_ms = 0;
            __write_dialog_status("wake_detected", "已听到唤醒词，请开始说话");
            if (ai_playing) {
                interrupt = true;
                start_after_interrupt = true;
                ai_playing = false;
                printf("wakeup signal: interrupting current response\n");
            } else {
                running = true;
                printf("wakeup signal: listening\n");
            }
        }
        if (interrupt_only_request) {
            interrupt_only_request = false;
            session_active = false;
            waiting_for_speech = false;
            speech_start_deadline_ms = 0;
            follow_up_deadline_ms = 0;
            running = false;
            start_after_interrupt = false;
            interrupt = true;
            __write_dialog_status("interrupted", "已请求打断当前回答");
        }
        // printf("volc....................\n");
        if (running) {
            last_time_ms = __get_time_ms();
            // __get_fps();
            if (pa_simple_read(demo.p_capture, demo.audio_rec_buf, demo.frame_len, &error) < 0) {
                fprintf(stderr, "录音失败: %s\n", pa_strerror(error));
                break;
            }
            info.commit = false;
            volc_send_audio_data(demo.engine, demo.audio_rec_buf, demo.frame_len, &info);
            if (
                waiting_for_speech &&
                speech_start_deadline_ms != 0 &&
                __get_time_ms() >= speech_start_deadline_ms
            ) {
                _ws_clear_buffer(&demo);
                __end_continuous_session(
                    follow_up_deadline_ms != 0
                        ? "连续对话已结束，请重新说“小安小安”"
                        : "没有听到问题，请重新说“小安小安”"
                );
            }
            diff_time_ms = __get_time_ms() - last_time_ms;
            if (diff_time_ms < (uint64_t)demo.audio_frame_ms) {
                usleep(
                    (useconds_t)(
                        ((uint64_t)demo.audio_frame_ms - diff_time_ms) * 1000
                    )
                );
            }
        } else {
            usleep(100000);
        }
        if (video_upload) {
            /* 视频为可选调试路径，当前 K1 默认没有加载 send_video.h264。 */
            video_info.data_type = VOLC_VIDEO_DATA_TYPE_H264;
            if ((tick++ % 5) == 0) {
                volc_send_video_data(demo.engine, demo.video_rec_buf, demo.video_frame_len, &video_info);
            }
        }
        if (interrupt) {
            /* 打断前清空本地 TTS 缓冲，避免继续播放上一轮残留音频。 */
            interrupt = false;
            pthread_mutex_lock(&demo.ring_buf_mutex);
            volc_ringbuf_clear(demo.ring_buf);
            pthread_mutex_unlock(&demo.ring_buf_mutex);
            volc_interrupt(demo.engine);
            if (start_after_interrupt) {
                start_after_interrupt = false;
                session_active = true;
                waiting_for_speech = true;
                speech_start_deadline_ms = __get_time_ms() + WAKE_SPEECH_TIMEOUT_MS;
                running = true;
                __write_dialog_status("listening", "回答已打断，请继续说话");
                printf("状态: 打断后继续聆听\n");
            }
        }
        if (clear) {
            clear = false;
            _ws_clear_buffer(&demo);
        }
        if (stop) {
            stop = false;
            volc_stop(demo.engine);
        }
        if (start) {
            start = false;
            volc_start(demo.engine,&opt);
        }
        if (destory) {
            destory = false;
            volc_destroy(demo.engine);
            demo.engine = NULL; // 防止后续代码意外使用已释放的句柄
        }
        if (session_update) {
            session_update = false;
            volc_update(demo.engine, WS_SESSION_UPDATE, strlen(WS_SESSION_UPDATE));
        }
    }

    /* 阶段 5：通知播放线程退出并释放本进程持有的音频和缓冲资源。 */
    pthread_join(demo.audio_playback_task, NULL);

err_out_label:
    session_active = false;
    unlink(DIALOG_SESSION_MARKER);
    __write_dialog_status("offline", "语音服务已停止");
	if (demo.audio_rec_buf) {
		free(demo.audio_rec_buf);
	}
	if (demo.ring_buf) {
        pthread_mutex_destroy(&demo.ring_buf_mutex);
        volc_ringbuf_destroy(demo.ring_buf);
    }
	if (demo.p_capture) {
		pa_simple_free(demo.p_capture);
	}
	if (demo.p_playback) {
		pa_simple_drain(demo.p_playback, &error);
		pa_simple_free(demo.p_playback);
	}
	getchar();
	return 0;
}
