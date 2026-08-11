#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

SYSTEMCTL=${AI_CAT_SYSTEMCTL:-/usr/bin/systemctl}
SERVICE=${AI_CAT_SERVICE:-ai-cat-controller.service}
ENV_FILE=${AI_CAT_ENV_FILE:-/etc/ai-cat-controller.env}
BACKUP_ROOT=${AI_CAT_BACKUP_ROOT:-/root/ai-cat-backups}
RUNTIME_DIR=${AI_CAT_RUNTIME_DIR:-/var/lib/ai-cat-controller}

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this backup as root." >&2
    exit 1
fi

for command in tar sha256sum; do
    command -v "${command}" >/dev/null 2>&1 || {
        echo "Required command is unavailable: ${command}" >&2
        exit 1
    }
done
[[ -x ${SYSTEMCTL} ]] || {
    echo "systemctl is unavailable: ${SYSTEMCTL}" >&2
    exit 1
}

read_env_value() {
    local key=$1
    local value
    [[ -f ${ENV_FILE} ]] || return 0
    value=$(awk -F= -v key="${key}" '$1 == key {sub(/^[^=]*=/, ""); print}' "${ENV_FILE}" | tail -n 1)
    value=${value#\"}
    value=${value%\"}
    printf '%s' "${value}"
}

PROJECT_DIR=${AI_CAT_PROJECT_DIR:-$(${SYSTEMCTL} show "${SERVICE}" -p WorkingDirectory --value 2>/dev/null || true)}
PROJECT_DIR=${PROJECT_DIR:-/opt/ai-cat-controller}
DATA_PATH=${AI_CAT_DATA_PATH:-$(read_env_value AI_CAT_DATA_PATH)}
DATA_PATH=${DATA_PATH:-.data/ai-cat-mock.db}
if [[ ${DATA_PATH} != /* ]]; then
    DATA_PATH=${PROJECT_DIR}/${DATA_PATH}
fi

[[ -d ${PROJECT_DIR} ]] || {
    echo "Project directory does not exist: ${PROJECT_DIR}" >&2
    exit 1
}
[[ -f ${DATA_PATH} ]] || {
    echo "Product database does not exist: ${DATA_PATH}" >&2
    exit 1
}

mkdir -p "${BACKUP_ROOT}"
chmod 700 "${BACKUP_ROOT}"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR=${BACKUP_ROOT}/before-growth-v1-${timestamp}-$$
mkdir "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

services=(
    ai-cat-controller.service
    toy_motor.service
    volc-pulseaudio.service
    volc-k1-wake-word.service
    volc-conv-ai.service
)
declare -A service_was_active=()
: >"${BACKUP_DIR}/services.tsv"
for unit in "${services[@]}"; do
    active=$(${SYSTEMCTL} is-active "${unit}" 2>/dev/null || true)
    enabled=$(${SYSTEMCTL} is-enabled "${unit}" 2>/dev/null || true)
    service_was_active["${unit}"]=${active:-unknown}
    printf '%s\t%s\t%s\n' "${unit}" "${active:-unknown}" "${enabled:-unknown}" \
        >>"${BACKUP_DIR}/services.tsv"
done

controller_was_active=$(${SYSTEMCTL} is-active "${SERVICE}" 2>/dev/null || true)
controller_stopped=false
backup_complete=false

restore_active_services() {
    local unit current restore_status=0

    for unit in "${services[@]}"; do
        [[ ${service_was_active["${unit}"]:-unknown} == active ]] || continue
        current=$(${SYSTEMCTL} is-active "${unit}" 2>/dev/null || true)
        if [[ ${current} != active ]] && ! ${SYSTEMCTL} start "${unit}"; then
            echo "WARNING: failed to restore active service ${unit}" >&2
            restore_status=1
        fi
    done
    return "${restore_status}"
}

cleanup() {
    local status=$?
    if [[ ${controller_stopped} == true ]]; then
        if ! restore_active_services; then
            status=1
        fi
    fi
    if [[ ${backup_complete} != true ]]; then
        echo "Backup is incomplete and must not be used: ${BACKUP_DIR}" >&2
    fi
    return "${status}"
}
trap cleanup EXIT

if [[ ${controller_was_active} == active ]]; then
    ${SYSTEMCTL} stop "${SERVICE}"
    controller_stopped=true
fi

sync
cp -a -- "${DATA_PATH}" "${BACKUP_DIR}/product-data.db"
chmod 600 "${BACKUP_DIR}/product-data.db"

tar \
    --exclude=.git \
    --exclude=.venv \
    --exclude=.data \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C "${PROJECT_DIR}" \
    -czf "${BACKUP_DIR}/project.tar.gz" .

if [[ -f ${ENV_FILE} ]]; then
    cp -a -- "${ENV_FILE}" "${BACKUP_DIR}/ai-cat-controller.env"
    chmod 600 "${BACKUP_DIR}/ai-cat-controller.env"
fi

fragment=$(${SYSTEMCTL} show "${SERVICE}" -p FragmentPath --value 2>/dev/null || true)
if [[ -n ${fragment} && -f ${fragment} ]]; then
    cp -a -- "${fragment}" "${BACKUP_DIR}/ai-cat-controller.service"
fi

if [[ -d ${RUNTIME_DIR} ]]; then
    tar -C "${RUNTIME_DIR}" -czf "${BACKUP_DIR}/runtime-data.tar.gz" .
fi

if command -v git >/dev/null 2>&1 \
    && git -C "${PROJECT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "${PROJECT_DIR}" rev-parse HEAD >"${BACKUP_DIR}/git-head.txt"
    git -C "${PROJECT_DIR}" branch --show-current >"${BACKUP_DIR}/git-branch.txt"
    git -C "${PROJECT_DIR}" status --short >"${BACKUP_DIR}/git-status.txt"
    git -C "${PROJECT_DIR}" diff --binary >"${BACKUP_DIR}/working-tree.patch"
else
    printf '%s\n' unavailable >"${BACKUP_DIR}/git-head.txt"
fi

{
    printf 'BACKUP_FORMAT=%q\n' 1
    printf 'PROJECT_DIR=%q\n' "${PROJECT_DIR}"
    printf 'DATA_PATH=%q\n' "${DATA_PATH}"
    printf 'ENV_FILE=%q\n' "${ENV_FILE}"
    printf 'RUNTIME_DIR=%q\n' "${RUNTIME_DIR}"
    printf 'SERVICE=%q\n' "${SERVICE}"
    printf 'UNIT_FRAGMENT=%q\n' "${fragment}"
    printf 'CREATED_AT=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"${BACKUP_DIR}/metadata.env"

(
    cd "${BACKUP_DIR}"
    find . -type f ! -name SHA256SUMS -print0 \
        | sort -z \
        | xargs -0 sha256sum >SHA256SUMS
)

if [[ ${controller_stopped} == true ]]; then
    restore_active_services
    ${SYSTEMCTL} is-active --quiet "${SERVICE}"
    controller_stopped=false
fi

touch "${BACKUP_DIR}/.complete"
backup_complete=true
printf '%s\n' "${BACKUP_DIR}"
