#!/usr/bin/env bash
# sbatch_cluster_gpu_mpi.sh — entry point for GPU+MPI QFT benchmarks.
#
# This script is NOT submitted directly via sbatch.
# It dispatches to the appropriate submit orchestrator based on GPU availability.
#
# Usage (on cluster login node, from repo root):
#   bash experiments/scripts/sbatch_cluster_gpu_mpi.sh [a40|2080ti]
#
# Without an argument, tries A40 first and falls back to 2080 Ti.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info() { printf '>>> %s\n' "$*"; }
die()  { printf '>>> ERROR: %s\n' "$*" >&2; exit 1; }

GPU_TYPE="${1:-auto}"

case "${GPU_TYPE}" in
    a40)
        info "Targeting A40 nodes (crannog01)..."
        bash "${SCRIPT_DIR}/submit_cluster_gpu_mpi_a40.sh"
        ;;
    2080ti)
        info "Targeting RTX 2080 Ti nodes (damnii07)..."
        bash "${SCRIPT_DIR}/submit_cluster_gpu_mpi_2080ti.sh"
        ;;
    auto)
        # Try A40 first; fall back to 2080 Ti if node is not available
        info "Auto-detecting GPU type..."
        if sinfo -N -p Teaching --noheader -o '%N %G' 2>/dev/null | grep -q 'crannog'; then
            info "A40 nodes detected — using A40 path."
            bash "${SCRIPT_DIR}/submit_cluster_gpu_mpi_a40.sh"
        else
            info "A40 nodes not detected — falling back to 2080 Ti path."
            bash "${SCRIPT_DIR}/submit_cluster_gpu_mpi_2080ti.sh"
        fi
        ;;
    *)
        die "Unknown GPU type '${GPU_TYPE}'. Use: a40 | 2080ti | auto"
        ;;
esac
