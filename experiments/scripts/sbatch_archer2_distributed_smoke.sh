#!/usr/bin/env bash
#SBATCH --job-name=quest-dist-smoke
#SBATCH --account=m25ext-s2866920
#SBATCH --partition=standard
#SBATCH --qos=short
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:20:00
#SBATCH --output=experiments/results/raw/archer2_%x_%j.out
#SBATCH --error=experiments/results/raw/archer2_%x_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present
ensure_minimum_cmake 3.21

BACKEND="${BACKEND:-cpu_mpi}"
BENCH_PLATFORM="${BENCH_PLATFORM:-archer2}"
BENCH_QUBIT="${BENCH_QUBIT:-26}"
REPS="${REPS:-1}"
WARMUP="${WARMUP:-0}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
PREHEAT_MODE="${PREHEAT_MODE:-light}"
PREHEAT_QUBITS="${PREHEAT_QUBITS:-24}"
RUN_RAW_DIR="$(current_raw_results_dir)"
OUTPUT_PATH="${RUN_RAW_DIR}/qft_archer2_${BACKEND}_on_n${SLURM_JOB_NUM_NODES:-4}_q${BENCH_QUBIT}_t${SLURM_CPUS_PER_TASK:-128}_smoke.tsv"
EXE="${REPO_ROOT}/experiments/build/qft/${BACKEND}/qft"

[ -x "${EXE}" ] || die "Missing executable: ${EXE}"

export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-128}"
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export BENCH_PLATFORM

info "ARCHER2 distributed QFT smoke"
info "Node count: ${SLURM_JOB_NUM_NODES:-unknown}"
info "OMP_NUM_THREADS=${OMP_NUM_THREADS}"
info "Output: ${OUTPUT_PATH}"

srun --hint=nomultithread --cpu-bind=cores \
    "${EXE}" \
    --qubits "${BENCH_QUBIT}" \
    --reps "${REPS}" \
    --warmup "${WARMUP}" \
    --distribution on \
    --sync-mode "${SYNC_MODE}" \
    --preheat-mode "${PREHEAT_MODE}" \
    --preheat-qubits "${PREHEAT_QUBITS}" \
    --label "qft_dist_smoke" \
    --output "${OUTPUT_PATH}"
