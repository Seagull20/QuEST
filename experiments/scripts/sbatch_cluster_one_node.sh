#!/usr/bin/env bash
#SBATCH --job-name=quest-suite-gpu
#SBATCH --partition=Teaching
#SBATCH --nodelist=crannog01
#SBATCH --gres=gpu:a40:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=01:00:00
#SBATCH --output=experiments/results/raw/cluster_%x_%j.out
#SBATCH --error=experiments/results/raw/cluster_%x_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

BACKEND="${1:-gpu}"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present

case "${BACKEND}" in
    gpu|cuquantum)
        ;;
    gpu_mpi)
        die "Use experiments/scripts/sbatch_cluster_gpu_mpi.sh for the gpu_mpi QFT smoke path."
        ;;
    *)
        die "Unsupported cluster backend '${BACKEND}'. Use gpu or cuquantum."
        ;;
esac

if [ "${BACKEND}" = "cuquantum" ] && [ -z "${CUQUANTUM_ROOT:-}" ]; then
    for candidate in "${HOME}"/.local/lib/python*/site-packages/cuquantum; do
        if [ -f "${candidate}/include/custatevec.h" ]; then
            export CUQUANTUM_ROOT="${candidate}"
            export LD_LIBRARY_PATH="${CUQUANTUM_ROOT}/lib:${LD_LIBRARY_PATH:-}"
            info "Detected CUQUANTUM_ROOT=${CUQUANTUM_ROOT}"
            break
        fi
    done
fi

info "Cluster one-node suite"
info "Node: $(hostname)"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
fi

build_suite_targets "${BACKEND}"

python3 "${SCRIPT_DIR}/run_suite.py" one-node \
    --platform cluster \
    --backend "${BACKEND}" \
    --deployment off \
    --raw-dir "${RAW_RESULTS_DIR}"
