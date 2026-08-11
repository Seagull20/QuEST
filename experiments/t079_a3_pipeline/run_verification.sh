#!/usr/bin/env bash
# T-079 spec A3 (win/on/tiled) cluster verification.
#
# Runs from the repository root with no arguments, inside an already allocated
# Slurm job on a node with at least two visible GPUs:
#
#     bash experiments/t079_a3_pipeline/run_verification.sh
#
# Legs, in order:
#   1. build the fork with ENABLE_NVCOMP=ON
#   2. bit-identity gate — A3 against the raw CPU-staged exchange at q24 and
#      q26 on 2 and 4 ranks, plus a non-power-of-two unit that exercises the
#      short final unit.  The states must hash identically; nothing is timed
#      until this passes.
#   3. counter engagement — the reported mode names the fused path, no payload
#      byte crossed MPI, no exchange fell back, the codec counters moved, and
#      the effective unit is the one the run claims.
#   4. per-unit raw fallback — the force-raw ablation drives every unit down
#      the incompressible branch inside the same pipeline, still bit-identical.
#   5. mode regression — win/off/tiled and win/on/bulk still behave as before,
#      since T-079 reordered the dispatch they both pass through.
#   6. timing smoke — A3 against win/off/tiled at q26 on a compressible state,
#      to show the codec is executing inside the pipeline rather than being
#      voted off.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
T039_DIR="${REPO_ROOT}/experiments/t039_bulk_async"
BUILD_DIR="${T079_BUILD_DIR:-${SCRIPT_DIR}/build}"
RESULT_DIR="${T079_RESULT_DIR:-${SCRIPT_DIR}/results/$(date -u +%Y%m%dT%H%M%SZ)}"

CUDA_COMPILER="${T079_CUDA_COMPILER:-/home/s2866920/miniconda3/envs/quest_env/bin/nvcc}"
CUDA_ROOT="${T079_CUDA_ROOT:-/home/s2866920/miniconda3/envs/quest_env}"
CUDA_ARCH="${T079_CUDA_ARCH:-75}"
MPI_CXX_COMPILER="${T079_MPI_CXX_COMPILER:-/usr/bin/mpicxx}"
MPI_LAUNCHER="${T079_MPI_LAUNCHER:-mpirun}"
BUILD_JOBS="${T079_BUILD_JOBS:-4}"
NVCOMP_ROOT="${NVCOMP_ROOT:-$HOME/miniconda3/envs/quest_compression}"

die() {
    printf 'T079_VERIFY_ERROR: %s\n' "$*" >&2
    exit 1
}

[ -f "${T039_DIR}/check_results.py" ] || die "missing T-039 state checker at ${T039_DIR}"
[ -f "${T039_DIR}/src/t039_bulk_async_verify.cpp" ] || die "missing T-039 probe source"
[ -n "${SLURM_JOB_ID:-}" ] || die "run inside an already allocated Slurm job"
[ -d "${NVCOMP_ROOT}" ] || die "NVCOMP_ROOT not found: ${NVCOMP_ROOT}"
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

# ---------------------------------------------------------------- 1. build ---
mkdir -p "${BUILD_DIR}" "${RESULT_DIR}"
cmake -S "${REPO_ROOT}" -B "${BUILD_DIR}" \
    -DENABLE_NVCOMP=ON "-DNVCOMP_ROOT=${NVCOMP_ROOT}" \
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
    -DUSER_SOURCE="${T039_DIR}/src/t039_bulk_async_verify.cpp" \
    -DOUTPUT_EXE=t079_a3_pipeline_verify
cmake --build "${BUILD_DIR}" --parallel "${BUILD_JOBS}" --target t079_a3_pipeline_verify

BINARY="${BUILD_DIR}/t079_a3_pipeline_verify"
[ -x "${BINARY}" ] || die "probe binary was not produced at ${BINARY}"

export LD_LIBRARY_PATH="${NVCOMP_ROOT}/lib:${CUDA_ROOT}/targets/x86_64-linux/lib:${CUDA_ROOT}/lib:${LD_LIBRARY_PATH:-}"

# ------------------------------------------------------------- case driver ---
# distributed_targets q ranks -> the target qubits that force an exchange
distributed_targets() {
    local qubits="$1"
    local ranks="$2"
    local rank_bits=0
    local remaining="${ranks}"
    local target
    local separator=

    while [ "${remaining}" -gt 1 ]; do
        rank_bits=$((rank_bits + 1))
        remaining=$((remaining >> 1))
    done
    [ "${remaining}" -eq 1 ] || die "rank count must be a power of two: ${ranks}"

    for ((target = qubits - rank_bits; target < qubits; target++)); do
        printf '%s%s' "${separator}" "${target}"
        separator=,
    done
}

LAST_CASE_SECONDS=0

# run_case <mode: raw|tiled_materialize|bulk_async> <codec: off|on|force_raw>
#          <qubits> <ranks> <output> [tile_mib_or_empty] [tile_bytes_or_empty]
run_case() {
    local mode="$1"
    local codec="$2"
    local qubits="$3"
    local ranks="$4"
    local output="$5"
    local tile_mib="${6:-}"
    local tile_bytes="${7:-}"
    local targets
    targets="$(distributed_targets "${qubits}" "${ranks}")"

    local env_pairs=( QUEST_FORCE_CPU_STAGING=1 QUEST_GPU_STAGING_STATS=1 )
    local exports=( -x QUEST_FORCE_CPU_STAGING -x QUEST_GPU_STAGING_STATS -x LD_LIBRARY_PATH )

    if [ "${mode}" != raw ]; then
        env_pairs+=( "QUEST_GPU_STAGING_MODE=${mode}" )
        exports+=( -x QUEST_GPU_STAGING_MODE )
    fi
    if [ -n "${tile_mib}" ]; then
        env_pairs+=( "QUEST_GPU_STAGING_TILE_MB=${tile_mib}" )
        exports+=( -x QUEST_GPU_STAGING_TILE_MB )
    fi
    if [ -n "${tile_bytes}" ]; then
        env_pairs+=( "QUEST_GPU_STAGING_TILE_BYTES=${tile_bytes}" )
        exports+=( -x QUEST_GPU_STAGING_TILE_BYTES )
    fi
    if [ "${codec}" != off ]; then
        env_pairs+=( QUEST_ENABLE_EXCHANGE_COMPRESSION=1 QUEST_EXCHANGE_COMPRESSION_STATS=1 )
        exports+=( -x QUEST_ENABLE_EXCHANGE_COMPRESSION -x QUEST_EXCHANGE_COMPRESSION_STATS )
    fi
    if [ "${codec}" = force_raw ]; then
        env_pairs+=( QUEST_EXCHANGE_COMPRESSION_FORCE_RAW=1 )
        exports+=( -x QUEST_EXCHANGE_COMPRESSION_FORCE_RAW )
    fi

    printf 'T079_RUN mode=%s codec=%s qubits=%s ranks=%s tile_mib=%s tile_bytes=%s log=%s\n' \
        "${mode}" "${codec}" "${qubits}" "${ranks}" "${tile_mib:-default}" \
        "${tile_bytes:-default}" "${output}"

    local started finished
    started="$(date +%s.%N)"
    env -u QUEST_GPU_STAGING_MODE -u QUEST_ENABLE_EXCHANGE_COMPRESSION \
        -u QUEST_EXCHANGE_COMPRESSION_FORCE_RAW -u QUEST_GPU_STAGING_TILE_MB \
        -u QUEST_GPU_STAGING_TILE_BYTES \
        -u QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK \
        -u QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK_RANK \
        "${env_pairs[@]}" \
        "${MPI_LAUNCHER}" -np "${ranks}" --oversubscribe --map-by "ppr:${ranks}:node" \
        "${exports[@]}" \
        "${BINARY}" --qubits "${qubits}" --targets "${targets}" \
        >"${output}" 2>&1
    finished="$(date +%s.%N)"
    LAST_CASE_SECONDS="$(awk -v a="${started}" -v b="${finished}" 'BEGIN {printf "%.3f", b - a}')"
}

check_states() {
    local raw_log="$1"
    local window_log="$2"
    local ranks="$3"
    local expect_mode="$4"

    T039_EXPECT_WINDOW_MODE="${expect_mode}" \
        python3 "${T039_DIR}/check_results.py" "${raw_log}" "${window_log}" "${ranks}"
}

# ------------------------------------------------- 2/3. bit identity + counters ---
# The raw baseline for a (qubits, ranks) point is reused by every window case
# at that point, so it is produced once.  RAW_LOG is set as a side effect
# rather than printed: run_case writes progress to stdout, which a command
# substitution would swallow into the path.
RAW_LOG=""

raw_baseline() {
    local qubits="$1"
    local ranks="$2"
    RAW_LOG="${RESULT_DIR}/raw_q${qubits}_r${ranks}.log"
    if [ ! -s "${RAW_LOG}" ]; then
        run_case raw off "${qubits}" "${ranks}" "${RAW_LOG}"
    fi
}

a3_case() {
    local qubits="$1"
    local ranks="$2"
    local label="$3"
    local tile_mib="${4:-}"
    local tile_bytes="${5:-}"
    local a3_log="${RESULT_DIR}/a3_${label}_q${qubits}_r${ranks}.log"
    local unit_args=()

    if [ -n "${tile_mib}" ]; then
        unit_args=( --expect-unit-mib "${tile_mib}" )
    fi

    raw_baseline "${qubits}" "${ranks}"
    run_case tiled_materialize on "${qubits}" "${ranks}" "${a3_log}" "${tile_mib}" "${tile_bytes}"
    check_states "${RAW_LOG}" "${a3_log}" "${ranks}" tiled_materialize_codec
    python3 "${SCRIPT_DIR}/check_counters.py" "${a3_log}" --ranks "${ranks}" "${unit_args[@]}"
}

a3_case 24 2 default
a3_case 24 4 default
a3_case 26 2 default
a3_case 26 4 default

# 128 MiB is the unit the campaign intends to run; 4 MiB deepens the pipeline
# so the double-buffer and two-back slot dependencies are exercised many times
# in one exchange.
a3_case 26 4 unit4 4
a3_case 26 4 unit128 128

# A unit that does not divide the payload leaves a short final unit, which is
# the one the drain handles outside the loop.
a3_case 24 2 partial-tail "" 20000000

# --------------------------------------------- 4. per-unit raw fallback ---
FORCE_RAW_LOG="${RESULT_DIR}/a3_force_raw_q24_r2.log"
raw_baseline 24 2
run_case tiled_materialize force_raw 24 2 "${FORCE_RAW_LOG}"
check_states "${RAW_LOG}" "${FORCE_RAW_LOG}" 2 tiled_materialize_codec
python3 "${SCRIPT_DIR}/check_counters.py" "${FORCE_RAW_LOG}" --ranks 2 --expect-all-fallback

# ------------------------------------------------- 5. mode regressions ---
# T-079 reordered the dispatch these two arms pass through, so both are
# re-proved against the same raw baseline.
raw_baseline 26 4
RAW_Q26_R4="${RAW_LOG}"

I1_LOG="${RESULT_DIR}/i1_q26_r4.log"
run_case tiled_materialize off 26 4 "${I1_LOG}"
I1_SECONDS="${LAST_CASE_SECONDS}"
check_states "${RAW_Q26_R4}" "${I1_LOG}" 4 tiled_materialize

A2_LOG="${RESULT_DIR}/a2_q26_r4.log"
run_case bulk_async on 26 4 "${A2_LOG}"
check_states "${RAW_Q26_R4}" "${A2_LOG}" 4 bulk_async

# ---------------------------------------------------- 6. timing smoke ---
# Same state, same schedule, same unit: the only difference is whether the
# codec runs inside the pipeline.  A3 must differ measurably from I1 on this
# highly compressible state; equality would mean the codec was voted off.
A3_TIMED_LOG="${RESULT_DIR}/a3_timed_q26_r4.log"
run_case tiled_materialize on 26 4 "${A3_TIMED_LOG}"
A3_SECONDS="${LAST_CASE_SECONDS}"
check_states "${RAW_Q26_R4}" "${A3_TIMED_LOG}" 4 tiled_materialize_codec
python3 "${SCRIPT_DIR}/check_counters.py" "${A3_TIMED_LOG}" --ranks 4

printf 'T079_TIMING_SMOKE i1_seconds=%s a3_seconds=%s ratio_i1_over_a3=%s\n' \
    "${I1_SECONDS}" "${A3_SECONDS}" \
    "$(awk -v i="${I1_SECONDS}" -v a="${A3_SECONDS}" 'BEGIN {printf "%.3f", (a > 0)? i / a : 0}')"
printf 'T079_TIMING_NOTE whole-process wall time on a correctness probe, not a benchmark: it includes build-independent set-up and one Hadamard per distributed target. Treat it as engagement evidence only.\n'

printf 'T079_VERIFICATION_PASS results=%s\n' "${RESULT_DIR}"
