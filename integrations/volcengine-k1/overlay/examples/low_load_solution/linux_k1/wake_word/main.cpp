#include <algorithm>
#include <chrono>
#include <cmath>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
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
constexpr size_t kPreSpeechSamples = 8000;
constexpr size_t kMinUtteranceSamples = 8000;
constexpr size_t kMaxUtteranceSamples = 80000;
constexpr float kVadThreshold = 0.5f;
constexpr auto kTriggerSettleDelay = std::chrono::seconds(1);
constexpr const char* kDialogSessionMarker = "/run/ai-cat/dialog-session-active";
constexpr const char* kDialogStatusPath = "/run/ai-cat/dialog-status.json";
constexpr const char* kDialogService = "volc-conv-ai.service";
constexpr size_t kMaxDialogStatusBytes = 16 * 1024;
constexpr auto kCloudReadyTimeout = std::chrono::seconds(30);

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
        "晓安晓安",
        "下安下安",
        "小岸小岸",
        "小安小岸",
        "谢啊谢",
    };
    for (const char* alias : aliases) {
        if (text.find(alias) != std::string::npos) {
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

int runSystemctl(std::vector<std::string> arguments) {
    arguments.insert(arguments.begin(), "systemctl");
    std::vector<char*> argv;
    argv.reserve(arguments.size() + 1);
    for (std::string& argument : arguments) {
        argv.push_back(argument.data());
    }
    argv.push_back(nullptr);

    pid_t pid = -1;
    const int spawn_status = posix_spawn(
        &pid,
        "/bin/systemctl",
        nullptr,
        nullptr,
        argv.data(),
        environ);
    if (spawn_status != 0) {
        std::cerr << "[WakeWord] Failed to start systemctl: "
                  << spawn_status << std::endl;
        return -1;
    }
    int child_status = 0;
    pid_t wait_result;
    do {
        wait_result = waitpid(pid, &child_status, 0);
    } while (wait_result < 0 && errno == EINTR);
    if (wait_result < 0 || !WIFEXITED(child_status)) {
        return -1;
    }
    return WEXITSTATUS(child_status);
}

bool dialogStatusIsReady(
    const FileVersion& previous_version,
    bool require_new_status
) {
    const FileVersion current_version = readFileVersion(kDialogStatusPath);
    if (!current_version.exists || current_version.size <= 0 ||
        current_version.size > static_cast<off_t>(kMaxDialogStatusBytes) ||
        (require_new_status && !fileVersionChanged(previous_version, current_version))) {
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

    const std::string status(buffer.data(), bytes_read);
    const bool ready = status.find("\"state\":\"ready\"") != std::string::npos ||
        status.find("\"state\":\"interrupted\"") != std::string::npos;
    const size_t pid_key = status.find("\"pid\":");
    if (!ready || pid_key == std::string::npos) {
        return false;
    }
    const char* pid_start = status.c_str() + pid_key + 6;
    char* pid_end = nullptr;
    errno = 0;
    const long dialog_pid = std::strtol(pid_start, &pid_end, 10);
    return errno == 0 && pid_end != pid_start && dialog_pid > 1 &&
        (::kill(static_cast<pid_t>(dialog_pid), 0) == 0 || errno == EPERM);
}

bool triggerConversation() {
    const bool already_active =
        runSystemctl({"is-active", "--quiet", kDialogService}) == 0;
    const FileVersion previous_status = readFileVersion(kDialogStatusPath);
    if (!already_active &&
        runSystemctl({"--no-block", "start", kDialogService}) != 0) {
        std::cerr << "[WakeWord] Failed to start " << kDialogService << std::endl;
        return false;
    }

    const auto deadline = std::chrono::steady_clock::now() + kCloudReadyTimeout;
    while (!exit_requested && std::chrono::steady_clock::now() < deadline) {
        if (dialogStatusIsReady(previous_status, !already_active)) {
            break;
        }
        usleep(100 * 1000);
    }
    if (exit_requested ||
        !dialogStatusIsReady(previous_status, !already_active)) {
        std::cerr << "[WakeWord] Cloud dialog did not become ready within "
                  << std::chrono::duration_cast<std::chrono::seconds>(
                         kCloudReadyTimeout).count()
                  << " seconds" << std::endl;
        return false;
    }

    if (runSystemctl({"kill", "--signal=SIGUSR1", kDialogService}) != 0) {
        std::cerr << "[WakeWord] Failed to signal " << kDialogService << std::endl;
        return false;
    }
    std::cout << "[WakeWord] Conversation triggered" << std::endl;
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    std::string wake_phrase = "小安小安";
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
                      << " [--phrase 小安小安] [--model-dir PATH]"
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
    model.setHotwords({wake_phrase, "小安"}, 5.0f);
    std::cout << "[WakeWord] SenseVoice ready, phrase=\"" << wake_phrase << "\"" << std::endl;

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
        return pa_simple_new(
            nullptr,
            "volc-k1-wake-word",
            PA_STREAM_RECORD,
            nullptr,
            "wake-word-capture",
            &sample_spec,
            nullptr,
            nullptr,
            &pulse_error);
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

    std::cout << "[WakeWord] Listening" << std::endl;
    while (!exit_requested) {
        if (access(kDialogSessionMarker, F_OK) == 0) {
            if (capture != nullptr) {
                pa_simple_free(capture);
                capture = nullptr;
                recording = false;
                speech_run = 0;
                silence_run = 0;
                utterance.clear();
                pre_speech.clear();
                std::cout << "[WakeWord] Capture paused for active conversation"
                          << std::endl;
            }
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
            while (pre_speech.size() > kPreSpeechSamples) {
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
        const bool utterance_finished = silence_run >= kSilenceFrames;
        const bool utterance_full = utterance.size() >= kMaxUtteranceSamples;
        if (!utterance_finished && !utterance_full) {
            continue;
        }

        if (utterance.size() >= kMinUtteranceSamples) {
            const std::vector<float> audio = normalizeAudio(utterance);
            const std::string recognized = model.recognize(audio);
            const std::string normalized = normalizeText(recognized);
            const bool matched = matchesWakePhrase(normalized, wake_phrase);

            if (debug_transcripts || matched) {
                std::cout << "[WakeWord] ASR: " << recognized << std::endl;
            }
            if (matched) {
                std::cout << "[WakeWord] Matched: " << wake_phrase << std::endl;
                pa_simple_free(capture);
                capture = nullptr;
                if (triggerConversation()) {
                    resume_not_before =
                        std::chrono::steady_clock::now() + kTriggerSettleDelay;
                    std::cout << "[WakeWord] Waiting for dialog session marker"
                              << std::endl;
                } else {
                    resume_not_before =
                        std::chrono::steady_clock::now() + kTriggerSettleDelay;
                }
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
