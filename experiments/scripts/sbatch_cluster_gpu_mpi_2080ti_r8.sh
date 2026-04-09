#!/usr/bin/env bash
#SBATCH --job-name=quest-qft-gpu-mpi-2080ti-r8
#SBATCH --partition=Teaching
#SBATCH --nodelist=damnii07
#SBATCH --gres=gpu:nvidia_geforce_rtx_2080_ti:8
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=2
#SBATCH --time=00:45:00
#SBATCH --output=experiments/results/raw/qft_gpu_mpi_2080ti_r8_%j.out
#SBATCH --error=experiments/results/raw/qft_gpu_mpi_2080ti_r8_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present

NUM_RANKS=8
Q_MAX_FILE="${RAW_RESULTS_DIR}/q_max_2080ti.txt"

if [ -n "${QUEST_GPU_MPI_QUBITS:-}" ]; then
    Q_MAX="${QUEST_GPU_MPI_QUBITS}"
    info "Using qubit count from QUEST_GPU_MPI_QUBITS=${Q_MAX}"
elif [ -f "${Q_MAX_FILE}" ]; then
    Q_MAX=$(cat "${Q_MAX_FILE}")
    info "Using qubit count from probe: q_max=${Q_MAX}"
else
    die "q_max file not found at ${Q_MAX_FILE}. Run the 2080 Ti probe job first, or set QUEST_GPU_MPI_QUBITS."
fi

info "=== QFT GPU+MPI Benchmark — 2080 Ti × ${NUM_RANKS} ranks ==="
info "Node: $(hostname)"
info "Total qubits: ${Q_MAX}  |  Ranks: ${NUM_RANKS}  |  GPUs: ${NUM_RANKS}"
info "Per-GPU local qubits: $((Q_MAX - 3)) (each rank holds 1/8 of the statevector)"
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader

info "Building qft/gpu_mpi..."
"${EXPERIMENTS_DIR}/build.sh" qft gpu_mpi

QFT_EXE="${BUILD_ROOT}/qft/gpu_mpi/qft"
OUT_TSV="${RAW_RESULTS_DIR}/qft_gpu_mpi_2080ti_r${NUM_RANKS}_q${Q_MAX}_${SLURM_JOB_ID}.tsv"

info "Launching QFT (srun, ${NUM_RANKS} ranks)..."
srun \
    --ntasks="${NUM_RANKS}" \
    --ntasks-per-node="${NUM_RANKS}" \
    "${QFT_EXE}" \
        --distribution on \
        --qubits "${Q_MAX}" \
        --reps 5 \
        --warmup 1 \
        --sync-mode benchmark \
        --output "${OUT_TSV}"

info "Results: ${OUT_TSV}"
info "Done: $(date)"
