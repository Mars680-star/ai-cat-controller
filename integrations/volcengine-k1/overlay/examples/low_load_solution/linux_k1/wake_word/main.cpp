/*
 * Always-on offline wake-word and named-interruption process for K1.
 *
 * Maintenance notes:
 * - This process must not keep a Volcengine session open while idle.
 * - Wake/interruption phrases and VAD timing are maintained in this file.
 * - prepare_sdk.sh copies this canonical source into the reconstructed SDK.
 * - Deploy by rebuilding the wake-word binary; do not edit only the K1 copy.
 */
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <iostream>
#include <spawn.h>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <vector>

#include <unistd.h>

#include <pulse/error.h>
#include <pulse/simple.h>

#include "backends/sensevoice/sensevoice_model.hpp"
#include "ten_vad.h"

extern char** environ;

namespace {

constexpr int kSampleRate = 16000;
constexpr int kHopSize = 256;
constexpr int kSpeechStartFrames = 3;
constexpr int kSilenceFrames = 18;
constexpr int kInterruptSilenceFrames = 6;
constexpr size_t kPreSpeechSamples = 8000;
constexpr size_t kInterruptPreSpeechSamples = 3200;
constexpr size_t kMinUtteranceSamples = 8000;
constexpr size_t kMinInterruptUtteranceSamples = 6400;
constexpr size_t kMaxUtteranceSamples = 80000;
constexpr size_t kMaxWakeUtteranceSamples = 48000;
constexpr size_t kMaxInterruptUtteranceSamples = 24000;
constexpr float kVadThreshold = 0.5f;
constexpr auto kTriggerSettleDelay = std::chrono::seconds(1);
constexpr const char* kDialogSessionMarker = "/run/ai-cat/dialog-session-active";
constexpr const char* kLocalSpeechMarker = "/run/ai-cat/local-speech-active";
constexpr const char* kDialogStatusPath = "/run/ai-cat/dialog-status.json";
constexpr const char* kWakePrerollPath = "/run/ai-cat/wake-preroll.pcm";
constexpr const char* kWakePrerollTmpPath = "/run/ai-cat/wake-preroll.pcm.tmp";
constexpr const char* kDialogService = "volc-conv-ai.service";
constexpr size_t kMaxDialogStatusBytes = 16 * 1024;
constexpr size_t kMaxWakePrerollSamples = kSampleRate * 30;
constexpr auto kCloudReadyTimeout = std::chrono::seconds(30);
constexpr auto kInterruptContinuationWindow = std::chrono::seconds(4);
constexpr auto kActivationContinuationWindow = std::chrono::seconds(3);

enum class CaptureMode {
    Paused,
    Wake,
    Interrupt,
};

volatile std::sig_atomic_t exit_requested = 0;

void handleSignal(int) {
    exit_requested = 1;
}

void eraseAll(std::string& value, const std::string& token) {
    size_t pos = 0;
    while ((pos = value.find(token, pos)) != std::string::npos) {
        value.erase(pos, token.size());
    }
}

std::string normalizeText(std::string text) {
    static const char* separators[] = {
        " ", "\t", "\r", "\n", ",", ".", "!", "?", "，", "。", "！", "？", "、",
    };
    for (const char* separator : separators) {
        eraseAll(text, separator);
    }
    return text;
}

bool matchesWakePhrase(const std::string& text, const std::string& wake_phrase) {
    if (text.find(wake_phrase) != std::string::npos) {
        return true;
    }

    static const char* aliases[] = {
        "晓安开启对话模式",
        "下安开启对话模式",
        "小岸开启对话模式",
    };
    for (const char* alias : aliases) {
        if (text.find(alias) != std::string::npos) {
            return true;
        }
    }
    return false;
}

bool matchesActivationPrefix(const std::string& text) {
    static const char* names[] = {
        "小安",
        "晓安",
        "小岸",
        "下安",
    };
    for (const char* name : names) {
        if (text.find(name) != std::string::npos) {
            return true;
        }
    }
    return false;
}

bool matchesActivationSuffix(const std::string& text) {
    static const char* phrases[] = {
        "开启对话模式",
        "开始对话模式",
        "开启对话",
    };
    for (const char* phrase : phrases) {
        if (text.find(phrase) != std::string::npos) {
            return true;
        }
    }
    return false;
}

bool matchesInterruptPhrase(const std::string& text) {
    /*
     * Keep interruption phrases name-prefixed. The detector is active while
     * the speaker is playing, so generic words such as "停" would make the
     * assistant's own audio much too easy to mistake for a user command.
     */
    static const char* phrases[] = {
        "小安停下",
        "小安停止",
        "小安别说了",
        "小安安静",
        "小安暂停",
        "小安结束对话",
        "小安退出对话",
        "小安结束",
        "小安退出",
        "晓安停下",
        "小岸停下",
        "下安停下",
    };
    for (const char* phrase : phrases) {
        if (text.find(phrase) != std::string::npos) {
            return true;
        }
    }
    return false;
}

bool matchesInterruptPrefix(const std::string& text) {
    static const char* prefixes[] = {
        "小安",
        "晓安",
        "小岸",
        "下安",
    };
    for (const char* prefix : prefixes) {
        if (text.find(prefix) != std::string::npos) {
            return true;
        }
    }
    return false;
}

bool matchesInterruptSuffix(const std::string& text) {
    static const char* suffixes[] = {
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
    for (const char* suffix : suffixes) {
        if (text.find(suffix) != std::string::npos) {
            return true;
        }
    }
    return false;
}

std::vector<float> normalizeAudio(const std::vector<int16_t>& samples) {
    std::vector<float> audio(samples.size());
    double sum_squares = 0.0;

    for (int16_t sample : samples) {
        const float normalized = static_cast<float>(sample) / 32768.0f;
        sum_squares += static_cast<double>(normalized) * normalized;
    }

    const float rms = samples.empty()
        ? 0.0f
        : static_cast<float>(std::sqrt(sum_squares / samples.size()));
    const float gain = rms > 0.0001f
        ? std::clamp(0.1f / rms, 1.0f, 5.0f)
        : 1.0f;

    for (size_t i = 0; i < samples.size(); ++i) {
        audio[i] = std::clamp(
            static_cast<float>(samples[i]) / 32768.0f * gain,
            -1.0f,
            1.0f);
    }
    return audio;
}

struct FileVersion {
    bool exists = false;
    dev_t device = 0;
    ino_t inode = 0;
    time_t modified_seconds = 0;
    long modified_nanoseconds = 0;
    off_t size = 0;
};

FileVersion readFileVersion(const char* path) {
    struct stat file_stat {};
    FileVersion version;
    if (stat(path, &file_stat) != 0) {
        return version;
    }
    version.exists = true;
    version.device = file_stat.st_dev;
    version.inode = file_stat.st_ino;
    version.modified_seconds = file_stat.st_mtim.tv_sec;
    version.modified_nanoseconds = file_stat.st_mtim.tv_nsec;
    version.size = file_stat.st_size;
    return version;
}

bool fileVersionChanged(const FileVersion& before, const FileVersion& after) {
    return before.exists != after.exists || before.device != after.device ||
        before.inode != after.inode ||
        before.modified_seconds != after.modified_seconds ||
        before.modified_nanoseconds != after.modified_nanoseconds ||
        before.size != after.size;
}

bool readDialogStatus(std::string& status, FileVersion* version = nullptr) {
    const FileVersion current_version = readFileVersion(kDialogStatusPath);
    if (!current_version.exists || current_version.size <= 0 ||
        current_version.size > static_cast<off_t>(kMaxDialogStatusBytes)) {
        return false;
    }

    FILE* file = std::fopen(kDialogStatusPath, "rb");
    if (file == nullptr) {
        return false;
    }
    std::vector<char> buffer(static_cast<size_t>(current_version.size) + 1, '\0');
    const size_t bytes_read = std::fread(
        buffer.data(),
        1,
        static_cast<size_t>(current_version.size),
        file);
    const bool read_ok = bytes_read == static_cast<size_t>(current_version.size);
    std::fclose(file);
    if (!read_ok) {
        return false;
    }
    status.assign(buffer.data(), bytes_read);
    if (version != nullptr) {
        *version = current_version;
    }
    return true;
}

pid_t dialogStatusPid(const std::string& status) {
    const size_t pid_key = status.find("\"pid\":");
    if (pid_key == std::string::npos) {
        return -1;
    }
    const char* pid_start = status.c_str() + pid_key + 6;
    char* pid_end = nullptr;
    errno = 0;
    const long dialog_pid = std::strtol(pid_start, &pid_end, 10);
    if (errno != 0 || pid_end == pid_start || dialog_pid <= 1) {
        return -1;
    }
    const pid_t pid = static_cast<pid_t>(dialog_pid);
    return (::kill(pid, 0) == 0 || errno == EPERM) ? pid : -1;
}

struct DialogRuntimeState {
    bool session_active = false;
    bool can_interrupt = false;
    pid_t dialog_pid = -1;
};

DialogRuntimeState readDialogRuntimeState() {
    DialogRuntimeState state;
    std::string status;

    state.session_active = access(kDialogSessionMarker, F_OK) == 0;
    if (!readDialogStatus(status)) {
        return state;
    }
    state.dialog_pid = dialogStatusPid(status);
    if (state.dialog_pid <= 1) {
        return state;
    }
    state.session_active = state.session_active ||
        status.find("\"session_active\":true") != std::string::npos;
    state.can_interrupt =
        status.find("\"can_interrupt\":true") != std::string::npos;
    return state;
}

bool startDialogService() {
    char command[] = "systemctl";
    char no_block[] = "--no-block";
    char start[] = "start";
    char service[] = "volc-conv-ai.service";
    char* argv[] = {command, no_block, start, service, nullptr};
    pid_t pid = -1;
    const int spawn_status = posix_spawn(
        &pid,
        "/bin/systemctl",
        nullptr,
        nullptr,
        argv,
        environ);
    if (spawn_status != 0) {
        std::cerr << "[WakeWord] Failed to spawn dialog service start: "
                  << spawn_status << std::endl;
        return false;
    }
    int child_status = 0;
    pid_t wait_result;
    do {
        wait_result = waitpid(pid, &child_status, 0);
    } while (wait_result < 0 && errno == EINTR);
    return wait_result >= 0 && WIFEXITED(child_status) &&
        WEXITSTATUS(child_status) == 0;
}

bool dialogStatusIsReady(
    const FileVersion& previous_version,
    bool require_new_status
) {
    FileVersion current_version;
    std::string status;
    if (!readDialogStatus(status, &current_version) ||
        (require_new_status && !fileVersionChanged(previous_version, current_version))) {
        return false;
    }
    const bool ready = status.find("\"state\":\"ready\"") != std::string::npos ||
        status.find("\"state\":\"interrupted\"") != std::string::npos;
    return ready && dialogStatusPid(status) > 1;
}

pa_simple* openCaptureStream(
    const pa_sample_spec& sample_spec,
    int* pulse_error
) {
    return pa_simple_new(
        nullptr,
        "volc-k1-wake-word",
        PA_STREAM_RECORD,
        nullptr,
        "wake-word-capture",
        &sample_spec,
        nullptr,
        nullptr,
        pulse_error);
}

bool playImmediateReadyTone(const pa_sample_spec& sample_spec) {
    static const int16_t wave[16] = {
        0, 3444, 6363, 8316, 9000, 8316, 6363, 3444,
        0, -3444, -6363, -8316, -9000, -8316, -6363, -3444,
    };
    constexpr int kToneDurationMs = 220;
    const int sample_count =
        static_cast<int>(sample_spec.rate) * kToneDurationMs / 1000;
    const int fade_samples = std::max(1, static_cast<int>(sample_spec.rate) / 200);
    std::vector<int16_t> tone(static_cast<size_t>(sample_count));
    for (int i = 0; i < sample_count; ++i) {
        int envelope = fade_samples;
        if (i < envelope) {
            envelope = i;
        }
        if (sample_count - i - 1 < envelope) {
            envelope = sample_count - i - 1;
        }
        tone[static_cast<size_t>(i)] = static_cast<int16_t>(
            static_cast<int32_t>(wave[i % 16]) * envelope / fade_samples);
    }

    int pulse_error = 0;
    pa_simple* playback = pa_simple_new(
        nullptr,
        "volc-k1-wake-word",
        PA_STREAM_PLAYBACK,
        nullptr,
        "wake-ready-tone",
        &sample_spec,
        nullptr,
        nullptr,
        &pulse_error);
    if (playback == nullptr) {
        std::cerr << "[WakeWord] Immediate ready tone open failed: "
                  << pa_strerror(pulse_error) << std::endl;
        return false;
    }
    const bool played =
        pa_simple_write(
            playback,
            tone.data(),
            tone.size() * sizeof(tone[0]),
            &pulse_error) >= 0 &&
        pa_simple_drain(playback, &pulse_error) >= 0;
    if (!played) {
        std::cerr << "[WakeWord] Immediate ready tone playback failed: "
                  << pa_strerror(pulse_error) << std::endl;
    }
    pa_simple_free(playback);
    return played;
}

bool captureWakePrerollUntilReady(
    const FileVersion& previous_status,
    bool require_new_status,
    const pa_sample_spec& sample_spec,
    std::vector<int16_t>& samples,
    bool& capture_succeeded
) {
    int pulse_error = 0;
    pa_simple* capture = openCaptureStream(sample_spec, &pulse_error);
    capture_succeeded = capture != nullptr;
    if (!capture_succeeded) {
        std::cerr << "[WakeWord] Wake preroll capture failed to open: "
                  << pa_strerror(pulse_error)
                  << "; dialog will use its fallback tone" << std::endl;
    }

    std::vector<int16_t> frame(kHopSize);
    const auto deadline = std::chrono::steady_clock::now() + kCloudReadyTimeout;
    while (!exit_requested && std::chrono::steady_clock::now() < deadline) {
        if (dialogStatusIsReady(previous_status, require_new_status)) {
            break;
        }
        if (capture == nullptr) {
            usleep(100 * 1000);
            continue;
        }
        if (pa_simple_read(
                capture,
                frame.data(),
                frame.size() * sizeof(frame[0]),
                &pulse_error) < 0) {
            std::cerr << "[WakeWord] Wake preroll capture failed: "
                      << pa_strerror(pulse_error)
                      << "; dialog will use its fallback tone" << std::endl;
            pa_simple_free(capture);
            capture = nullptr;
            capture_succeeded = false;
            samples.clear();
            continue;
        }
        const size_t remaining = kMaxWakePrerollSamples - samples.size();
        const size_t append_count = std::min(remaining, frame.size());
        samples.insert(samples.end(), frame.begin(), frame.begin() + append_count);
    }
    if (capture != nullptr) {
        pa_simple_free(capture);
    }
    return !exit_requested &&
        dialogStatusIsReady(previous_status, require_new_status);
}

bool writeWakePrerollAtomically(const std::vector<int16_t>& samples) {
    if (mkdir("/run/ai-cat", 0755) != 0 && errno != EEXIST) {
        std::cerr << "[WakeWord] Failed to create /run/ai-cat: "
                  << std::strerror(errno) << std::endl;
        return false;
    }
    const int fd = open(
        kWakePrerollTmpPath,
        O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW,
        0600);
    if (fd < 0) {
        std::cerr << "[WakeWord] Failed to open wake preroll temp file: "
                  << std::strerror(errno) << std::endl;
        return false;
    }

    const uint8_t* bytes = reinterpret_cast<const uint8_t*>(samples.data());
    const size_t byte_count = samples.size() * sizeof(samples[0]);
    size_t written = 0;
    bool write_ok = true;
    while (written < byte_count) {
        const ssize_t result = write(fd, bytes + written, byte_count - written);
        if (result < 0 && errno == EINTR) {
            continue;
        }
        if (result <= 0) {
            write_ok = false;
            break;
        }
        written += static_cast<size_t>(result);
    }
    if (write_ok && fsync(fd) != 0) {
        write_ok = false;
    }
    if (close(fd) != 0) {
        write_ok = false;
    }
    if (!write_ok || std::rename(kWakePrerollTmpPath, kWakePrerollPath) != 0) {
        const int saved_errno = errno;
        unlink(kWakePrerollTmpPath);
        std::cerr << "[WakeWord] Failed to publish wake preroll: "
                  << std::strerror(saved_errno) << std::endl;
        return false;
    }
    return true;
}

bool triggerConversation(const pa_sample_spec& sample_spec) {
    const auto trigger_started = std::chrono::steady_clock::now();
    const FileVersion previous_status = readFileVersion(kDialogStatusPath);
    const DialogRuntimeState initial_state = readDialogRuntimeState();
    const bool already_running = initial_state.dialog_pid > 1;
    unlink(kWakePrerollPath);
    unlink(kWakePrerollTmpPath);
    if (!already_running && !startDialogService()) {
        std::cerr << "[WakeWord] Failed to start " << kDialogService << std::endl;
        return false;
    }

    std::cout << "[WakeWord] Immediate ready tone starting after "
              << std::chrono::duration_cast<std::chrono::milliseconds>(
                     std::chrono::steady_clock::now() - trigger_started).count()
              << " ms" << std::endl;
    const bool immediate_tone_played = playImmediateReadyTone(sample_spec);
    const auto tone_finished = std::chrono::steady_clock::now();
    if (immediate_tone_played) {
        std::cout << "[WakeWord] Immediate ready tone played after "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(
                         tone_finished - trigger_started).count()
                  << " ms" << std::endl;
    }

    std::vector<int16_t> preroll;
    bool preroll_capture_succeeded = false;
    if (!captureWakePrerollUntilReady(
            previous_status,
            !already_running,
            sample_spec,
            preroll,
            preroll_capture_succeeded)) {
        std::cerr << "[WakeWord] Cloud dialog did not become ready within "
                  << std::chrono::duration_cast<std::chrono::seconds>(
                         kCloudReadyTimeout).count()
                  << " seconds" << std::endl;
        unlink(kWakePrerollPath);
        unlink(kWakePrerollTmpPath);
        return false;
    }

    const bool handoff_ready =
        immediate_tone_played &&
        preroll_capture_succeeded;
    /*
     * A pre-warmed cloud session can already be ready before the first capture
     * read. Publish one silent frame so the dialog still knows the immediate
     * tone was played and does not emit its delayed fallback tone as well.
     */
    if (handoff_ready && preroll.empty()) {
        preroll.assign(kHopSize, 0);
    }
    const bool handoff_published =
        handoff_ready && writeWakePrerollAtomically(preroll);
    if (handoff_published) {
        std::cout << "[WakeWord] Wake preroll written: bytes="
                  << preroll.size() * sizeof(preroll[0])
                  << ", cloud_ready_ms="
                  << std::chrono::duration_cast<std::chrono::milliseconds>(
                         std::chrono::steady_clock::now() - trigger_started).count()
                  << std::endl;
    } else {
        unlink(kWakePrerollPath);
        unlink(kWakePrerollTmpPath);
        std::cerr << "[WakeWord] Preroll handoff unavailable; "
                  << "dialog will play its fallback tone" << std::endl;
    }
    const DialogRuntimeState ready_state = readDialogRuntimeState();
    if (ready_state.dialog_pid <= 1) {
        unlink(kWakePrerollPath);
        std::cerr << "[WakeWord] Dialog became unavailable before trigger"
                  << std::endl;
        return false;
    }
    if (::kill(ready_state.dialog_pid, SIGUSR1) != 0) {
        unlink(kWakePrerollPath);
        std::cerr << "[WakeWord] Failed to signal " << kDialogService << ": "
                  << std::strerror(errno) << std::endl;
        return false;
    }
    std::cout << "[WakeWord] Conversation triggered" << std::endl;
    return true;
}

bool triggerInterruption() {
    const DialogRuntimeState dialog_state = readDialogRuntimeState();
    if (!dialog_state.can_interrupt || dialog_state.dialog_pid <= 1) {
        std::cerr << "[WakeWord] Interrupt ignored because dialog is offline"
                  << std::endl;
        return false;
    }
    if (::kill(dialog_state.dialog_pid, SIGUSR2) != 0) {
        std::cerr << "[WakeWord] Failed to interrupt " << kDialogService
                  << ": " << std::strerror(errno) << std::endl;
        return false;
    }
    std::cout << "[WakeWord] Conversation interrupted by local keyword"
              << std::endl;
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    std::string wake_phrase = "小安开启对话模式";
    std::string model_dir = "/root/.cache/sensevoice";
    bool debug_transcripts = false;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--phrase" && i + 1 < argc) {
            wake_phrase = argv[++i];
        } else if (arg == "--model-dir" && i + 1 < argc) {
            model_dir = argv[++i];
        } else if (arg == "--debug-transcripts") {
            debug_transcripts = true;
        } else if (arg == "--help") {
            std::cout << "Usage: " << argv[0]
                      << " [--phrase 小安开启对话模式] [--model-dir PATH]"
                      << " [--debug-transcripts]" << std::endl;
            return 0;
        } else {
            std::cerr << "Unknown argument: " << arg << std::endl;
            return 2;
        }
    }

    std::signal(SIGINT, handleSignal);
    std::signal(SIGTERM, handleSignal);

    asr::sensevoice::SenseVoiceModel::Config model_config;
    model_config.model_path = model_dir + "/model_quant_optimized.onnx";
    model_config.cmvn_path = model_dir + "/am.mvn";
    model_config.vocab_path = model_dir + "/tokens.txt";
    model_config.decoder_path = model_dir + "/sensevoice_decoder_model.onnx";
    model_config.language = "zh";
    model_config.num_threads = 2;
    model_config.provider = "spacemit";

    asr::sensevoice::SenseVoiceModel model(model_config);
    if (!model.initialize()) {
        std::cerr << "[WakeWord] Failed to initialize SenseVoice" << std::endl;
        return 1;
    }
    model.setHotwords(
        {
            wake_phrase,
            "小安",
            "开启对话模式",
            "小安停下",
            "小安别说了",
            "小安安静",
            "小安暂停",
        },
        8.0f);
    std::cout << "[WakeWord] SenseVoice ready, phrase=\"" << wake_phrase
              << "\", interrupt=\"小安停下\"" << std::endl;

    ten_vad_handle_t vad = nullptr;
    if (ten_vad_create(&vad, kHopSize, kVadThreshold) != 0) {
        std::cerr << "[WakeWord] Failed to initialize Ten-VAD" << std::endl;
        return 1;
    }

    pa_sample_spec sample_spec;
    sample_spec.format = PA_SAMPLE_S16LE;
    sample_spec.rate = kSampleRate;
    sample_spec.channels = 1;
    int pulse_error = 0;
    const auto openCapture = [&sample_spec, &pulse_error]() {
        return openCaptureStream(sample_spec, &pulse_error);
    };
    pa_simple* capture = openCapture();
    if (capture == nullptr) {
        std::cerr << "[WakeWord] PulseAudio capture failed: "
                  << pa_strerror(pulse_error) << std::endl;
        ten_vad_destroy(&vad);
        return 1;
    }

    std::deque<int16_t> pre_speech;
    std::vector<int16_t> utterance;
    std::vector<int16_t> frame(kHopSize);
    bool recording = false;
    int speech_run = 0;
    int silence_run = 0;
    auto resume_not_before = std::chrono::steady_clock::time_point::min();
    auto interrupt_prefix_deadline =
        std::chrono::steady_clock::time_point::min();
    auto activation_prefix_deadline =
        std::chrono::steady_clock::time_point::min();
    CaptureMode capture_mode = CaptureMode::Wake;

    std::cout << "[WakeWord] Listening" << std::endl;
    while (!exit_requested) {
        const DialogRuntimeState dialog_state = readDialogRuntimeState();
        const bool local_speech_active = access(kLocalSpeechMarker, F_OK) == 0;
        CaptureMode desired_mode = CaptureMode::Wake;
        if (local_speech_active) {
            desired_mode = CaptureMode::Paused;
        } else if (dialog_state.can_interrupt) {
            desired_mode = CaptureMode::Interrupt;
        } else if (dialog_state.session_active) {
            desired_mode = CaptureMode::Paused;
        }

        if (desired_mode != capture_mode) {
            if (capture != nullptr) {
                pa_simple_free(capture);
                capture = nullptr;
            }
            recording = false;
            speech_run = 0;
            silence_run = 0;
            utterance.clear();
            pre_speech.clear();
            interrupt_prefix_deadline =
                std::chrono::steady_clock::time_point::min();
            activation_prefix_deadline =
                std::chrono::steady_clock::time_point::min();
            capture_mode = desired_mode;
            if (capture_mode == CaptureMode::Interrupt) {
                std::cout << "[WakeWord] Listening for interruption keyword"
                          << std::endl;
            } else if (capture_mode == CaptureMode::Paused) {
                std::cout
                    << (local_speech_active
                            ? "[WakeWord] Capture paused for local speech"
                            : "[WakeWord] Capture paused for active conversation")
                    << std::endl;
            } else {
                std::cout << "[WakeWord] Wake listening mode" << std::endl;
            }
        }

        if (capture_mode == CaptureMode::Paused) {
            usleep(50 * 1000);
            continue;
        }

        if (capture == nullptr) {
            if (std::chrono::steady_clock::now() < resume_not_before) {
                usleep(50 * 1000);
                continue;
            }
            capture = openCapture();
            if (capture == nullptr) {
                std::cerr << "[WakeWord] PulseAudio resume failed: "
                          << pa_strerror(pulse_error) << std::endl;
                break;
            }
            std::cout << "[WakeWord] Listening resumed" << std::endl;
        }

        if (pa_simple_read(
                capture,
                frame.data(),
                frame.size() * sizeof(frame[0]),
                &pulse_error) < 0) {
            std::cerr << "[WakeWord] PulseAudio read failed: "
                      << pa_strerror(pulse_error) << std::endl;
            break;
        }

        if (std::chrono::steady_clock::now() < resume_not_before) {
            continue;
        }

        float probability = 0.0f;
        int speech = 0;
        if (ten_vad_process(vad, frame.data(), frame.size(), &probability, &speech) != 0) {
            std::cerr << "[WakeWord] Ten-VAD processing failed" << std::endl;
            break;
        }

        if (!recording) {
            pre_speech.insert(pre_speech.end(), frame.begin(), frame.end());
            const size_t pre_speech_limit =
                capture_mode == CaptureMode::Interrupt
                    ? kInterruptPreSpeechSamples
                    : kPreSpeechSamples;
            while (pre_speech.size() > pre_speech_limit) {
                pre_speech.pop_front();
            }

            speech_run = speech ? speech_run + 1 : 0;
            if (speech_run >= kSpeechStartFrames) {
                recording = true;
                silence_run = 0;
                utterance.assign(pre_speech.begin(), pre_speech.end());
                pre_speech.clear();
                std::cout << "[WakeWord] Speech detected" << std::endl;
            }
            continue;
        }

        utterance.insert(utterance.end(), frame.begin(), frame.end());
        silence_run = speech ? 0 : silence_run + 1;
        const int silence_frames = capture_mode == CaptureMode::Interrupt
            ? kInterruptSilenceFrames
            : kSilenceFrames;
        const bool utterance_finished = silence_run >= silence_frames;
        const size_t max_utterance_samples =
            capture_mode == CaptureMode::Wake
                ? kMaxWakeUtteranceSamples
                : (
                    capture_mode == CaptureMode::Interrupt
                        ? kMaxInterruptUtteranceSamples
                        : kMaxUtteranceSamples
                );
        const bool utterance_full = utterance.size() >= max_utterance_samples;
        if (!utterance_finished && !utterance_full) {
            continue;
        }

        const size_t min_utterance_samples =
            capture_mode == CaptureMode::Interrupt
                ? kMinInterruptUtteranceSamples
                : kMinUtteranceSamples;
        if (utterance.size() >= min_utterance_samples) {
            const std::vector<float> audio = normalizeAudio(utterance);
            const auto recognize_started = std::chrono::steady_clock::now();
            const std::string recognized = model.recognize(audio);
            const auto recognize_finished = std::chrono::steady_clock::now();
            const std::string normalized = normalizeText(recognized);
            const auto now = std::chrono::steady_clock::now();
            bool wake_matched = capture_mode == CaptureMode::Wake &&
                (
                    matchesWakePhrase(normalized, wake_phrase) ||
                    (
                        now <= activation_prefix_deadline &&
                        matchesActivationSuffix(normalized)
                    )
                );
            bool interrupt_matched = false;
            if (capture_mode == CaptureMode::Wake) {
                if (!wake_matched && matchesActivationPrefix(normalized)) {
                    activation_prefix_deadline =
                        now + kActivationContinuationWindow;
                    std::cout << "[WakeWord] Activation prefix accepted; "
                              << "waiting for dialog-mode suffix" << std::endl;
                } else if (
                    wake_matched || now > activation_prefix_deadline
                ) {
                    activation_prefix_deadline =
                        std::chrono::steady_clock::time_point::min();
                }
            }
            if (capture_mode == CaptureMode::Interrupt) {
                interrupt_matched = matchesInterruptPhrase(normalized) ||
                    (now <= interrupt_prefix_deadline &&
                     matchesInterruptSuffix(normalized));
                if (!interrupt_matched && matchesInterruptPrefix(normalized)) {
                    interrupt_prefix_deadline =
                        now + kInterruptContinuationWindow;
                } else if (interrupt_matched ||
                           now > interrupt_prefix_deadline) {
                    interrupt_prefix_deadline =
                        std::chrono::steady_clock::time_point::min();
                }
            }
            const bool matched = wake_matched || interrupt_matched;

            if (debug_transcripts || matched) {
                std::cout << "[WakeWord] ASR: " << recognized << std::endl;
            }
            if (wake_matched) {
                std::cout << "[WakeWord] Matched: " << wake_phrase << std::endl;
                pa_simple_free(capture);
                capture = nullptr;
                if (triggerConversation(sample_spec)) {
                    resume_not_before =
                        std::chrono::steady_clock::now() + kTriggerSettleDelay;
                    std::cout << "[WakeWord] Waiting for dialog session marker"
                              << std::endl;
                } else {
                    resume_not_before =
                        std::chrono::steady_clock::now() + kTriggerSettleDelay;
                }
            } else if (interrupt_matched) {
                std::cout << "[WakeWord] Interrupt matched: 小安停下" << std::endl;
                pa_simple_free(capture);
                capture = nullptr;
                const bool interrupted = triggerInterruption();
                std::cout
                    << "[WakeWord][latency] stage=interrupt_signal "
                    << "audio_ms="
                    << (utterance.size() * 1000 / kSampleRate)
                    << " endpoint_silence_ms="
                    << (kInterruptSilenceFrames * kHopSize * 1000 /
                        kSampleRate)
                    << " asr_ms="
                    << std::chrono::duration_cast<std::chrono::milliseconds>(
                           recognize_finished - recognize_started).count()
                    << " signal_sent=" << (interrupted ? 1 : 0)
                    << std::endl;
                resume_not_before =
                    std::chrono::steady_clock::now() + kTriggerSettleDelay;
            }
        }

        recording = false;
        speech_run = 0;
        silence_run = 0;
        utterance.clear();
        pre_speech.clear();
    }

    if (capture != nullptr) {
        pa_simple_free(capture);
    }
    ten_vad_destroy(&vad);
    return 0;
}
