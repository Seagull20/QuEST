#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${T039_BUILD_DIR:-${SCRIPT_DIR}/build}"
CUDA_COMPILER="${T039_CUDA_COMPILER:-/home/s2866920/miniconda3/envs/quest_env/bin/nvcc}"
CUDA_ROOT="${T039_CUDA_ROOT:-/home/s2866920/miniconda3/envs/quest_env}"
CUDA_ARCH="${T039_CUDA_ARCH:-75}"
MPI_CXX_COMPILER="${T039_MPI_CXX_COMPILER:-/usr/bin/mpicxx}"
MPI_LAUNCHER="${T039_MPI_LAUNCHER:-mpirun}"
BUILD_JOBS="${T039_BUILD_JOBS:-4}"

die() {
    printf 'T039_VERIFY_ERROR: %s\n' "$*" >&2
    exit 1
}

[ -n "${SLURM_JOB_ID:-}" ] || die "run inside an already allocated Slurm job"
[ -x /usr/bin/cc ] || die "/usr/bin/cc is unavailable"
[ -x /usr/bin/c++ ] || die "/usr/bin/c++ is unavailable"
[ -x "${MPI_CXX_COMPILER}" ] || die "MPI C++ compiler is unavailable at ${MPI_CXX_COMPILER}"
[ -x "${CUDA_COMPILER}" ] || die "nvcc is unavailable at ${CUDA_COMPILER}"
command -v cmake >/dev/null 2>&1 || die "cmake is unavailable"
command -v "${MPI_LAUNCHER}" >/dev/null 2>&1 || die "MPI launcher is unavailable: ${MPI_LAUNCHER}"
command -v python3 >/dev/null 2>&1 || die "python3 is unavailable"
command -v nvidia-smi >/dev/null 2>&1 || die "nvidia-smi is unavailable"

VISIBLE_GPU_COUNT="$(nvidia-smi -L | wc -l | tr -d '[:space:]')"
[ "${VISIBLE_GPU_COUNT}" -ge 2 ] || die "expected at least 2 visible GPUs, observed ${VISIBLE_GPU_COUNT}"

case "${CONDA_DEFAULT_ENV:-}:${CONDA_PREFIX:-}" in
    *quest_compression*)
        die "deactivate quest_compression before configure; it breaks MPI detection"
        ;;
esac

mkdir -p "${BUILD_DIR}"
cmake -S "${REPO_ROOT}" -B "${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=/usr/bin/cc \
    -DCMAKE_CXX_COMPILER=/usr/bin/c++ \
    -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/c++ \
    -DCMAKE_CUDA_COMPILER="${CUDA_COMPILER}" \
    -DCUDAToolkit_ROOT="${CUDA_ROOT}" \
    -DMPI_CXX_COMPILER="${MPI_CXX_COMPILER}" \
    -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCH}" \
    -DENABLE_CUDA=ON \
    -DENABLE_DISTRIBUTION=ON \
    -DENABLE_MULTITHREADING=OFF \
    -DENABLE_PROFILING_MARKERS=OFF \
    -DENABLE_CUQUANTUM=OFF \
    -DENABLE_TESTING=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DUSER_SOURCE="${SCRIPT_DIR}/src/t039_bulk_async_verify.cpp" \
    -DOUTPUT_EXE=t039_bulk_async_verify
cmake --build "${BUILD_DIR}" --parallel "${BUILD_JOBS}" --target t039_bulk_async_verify

CUDA_LIBRARY_PATH="${CUDA_ROOT}/targets/x86_64-linux/lib:${CUDA_ROOT}/lib"
export LD_LIBRARY_PATH="${CUDA_LIBRARY_PATH}:${LD_LIBRARY_PATH:-}"
BINARY="${BUILD_DIR}/t039_bulk_async_verify"
RESULT_DIR="${T039_RESULT_DIR:-${SCRIPT_DIR}/results/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "${RESULT_DIR}"

run_case() {
    local mode="$1"
    local qubits="$2"
    local ranks="$3"
    local targets="$4"
    local output="$5"
    local map_mode="${6:-}"
    local fallback_rank="${7:-}"
    local map_args=()

    if [ -n "${map_mode}" ]; then
        map_args=(--map-by "${map_mode}")
    fi

    printf 'T039_RUN mode=%s qubits=%s ranks=%s targets=%s log=%s\n' \
        "${mode}" "${qubits}" "${ranks}" "${targets}" "${output}"

    if [ "${mode}" = raw ]; then
        env -u QUEST_GPU_STAGING_MODE -u QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK \
            -u QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK_RANK \
            -u QUEST_ENABLE_EXCHANGE_COMPRESSION \
            QUEST_FORCE_CPU_STAGING=1 QUEST_GPU_STAGING_STATS=1 \
            "${MPI_LAUNCHER}" -np "${ranks}" --oversubscribe "${map_args[@]}" \
            -x QUEST_FORCE_CPU_STAGING -x QUEST_GPU_STAGING_STATS -x LD_LIBRARY_PATH \
            "${BINARY}" --qubits "${qubits}" --targets "${targets}" \
            >"${output}" 2>&1
    elif [ -n "${fallback_rank}" ]; then
        env QUEST_GPU_STAGING_MODE=bulk_async \
            QUEST_FORCE_CPU_STAGING=1 QUEST_GPU_STAGING_STATS=1 \
            QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK_RANK="${fallback_rank}" \
            "${MPI_LAUNCHER}" -np "${ranks}" --oversubscribe "${map_args[@]}" \
            -x QUEST_GPU_STAGING_MODE -x QUEST_FORCE_CPU_STAGING \
            -x QUEST_GPU_STAGING_STATS -x QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK_RANK \
            -x LD_LIBRARY_PATH "${BINARY}" --qubits "${qubits}" --targets "${targets}" \
            >"${output}" 2>&1
    else
        env QUEST_GPU_STAGING_MODE=bulk_async \
            QUEST_FORCE_CPU_STAGING=1 QUEST_GPU_STAGING_STATS=1 \
            "${MPI_LAUNCHER}" -np "${ranks}" --oversubscribe "${map_args[@]}" \
            -x QUEST_GPU_STAGING_MODE -x QUEST_FORCE_CPU_STAGING \
            -x QUEST_GPU_STAGING_STATS -x LD_LIBRARY_PATH \
            "${BINARY}" --qubits "${qubits}" --targets "${targets}" \
            >"${output}" 2>&1
    fi
}

run_pair() {
    local qubits="$1"
    local ranks="$2"
    local targets="$3"
    local map_mode="$4"
    local raw_log="${RESULT_DIR}/raw_q${qubits}_r${ranks}.log"
    local window_log="${RESULT_DIR}/bulk_async_q${qubits}_r${ranks}.log"

    run_case raw "${qubits}" "${ranks}" "${targets}" "${raw_log}" "${map_mode}"
    run_case bulk_async "${qubits}" "${ranks}" "${targets}" "${window_log}" "${map_mode}"
    python3 "${SCRIPT_DIR}/check_results.py" "${raw_log}" "${window_log}" "${ranks}"
}

distributed_targets() {
    local qubits="$1"
    local ranks="$2"
    local distributed_rank_bits=0
    local remaining_ranks="${ranks}"
    local target
    local separator=

    while [ "${remaining_ranks}" -gt 1 ]; do
        distributed_rank_bits=$((distributed_rank_bits + 1))
        remaining_ranks=$((remaining_ranks >> 1))
    done

    [ "${remaining_ranks}" -eq 1 ] || die "rank count must be a power of two: ${ranks}"

    for ((target = qubits - distributed_rank_bits; target < qubits; target++)); do
        printf '%s%s' "${separator}" "${target}"
        separator=,
    done
}

# One node is required for the positive window cases. The map-by clauses keep
# the 4-rank case on one node when the allocation contains multiple nodes.
run_pair 24 2 "$(distributed_targets 24 2)" ppr:2:node
run_pair 24 4 "$(distributed_targets 24 4)" ppr:4:node
run_pair 26 2 "$(distributed_targets 26 2)" ppr:2:node
run_pair 26 4 "$(distributed_targets 26 4)" ppr:4:node

# Deliberately make rank 1 ineligible. Both endpoints must agree pairwise and
# then use the unchanged CPU-staged payload path without hanging.
FALLBACK_RAW_LOG="${RESULT_DIR}/raw_fallback_q24_r2.log"
FALLBACK_LOG="${RESULT_DIR}/bulk_async_registration_fallback_q24_r2.log"
FALLBACK_TARGETS="$(distributed_targets 24 2)"
run_case raw 24 2 "${FALLBACK_TARGETS}" "${FALLBACK_RAW_LOG}" ppr:2:node
run_case bulk_async 24 2 "${FALLBACK_TARGETS}" "${FALLBACK_LOG}" ppr:2:node 1
python3 "${SCRIPT_DIR}/check_results.py" \
    "${FALLBACK_RAW_LOG}" "${FALLBACK_LOG}" 2 --expect-registration-fallback 2

if [ "${SLURM_NNODES:-1}" -ge 2 ]; then
    OFFNODE_RAW_LOG="${RESULT_DIR}/raw_offnode_q24_r4.log"
    OFFNODE_LOG="${RESULT_DIR}/bulk_async_offnode_q24_r4.log"
    # With two ranks per node, target 23 pairs ranks across nodes; target 22
    # remains an on-node comparison in the same matrix.
    OFFNODE_TARGETS="$(distributed_targets 24 4)"
    run_case raw 24 4 "${OFFNODE_TARGETS}" "${OFFNODE_RAW_LOG}" ppr:2:node
    run_case bulk_async 24 4 "${OFFNODE_TARGETS}" "${OFFNODE_LOG}" ppr:2:node
    python3 "${SCRIPT_DIR}/check_results.py" \
        "${OFFNODE_RAW_LOG}" "${OFFNODE_LOG}" 4 --expect-offnode
else
    printf 'T039_OFFNODE_FALLBACK_SKIPPED allocation_nodes=%s (run this exact command in a two-node allocation)\n' \
        "${SLURM_NNODES:-1}"
fi

printf 'T039_VERIFICATION_PASS results=%s\n' "${RESULT_DIR}"
