#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPERIMENTS_DIR="${REPO_ROOT}/experiments"
RAW_RESULTS_DIR="${EXPERIMENTS_DIR}/results/raw"
PROCESSED_RESULTS_DIR="${EXPERIMENTS_DIR}/results/processed"
TOOLCHAIN_ENV="${REPO_ROOT}/.quest_toolchain_env.sh"

info() { printf '>>> %s\n' "$*"; }
warn() { printf '>>> WARNING: %s\n' "$*" >&2; }
die()  { printf '>>> ERROR: %s\n' "$*" >&2; exit 1; }

ensure_results_dirs() {
    mkdir -p "${RAW_RESULTS_DIR}" "${PROCESSED_RESULTS_DIR}"
}

source_toolchain_env_if_present() {
    if [ -f "${TOOLCHAIN_ENV}" ]; then
        set +u
        # shellcheck disable=SC1090
        . "${TOOLCHAIN_ENV}"
        set -u
    fi
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
