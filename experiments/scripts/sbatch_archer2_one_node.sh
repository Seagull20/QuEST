#!/usr/bin/env bash
#SBATCH --job-name=quest-suite-cpu
#SBATCH --account=m25ext-s2866920
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
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

info "ARCHER2 one-node suite"
info "Node: $(hostname)"
info "OMP_NUM_THREADS=${OMP_NUM_THREADS}"

build_suite_targets cpu_mpi

python3 "${SCRIPT_DIR}/run_suite.py" one-node \
    --platform archer2 \
    --backend cpu_mpi \
    --deployment off \
    --raw-dir "${RAW_RESULTS_DIR}"
