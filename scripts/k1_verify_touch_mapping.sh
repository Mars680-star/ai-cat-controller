#!/usr/bin/env bash

set -Eeuo pipefail

SYSTEMCTL=${AI_CAT_SYSTEMCTL:-/usr/bin/systemctl}
SERVICE=${AI_CAT_SERVICE:-ai-cat-controller.service}
SERIAL_PATH=${AI_CAT_DEVICE_SERIAL_PATH:-/proc/device-tree/serial-number}

[[ -r ${SERIAL_PATH} ]] || {
    echo "Cannot read device serial: ${SERIAL_PATH}" >&2
    exit 1
}
serial=$(tr -d '\0\n' <"${SERIAL_PATH}")
[[ -n ${serial} ]] || {
    echo "Device serial is empty." >&2
    exit 1
}
${SYSTEMCTL} is-active --quiet "${SERVICE}" || {
    echo "Service is not active: ${SERVICE}" >&2
    exit 1
}

expected_hardware_sensor() {
    local physical_sensor=$1
    if [[ ${serial} != 7c2b63fd4a138 ]]; then
        printf '%s' "${physical_sensor}"
        return
    fi
    case "${physical_sensor}" in
        head) printf '%s' nose ;;
        nose) printf '%s' head ;;
        back) printf '%s' left_foot ;;
        left_foot) printf '%s' back ;;
        right_foot) printf '%s' right_foot ;;
    esac
}

verify_one() {
    local sensor=$1
    local instruction=$2
    local expected_phrase=$3
    local started line actual_sensor actual_hardware accepted

    echo
    echo "Expected physical sensor: ${sensor}"
    echo "Expected phrase: ${expected_phrase}"
    started=$(date '+%Y-%m-%d %H:%M:%S')
    read -r -p "${instruction}, then press Enter: "
    sleep 1
    line=$(journalctl -u "${SERVICE}" --since "${started}" --no-pager \
        | grep 'K1 touch imported:' \
        | tail -n 1 || true)
    [[ -n ${line} ]] || {
        echo "No imported touch event was found." >&2
        return 1
    }
    actual_hardware=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^hardware_sensor=/) {sub(/^hardware_sensor=/, "", $i); print $i}}' <<<"${line}")
    actual_sensor=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^sensor=/) {sub(/^sensor=/, "", $i); print $i}}' <<<"${line}")
    echo "Observed: hardware_sensor=${actual_hardware} sensor=${actual_sensor}"
    [[ ${actual_sensor} == "${sensor}" ]] || {
        echo "Logical sensor mismatch: expected ${sensor}." >&2
        return 1
    }
    [[ ${actual_hardware} == "$(expected_hardware_sensor "${sensor}")" ]] || {
        echo "Hardware sensor mismatch for device ${serial}." >&2
        return 1
    }
    read -r -p "Did the movement and phrase match this physical part? [y/N]: " accepted
    [[ ${accepted,,} == y || ${accepted,,} == yes ]] || {
        echo "Physical feedback was rejected." >&2
        return 1
    }
    sleep 4
}

echo "K1 touch acceptance for serial ${serial}"
verify_one head "Touch the HEAD once" "摸摸头，好舒服呀。"
verify_one nose "Touch the NOSE once" "呀，鼻子有点痒。"
verify_one back "Touch the BACK once" "轻轻摸背，我很喜欢。"
verify_one left_foot "Touch the LEFT PAW twice within 3 seconds" "你碰到我的左爪啦。"
verify_one right_foot "Touch the RIGHT PAW twice within 3 seconds" "你碰到我的右爪啦。"

echo
echo "PASS: all five physical touch locations matched their logical events and feedback."
