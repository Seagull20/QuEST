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

    [ -s "${base}.nsys-rep" ]
}

benchmark_selected() {
    local requested="$1"
    case " ${benchmarks} " in
        *" ${requested} "*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

validate_benchmarks() {
    local benchmark

    [ -n "${benchmarks//[[:space:]]/}" ] || die "QUEST_GPU_MPI_SUITE_BENCHMARKS cannot be empty."
    for benchmark in ${benchmarks}; do
        case "${benchmark}" in
            gate_micro|qft|random)
                ;;
            *)
                die "Unsupported benchmark '${benchmark}' in QUEST_GPU_MPI_SUITE_BENCHMARKS."
                ;;
        esac
    done
}

main() {
    local mode="${QUEST_GPU_MPI_SUITE_MODE:-validate}"
    local gpu_type="${QUEST_GPU_MPI_GPU_TYPE:-unknown}"
    local ranks="${QUEST_GPU_MPI_RANKS:-${SLURM_NTASKS:-4}}"
    local qubits="${QUEST_GPU_MPI_SUITE_QUBITS:-24}"
    local benchmarks="${QUEST_GPU_MPI_SUITE_BENCHMARKS:-gate_micro qft random}"
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
    local gate_out=""
    local qft_out=""
    local random_out=""
    local kind
    local ratio
    local point
    local profile_base
    local cmd=()
    local rank_breakdown
    local summary_breakdown
    local communication_breakdown
    local computation_breakdown
    local lifecycle_breakdown
    local runtime_breakdown
    local sqlite_path
    local benchmark_name

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
    validate_benchmarks
    case "${warmup}" in
        ''|*[!0-9]*)
            die "QUEST_GPU_MPI_SUITE_WARMUP must be a non-negative integer, got '${warmup}'."
            ;;
    esac

    cd "${REPO_ROOT}"
    ensure_results_dirs
    source_system_profile_if_present
    source_toolchain_env_if_present
    ensure_minimum_cmake 3.21

    if [ "${mode}" = "profile" ]; then
        ensure_nsys_available
        export QUEST_BENCH_ENABLE_PROFILING_MARKERS=1
    else
        export QUEST_BENCH_ENABLE_PROFILING_MARKERS=0
    fi

    run_tag="${QUEST_GPU_MPI_SUITE_RUN_TAG:-gpu_mpi_proposal_${mode}_${gpu_type}_r${ranks}_${job_id}}"
    run_raw_dir="$(current_raw_results_dir)/${run_tag}"
    profile_dir="${run_raw_dir}/profiles"
    threads="${SLURM_CPUS_PER_TASK:-1}"

    mkdir -p "${run_raw_dir}" "${profile_dir}"
    rank_breakdown="${run_raw_dir}/procedure_breakdown_rank.tsv"
    summary_breakdown="${run_raw_dir}/procedure_breakdown.tsv"
    communication_breakdown="${run_raw_dir}/communication_breakdown_rank.tsv"
    computation_breakdown="${run_raw_dir}/computation_breakdown_rank.tsv"
    lifecycle_breakdown="${run_raw_dir}/lifecycle_breakdown_rank.tsv"
    runtime_breakdown="${run_raw_dir}/cuda_runtime_summary_rank.tsv"
    if [ "${mode}" = "profile" ]; then
        rm -f \
            "${rank_breakdown}" \
            "${summary_breakdown}" \
            "${communication_breakdown}" \
            "${computation_breakdown}" \
            "${lifecycle_breakdown}" \
            "${runtime_breakdown}"
    fi

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
    info "Benchmarks: ${benchmarks}"
    info "Raw dir: ${run_raw_dir}"
    info "Profile dir: ${profile_dir}"

    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=index,name,memory.total,compute_cap --format=csv,noheader || true
    else
        warn "nvidia-smi not found in job environment."
    fi

    info "Building proposal suite gpu_mpi targets"
    for benchmark_name in ${benchmarks}; do
        "${EXPERIMENTS_DIR}/build.sh" "${benchmark_name}" gpu_mpi Release
    done

    gate_exe="${BUILD_ROOT}/gate_micro/gpu_mpi/gate_micro"
    qft_exe="${BUILD_ROOT}/qft/gpu_mpi/qft"
    random_exe="${BUILD_ROOT}/random/gpu_mpi/random"
    if benchmark_selected gate_micro; then
        [ -x "${gate_exe}" ] || die "Missing executable: ${gate_exe}"
    fi
    if benchmark_selected qft; then
        [ -x "${qft_exe}" ] || die "Missing executable: ${qft_exe}"
    fi
    if benchmark_selected random; then
        [ -x "${random_exe}" ] || die "Missing executable: ${random_exe}"
    fi

    if command -v mpirun >/dev/null 2>&1; then
        launcher=(mpirun -np "${ranks}")
    elif command -v srun >/dev/null 2>&1; then
        launcher=(srun --ntasks="${ranks}")
    else
        die "Neither mpirun nor srun is available for launching the MPI benchmark."
    fi

    run_point() {
        point="$1"
        benchmark_name="$2"
        shift 2
        cmd=("$@")
        info "Running ${point}"
        if [ "${mode}" = "profile" ]; then
            profile_base="${profile_dir}/${point}"
            nsys profile \
                --trace=cuda,mpi,nvtx,osrt \
                --mpi-impl=openmpi \
                --force-overwrite=true \
                -o "${profile_base}" \
                "${launcher[@]}" "${cmd[@]}"
            profile_report_exists "${profile_base}" || die "No Nsight report detected for ${profile_base}"
            sqlite_path="${profile_base}.sqlite"
            export_nsys_sqlite "${profile_base}.nsys-rep" "${sqlite_path}"
            python3 "${SCRIPT_DIR}/profile_breakdown.py" \
                --sqlite "${sqlite_path}" \
                --point "${point}" \
                --benchmark "${benchmark_name}" \
                --num-qubits "${qubits}" \
                --mpi-ranks "${ranks}" \
                --slurm-nodes "${SLURM_NNODES:-1}" \
                --gpus "${ranks}" \
                --rank-output "${rank_breakdown}" \
                --summary-output "${summary_breakdown}" \
                --communication-output "${communication_breakdown}" \
                --computation-output "${computation_breakdown}" \
                --lifecycle-output "${lifecycle_breakdown}" \
                --runtime-output "${runtime_breakdown}"
        else
            "${launcher[@]}" "${cmd[@]}"
        fi
    }

    if benchmark_selected gate_micro; then
        gate_out="${run_raw_dir}/gate_micro_cluster_gpu_mpi_on_${gpu_type}_r${ranks}_q${qubits}_${job_id}.tsv"
        for kind in h cnot cphase hn; do
            run_point "gate_micro_${kind}" gate_micro \
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
    fi

    if benchmark_selected qft; then
        qft_out="${run_raw_dir}/qft_cluster_gpu_mpi_on_${gpu_type}_r${ranks}_q${qubits}_${job_id}.tsv"
        run_point "qft" qft \
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
    fi

    if benchmark_selected random; then
        random_out="${run_raw_dir}/random_cluster_gpu_mpi_on_${gpu_type}_r${ranks}_q${qubits}_${job_id}.tsv"
        for ratio in ${random_ratios}; do
            run_point "random_tqr$(ratio_token "${ratio}")" random \
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
    fi

    {
        printf 'mode=%s\n' "${mode}"
        printf 'gpu_type=%s\n' "${gpu_type}"
        printf 'ranks=%s\n' "${ranks}"
        printf 'qubits=%s\n' "${qubits}"
        printf 'benchmarks=%s\n' "${benchmarks}"
        printf 'raw_dir=%s\n' "${run_raw_dir}"
        printf 'profile_dir=%s\n' "${profile_dir}"
        printf 'gate_micro_tsv=%s\n' "${gate_out}"
        printf 'qft_tsv=%s\n' "${qft_out}"
        printf 'random_tsv=%s\n' "${random_out}"
        if [ "${mode}" = "profile" ]; then
            printf 'procedure_breakdown_rank_tsv=%s\n' "${rank_breakdown}"
            printf 'procedure_breakdown_tsv=%s\n' "${summary_breakdown}"
            printf 'communication_breakdown_rank_tsv=%s\n' "${communication_breakdown}"
            printf 'computation_breakdown_rank_tsv=%s\n' "${computation_breakdown}"
            printf 'lifecycle_breakdown_rank_tsv=%s\n' "${lifecycle_breakdown}"
            printf 'cuda_runtime_summary_rank_tsv=%s\n' "${runtime_breakdown}"
        fi
    } > "${run_raw_dir}/suite_manifest.txt"

    info "Proposal suite complete: ${run_raw_dir}"
    info "Done: $(date)"
}

main "$@"
