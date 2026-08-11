#!/usr/bin/env bash
# T-079 spec A3 (win/on/tiled) cluster verification.
#
# Runs from the repository root with no arguments, inside an already allocated
# Slurm job on a node with at least two visible GPUs:
#
#     bash experiments/t079_a3_pipeline/run_verification.sh
#
# Legs, in order:
#   0. provenance — commit, working-tree state, build configuration, nvcomp
#      version and the complete driver transcript are written into the results
#      directory, so the archive is bound to the code that produced it
#   1. build the fork with ENABLE_NVCOMP=ON
#   2. bit-identity gate — A3 against the raw CPU-staged exchange at q24 and
#      q26 on 2 and 4 ranks and at four unit sizes. States must hash
#      identically and the record key set must be exactly the expected one.
#      Nothing is timed until this passes.
#   3. per-unit gate + counter engagement — the reported mode names the fused
#      path, no payload byte crossed MPI, no exchange fell back, and the codec
#      was invoked on exactly the units the per-unit minimum-size gate should
#      have let through. The 4 MiB leg is BELOW the gate and must therefore
#      encode nothing; the 20,000,000-byte leg straddles it and must encode all
#      but its short final unit.
#   4. per-unit raw staging — the force-raw ablation drives every unit down the
#      un-encoded branch inside the same pipeline, still bit-identical.
#   5. mode regressions — win/off/tiled and win/on/bulk still behave as before,
#      since T-079 reordered the dispatch they both pass through, and the
#      win/on/bulk leg must still show its own codec engaged.
#   6. engagement smoke — A3 and win/off/tiled at q26, run back to back. This
#      records wall times; it does not assert anything about them. Codec
#      engagement is proved by the counters in leg 3, not by a clock.
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

mkdir -p "${RESULT_DIR}"
# Everything from here on is captured, so the archive holds the full transcript
# and not just the per-case logs.
exec > >(tee -a "${RESULT_DIR}/driver.log") 2>&1

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

# ----------------------------------------------------------- 0. provenance ---
MANIFEST="${RESULT_DIR}/manifest.txt"
{
    printf 't079_verification_started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'slurm_job_id=%s\n' "${SLURM_JOB_ID:-unset}"
    printf 'slurm_nodelist=%s\n' "${SLURM_NODELIST:-unset}"
    printf 'host=%s\n' "$(hostname)"
    printf 'repo_root=%s\n' "${REPO_ROOT}"
    printf 'quest_commit=%s\n' "$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)"
    printf 'quest_branch=%s\n' "$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    printf 'quest_describe=%s\n' "$(git -C "${REPO_ROOT}" describe --always --dirty 2>/dev/null || echo unknown)"
    # A dirty tree does not stop the run, but the archive must say so: a number
    # produced from uncommitted source is not bound to a commit.
    if [ -n "$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null || true)" ]; then
        printf 'quest_worktree=DIRTY\n'
    else
        printf 'quest_worktree=clean\n'
    fi
    printf 'nvcomp_root=%s\n' "${NVCOMP_ROOT}"
    printf 'cuda_root=%s\n' "${CUDA_ROOT}"
    printf 'cuda_arch=%s\n' "${CUDA_ARCH}"
    printf 'nvcc_version=%s\n' "$("${CUDA_COMPILER}" --version 2>/dev/null | tr '\n' ' ' || echo unknown)"
    printf 'mpi_launcher_version=%s\n' "$("${MPI_LAUNCHER}" --version 2>/dev/null | head -1 || echo unknown)"
    printf 'gpus=%s\n' "$(nvidia-smi -L | tr '\n' ';')"
} > "${MANIFEST}"
git -C "${REPO_ROOT}" status --porcelain > "${RESULT_DIR}/git_status.txt" 2>/dev/null || true
git -C "${REPO_ROOT}" diff > "${RESULT_DIR}/git_worktree.diff" 2>/dev/null || true
# nvcomp ships no version symbol we can query portably, so record what is
# actually on the link line.
{
    ls -l "${NVCOMP_ROOT}/lib" 2>/dev/null | grep -i nvcomp || true
    find "${NVCOMP_ROOT}" -name 'nvcomp*version*' -o -name 'nvcomp_version*' 2>/dev/null || true
} > "${RESULT_DIR}/nvcomp_version.txt"

# ---------------------------------------------------------------- 1. build ---
mkdir -p "${BUILD_DIR}"
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
    -DOUTPUT_EXE=t079_a3_pipeline_verify 2>&1 | tee "${RESULT_DIR}/cmake_configure.log"
cmake --build "${BUILD_DIR}" --parallel "${BUILD_JOBS}" --target t079_a3_pipeline_verify \
    2>&1 | tee "${RESULT_DIR}/cmake_build.log"

BINARY="${BUILD_DIR}/t079_a3_pipeline_verify"
[ -x "${BINARY}" ] || die "probe binary was not produced at ${BINARY}"
cp "${BUILD_DIR}/CMakeCache.txt" "${RESULT_DIR}/CMakeCache.txt" 2>/dev/null || true

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
        python3 "${SCRIPT_DIR}/check_counters.py" "${RAW_LOG}" \
            --ranks "${ranks}" --qubits "${qubits}" \
            --targets "$(distributed_targets "${qubits}" "${ranks}")" \
            --expect-mode raw --window none --codec none
    fi
}

# ---------------------------------- 2/3. bit identity, per-unit gate, counters ---
# a3_case <qubits> <ranks> <label> <unit_bytes> <units_per_exchange>
#         <gated_per_exchange> [tile_mib] [tile_bytes]
#
# unit_bytes is the exact unit the run must announce.  units/gated per exchange
# are ceil(payload_per_rank / unit) and the number of those units that fall
# below the 16 MiB minimum-size gate; the checker multiplies them by each
# rank's own observed exchange count.
a3_case() {
    local qubits="$1"
    local ranks="$2"
    local label="$3"
    local unit_bytes="$4"
    local units="$5"
    local gated="$6"
    local tile_mib="${7:-}"
    local tile_bytes="${8:-}"
    local a3_log="${RESULT_DIR}/a3_${label}_q${qubits}_r${ranks}.log"

    raw_baseline "${qubits}" "${ranks}"
    run_case tiled_materialize on "${qubits}" "${ranks}" "${a3_log}" "${tile_mib}" "${tile_bytes}"
    check_states "${RAW_LOG}" "${a3_log}" "${ranks}" tiled_materialize_codec
    python3 "${SCRIPT_DIR}/check_counters.py" "${a3_log}" \
        --ranks "${ranks}" --qubits "${qubits}" \
        --targets "$(distributed_targets "${qubits}" "${ranks}")" \
        --expect-mode tiled_materialize_codec --window required --codec required \
        --expect-unit-bytes "${unit_bytes}" \
        --units-per-exchange "${units}" --gated-per-exchange "${gated}"
}

# payload per rank = 2^q / ranks * 16 B.  Default unit is 16 MiB = the gate
# exactly, so every default unit is encoded (the gate is `bytes < minBytes`).
a3_case 24 2 default 16777216 8 0     # 128 MiB / 16 MiB
a3_case 24 4 default 16777216 4 0     #  64 MiB / 16 MiB
a3_case 26 2 default 16777216 32 0    # 512 MiB / 16 MiB
a3_case 26 4 default 16777216 16 0    # 256 MiB / 16 MiB

# 4 MiB is BELOW the 16 MiB gate: the fused path still runs, and every unit
# must reach the wire GATED_OFF with the codec never invoked.  Round 1 passed
# bit identity here while compressing all 128 units, which is the defect this
# leg now catches.
a3_case 26 4 unit4-below-gate 4194304 64 64 4

# 128 MiB is the unit the campaign intends to run.
a3_case 26 4 unit128 134217728 2 0 128

# 20,000,000 bytes straddles the gate: six full units above it, and a
# 14,217,728-byte final unit below it that must be gated off.  This exercises
# the short-tail drain and the mixed encoded/raw sequence together.
a3_case 24 2 partial-tail-straddles-gate 20000000 7 1 "" 20000000

# ------------------------------------------- 4. per-unit raw staging ---
# The force-raw ablation never invokes the codec, so every unit is GATED_OFF
# and the staged bytes must equal the raw bytes exactly.
FORCE_RAW_LOG="${RESULT_DIR}/a3_force_raw_q24_r2.log"
raw_baseline 24 2
run_case tiled_materialize force_raw 24 2 "${FORCE_RAW_LOG}"
check_states "${RAW_LOG}" "${FORCE_RAW_LOG}" 2 tiled_materialize_codec
python3 "${SCRIPT_DIR}/check_counters.py" "${FORCE_RAW_LOG}" \
    --ranks 2 --qubits 24 --targets "$(distributed_targets 24 2)" \
    --expect-mode tiled_materialize_codec --window required --codec required \
    --expect-unit-bytes 16777216 --units-per-exchange 8 --gated-per-exchange 8

# ------------------------------------------------- 5. mode regressions ---
# T-079 reordered the dispatch these two arms pass through, so both are
# re-proved against the same raw baseline — and neither may prepare the fused
# pipeline, which the checker asserts by the absence of its start-up record.
raw_baseline 26 4
RAW_Q26_R4="${RAW_LOG}"
Q26_R4_TARGETS="$(distributed_targets 26 4)"

I1_LOG="${RESULT_DIR}/i1_q26_r4.log"
run_case tiled_materialize off 26 4 "${I1_LOG}"
I1_SECONDS="${LAST_CASE_SECONDS}"
check_states "${RAW_Q26_R4}" "${I1_LOG}" 4 tiled_materialize
python3 "${SCRIPT_DIR}/check_counters.py" "${I1_LOG}" \
    --ranks 4 --qubits 26 --targets "${Q26_R4_TARGETS}" \
    --expect-mode tiled_materialize --window required --codec none

# win/on/bulk keeps its own chunk axis: 256 MiB payload / 64 MiB default chunk
# = 4 chunks per exchange, none of them gated (the bulk path's gate is
# per-exchange and unchanged by T-079).
A2_LOG="${RESULT_DIR}/a2_q26_r4.log"
run_case bulk_async on 26 4 "${A2_LOG}"
check_states "${RAW_Q26_R4}" "${A2_LOG}" 4 bulk_async
python3 "${SCRIPT_DIR}/check_counters.py" "${A2_LOG}" \
    --ranks 4 --qubits 26 --targets "${Q26_R4_TARGETS}" \
    --expect-mode bulk_async --window required --codec required \
    --units-per-exchange 4 --gated-per-exchange 0

# ------------------------------------------------ 6. engagement smoke ---
# Same state, same schedule, same unit; the only difference is whether the
# codec runs inside the pipeline.  These wall times are recorded, not asserted:
# this is a correctness probe, one Hadamard per distributed target inside a
# whole process lifetime, and it is not a benchmark.  Codec engagement is
# established by the counters above.
A3_TIMED_LOG="${RESULT_DIR}/a3_timed_q26_r4.log"
run_case tiled_materialize on 26 4 "${A3_TIMED_LOG}"
A3_SECONDS="${LAST_CASE_SECONDS}"
check_states "${RAW_Q26_R4}" "${A3_TIMED_LOG}" 4 tiled_materialize_codec
python3 "${SCRIPT_DIR}/check_counters.py" "${A3_TIMED_LOG}" \
    --ranks 4 --qubits 26 --targets "${Q26_R4_TARGETS}" \
    --expect-mode tiled_materialize_codec --window required --codec required \
    --expect-unit-bytes 16777216 --units-per-exchange 16 --gated-per-exchange 0

{
    printf 'i1_wall_seconds=%s\n' "${I1_SECONDS}"
    printf 'a3_wall_seconds=%s\n' "${A3_SECONDS}"
    printf 't079_verification_finished_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "${MANIFEST}"

printf 'T079_ENGAGEMENT_SMOKE i1_wall_seconds=%s a3_wall_seconds=%s (recorded, not asserted: whole-process wall time on a correctness probe)\n' \
    "${I1_SECONDS}" "${A3_SECONDS}"
printf 'T079_VERIFICATION_PASS results=%s commit=%s\n' \
    "${RESULT_DIR}" "$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)"
