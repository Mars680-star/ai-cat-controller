cmake_minimum_required(VERSION 3.16)
project(VolcK1WakeWord LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(PkgConfig REQUIRED)
find_package(Threads REQUIRED)
pkg_check_modules(PULSE REQUIRED IMPORTED_TARGET libpulse)
pkg_check_modules(PULSE_SIMPLE REQUIRED IMPORTED_TARGET libpulse-simple)
pkg_check_modules(FFTW3F REQUIRED IMPORTED_TARGET fftw3f)

find_path(ONNXRUNTIME_INCLUDE_DIR onnxruntime_cxx_api.h PATHS /usr/include REQUIRED)
find_library(ONNXRUNTIME_LIBRARY onnxruntime PATHS /usr/lib /lib REQUIRED)
find_library(SPACEMIT_EP_LIBRARY spacemit_ep PATHS /usr/lib /lib REQUIRED)
find_library(TEN_VAD_LIBRARY ten_vad
    PATHS /usr/local/share/third_party/ten-vad
    NO_DEFAULT_PATH
    REQUIRED
)

set(SENSEVOICE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/vendor/backends/sensevoice)

add_executable(volc_k1_wake_word
    main.cpp
    ${SENSEVOICE_DIR}/sensevoice_model.cpp
    ${SENSEVOICE_DIR}/feature_extractor.cpp
    ${SENSEVOICE_DIR}/tokenizer.cpp
    ${SENSEVOICE_DIR}/hotword_scorer.cpp
)

# The K1 image has less than 1 GiB RAM and no swap. Optimizing this header-heavy
# entry point at -O3 can exhaust memory; the inference sources remain optimized.
set_source_files_properties(main.cpp PROPERTIES COMPILE_OPTIONS "-O0;-g0")

target_compile_definitions(volc_k1_wake_word PRIVATE USE_SPACEMIT_EP)
target_include_directories(volc_k1_wake_word PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/vendor
    ${ONNXRUNTIME_INCLUDE_DIR}
    /usr/local/share/third_party/ten-vad
)
target_link_libraries(volc_k1_wake_word PRIVATE
    ${ONNXRUNTIME_LIBRARY}
    ${SPACEMIT_EP_LIBRARY}
    ${TEN_VAD_LIBRARY}
    PkgConfig::PULSE
    PkgConfig::PULSE_SIMPLE
    PkgConfig::FFTW3F
    Threads::Threads
    m
)
set_target_properties(volc_k1_wake_word PROPERTIES
    BUILD_RPATH /usr/local/share/third_party/ten-vad
    INSTALL_RPATH /usr/local/share/third_party/ten-vad
)
