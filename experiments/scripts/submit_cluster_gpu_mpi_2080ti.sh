#!/usr/bin/env bash
# submit_cluster_gpu_mpi_2080ti.sh
#
# Fallback submit script for RTX 2080 Ti nodes (damnii07).
# Use this when A40 nodes are unavailable.
# Job graph:
#   probe → [r1, r2, r4, r8]  (all run in parallel after probe completes)
#
# Run this script on the login node from the repo root:
#   bash experiments/scripts/submit_cluster_gpu_mpi_2080ti.sh
#
# To override qubit count (skip probe dependency):
#   QUEST_GPU_MPI_QUBITS=29 bash experiments/scripts/submit_cluster_gpu_mpi_2080ti.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

info() { printf '>>> %s\n' "$*"; }
die()  { printf '>>> ERROR: %s\n' "$*" >&2; exit 1; }

if ! command -v srun >/dev/null 2>&1 && ! command -v mpirun >/dev/null 2>&1; then
    die "Neither srun nor mpirun found.
To install OpenMPI via conda:
  conda install -n quest_env -c conda-forge openmpi
Then re-activate the environment and retry."
fi

info "Submitting 2080 Ti GPU+MPI QFT benchmark suite (fallback)..."
info "Repo root: ${REPO_ROOT}"

cd "${REPO_ROOT}"

# Phase 1: GPU capacity probe (single 2080 Ti)
PROBE_JID=$(sbatch --parsable \
    "${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_2080ti_probe.sh")
info "Submitted probe job: ${PROBE_JID}"

# Phase 2: QFT benchmark jobs (all in parallel, depend on probe)
for RANKS in 1 2 4 8; do
    BENCH_SCRIPT="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_2080ti_r${RANKS}.sh"
    [ -f "${BENCH_SCRIPT}" ] || die "Script not found: ${BENCH_SCRIPT}"

    JID=$(sbatch --parsable \
        --dependency="afterok:${PROBE_JID}" \
        "${BENCH_SCRIPT}")
    info "Submitted 2080 Ti r${RANKS} job: ${JID}  (depends on probe ${PROBE_JID})"
done

info ""
info "Monitor progress:"
info "  squeue -u \$USER -o '%.10i %.30j %.8T %.10M %.6D %.15R'"
info "Results will appear in: ${REPO_ROOT}/experiments/results/raw/"
