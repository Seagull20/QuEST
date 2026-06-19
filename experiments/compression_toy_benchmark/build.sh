#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEST_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_ROOT="${SCRIPT_DIR}/build"

info() { printf '>>> %s\n' "$*"; }
die() { printf '>>> ERROR: %s\n' "$*" >&2; exit 1; }
has_cmd() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<'EOF'
Usage:
  experiments/compression_toy_benchmark/build.sh [all|capture|exchange|clean]

Environment:
  NVCOMP_ROOT=<path>                         nvCOMP install prefix for exchange build.
  QUEST_COMPRESSION_CUDA_ARCH=<arch>         CUDA architecture override, e.g. 75 or 86.
  QUEST_BENCH_BUILD_PARALLEL=<N>             Build parallelism override.
EOF
}

find_toolchain_env() {
    local candidate
    for candidate in \
        "${QUEST_ROOT}/.quest_toolchain_env.sh" \
        "${QUEST_ROOT}/../.quest_toolchain_env.sh" \
        "${QUEST_ROOT}/../../.quest_toolchain_env.sh"; do
        if [ -f "${candidate}" ]; then
            printf '%s' "${candidate}"
            return 0
        fi
    done
    return 1
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

detect_cuda_arch() {
    local arch
    if [ -n "${QUEST_COMPRESSION_CUDA_ARCH:-}" ]; then
        printf '%s' "${QUEST_COMPRESSION_CUDA_ARCH}"
        return 0
    fi
    has_cmd nvidia-smi || return 1
    arch="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 | tr -d '.[:space:]')"
    [ -n "${arch}" ] || return 1
    printf '%s' "${arch}"
}

parallel_jobs() {
    if [ -n "${QUEST_BENCH_BUILD_PARALLEL:-}" ]; then
        printf '%s' "${QUEST_BENCH_BUILD_PARALLEL}"
    elif [ -n "${SLURM_CPUS_ON_NODE:-}" ]; then
        printf '%s' "${SLURM_CPUS_ON_NODE}"
    elif has_cmd getconf; then
        getconf _NPROCESSORS_ONLN
    else
        printf '1'
    fi
}

build_capture() {
    local build_dir="${BUILD_ROOT}/capture"
    local arch="${QUEST_COMPRESSION_CUDA_ARCH:-}"

    has_cmd cmake || die "cmake not found."
    if ! has_cmd nvcc; then
        die "nvcc not found; load a CUDA module before building capture."
    fi
    if [ -z "${arch}" ]; then
        arch="$(detect_cuda_arch || true)"
    fi
    [ -n "${arch}" ] || arch=75

    rm -rf "${build_dir}"
    mkdir -p "${build_dir}"
    info "Configuring QuEST capture build"
    cmake -S "${QUEST_ROOT}" -B "${build_dir}" \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_EXAMPLES=OFF \
        -DENABLE_TESTING=OFF \
        -DENABLE_MULTITHREADING=ON \
        -DENABLE_DISTRIBUTION=ON \
        -DENABLE_CUDA=ON \
        "-DCMAKE_CUDA_ARCHITECTURES=${arch}" \
        "-DUSER_SOURCE=${SCRIPT_DIR}/src/capture_quest_payloads.c" \
        -DOUTPUT_EXE=capture_quest_payloads
    info "Building capture_quest_payloads"
    cmake --build "${build_dir}" --parallel "$(parallel_jobs)" --target capture_quest_payloads
}

build_exchange() {
    local build_dir="${BUILD_ROOT}/exchange"
    local arch="${QUEST_COMPRESSION_CUDA_ARCH:-}"

    has_cmd cmake || die "cmake not found."
    if ! has_cmd nvcc; then
        die "nvcc not found; load a CUDA module before building exchange."
    fi
    if [ -z "${arch}" ]; then
        arch="$(detect_cuda_arch || true)"
    fi
    [ -n "${arch}" ] || arch=75

    rm -rf "${build_dir}"
    mkdir -p "${build_dir}"
    info "Configuring compression exchange build"
    cmake -S "${SCRIPT_DIR}" -B "${build_dir}" \
        -DCMAKE_BUILD_TYPE=Release \
        "-DCMAKE_CUDA_ARCHITECTURES=${arch}"
    info "Building compression_exchange"
    cmake --build "${build_dir}" --parallel "$(parallel_jobs)" --target compression_exchange
}

main() {
    local cmd="${1:-all}"
    cd "${QUEST_ROOT}"
    source_toolchain_env_if_present

    case "${cmd}" in
        all)
            build_capture
            build_exchange
            ;;
        capture)
            build_capture
            ;;
        exchange)
            build_exchange
            ;;
        clean)
            rm -rf "${BUILD_ROOT}"
            info "Removed ${BUILD_ROOT}"
            ;;
        -h|--help)
            usage
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
}

main "$@"
