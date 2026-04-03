#!/usr/bin/env bash
#SBATCH --job-name=quest-point-cpu
#SBATCH --account=m25ext-s2866920
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --time=04:00:00
#SBATCH --output=experiments/results/raw/archer2_%x_%A_%a.out
#SBATCH --error=experiments/results/raw/archer2_%x_%A_%a.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present

BENCHMARK="${BENCHMARK:-}"
BACKEND="${BACKEND:-cpu_mpi}"
DEPLOYMENT="${DEPLOYMENT:-off}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
REPS="${REPS:-3}"
WARMUP="${WARMUP:-1}"
if [ -n "${POINT_REPS:-}" ]; then
    REPS="${POINT_REPS}"
fi
if [ -n "${POINT_WARMUP:-}" ]; then
    WARMUP="${POINT_WARMUP}"
fi
SEED="${SEED:-20260402}"
BENCH_LABEL="${BENCH_LABEL:-}"
BENCH_PLATFORM="${BENCH_PLATFORM:-archer2}"
RUN_RAW_DIR="$(current_raw_results_dir)"
POINT_TAG="${POINT_TAG:-}"

if [ -n "${BENCH_QUBIT:-}" ]; then
    QUBIT="${BENCH_QUBIT}"
elif [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then
    QUBIT="${SLURM_ARRAY_TASK_ID}"
else
    die "BENCH_QUBIT or SLURM_ARRAY_TASK_ID must be set."
fi

[ -n "${BENCHMARK}" ] || die "BENCHMARK must be set."

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-32}"
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export BENCH_PLATFORM

EXE="${REPO_ROOT}/experiments/build/${BENCHMARK}/${BACKEND}/${BENCHMARK}"
[ -x "${EXE}" ] || die "Missing executable: ${EXE}"

if [ -z "${BENCH_LABEL}" ]; then
    BENCH_LABEL="${BENCHMARK}"
fi

case "${BENCHMARK}" in
    qft)
        OUTPUT_PATH="${RUN_RAW_DIR}/qft_archer2_${BACKEND}_${DEPLOYMENT}_q${QUBIT}${POINT_TAG:+_${POINT_TAG}}.tsv"
        CMD=(
            "${EXE}"
            --qubits "${QUBIT}"
            --reps "${REPS}"
            --warmup "${WARMUP}"
            --distribution "${DEPLOYMENT}"
            --sync-mode "${SYNC_MODE}"
            --label "${BENCH_LABEL}"
            --output "${OUTPUT_PATH}"
        )
        ;;
    h_sweep)
        OUTPUT_PATH="${RUN_RAW_DIR}/h_sweep_archer2_${BACKEND}_${DEPLOYMENT}_q${QUBIT}${POINT_TAG:+_${POINT_TAG}}.tsv"
        CMD=(
            "${EXE}"
            --qubits "${QUBIT}"
            --reps "${REPS}"
            --warmup "${WARMUP}"
            --distribution "${DEPLOYMENT}"
            --sync-mode "${SYNC_MODE}"
            --label "${BENCH_LABEL}"
            --output "${OUTPUT_PATH}"
        )
        ;;
    random)
        if [ -z "${POINT_TAG}" ] && [ "${RANDOM_SPLIT_BY_REP:-0}" = "1" ] && [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then
            POINT_TAG="rep${SLURM_ARRAY_TASK_ID}"
        fi
        DEPTH="${RANDOM_DEPTH:-$((2 * QUBIT))}"
        OUTPUT_PATH="${RUN_RAW_DIR}/random_archer2_${BACKEND}_${DEPLOYMENT}_q${QUBIT}${POINT_TAG:+_${POINT_TAG}}.tsv"
        CMD=(
            "${EXE}"
            --qubits "${QUBIT}"
            --depth "${DEPTH}"
            --seed "${SEED}"
            --reps "${REPS}"
            --warmup "${WARMUP}"
            --distribution "${DEPLOYMENT}"
            --sync-mode "${SYNC_MODE}"
            --label "${BENCH_LABEL}"
            --output "${OUTPUT_PATH}"
        )
        ;;
    *)
        die "Unsupported BENCHMARK '${BENCHMARK}'."
        ;;
esac

info "ARCHER2 benchmark point job"
info "Benchmark: ${BENCHMARK}"
info "Qubit: ${QUBIT}"
info "Node: $(hostname)"
info "Output: ${OUTPUT_PATH}"

"${CMD[@]}"
