#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEST_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BENCHMARKS_DIR="${SCRIPT_DIR}/benchmarks"
BUILD_ROOT="${SCRIPT_DIR}/build"
TOOLCHAIN_ENV="${QUEST_ROOT}/.quest_toolchain_env.sh"

info()  { printf '>>> %s\n' "$*"; }
warn()  { printf '>>> WARNING: %s\n' "$*" >&2; }
die()   { printf '>>> ERROR: %s\n' "$*" >&2; exit 1; }
has_cmd() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<'EOF'
Usage:
  ./experiments/build.sh list
  ./experiments/build.sh clean
  ./experiments/build.sh <benchmark> <backend> [build_type]

Benchmarks:
  probe
  h_sweep
  qft
  random

Backends:
  cpu
  cpu_mpi
  gpu
  cuquantum
  gpu_mpi

Build type:
  Release (default), Debug, RelWithDebInfo, MinSizeRel
EOF
}

list_benchmarks() {
    find "${BENCHMARKS_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name common -exec basename {} \; | sort
}

detect_cuda_arch() {
    local arch

    has_cmd nvidia-smi || return 1
    has_cmd nvcc || return 1

    arch="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 | tr -d '.[:space:]')"
    [ -n "${arch}" ] || return 1
    printf '%s' "${arch}"
}

resolve_source() {
    local benchmark="$1"
    local source="${BENCHMARKS_DIR}/${benchmark}/main.c"

    [ -f "${source}" ] || die "Benchmark source not found: ${source}"
    USER_SOURCE="${source}"
}

configure_backend_defines() {
    local backend="$1"
    local supports_distribution supports_gpu supports_cuquantum
    local c_flags=""

    case "${backend}" in
        cpu)
            supports_distribution=0
            supports_gpu=0
            supports_cuquantum=0
            ;;
        cpu_mpi)
            supports_distribution=1
            supports_gpu=0
            supports_cuquantum=0
            ;;
        gpu)
            supports_distribution=0
            supports_gpu=1
            supports_cuquantum=0
            ;;
        cuquantum)
            supports_distribution=0
            supports_gpu=1
            supports_cuquantum=1
            ;;
        gpu_mpi)
            die "TODO: gpu_mpi build path reserved for future implementation"
            ;;
        *)
            die "Unsupported backend '${backend}'."
            ;;
    esac

    c_flags="${CFLAGS:-}"
    if [ -n "${c_flags}" ]; then
        c_flags="${c_flags} "
    fi
    c_flags="${c_flags}-DBENCH_BUILD_BACKEND_STR=\\\"${backend}\\\""
    c_flags="${c_flags} -DBENCH_BUILD_SUPPORTS_DISTRIBUTION=${supports_distribution}"
    c_flags="${c_flags} -DBENCH_BUILD_SUPPORTS_GPU=${supports_gpu}"
    c_flags="${c_flags} -DBENCH_BUILD_SUPPORTS_CUQUANTUM=${supports_cuquantum}"

    CMAKE_ARGS+=("-DCMAKE_C_FLAGS=${c_flags}")
}

build_common_args() {
    local build_dir="$1"
    local build_type="$2"
    local user_sources="$3"
    local output_exe="$4"

    CMAKE_ARGS=(
        -S "${QUEST_ROOT}"
        -B "${build_dir}"
        -DCMAKE_BUILD_TYPE="${build_type}"
        -DBUILD_EXAMPLES=OFF
        -DENABLE_TESTING=OFF
        -DENABLE_DISTRIBUTION=OFF
        "-DUSER_SOURCE=${user_sources}"
        "-DOUTPUT_EXE=${output_exe}"
    )

    if has_cmd ninja; then
        CMAKE_ARGS+=(-G Ninja)
    fi
}

configure_backend_args() {
    local backend="$1"
    local cuda_arch=""

    BACKEND_ARGS=(-DENABLE_MULTITHREADING=ON)

    case "${backend}" in
        cpu)
            BACKEND_ARGS+=(-DENABLE_CUDA=OFF)
            if [ "$(uname -s)" = "Darwin" ] && [ -d /opt/homebrew/opt/libomp ]; then
                BACKEND_ARGS+=("-DOpenMP_ROOT=/opt/homebrew/opt/libomp")
            fi
            ;;
        cpu_mpi)
            BACKEND_ARGS+=(-DENABLE_DISTRIBUTION=ON -DENABLE_CUDA=OFF)
            if [ "$(uname -s)" = "Darwin" ] && [ -d /opt/homebrew/opt/libomp ]; then
                BACKEND_ARGS+=("-DOpenMP_ROOT=/opt/homebrew/opt/libomp")
            fi
            ;;
        gpu)
            has_cmd nvcc || die "nvcc not found. Cannot build GPU backend."
            BACKEND_ARGS+=(-DENABLE_CUDA=ON)
            if cuda_arch="$(detect_cuda_arch)"; then
                info "Detected CUDA architecture: ${cuda_arch}"
            else
                cuda_arch="86"
                warn "Could not auto-detect GPU arch. Defaulting to ${cuda_arch}."
            fi
            BACKEND_ARGS+=("-DCMAKE_CUDA_ARCHITECTURES=${cuda_arch}")
            ;;
        cuquantum)
            has_cmd nvcc || die "nvcc not found. Cannot build cuQuantum backend."
            BACKEND_ARGS+=(-DENABLE_CUDA=ON -DENABLE_CUQUANTUM=ON)
            if cuda_arch="$(detect_cuda_arch)"; then
                info "Detected CUDA architecture: ${cuda_arch}"
            else
                cuda_arch="86"
                warn "Could not auto-detect GPU arch. Defaulting to ${cuda_arch}."
            fi
            BACKEND_ARGS+=("-DCMAKE_CUDA_ARCHITECTURES=${cuda_arch}")
            ;;
        gpu_mpi)
            die "TODO: gpu_mpi build path reserved for future implementation"
            ;;
        *)
            die "Unsupported backend '${backend}'."
            ;;
    esac

    if [ -n "${CC:-}" ]; then
        BACKEND_ARGS+=("-DCMAKE_C_COMPILER=${CC}")
    fi
    if [ -n "${CXX:-}" ]; then
        BACKEND_ARGS+=("-DCMAKE_CXX_COMPILER=${CXX}")
    fi
    if [ -n "${CUDACXX:-}" ]; then
        BACKEND_ARGS+=("-DCMAKE_CUDA_COMPILER=${CUDACXX}")
    fi
    if [ -n "${CUDAHOSTCXX:-}" ]; then
        BACKEND_ARGS+=("-DCMAKE_CUDA_HOST_COMPILER=${CUDAHOSTCXX}")
    fi
}

build_target() {
    local benchmark="$1"
    local backend="$2"
    local build_type="$3"
    local build_dir output_exe

    resolve_source "${benchmark}"

    output_exe="${benchmark}"
    build_dir="${BUILD_ROOT}/${benchmark}/${backend}"

    if [ -f "${TOOLCHAIN_ENV}" ]; then
        set +u
        # shellcheck disable=SC1090
        . "${TOOLCHAIN_ENV}"
        set -u
    fi

    build_common_args "${build_dir}" "${build_type}" "${USER_SOURCE}" "${output_exe}"
    configure_backend_args "${backend}"
    configure_backend_defines "${backend}"
    CMAKE_ARGS+=("${BACKEND_ARGS[@]}")

    rm -rf "${build_dir}"
    mkdir -p "${build_dir}"

    info "Configuring ${benchmark} (${backend}, ${build_type})"
    cmake "${CMAKE_ARGS[@]}"

    info "Building executable ${output_exe}"
    cmake --build "${build_dir}" --target "${output_exe}"

    info "Build complete: ${build_dir}/${output_exe}"
}

main() {
    local cmd="${1:-}"

    case "${cmd}" in
        list)
            list_benchmarks
            ;;
        clean)
            rm -rf "${BUILD_ROOT}"
            info "Removed ${BUILD_ROOT}"
            ;;
        -h|--help|"")
            usage
            ;;
        *)
            [ $# -ge 2 ] || die "Expected <benchmark> <backend> [build_type]."
            build_target "$1" "$2" "${3:-Release}"
            ;;
    esac
}

main "$@"
