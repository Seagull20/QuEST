#!/usr/bin/env bash
#SBATCH --job-name=quest-probe-gpu-mpi
#SBATCH --partition=Teaching
#SBATCH --nodelist=crannog01
#SBATCH --gres=gpu:a40:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:15:00
#SBATCH --output=experiments/results/raw/probe_gpu_mpi_%j.out
#SBATCH --error=experiments/results/raw/probe_gpu_mpi_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present

info "=== GPU Capacity Probe (A40) ==="
info "Node: $(hostname)"
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader

# Build probe with single-GPU backend (no MPI/distribution needed for probe)
info "Building probe/gpu..."
"${EXPERIMENTS_DIR}/build.sh" probe gpu

PROBE_EXE="${BUILD_ROOT}/probe/gpu/probe"
PROBE_OUT="${RAW_RESULTS_DIR}/probe_a40_${SLURM_JOB_ID}.tsv"
Q_MAX_FILE="${RAW_RESULTS_DIR}/q_max_a40.txt"

info "Running probe (search range 20–34 qubits)..."
"${PROBE_EXE}" \
    --distribution off \
    --search-min 20 \
    --search-max 34 \
    --validation alloc_only \
    --output "${PROBE_OUT}"

# Extract max_qubits from the TSV (header row identifies the column)
Q_MAX=$(awk -F'\t' '
    NR==1 { for (i=1; i<=NF; i++) if ($i == "max_qubits") col=i }
    NR==2 { if (col) print $col }
' "${PROBE_OUT}")

if [ -z "${Q_MAX}" ] || [ "${Q_MAX}" -lt 1 ] 2>/dev/null; then
    die "Probe failed to determine q_max. Check ${PROBE_OUT} for details."
fi

echo "${Q_MAX}" > "${Q_MAX_FILE}"
info "q_max (A40) = ${Q_MAX} qubits  →  written to ${Q_MAX_FILE}"
info "Probe TSV: ${PROBE_OUT}"
info "Done: $(date)"
