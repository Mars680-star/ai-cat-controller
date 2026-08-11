#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

SYSTEMCTL=${AI_CAT_SYSTEMCTL:-/usr/bin/systemctl}
BACKUP_ROOT=${AI_CAT_BACKUP_ROOT:-/root/ai-cat-backups}

for command in realpath stat tar sha256sum; do
    command -v "${command}" >/dev/null 2>&1 || {
        echo "Required command is unavailable: ${command}" >&2
        exit 1
    }
done
[[ -x ${SYSTEMCTL} ]] || {
    echo "systemctl is unavailable: ${SYSTEMCTL}" >&2
    exit 1
}

usage() {
    echo "Usage: $0 BACKUP_DIRECTORY [--check|--yes]" >&2
}

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this rollback as root." >&2
    exit 1
fi
[[ $# -ge 1 && $# -le 2 ]] || {
    usage
    exit 2
}

BACKUP_DIR=${1%/}
AUTO_CONFIRM=${2:-}
[[ -d ${BACKUP_DIR} && -f ${BACKUP_DIR}/.complete ]] || {
    echo "Backup is missing or incomplete: ${BACKUP_DIR}" >&2
    exit 1
}
[[ -f ${BACKUP_DIR}/metadata.env && -f ${BACKUP_DIR}/SHA256SUMS ]] || {
    echo "Backup metadata is incomplete." >&2
    exit 1
}
BACKUP_ROOT=$(realpath -m "${BACKUP_ROOT}")
BACKUP_DIR=$(realpath -e "${BACKUP_DIR}")
[[ ${BACKUP_DIR} == "${BACKUP_ROOT}/"* ]] || {
    echo "Backup must be below ${BACKUP_ROOT}." >&2
    exit 1
}
[[ ! -L ${BACKUP_DIR}/metadata.env && $(stat -c %u "${BACKUP_DIR}") == 0 ]] || {
    echo "Backup ownership or metadata type is unsafe." >&2
    exit 1
}

(
    cd "${BACKUP_DIR}"
    sha256sum -c SHA256SUMS
)

# metadata.env is created by the root-only backup command with shell-escaped values.
# shellcheck disable=SC1090
source "${BACKUP_DIR}/metadata.env"
[[ ${BACKUP_FORMAT:-} == 1 ]] || {
    echo "Unsupported backup format: ${BACKUP_FORMAT:-missing}" >&2
    exit 1
}
[[ -f ${BACKUP_DIR}/project.tar.gz && -f ${BACKUP_DIR}/product-data.db ]] || {
    echo "Backup payload is incomplete." >&2
    exit 1
}
for required_path in "${PROJECT_DIR}" "${DATA_PATH}" "${ENV_FILE}" "${RUNTIME_DIR}"; do
    [[ ${required_path} == /* && ${required_path} != / ]] || {
        echo "Unsafe restore path in metadata: ${required_path}" >&2
        exit 1
    }
done
tar -tzf "${BACKUP_DIR}/project.tar.gz" >/dev/null
if [[ -f ${BACKUP_DIR}/runtime-data.tar.gz ]]; then
    tar -tzf "${BACKUP_DIR}/runtime-data.tar.gz" >/dev/null
fi

if [[ ${AUTO_CONFIRM} == --check ]]; then
    echo "Backup verification passed: ${BACKUP_DIR}"
    exit 0
fi

if [[ ${AUTO_CONFIRM} != --yes ]]; then
    printf 'Restore %s to %s? Type ROLLBACK: ' "${BACKUP_DIR}" "${PROJECT_DIR}"
    read -r confirmation
    [[ ${confirmation} == ROLLBACK ]] || {
        echo "Rollback cancelled."
        exit 2
    }
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
EMERGENCY_DIR=${BACKUP_ROOT}/before-rollback-${timestamp}-$$
mkdir -p "${EMERGENCY_DIR}/project-current"
chmod 700 "${EMERGENCY_DIR}"

services=(
    ai-cat-controller.service
    toy_motor.service
    volc-conv-ai.service
)
for unit in "${services[@]}"; do
    ${SYSTEMCTL} stop "${unit}" 2>/dev/null || true
done

if [[ -d ${PROJECT_DIR} ]]; then
    tar \
        --exclude=.git \
        --exclude=.venv \
        --exclude=.data \
        -C "${PROJECT_DIR}" \
        -czf "${EMERGENCY_DIR}/project-current.tar.gz" .
else
    mkdir -p "${PROJECT_DIR}"
fi

if [[ -f ${DATA_PATH} ]]; then
    mkdir -p "${EMERGENCY_DIR}/database"
    cp -a -- "${DATA_PATH}" "${EMERGENCY_DIR}/database/product-data.db"
fi
if [[ -f ${ENV_FILE} ]]; then
    cp -a -- "${ENV_FILE}" "${EMERGENCY_DIR}/ai-cat-controller.env"
fi
if [[ -d ${RUNTIME_DIR} ]]; then
    tar -C "${RUNTIME_DIR}" -czf "${EMERGENCY_DIR}/runtime-data.tar.gz" .
fi

shopt -s dotglob nullglob
for entry in "${PROJECT_DIR}"/*; do
    name=$(basename "${entry}")
    case "${name}" in
        .git|.venv|.data)
            continue
            ;;
    esac
    mv -- "${entry}" "${EMERGENCY_DIR}/project-current/"
done
shopt -u dotglob nullglob
tar -C "${PROJECT_DIR}" -xzf "${BACKUP_DIR}/project.tar.gz"

mkdir -p "$(dirname "${DATA_PATH}")"
if [[ -f ${DATA_PATH} ]]; then
    mv -- "${DATA_PATH}" "${EMERGENCY_DIR}/database/replaced-product-data.db"
fi
rm -f -- "${DATA_PATH}-wal" "${DATA_PATH}-shm"
cp -a -- "${BACKUP_DIR}/product-data.db" "${DATA_PATH}"
chmod 600 "${DATA_PATH}"

if [[ -f ${BACKUP_DIR}/ai-cat-controller.env ]]; then
    mkdir -p "$(dirname "${ENV_FILE}")"
    cp -a -- "${BACKUP_DIR}/ai-cat-controller.env" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
fi

if [[ -f ${BACKUP_DIR}/runtime-data.tar.gz ]]; then
    mkdir -p "${RUNTIME_DIR}"
    find "${RUNTIME_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    tar -C "${RUNTIME_DIR}" -xzf "${BACKUP_DIR}/runtime-data.tar.gz"
fi

if [[ -f ${BACKUP_DIR}/ai-cat-controller.service && -n ${UNIT_FRAGMENT:-} ]]; then
    mkdir -p "$(dirname "${UNIT_FRAGMENT}")"
    cp -a -- "${BACKUP_DIR}/ai-cat-controller.service" "${UNIT_FRAGMENT}"
fi
${SYSTEMCTL} daemon-reload

while IFS=$'\t' read -r unit active enabled; do
    case "${enabled}" in
        enabled)
            ${SYSTEMCTL} enable "${unit}" >/dev/null 2>&1 || true
            ;;
        disabled)
            ${SYSTEMCTL} disable "${unit}" >/dev/null 2>&1 || true
            ;;
    esac
    if [[ ${active} == active ]]; then
        ${SYSTEMCTL} start "${unit}"
    else
        ${SYSTEMCTL} stop "${unit}" 2>/dev/null || true
    fi
done <"${BACKUP_DIR}/services.tsv"

if grep -q $'^ai-cat-controller.service\tactive\t' "${BACKUP_DIR}/services.tsv"; then
    ${SYSTEMCTL} is-active --quiet ai-cat-controller.service
fi

{
    printf 'ROLLED_BACK_FROM=%s\n' "${BACKUP_DIR}"
    printf 'ROLLBACK_COMPLETED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'DISPLACED_STATE=%s\n' "${EMERGENCY_DIR}"
} >"${EMERGENCY_DIR}/rollback-result.txt"

echo "Rollback complete. Displaced state: ${EMERGENCY_DIR}"
