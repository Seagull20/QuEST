#!/usr/bin/env bash
#SBATCH --job-name=quest-qft-mpi
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=01:00:00
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

export OMP_NUM_THREADS=32
export OMP_PLACES=cores
export OMP_PROC_BIND=close

info "ARCHER2 QFT MPI extension"
info "Node: $(hostname)"
info "Allocated nodes: ${SLURM_JOB_NUM_NODES:-unknown}"

build_suite_targets cpu_mpi

python3 "${SCRIPT_DIR}/run_suite.py" mpi-qft \
    --platform archer2 \
    --backend cpu_mpi \
    --deployment on \
    --raw-dir "${RAW_RESULTS_DIR}" \
    --extra-qubits 2 \
    --node-counts 2 4 8 \
    --cpus-per-task 32
