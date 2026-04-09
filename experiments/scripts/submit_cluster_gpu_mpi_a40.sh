#!/usr/bin/env bash
# submit_cluster_gpu_mpi_a40.sh
#
# Submits the GPU+MPI QFT benchmark suite targeting A40 nodes (crannog01).
# Job graph:
#   probe → [r1, r2]  (r1 and r2 run in parallel after probe completes)
#
# Run this script on the login node from the repo root:
#   bash experiments/scripts/submit_cluster_gpu_mpi_a40.sh
#
# To override qubit count (skip probe dependency):
#   QUEST_GPU_MPI_QUBITS=30 bash experiments/scripts/submit_cluster_gpu_mpi_a40.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

info() { printf '>>> %s\n' "$*"; }
die()  { printf '>>> ERROR: %s\n' "$*" >&2; exit 1; }

# Guard: MPI must be available for the benchmark jobs to run
if ! command -v srun >/dev/null 2>&1 && ! command -v mpirun >/dev/null 2>&1; then
    die "Neither srun nor mpirun found.
To install OpenMPI via conda:
  conda install -n quest_env -c conda-forge openmpi
Then re-activate the environment and retry."
fi

info "Submitting A40 GPU+MPI QFT benchmark suite..."
info "Repo root: ${REPO_ROOT}"

# All sbatch commands must be submitted from the repo root so that relative
# --output paths (experiments/results/raw/...) resolve correctly.
cd "${REPO_ROOT}"

# Phase 1: GPU capacity probe (single A40, no MPI)
PROBE_JID=$(sbatch --parsable \
    "${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_probe.sh")
info "Submitted probe job: ${PROBE_JID}"

# Phase 2: QFT benchmark jobs (run in parallel, depend on probe)
for RANKS in 1 2; do
    BENCH_SCRIPT="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_a40_r${RANKS}.sh"
    [ -f "${BENCH_SCRIPT}" ] || die "Script not found: ${BENCH_SCRIPT}"

    JID=$(sbatch --parsable \
        --dependency="afterok:${PROBE_JID}" \
        "${BENCH_SCRIPT}")
    info "Submitted A40 r${RANKS} job: ${JID}  (depends on probe ${PROBE_JID})"
done

info ""
info "Monitor progress:"
info "  squeue -u \$USER -o '%.10i %.30j %.8T %.10M %.6D %.15R'"
info "Results will appear in: ${REPO_ROOT}/experiments/results/raw/"
