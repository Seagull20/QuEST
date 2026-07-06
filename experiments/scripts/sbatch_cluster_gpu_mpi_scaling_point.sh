#!/usr/bin/env bash
# Slurm payload for one-node GPU strong/weak scaling.
#SBATCH --job-name=quest-gpu-scaling
#SBATCH --time=03:30:00

set -euo pipefail

SCRIPT_PATH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ -f "${SLURM_SUBMIT_DIR}/experiments/build.sh" ]; then
    REPO_ROOT="${SLURM_SUBMIT_DIR}"
else
    REPO_ROOT="${SCRIPT_PATH_ROOT}"
fi
SCRIPT_DIR="${REPO_ROOT}/experiments/scripts"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

MPIRUN_PREFIX=(mpirun)

write_point_manifest() {
    local path="$1"
    local workload benchmark gate_kind gate_repeats
    local ranks qubits membership point_id source_file profile_point profile_selected

    printf 'point_id\tbenchmark\tgate_kind\tnum_qubits\tmpi_ranks\tgpus\tslurm_nodes\tscale_membership\tsource_file\tprofile_point\tprofile_selected\tgate_repeats\trandom_depth\ttwo_qubit_ratio\tseed\n' > "${path}"
    for workload in h cnot cphase qft random; do
        benchmark="${workload}"
        gate_kind="none"
        gate_repeats=0
        case "${workload}" in
            h)
                benchmark="gate_micro"
                gate_kind="h"
                gate_repeats=16
                ;;
            cnot)
                benchmark="gate_micro"
                gate_kind="cnot"
                gate_repeats=16
                ;;
            cphase)
                benchmark="gate_micro"
                gate_kind="cphase"
                gate_repeats=256
                ;;
        esac

        while read -r ranks qubits membership; do
            point_id="${workload}_p${ranks}_q${qubits}"
            source_file="timing/${point_id}.tsv"
            profile_point="${point_id}"
            profile_selected=0
            if [ "${workload}" = "qft" ]; then
                profile_selected=1
            elif [ "${workload}" = "h" ] || [ "${workload}" = "random" ]; then
                case "${ranks}:${qubits}" in
                    1:28|1:26|2:27|4:28)
                        profile_selected=1
                        ;;
                esac
            fi
            printf '%s\t%s\t%s\t%s\t%s\t%s\t1\t%s\t%s\t%s\t%s\t%s\t8\t0.5\t20260402\n' \
                "${point_id}" "${benchmark}" "${gate_kind}" "${qubits}" "${ranks}" "${ranks}" \
                "${membership}" "${source_file}" "${profile_point}" "${profile_selected}" "${gate_repeats}" >> "${path}"
        done <<'EOF'
1 28 strong
2 28 strong
4 28 strong,weak
1 26 weak
2 27 weak
EOF
    done
}

profile_report_exists() {
    [ -s "$1.nsys-rep" ]
}

record_command() {
    local item
    printf '[%s]' "$(date --iso-8601=seconds)" >> "${COMMAND_LOG}"
    for item in "$@"; do
        printf ' %q' "${item}" >> "${COMMAND_LOG}"
    done
    printf '\n' >> "${COMMAND_LOG}"
}

run_logged() {
    record_command "$@"
    "$@"
}

capture_gpu_snapshot() {
    local point="$1"
    local phase="$2"
    local timestamp
    local row

    command -v nvidia-smi >/dev/null 2>&1 || return 0
    timestamp="$(date --iso-8601=seconds)"
    while IFS= read -r row; do
        row="${row//, /$'\t'}"
        printf '%s\t%s\t%s\t%s\n' "${timestamp}" "${point}" "${phase}" "${row}" >> "${GPU_SAMPLES}"
    done < <(nvidia-smi --query-gpu=index,uuid,name,temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits)
}

write_git_snapshot() {
    local environment_dir="$1"
    local commit="${QUEST_SCALING_GIT_COMMIT:-}"

    if [ -z "${commit}" ]; then
        commit="$(git rev-parse HEAD 2>/dev/null || true)"
    fi
    printf '%s\n' "${commit:-unavailable}" > "${environment_dir}/git_commit.txt"
    git status --short --branch > "${environment_dir}/git_status.txt" 2>&1 || \
        printf 'git status unavailable in batch environment\n' > "${environment_dir}/git_status.txt"
}

write_environment_snapshot() {
    mkdir -p "${RUN_DIR}/environment"
    write_git_snapshot "${RUN_DIR}/environment"
    scontrol show job "${SLURM_JOB_ID}" -o > "${RUN_DIR}/environment/scontrol_job.txt" 2>&1 || true
    scontrol show partition "${QUEST_SCALING_PARTITION}" -o > "${RUN_DIR}/environment/scontrol_partition.txt" 2>&1 || true
    sacctmgr -n -P show assoc user="${USER}" format=Cluster,Account,User,Partition,QOS,DefaultQOS,GrpTRES,MaxTRES,MaxJobs,MaxSubmit \
        > "${RUN_DIR}/environment/slurm_association.tsv" 2>&1 || true
    sacctmgr -n -P show qos name=teaching format=Name,Priority,MaxTRESPJ,MaxTRESPU,GrpTRES,MaxJobsPU,MaxSubmitJobsPU \
        > "${RUN_DIR}/environment/slurm_qos.tsv" 2>&1 || true
    module -t list > "${RUN_DIR}/environment/modules.txt" 2>&1 || true
    nvidia-smi -L > "${RUN_DIR}/environment/nvidia_smi_list.txt" 2>&1 || true
    nvidia-smi topo -m > "${RUN_DIR}/environment/nvidia_smi_topology.txt" 2>&1 || true
    nvidia-smi -q > "${RUN_DIR}/environment/nvidia_smi_query.txt" 2>&1 || true
    {
        printf 'hostname=%s\n' "$(hostname)"
        printf 'slurm_job_id=%s\n' "${SLURM_JOB_ID}"
        printf 'slurm_nodes=%s\n' "${SLURM_NNODES:-1}"
        printf 'slurm_ntasks=%s\n' "${SLURM_NTASKS:-4}"
        printf 'slurm_cpus_per_task=%s\n' "${SLURM_CPUS_PER_TASK:-2}"
        printf 'partition=%s\n' "${QUEST_SCALING_PARTITION}"
        printf 'gpu_type=%s\n' "${QUEST_SCALING_GPU_TYPE}"
        printf 'gpu_gres=%s\n' "${QUEST_SCALING_GPU_GRES}"
        printf 'build_type=Release\n'
        printf 'build_parallel=%s\n' "${QUEST_BENCH_BUILD_PARALLEL:-8}"
        printf 'precision=double\n'
        printf 'timing_reps=5\n'
        printf 'timing_warmup=1\n'
        printf 'profile_reps=1\n'
        printf 'profile_warmup=0\n'
        printf 'compression_mode=%s\n' "${QUEST_SCALING_COMPRESSION_MODE:-native}"
        printf 'bench_enable_nvcomp=%s\n' "${QUEST_BENCH_ENABLE_NVCOMP:-0}"
        printf 'exchange_compression_enabled=%s\n' "${QUEST_ENABLE_EXCHANGE_COMPRESSION:-unset}"
        printf 'exchange_compression_min_bytes=%s\n' "${QUEST_EXCHANGE_COMPRESSION_MIN_BYTES:-default}"
        printf 'exchange_compression_chunk_bytes=%s\n' "${QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES:-default}"
    } > "${RUN_DIR}/campaign_metadata.txt"
    {
        cmake --version | head -n 1
        nvcc --version | tail -n 1
        mpirun --version | head -n 1
        nsys --version | head -n 1
        python3 --version
    } > "${RUN_DIR}/environment/tool_versions.txt" 2>&1
}

build_targets() {
    local markers="$1"
    local benchmark

    export QUEST_BENCH_ENABLE_PROFILING_MARKERS="${markers}"
    for benchmark in gate_micro qft random; do
        run_logged "${EXPERIMENTS_DIR}/build.sh" "${benchmark}" gpu_mpi Release
    done
}

detect_nvcomp_root() {
    local candidate
    for candidate in \
        "${NVCOMP_ROOT:-}" \
        "${CONDA_PREFIX:-}" \
        "${HOME}/miniconda3/envs/quest_compression" \
        "${HOME}/miniconda3/envs/quest_env" \
        "/usr/local"; do
        [ -n "${candidate}" ] || continue
        if [ -f "${candidate}/include/nvcomp.hpp" ]; then
            printf '%s' "${candidate}"
            return 0
        fi
    done
    return 1
}

configure_nvcomp_runtime() {
    local root
    if [ -z "${NVCOMP_ROOT:-}" ] && root="$(detect_nvcomp_root)"; then
        export NVCOMP_ROOT="${root}"
    fi
    if [ -n "${NVCOMP_ROOT:-}" ] && [ -d "${NVCOMP_ROOT}/lib" ]; then
        export LD_LIBRARY_PATH="${NVCOMP_ROOT}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    elif [ -n "${NVCOMP_ROOT:-}" ] && [ -d "${NVCOMP_ROOT}/lib64" ]; then
        export LD_LIBRARY_PATH="${NVCOMP_ROOT}/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    fi
}

configure_compression_environment() {
    case "${QUEST_SCALING_COMPRESSION_MODE:-native}" in
        native)
            ;;
        off)
            configure_nvcomp_runtime
            export QUEST_BENCH_ENABLE_NVCOMP=1
            export QUEST_ENABLE_EXCHANGE_COMPRESSION=0
            export QUEST_EXCHANGE_COMPRESSION_VERIFY=0
            ;;
        on)
            configure_nvcomp_runtime
            export QUEST_BENCH_ENABLE_NVCOMP=1
            export QUEST_ENABLE_EXCHANGE_COMPRESSION=1
            export QUEST_EXCHANGE_COMPRESSION_VERIFY=0
            ;;
        *)
            die "QUEST_SCALING_COMPRESSION_MODE must be native, off, or on."
            ;;
    esac
}

configure_mpirun_prefix() {
    local var
    MPIRUN_PREFIX=(mpirun)
    for var in \
        NVCOMP_ROOT \
        LD_LIBRARY_PATH \
        QUEST_ENABLE_EXCHANGE_COMPRESSION \
        QUEST_EXCHANGE_COMPRESSION_VERIFY \
        QUEST_EXCHANGE_COMPRESSION_MIN_BYTES \
        QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES; do
        if [ -n "${!var:-}" ]; then
            MPIRUN_PREFIX+=(-x "${var}")
        fi
    done
}

make_benchmark_command() {
    local mode="$1"
    local point_id="$2"
    local benchmark="$3"
    local gate_kind="$4"
    local qubits="$5"
    local gate_repeats="$6"
    local random_depth="$7"
    local ratio="$8"
    local seed="$9"
    local output_path="${10}"
    local reps warmup sync_mode

    if [ "${mode}" = "timing" ]; then
        reps=5
        warmup=1
        sync_mode=benchmark
    else
        reps=1
        warmup=0
        sync_mode=profile
    fi

    case "${benchmark}" in
        gate_micro)
            BENCH_COMMAND=(
                "${BUILD_ROOT}/gate_micro/gpu_mpi/gate_micro"
                --distribution on
                --qubits "${qubits}"
                --gate-kind "${gate_kind}"
                --target "$((qubits - 1))"
                --gate-repeats "${gate_repeats}"
                --reps "${reps}"
                --warmup "${warmup}"
                --sync-mode "${sync_mode}"
                --preheat-mode off
                --preheat-qubits "${qubits}"
                --label "${point_id}_${mode}"
                --output "${output_path}"
            )
            if [ "${gate_kind}" = "cnot" ] || [ "${gate_kind}" = "cphase" ]; then
                BENCH_COMMAND+=(--control 0)
            fi
            ;;
        qft)
            BENCH_COMMAND=(
                "${BUILD_ROOT}/qft/gpu_mpi/qft"
                --distribution on
                --qubits "${qubits}"
                --reps "${reps}"
                --warmup "${warmup}"
                --sync-mode "${sync_mode}"
                --preheat-mode off
                --preheat-qubits "${qubits}"
                --label "${point_id}_${mode}"
                --output "${output_path}"
            )
            ;;
        random)
            BENCH_COMMAND=(
                "${BUILD_ROOT}/random/gpu_mpi/random"
                --distribution on
                --qubits "${qubits}"
                --depth "${random_depth}"
                --seed "${seed}"
                --two-qubit-ratio "${ratio}"
                --reps "${reps}"
                --warmup "${warmup}"
                --sync-mode "${sync_mode}"
                --preheat-mode off
                --preheat-qubits "${qubits}"
                --label "${point_id}_${mode}"
                --output "${output_path}"
            )
            ;;
        *)
            die "Unsupported scaling benchmark ${benchmark}."
            ;;
    esac
}

run_timing_points() {
    local point_id benchmark gate_kind qubits ranks gpus slurm_nodes membership source_file profile_point profile_selected
    local gate_repeats random_depth ratio seed output_path

    while IFS=$'\t' read -r point_id benchmark gate_kind qubits ranks gpus slurm_nodes membership source_file profile_point profile_selected gate_repeats random_depth ratio seed <&3; do
        [ "${point_id}" != "point_id" ] || continue
        output_path="${RUN_DIR}/${source_file}"
        make_benchmark_command timing "${point_id}" "${benchmark}" "${gate_kind}" "${qubits}" \
            "${gate_repeats}" "${random_depth}" "${ratio}" "${seed}" "${output_path}"
        capture_gpu_snapshot "${point_id}" before
        run_logged "${MPIRUN_PREFIX[@]}" -np "${ranks}" "${BENCH_COMMAND[@]}"
        capture_gpu_snapshot "${point_id}" after
    done 3< "${POINT_MANIFEST}"
}

run_profile_points() {
    local point_id benchmark gate_kind qubits ranks gpus slurm_nodes membership source_file profile_point profile_selected
    local gate_repeats random_depth ratio seed output_path profile_base sqlite_path

    while IFS=$'\t' read -r point_id benchmark gate_kind qubits ranks gpus slurm_nodes membership source_file profile_point profile_selected gate_repeats random_depth ratio seed <&3; do
        [ "${point_id}" != "point_id" ] || continue
        [ "${profile_selected}" = "1" ] || continue
        output_path="${RUN_DIR}/profiles/${point_id}.tsv"
        profile_base="${RUN_DIR}/profiles/${point_id}"
        sqlite_path="${profile_base}.sqlite"
        make_benchmark_command profile "${point_id}" "${benchmark}" "${gate_kind}" "${qubits}" \
            "${gate_repeats}" "${random_depth}" "${ratio}" "${seed}" "${output_path}"
        capture_gpu_snapshot "profile_${point_id}" before
        record_command nsys profile --trace=cuda,mpi,nvtx,osrt --mpi-impl=openmpi --force-overwrite=true -o "${profile_base}" "${MPIRUN_PREFIX[@]}" -np "${ranks}" "${BENCH_COMMAND[@]}"
        nsys profile \
            --trace=cuda,mpi,nvtx,osrt \
            --mpi-impl=openmpi \
            --force-overwrite=true \
            -o "${profile_base}" \
            "${MPIRUN_PREFIX[@]}" -np "${ranks}" "${BENCH_COMMAND[@]}"
        profile_report_exists "${profile_base}" || die "No Nsight report detected for ${point_id}."
        export_nsys_sqlite "${profile_base}.nsys-rep" "${sqlite_path}"
        run_logged python3 "${SCRIPT_DIR}/profile_breakdown.py" \
            --sqlite "${sqlite_path}" \
            --point "${point_id}" \
            --benchmark "${benchmark}" \
            --num-qubits "${qubits}" \
            --mpi-ranks "${ranks}" \
            --slurm-nodes 1 \
            --gpus "${gpus}" \
            --rank-output "${RUN_DIR}/procedure_breakdown_rank.tsv" \
            --summary-output "${RUN_DIR}/procedure_breakdown.tsv" \
            --communication-output "${RUN_DIR}/communication_breakdown_rank.tsv" \
            --computation-output "${RUN_DIR}/computation_breakdown_rank.tsv" \
            --lifecycle-output "${RUN_DIR}/lifecycle_breakdown_rank.tsv" \
            --runtime-output "${RUN_DIR}/cuda_runtime_summary_rank.tsv"
        capture_gpu_snapshot "profile_${point_id}" after
    done 3< "${POINT_MANIFEST}"
}

write_checksums() {
    (
        cd "${RUN_DIR}"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    )
}

main() {
    local job_id="${SLURM_JOB_ID:-manual}"
    local gpu_type="${QUEST_SCALING_GPU_TYPE:-2080ti}"
    local compression_mode="${QUEST_SCALING_COMPRESSION_MODE:-native}"
    local run_tag="${gpu_type}"

    [ "${SLURM_NNODES:-1}" -eq 1 ] || die "Scaling payload requires exactly one node."
    [ "${SLURM_NTASKS:-4}" -eq 4 ] || die "Scaling payload requires a four-task allocation."
    cd "${REPO_ROOT}"
    ensure_results_dirs
    source_system_profile_if_present
    source_toolchain_env_if_present
    ensure_minimum_cmake 3.21
    ensure_nsys_available
    configure_compression_environment
    configure_mpirun_prefix

    if [ "${compression_mode}" != "native" ]; then
        run_tag="${gpu_type}_${compression_mode}"
    fi
    RUN_DIR="$(current_raw_results_dir)/gpu_mpi_scaling_${run_tag}_${job_id}"
    POINT_MANIFEST="${RUN_DIR}/point_manifest.tsv"
    COMMAND_LOG="${RUN_DIR}/commands.log"
    GPU_SAMPLES="${RUN_DIR}/gpu_samples.tsv"
    export RUN_DIR POINT_MANIFEST COMMAND_LOG GPU_SAMPLES

    mkdir -p "${RUN_DIR}/timing" "${RUN_DIR}/profiles"
    : > "${COMMAND_LOG}"
    printf 'timestamp\tpoint\tphase\tindex\tuuid\tname\ttemperature_gpu_c\tpower_w\tutilization_gpu_pct\tmemory_used_mib\tmemory_total_mib\n' > "${GPU_SAMPLES}"
    write_point_manifest "${POINT_MANIFEST}"
    write_environment_snapshot

    export BENCH_PLATFORM=cluster
    export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
    export OMP_PLACES=cores
    export OMP_PROC_BIND=close

    info "Building timing executables"
    build_targets 0
    info "Running 25 timing points"
    run_timing_points

    info "Building profiling executables"
    build_targets 1
    rm -f \
        "${RUN_DIR}/procedure_breakdown_rank.tsv" \
        "${RUN_DIR}/procedure_breakdown.tsv" \
        "${RUN_DIR}/communication_breakdown_rank.tsv" \
        "${RUN_DIR}/computation_breakdown_rank.tsv" \
        "${RUN_DIR}/lifecycle_breakdown_rank.tsv" \
        "${RUN_DIR}/cuda_runtime_summary_rank.tsv"
    info "Running 13 profile points"
    run_profile_points

    run_logged python3 "${SCRIPT_DIR}/scaling_analysis.py" \
        --campaign-dir "${RUN_DIR}" \
        --manifest "${POINT_MANIFEST}" \
        --validate \
        --expected-reps 5 \
        --expected-warmup 1
    write_checksums
    info "Scaling campaign complete: ${RUN_DIR}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
