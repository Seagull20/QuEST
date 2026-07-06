#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_SCRIPT="${SCRIPT_DIR}/../build.sh"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

# shellcheck source=/dev/null
source "${BUILD_SCRIPT}" >/dev/null

has_cmd() {
    case "$1" in
        nvcc|ninja)
            return 0
            ;;
        *)
            command -v "$1" >/dev/null 2>&1
            ;;
    esac
}

detect_cuda_arch() { printf '75'; }

contains_arg() {
    local expected="$1"
    local arg
    for arg in "${CMAKE_ARGS[@]}"; do
        [ "${arg}" = "${expected}" ] && return 0
    done
    return 1
}

test_nvcomp_passthrough_is_enabled_only_for_gpu_mpi() {
    export QUEST_BENCH_ENABLE_NVCOMP=1
    CMAKE_ARGS=()
    build_common_args "/tmp/build" Release "/tmp/main.c" bench
    configure_backend_args gpu_mpi
    configure_optional_nvcomp_args gpu_mpi
    contains_arg "-DENABLE_NVCOMP=ON" || fail "gpu_mpi nvCOMP build flag missing"

    CMAKE_ARGS=()
    build_common_args "/tmp/build" Release "/tmp/main.c" bench
    configure_backend_args gpu
    if (configure_optional_nvcomp_args gpu) >/dev/null 2>&1; then
        fail "non-distributed gpu backend accepted nvCOMP passthrough"
    fi
}

test_compression_check_cpp_source_resolves() {
    resolve_source compression_check
    case "${USER_SOURCE}" in
        */experiments/benchmarks/compression_check/main.cpp)
            ;;
        *)
            fail "compression_check resolved to unexpected source: ${USER_SOURCE}"
            ;;
    esac
}

test_nvcomp_passthrough_is_enabled_only_for_gpu_mpi
test_compression_check_cpp_source_resolves
printf 'Build nvCOMP tests passed.\n'
