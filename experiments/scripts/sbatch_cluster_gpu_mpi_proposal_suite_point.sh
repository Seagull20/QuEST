#!/usr/bin/env bash
# Slurm payload for the proposal-aligned GPU+MPI suite.
#SBATCH --job-name=quest-proposal-gpu-mpi-suite
#SBATCH --time=01:30:00
#SBATCH --output=experiments/results/raw/proposal_gpu_mpi_suite_%j.out
#SBATCH --error=experiments/results/raw/proposal_gpu_mpi_suite_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

is_positive_int() {
    case "$1" in
        ''|*[!0-9]*|0)
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

ratio_token() {
    printf '%s' "$1" | tr '.' 'p'
}

profile_report_exists() {
    local base="$1"

    [ -e "${base}.nsys-rep" ] || [ -e "${base}.qdrep" ] || [ -e "${base}.sqlite" ]
}

main() {
    local mode="${QUEST_GPU_MPI_SUITE_MODE:-validate}"
    local gpu_type="${QUEST_GPU_MPI_GPU_TYPE:-unknown}"
    local ranks="${QUEST_GPU_MPI_RANKS:-${SLURM_NTASKS:-4}}"
    local qubits="${QUEST_GPU_MPI_SUITE_QUBITS:-24}"
    local reps="${QUEST_GPU_MPI_SUITE_REPS:-1}"
    local warmup="${QUEST_GPU_MPI_SUITE_WARMUP:-0}"
    local gate_repeats="${QUEST_GPU_MPI_SUITE_GATE_REPEATS:-64}"
    local random_depth="${QUEST_GPU_MPI_SUITE_RANDOM_DEPTH:-48}"
    local random_ratios="${QUEST_GPU_MPI_SUITE_RANDOM_RATIOS:-0.25 0.50}"
    local preheat_mode="${QUEST_GPU_MPI_PREHEAT_MODE:-off}"
    local preheat_qubits="${QUEST_GPU_MPI_PREHEAT_QUBITS:-24}"
    local job_id="${SLURM_JOB_ID:-manual}"
    local sync_mode="benchmark"
    local run_tag
    local run_raw_dir
    local profile_dir
    local threads
    local launcher=()
    local gate_exe
    local qft_exe
    local random_exe
    local gate_out
    local qft_out
    local random_out
    local kind
    local ratio
    local point
    local profile_base
    local cmd=()

    case "${mode}" in
        validate)
            sync_mode="benchmark"
            ;;
        profile)
            sync_mode="profile"
            ;;
        *)
            die "Unknown QUEST_GPU_MPI_SUITE_MODE '${mode}'. Use validate or profile."
            ;;
    esac

    validate_positive_int QUEST_GPU_MPI_RANKS "${ranks}"
    validate_positive_int QUEST_GPU_MPI_SUITE_QUBITS "${qubits}"
    validate_positive_int QUEST_GPU_MPI_SUITE_REPS "${reps}"
    validate_positive_int QUEST_GPU_MPI_SUITE_GATE_REPEATS "${gate_repeats}"
    validate_positive_int QUEST_GPU_MPI_SUITE_RANDOM_DEPTH "${random_depth}"
    validate_positive_int QUEST_GPU_MPI_PREHEAT_QUBITS "${preheat_qubits}"
    case "${warmup}" in
        ''|*[!0-9]*)
            die "QUEST_GPU_MPI_SUITE_WARMUP must be a non-negative integer, got '${warmup}'."
            ;;
    esac

    cd "${REPO_ROOT}"
    ensure_results_dirs
    source_toolchain_env_if_present
    ensure_minimum_cmake 3.21

    if [ "${mode}" = "profile" ]; then
        command -v nsys >/dev/null 2>&1 || die "nsys not found in allocated job environment."
    fi

    run_tag="${QUEST_GPU_MPI_SUITE_RUN_TAG:-gpu_mpi_proposal_${mode}_${gpu_type}_r${ranks}_${job_id}}"
    run_raw_dir="$(current_raw_results_dir)/${run_tag}"
    profile_dir="${run_raw_dir}/profiles"
    threads="${SLURM_CPUS_PER_TASK:-1}"

    mkdir -p "${run_raw_dir}" "${profile_dir}"

    export BENCH_PLATFORM="${BENCH_PLATFORM:-cluster}"
    export OMP_NUM_THREADS="${threads}"
    export OMP_PLACES=cores
    export OMP_PROC_BIND=close

    info "Proposal GPU+MPI suite payload"
    info "Mode: ${mode}"
    info "Node: $(hostname)"
    info "GPU type: ${gpu_type}"
    info "Ranks: ${ranks}"
    info "Qubits: ${qubits}"
    info "Raw dir: ${run_raw_dir}"
    info "Profile dir: ${profile_dir}"

    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=index,name,memory.total,compute_cap --format=csv,noheader || true
    else
        warn "nvidia-smi not found in job environment."
    fi

    info "Building proposal suite gpu_mpi targets"
    "${EXPERIMENTS_DIR}/build.sh" gate_micro gpu_mpi
    "${EXPERIMENTS_DIR}/build.sh" qft gpu_mpi
    "${EXPERIMENTS_DIR}/build.sh" random gpu_mpi

    gate_exe="${BUILD_ROOT}/gate_micro/gpu_mpi/gate_micro"
    qft_exe="${BUILD_ROOT}/qft/gpu_mpi/qft"
    random_exe="${BUILD_ROOT}/random/gpu_mpi/random"
    [ -x "${gate_exe}" ] || die "Missing executable: ${gate_exe}"
    [ -x "${qft_exe}" ] || die "Missing executable: ${qft_exe}"
    [ -x "${random_exe}" ] || die "Missing executable: ${random_exe}"

    if command -v mpirun >/dev/null 2>&1; then
        launcher=(mpirun -np "${ranks}")
    elif command -v srun >/dev/null 2>&1; then
        launcher=(srun --ntasks="${ranks}")
    else
        die "Neither mpirun nor srun is available for launching the MPI benchmark."
    fi

    run_point() {
        point="$1"
        shift
        cmd=("$@")
        info "Running ${point}"
        if [ "${mode}" = "profile" ]; then
            profile_base="${profile_dir}/${point}"
            nsys profile --force-overwrite=true -o "${profile_base}" "${launcher[@]}" "${cmd[@]}"
            profile_report_exists "${profile_base}" || warn "No Nsight report detected for ${profile_base}"
        else
            "${launcher[@]}" "${cmd[@]}"
        fi
    }

    gate_out="${run_raw_dir}/gate_micro_cluster_gpu_mpi_on_${gpu_type}_r${ranks}_q${qubits}_${job_id}.tsv"
    for kind in h cnot cphase hn; do
        run_point "gate_micro_${kind}" \
            "${gate_exe}" \
            --distribution on \
            --qubits "${qubits}" \
            --gate-kind "${kind}" \
            --gate-repeats "${gate_repeats}" \
            --reps "${reps}" \
            --warmup "${warmup}" \
            --sync-mode "${sync_mode}" \
            --preheat-mode "${preheat_mode}" \
            --preheat-qubits "${preheat_qubits}" \
            --label "gate_micro_${kind}_${mode}_${gpu_type}_r${ranks}" \
            --output "${gate_out}"
    done

    qft_out="${run_raw_dir}/qft_cluster_gpu_mpi_on_${gpu_type}_r${ranks}_q${qubits}_${job_id}.tsv"
    run_point "qft" \
        "${qft_exe}" \
        --distribution on \
        --qubits "${qubits}" \
        --reps "${reps}" \
        --warmup "${warmup}" \
        --sync-mode "${sync_mode}" \
        --preheat-mode "${preheat_mode}" \
        --preheat-qubits "${preheat_qubits}" \
        --label "qft_${mode}_${gpu_type}_r${ranks}" \
        --output "${qft_out}"

    random_out="${run_raw_dir}/random_cluster_gpu_mpi_on_${gpu_type}_r${ranks}_q${qubits}_${job_id}.tsv"
    for ratio in ${random_ratios}; do
        run_point "random_tqr$(ratio_token "${ratio}")" \
            "${random_exe}" \
            --distribution on \
            --qubits "${qubits}" \
            --depth "${random_depth}" \
            --seed 20260402 \
            --two-qubit-ratio "${ratio}" \
            --reps "${reps}" \
            --warmup "${warmup}" \
            --sync-mode "${sync_mode}" \
            --preheat-mode "${preheat_mode}" \
            --preheat-qubits "${preheat_qubits}" \
            --label "random_tqr${ratio}_${mode}_${gpu_type}_r${ranks}" \
            --output "${random_out}"
    done

    {
        printf 'mode=%s\n' "${mode}"
        printf 'gpu_type=%s\n' "${gpu_type}"
        printf 'ranks=%s\n' "${ranks}"
        printf 'qubits=%s\n' "${qubits}"
        printf 'raw_dir=%s\n' "${run_raw_dir}"
        printf 'profile_dir=%s\n' "${profile_dir}"
        printf 'gate_micro_tsv=%s\n' "${gate_out}"
        printf 'qft_tsv=%s\n' "${qft_out}"
        printf 'random_tsv=%s\n' "${random_out}"
    } > "${run_raw_dir}/suite_manifest.txt"

    info "Proposal suite complete: ${run_raw_dir}"
    info "Done: $(date)"
}

main "$@"
