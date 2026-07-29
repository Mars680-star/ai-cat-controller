#include <algorithm>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <string>
#include <vector>

#include <unistd.h>

#include <pulse/error.h>
#include <pulse/simple.h>

#include "backends/sensevoice/sensevoice_model.hpp"
#include "ten_vad.h"

namespace {

constexpr int kSampleRate = 16000;
constexpr int kHopSize = 256;
constexpr int kSpeechStartFrames = 3;
constexpr int kSilenceFrames = 18;
constexpr size_t kPreSpeechSamples = 8000;
constexpr size_t kMinUtteranceSamples = 8000;
constexpr size_t kMaxUtteranceSamples = 80000;
constexpr float kVadThreshold = 0.5f;
constexpr auto kWakeCooldown = std::chrono::seconds(20);

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

bool triggerConversation() {
    const int status = std::system("/bin/systemctl kill -s SIGUSR1 volc-conv-ai.service");
    if (status != 0) {
        std::cerr << "[WakeWord] Failed to signal volc-conv-ai.service" << std::endl;
        return false;
    }
    std::cout << "[WakeWord] Conversation triggered" << std::endl;
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    std::string wake_phrase = "小安小安";
    std::string model_dir = "/root/.cache/sensevoice";

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--phrase" && i + 1 < argc) {
            wake_phrase = argv[++i];
        } else if (arg == "--model-dir" && i + 1 < argc) {
            model_dir = argv[++i];
        } else if (arg == "--help") {
            std::cout << "Usage: " << argv[0]
                      << " [--phrase 小安小安] [--model-dir PATH]" << std::endl;
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
    auto cooldown_until = std::chrono::steady_clock::time_point::min();

    std::cout << "[WakeWord] Listening" << std::endl;
    while (!exit_requested) {
        if (capture == nullptr) {
            if (std::chrono::steady_clock::now() < cooldown_until) {
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

        if (std::chrono::steady_clock::now() < cooldown_until) {
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
            std::cout << "[WakeWord] ASR: " << recognized << std::endl;

            if (matchesWakePhrase(normalized, wake_phrase)) {
                std::cout << "[WakeWord] Matched: " << wake_phrase << std::endl;
                if (triggerConversation()) {
                    cooldown_until = std::chrono::steady_clock::now() + kWakeCooldown;
                    pa_simple_free(capture);
                    capture = nullptr;
                    std::cout << "[WakeWord] Capture paused for conversation" << std::endl;
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
