cmake_minimum_required(VERSION 3.16)
project(VolcConvAIK1Demo LANGUAGES C)

set(ENABLE_RTC_MODE OFF CACHE BOOL "Disable x64-only RTC transport" FORCE)
set(ENABLE_WS_MODE ON CACHE BOOL "Enable WebSocket transport" FORCE)
set(ENABLE_MBEDTLS ON CACHE BOOL "Enable TLS" FORCE)
set(USE_SYSTEM_CRYPTO ON CACHE BOOL "Use K1 system libraries" FORCE)
set(ENABLE_SAMPLE OFF CACHE BOOL "Disable the generic RTC sample" FORCE)
add_compile_definitions(PLATFORM_LINUX)

include(${CMAKE_CURRENT_SOURCE_DIR}/../../../volc_conv_ai/cmake/common.cmake)
include(${CMAKE_CURRENT_SOURCE_DIR}/../../../volc_conv_ai/cmake/linux.cmake)

find_package(PkgConfig REQUIRED)
pkg_check_modules(PULSE REQUIRED IMPORTED_TARGET libpulse)
pkg_check_modules(PULSE_SIMPLE REQUIRED IMPORTED_TARGET libpulse-simple)
pkg_check_modules(CURL REQUIRED IMPORTED_TARGET libcurl)
find_package(Threads REQUIRED)

add_executable(volc_conv_ai_k1_demo
    ../macos/volc_conv_ai_demo.c
    ../macos/util/volc_ringbuf.c
)

target_include_directories(volc_conv_ai_k1_demo PRIVATE
    ../macos
)

target_link_libraries(volc_conv_ai_k1_demo PRIVATE
    volc_conv_ai_a
    PkgConfig::PULSE
    PkgConfig::PULSE_SIMPLE
    PkgConfig::CURL
    Threads::Threads
)

if(NOT EXISTS "${CMAKE_BINARY_DIR}/conv_ai_config.json")
    configure_file(
        ${CMAKE_CURRENT_SOURCE_DIR}/configs/conv_ai_config.example.json
        ${CMAKE_BINARY_DIR}/conv_ai_config.json
        COPYONLY
    )
endif()
