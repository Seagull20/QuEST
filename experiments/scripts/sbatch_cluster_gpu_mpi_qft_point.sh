#!/usr/bin/env bash
# Slurm payload for one MLS-cluster GPU+MPI QFT smoke point.
#
# Resource requests are normally supplied by sbatch_cluster_gpu_mpi.sh.
# QuEST owns rank-to-GPU binding; this script intentionally does not set
# CUDA_VISIBLE_DEVICES or derive per-rank GPU IDs.
#SBATCH --job-name=quest-qft-gpu-mpi-smoke
#SBATCH --time=00:45:00
#SBATCH --output=experiments/results/raw/qft_gpu_mpi_smoke_%j.out
#SBATCH --error=experiments/results/raw/qft_gpu_mpi_smoke_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

is_positive_int() {
    case "$1" in
        ''|*[!0-9]*)
            return 1
            ;;
        0)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

validate_positive_int() {
    local name="$1"
    local value="$2"

    is_positive_int "${value}" || die "${name} must be a positive integer, got '${value}'."
}

main() {
    local gpu_type="${QUEST_GPU_MPI_GPU_TYPE:-unknown}"
    local ranks="${QUEST_GPU_MPI_RANKS:-${SLURM_NTASKS:-2}}"
    local qubits="${QUEST_GPU_MPI_QUBITS:-24}"
    local reps="${QUEST_GPU_MPI_REPS:-1}"
    local warmup="${QUEST_GPU_MPI_WARMUP:-0}"
    local sync_mode="${QUEST_GPU_MPI_SYNC_MODE:-benchmark}"
    local preheat_mode="${QUEST_GPU_MPI_PREHEAT_MODE:-off}"
    local preheat_qubits="${QUEST_GPU_MPI_PREHEAT_QUBITS:-24}"
    local job_id="${SLURM_JOB_ID:-manual}"
    local run_raw_dir
    local qft_exe
    local out_tsv
    local label
    local threads
    local launcher=()
    local cmd=()

    validate_positive_int QUEST_GPU_MPI_RANKS "${ranks}"
    validate_positive_int QUEST_GPU_MPI_QUBITS "${qubits}"
    validate_positive_int QUEST_GPU_MPI_REPS "${reps}"
    validate_positive_int QUEST_GPU_MPI_PREHEAT_QUBITS "${preheat_qubits}"
    case "${warmup}" in
        ''|*[!0-9]*)
            die "QUEST_GPU_MPI_WARMUP must be a non-negative integer, got '${warmup}'."
            ;;
    esac

    cd "${REPO_ROOT}"
    ensure_results_dirs
    source_toolchain_env_if_present
    ensure_minimum_cmake 3.21

    run_raw_dir="$(current_raw_results_dir)"
    qft_exe="${BUILD_ROOT}/qft/gpu_mpi/qft"
    out_tsv="${QUEST_GPU_MPI_OUTPUT:-${run_raw_dir}/qft_cluster_gpu_mpi_on_${gpu_type}_r${ranks}_q${qubits}_${job_id}.tsv}"
    label="${QUEST_GPU_MPI_LABEL:-qft_gpu_mpi_smoke_${gpu_type}_r${ranks}}"
    threads="${SLURM_CPUS_PER_TASK:-1}"

    mkdir -p "$(dirname "${out_tsv}")"

    export BENCH_PLATFORM="${BENCH_PLATFORM:-cluster}"
    export OMP_NUM_THREADS="${threads}"
    export OMP_PLACES=cores
    export OMP_PROC_BIND=close

    info "QFT gpu_mpi smoke payload"
    info "Node: $(hostname)"
    info "GPU type: ${gpu_type}"
    info "Ranks: ${ranks}"
    info "Qubits: ${qubits}"
    info "Reps/warmup: ${reps}/${warmup}"
    info "Sync mode: ${sync_mode}"
    info "Preheat: ${preheat_mode} (${preheat_qubits} qubits)"
    info "Output: ${out_tsv}"

    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader || true
    else
        warn "nvidia-smi not found in job environment."
    fi

    info "Building qft/gpu_mpi"
    "${EXPERIMENTS_DIR}/build.sh" qft gpu_mpi
    [ -x "${qft_exe}" ] || die "Missing executable after build: ${qft_exe}"

    cmd=(
        "${qft_exe}"
        --distribution on
        --qubits "${qubits}"
        --reps "${reps}"
        --warmup "${warmup}"
        --sync-mode "${sync_mode}"
        --preheat-mode "${preheat_mode}"
        --preheat-qubits "${preheat_qubits}"
        --label "${label}"
        --output "${out_tsv}"
    )

    if command -v mpirun >/dev/null 2>&1; then
        launcher=(mpirun -np "${ranks}")
    elif command -v srun >/dev/null 2>&1; then
        launcher=(srun --ntasks="${ranks}")
    else
        die "Neither mpirun nor srun is available for launching the MPI benchmark."
    fi

    info "Launching QFT with ${launcher[*]}"
    "${launcher[@]}" "${cmd[@]}"

    info "Results: ${out_tsv}"
    info "Done: $(date)"
}

main "$@"
