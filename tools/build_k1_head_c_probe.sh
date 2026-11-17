#!/usr/bin/env bash
# Build only the isolated diagnostic. Never install or run a motor command.
set -euo pipefail

probe_tools_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
probe_vendor_dir=/root/ai-cat-vendor/components
probe_output_dir=/root/ai-cat-install-20261013/head-c-probe

if [[ "$(uname -m)" != riscv64 ]]; then
    printf 'Refusing: build on the intended RISC-V device only.\n' >&2
    exit 1
fi
mkdir -p -- "$probe_output_dir"
cc -std=c11 -Wall -Wextra -Werror -O0 \
    -I "$probe_vendor_dir/include" \
    "$probe_tools_dir/k1_head_c_probe.c" \
    -Wl,--whole-archive "$probe_vendor_dir/lib/libmotor.a" -Wl,--no-whole-archive \
    -L "$probe_vendor_dir/lib" -Wl,-rpath,"$probe_vendor_dir/lib" \
    -lmisc_io -lgpiod -lpthread -lrt -ldl -lm \
    -Wl,--wrap=gpiod_chip_open_by_name \
    -Wl,--wrap=gpiod_line_request_output -Wl,--wrap=gpiod_line_request_input \
    -Wl,--wrap=gpiod_line_set_value -Wl,--wrap=gpiod_line_release \
    -Wl,--wrap=gpiod_chip_close -Wl,--wrap=usleep \
    -Wl,--wrap=fopen -Wl,--wrap=fclose \
    -o "$probe_output_dir/k1_head_c_probe"
printf 'Diagnostic built only; production binary and motion gates unchanged.\n'
