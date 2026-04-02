#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPERIMENTS_DIR="${REPO_ROOT}/experiments"
RAW_RESULTS_DIR="${EXPERIMENTS_DIR}/results/raw"
PROCESSED_RESULTS_DIR="${EXPERIMENTS_DIR}/results/processed"
find_toolchain_env() {
    local candidate

    for candidate in \
        "${REPO_ROOT}/.quest_toolchain_env.sh" \
        "${REPO_ROOT}/../.quest_toolchain_env.sh" \
        "${REPO_ROOT}/../../.quest_toolchain_env.sh"; do
        if [ -f "${candidate}" ]; then
            printf '%s' "${candidate}"
            return 0
        fi
    done

    return 1
}

info() { printf '>>> %s\n' "$*"; }
warn() { printf '>>> WARNING: %s\n' "$*" >&2; }
die()  { printf '>>> ERROR: %s\n' "$*" >&2; exit 1; }

version_ge() {
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]
}

ensure_results_dirs() {
    mkdir -p "${RAW_RESULTS_DIR}" "${PROCESSED_RESULTS_DIR}"
}

source_toolchain_env_if_present() {
    local toolchain_env=""

    if toolchain_env="$(find_toolchain_env)"; then
        set +u
        # shellcheck disable=SC1090
        . "${toolchain_env}"
        set -u
    fi
}

ensure_minimum_cmake() {
    local required_version="$1"
    local current_version=""

    if command -v cmake >/dev/null 2>&1; then
        current_version="$(cmake --version 2>/dev/null | awk 'NR==1 {print $3}')"
    fi

    if [ -n "${current_version}" ] && version_ge "${current_version}" "${required_version}"; then
        return 0
    fi

    if ! type module >/dev/null 2>&1; then
        if [ -f /etc/profile ]; then
            # Ensure the module function is available inside non-login batch shells.
            # shellcheck disable=SC1091
            . /etc/profile
        fi
    fi

    type module >/dev/null 2>&1 || die "module command unavailable; cannot load newer cmake."

    info "Loading cmake/3.29.4 to satisfy minimum CMake ${required_version}"
    module load cmake/3.29.4 >/dev/null 2>&1 || die "Failed to load cmake/3.29.4"

    current_version="$(cmake --version 2>/dev/null | awk 'NR==1 {print $3}')"
    [ -n "${current_version}" ] || die "cmake not found after loading cmake/3.29.4"
    version_ge "${current_version}" "${required_version}" || die "cmake ${current_version} is still below required ${required_version}"
}

build_suite_targets() {
    local backend="$1"
    local benchmark

    case "${backend}" in
        gpu_mpi)
            die "TODO: gpu_mpi build path reserved for future implementation"
            ;;
    esac

    for benchmark in probe qft h_sweep random; do
        info "Building ${benchmark} (${backend})"
        "${EXPERIMENTS_DIR}/build.sh" "${benchmark}" "${backend}"
    done
}
