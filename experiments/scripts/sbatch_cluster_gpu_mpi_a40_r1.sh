#!/usr/bin/env bash
#SBATCH --job-name=quest-qft-gpu-mpi-a40-r1
#SBATCH --partition=Teaching
#SBATCH --nodelist=crannog01
#SBATCH --gres=gpu:a40:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:45:00
#SBATCH --output=experiments/results/raw/qft_gpu_mpi_a40_r1_%j.out
#SBATCH --error=experiments/results/raw/qft_gpu_mpi_a40_r1_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present

NUM_RANKS=1
Q_MAX_FILE="${RAW_RESULTS_DIR}/q_max_a40.txt"

# Allow manual override via env var (useful for testing without probe job)
if [ -n "${QUEST_GPU_MPI_QUBITS:-}" ]; then
    Q_MAX="${QUEST_GPU_MPI_QUBITS}"
    info "Using qubit count from QUEST_GPU_MPI_QUBITS=${Q_MAX}"
elif [ -f "${Q_MAX_FILE}" ]; then
    Q_MAX=$(cat "${Q_MAX_FILE}")
    info "Using qubit count from probe: q_max=${Q_MAX}"
else
    die "q_max file not found at ${Q_MAX_FILE}. Run the probe job first, or set QUEST_GPU_MPI_QUBITS."
fi

info "=== QFT GPU+MPI Benchmark — A40 × ${NUM_RANKS} rank(s) ==="
info "Node: $(hostname)"
info "Total qubits: ${Q_MAX}  |  Ranks: ${NUM_RANKS}  |  GPUs: ${NUM_RANKS}"
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader

info "Building qft/gpu_mpi..."
"${EXPERIMENTS_DIR}/build.sh" qft gpu_mpi

QFT_EXE="${BUILD_ROOT}/qft/gpu_mpi/qft"
OUT_TSV="${RAW_RESULTS_DIR}/qft_gpu_mpi_a40_r${NUM_RANKS}_q${Q_MAX}_${SLURM_JOB_ID}.tsv"

info "Launching QFT (mpirun, ${NUM_RANKS} rank)..."
mpirun -np "${NUM_RANKS}" \
    "${QFT_EXE}" \
        --distribution on \
        --qubits "${Q_MAX}" \
        --reps 5 \
        --warmup 1 \
        --sync-mode benchmark \
        --output "${OUT_TSV}"

info "Results: ${OUT_TSV}"
info "Done: $(date)"
