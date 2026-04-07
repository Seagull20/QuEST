#!/usr/bin/env bash
#SBATCH --job-name=quest-dist-point
#SBATCH --account=m25ext-s2866920
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=03:00:00
#SBATCH --output=experiments/results/raw/archer2_%x_%A_%a.out
#SBATCH --error=experiments/results/raw/archer2_%x_%A_%a.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

resolve_qubit() {
    if [ -n "${BENCH_QUBIT:-}" ]; then
        printf '%s' "${BENCH_QUBIT}"
        return 0
    fi

    [ -n "${MANIFEST_PATH:-}" ] || die "MANIFEST_PATH or BENCH_QUBIT must be set."
    [ -n "${SLURM_ARRAY_TASK_ID:-}" ] || die "SLURM_ARRAY_TASK_ID missing for manifest-driven distributed suite."

    awk -v line="${SLURM_ARRAY_TASK_ID}" 'NR == line {print $1; found=1; exit} END {if (!found) exit 1}' "${MANIFEST_PATH}" ||
        die "No manifest entry for array task ${SLURM_ARRAY_TASK_ID}"
}

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present
ensure_minimum_cmake 3.21

BENCHMARK="${BENCHMARK:-}"
BACKEND="${BACKEND:-cpu_mpi}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
PREHEAT_MODE="${PREHEAT_MODE:-light}"
PREHEAT_QUBITS="${PREHEAT_QUBITS:-24}"
REPS="${REPS:-1}"
WARMUP="${WARMUP:-0}"
SEED="${SEED:-20260402}"
BENCH_LABEL="${BENCH_LABEL:-}"
BENCH_PLATFORM="${BENCH_PLATFORM:-archer2}"
RUN_RAW_DIR="$(current_raw_results_dir)"
QUBIT="$(resolve_qubit)"
NODES="${SLURM_JOB_NUM_NODES:-4}"
THREADS="${SLURM_CPUS_PER_TASK:-128}"

[ -n "${BENCHMARK}" ] || die "BENCHMARK must be set."

export OMP_NUM_THREADS="${THREADS}"
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export BENCH_PLATFORM

EXE="${REPO_ROOT}/experiments/build/${BENCHMARK}/${BACKEND}/${BENCHMARK}"
[ -x "${EXE}" ] || die "Missing executable: ${EXE}"

if [ -z "${BENCH_LABEL}" ]; then
    BENCH_LABEL="${BENCHMARK}_dist"
fi

case "${BENCHMARK}" in
    qft)
        OUTPUT_PATH="${RUN_RAW_DIR}/qft_archer2_${BACKEND}_on_n${NODES}_q${QUBIT}_t${THREADS}.tsv"
        CMD=(
            "${EXE}"
            --qubits "${QUBIT}"
            --reps "${REPS}"
            --warmup "${WARMUP}"
            --distribution on
            --sync-mode "${SYNC_MODE}"
            --preheat-mode "${PREHEAT_MODE}"
            --preheat-qubits "${PREHEAT_QUBITS}"
            --label "${BENCH_LABEL}"
            --output "${OUTPUT_PATH}"
        )
        ;;
    h_sweep)
        OUTPUT_PATH="${RUN_RAW_DIR}/h_sweep_archer2_${BACKEND}_on_n${NODES}_q${QUBIT}_t${THREADS}.tsv"
        CMD=(
            "${EXE}"
            --qubits "${QUBIT}"
            --reps "${REPS}"
            --warmup "${WARMUP}"
            --distribution on
            --sync-mode "${SYNC_MODE}"
            --preheat-mode "${PREHEAT_MODE}"
            --preheat-qubits "${PREHEAT_QUBITS}"
            --label "${BENCH_LABEL}"
            --output "${OUTPUT_PATH}"
        )
        ;;
    random)
        DEPTH="${RANDOM_DEPTH:-$((2 * QUBIT))}"
        OUTPUT_PATH="${RUN_RAW_DIR}/random_archer2_${BACKEND}_on_n${NODES}_q${QUBIT}_t${THREADS}.tsv"
        CMD=(
            "${EXE}"
            --qubits "${QUBIT}"
            --depth "${DEPTH}"
            --seed "${SEED}"
            --reps "${REPS}"
            --warmup "${WARMUP}"
            --distribution on
            --sync-mode "${SYNC_MODE}"
            --preheat-mode "${PREHEAT_MODE}"
            --preheat-qubits "${PREHEAT_QUBITS}"
            --label "${BENCH_LABEL}"
            --output "${OUTPUT_PATH}"
        )
        ;;
    *)
        die "Unsupported BENCHMARK '${BENCHMARK}'."
        ;;
esac

info "ARCHER2 distributed suite point"
info "Benchmark: ${BENCHMARK}"
info "Qubit: ${QUBIT}"
info "Nodes: ${NODES}"
info "OMP_NUM_THREADS=${THREADS}"
info "Output: ${OUTPUT_PATH}"

srun --hint=nomultithread --cpu-bind=cores "${CMD[@]}"
