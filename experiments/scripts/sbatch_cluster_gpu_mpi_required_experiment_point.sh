#!/usr/bin/env bash
# Slurm payload for one required follow-up experiment allocation.
#SBATCH --job-name=quest-required-experiment
#SBATCH --time=02:00:00

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SCRIPT_DIR="${REPO_ROOT}/experiments/scripts"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

write_point_manifest() {
    local mode="$1"
    local path="$2"
    local ranks qubits gate point_id repeats

    printf 'point_id\tbenchmark\tgate_kind\tnum_qubits\tmpi_ranks\tgpus\tslurm_nodes\tsource_file\tprofile_point\tprofile_selected\tgate_repeats\n' > "${path}"
    case "${mode}" in
        repro)
            for ranks in 1 2 4; do
                point_id="qft_p${ranks}_q28"
                printf '%s\tqft\tnone\t28\t%s\t%s\t1\ttiming/%s.tsv\t%s\t0\t0\n' \
                    "${point_id}" "${ranks}" "${ranks}" "${point_id}" "${point_id}" >> "${path}"
            done
            ;;
        qft-sweep)
            for qubits in 24 25 26 27 28 29; do
                for ranks in 1 2 4; do
                    point_id="qft_p${ranks}_q${qubits}"
                    printf '%s\tqft\tnone\t%s\t%s\t%s\t1\ttiming/%s.tsv\t%s\t0\t0\n' \
                        "${point_id}" "${qubits}" "${ranks}" "${ranks}" "${point_id}" "${point_id}" >> "${path}"
                done
            done
            ;;
        gate-path)
            for gate in h cphase; do
                for ranks in 1 4; do
                    point_id="${gate}_p${ranks}_q28"
                    if [ "${gate}" = h ]; then
                        repeats=16
                    else
                        repeats=256
                    fi
                    printf '%s\tgate_micro\t%s\t28\t%s\t%s\t1\ttiming/%s.tsv\t%s\t1\t%s\n' \
                        "${point_id}" "${gate}" "${ranks}" "${ranks}" "${point_id}" "${point_id}" "${repeats}" >> "${path}"
                done
            done
            ;;
        *)
            die "Unsupported required experiment mode: ${mode}"
            ;;
    esac
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
    local timestamp row

    command -v nvidia-smi >/dev/null 2>&1 || return 0
    timestamp="$(date --iso-8601=seconds)"
    while IFS= read -r row; do
        row="${row//, /$'\t'}"
        printf '%s\t%s\t%s\t%s\n' "${timestamp}" "${point}" "${phase}" "${row}" >> "${GPU_SAMPLES}"
    done < <(nvidia-smi --query-gpu=index,uuid,name,temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits)
}

write_environment_snapshot() {
    local env_dir="${RUN_DIR}/environment"
    mkdir -p "${env_dir}"
    printf '%s\n' "${QUEST_REQUIRED_GIT_COMMIT:-unavailable}" > "${env_dir}/git_commit.txt"
    git status --short --branch > "${env_dir}/git_status.txt" 2>&1 || true
    scontrol show job "${SLURM_JOB_ID}" -o > "${env_dir}/scontrol_job.txt" 2>&1 || true
    sacctmgr -n -P show qos name=teaching format=Name,Priority,MaxTRESPJ,MaxTRESPU,GrpTRES,MaxJobsPU,MaxSubmitJobsPU > "${env_dir}/slurm_qos.tsv" 2>&1 || true
    module -t list > "${env_dir}/modules.txt" 2>&1 || true
    nvidia-smi -L > "${env_dir}/nvidia_smi_list.txt" 2>&1 || true
    nvidia-smi topo -m > "${env_dir}/nvidia_smi_topology.txt" 2>&1 || true
    nvidia-smi -q > "${env_dir}/nvidia_smi_query.txt" 2>&1 || true
    {
        printf 'mode=%s\n' "${QUEST_REQUIRED_MODE}"
        printf 'allocation=%s\n' "${QUEST_REQUIRED_ALLOCATION:-0}"
        printf 'hostname=%s\n' "$(hostname)"
        printf 'slurm_job_id=%s\n' "${SLURM_JOB_ID}"
        printf 'slurm_nodes=%s\n' "${SLURM_NNODES:-1}"
        printf 'slurm_ntasks=%s\n' "${SLURM_NTASKS:-4}"
        printf 'gpu_type=2080ti\n'
        printf 'build_type=Release\n'
        printf 'build_parallel=%s\n' "${QUEST_BENCH_BUILD_PARALLEL:-8}"
        printf 'precision=double\n'
        printf 'timing_reps=10\n'
        printf 'timing_warmup=1\n'
        printf 'profile_reps=1\n'
        printf 'profile_warmup=0\n'
    } > "${RUN_DIR}/campaign_metadata.txt"
    {
        cmake --version | head -n 1
        nvcc --version | tail -n 1
        mpirun --version | head -n 1
        command -v nsys >/dev/null 2>&1 && nsys --version | head -n 1 || true
        python3 --version
    } > "${env_dir}/tool_versions.txt" 2>&1
}

make_benchmark_command() {
    local mode="$1" point_id="$2" benchmark="$3" gate_kind="$4" qubits="$5" gate_repeats="$6" output_path="$7"
    local reps warmup sync_mode
    if [ "${mode}" = timing ]; then
        reps=10
        warmup=1
        sync_mode=benchmark
    else
        reps=1
        warmup=0
        sync_mode=profile
    fi

    if [ "${benchmark}" = qft ]; then
        BENCH_COMMAND=(
            "${BUILD_ROOT}/qft/gpu_mpi/qft"
            --distribution on --qubits "${qubits}"
            --reps "${reps}" --warmup "${warmup}"
            --sync-mode "${sync_mode}" --preheat-mode off --preheat-qubits "${qubits}"
            --label "${point_id}_${mode}" --output "${output_path}"
        )
    else
        BENCH_COMMAND=(
            "${BUILD_ROOT}/gate_micro/gpu_mpi/gate_micro"
            --distribution on --qubits "${qubits}" --gate-kind "${gate_kind}"
            --target "$((qubits - 1))" --gate-repeats "${gate_repeats}"
            --reps "${reps}" --warmup "${warmup}"
            --sync-mode "${sync_mode}" --preheat-mode off --preheat-qubits "${qubits}"
            --label "${point_id}_${mode}" --output "${output_path}"
        )
        [ "${gate_kind}" != cphase ] || BENCH_COMMAND+=(--control 0)
    fi
}

run_timing_points() {
    local point_id benchmark gate_kind qubits ranks gpus slurm_nodes source_file profile_point profile_selected gate_repeats output_path
    while IFS=$'\t' read -r point_id benchmark gate_kind qubits ranks gpus slurm_nodes source_file profile_point profile_selected gate_repeats <&3; do
        [ "${point_id}" != point_id ] || continue
        output_path="${RUN_DIR}/${source_file}"
        make_benchmark_command timing "${point_id}" "${benchmark}" "${gate_kind}" "${qubits}" "${gate_repeats}" "${output_path}"
        capture_gpu_snapshot "${point_id}" before
        run_logged mpirun -np "${ranks}" "${BENCH_COMMAND[@]}"
        capture_gpu_snapshot "${point_id}" after
    done 3< "${POINT_MANIFEST}"
}

run_profile_points() {
    local point_id benchmark gate_kind qubits ranks gpus slurm_nodes source_file profile_point profile_selected gate_repeats
    local output_path profile_base sqlite_path
    while IFS=$'\t' read -r point_id benchmark gate_kind qubits ranks gpus slurm_nodes source_file profile_point profile_selected gate_repeats <&3; do
        [ "${point_id}" != point_id ] || continue
        [ "${profile_selected}" = 1 ] || continue
        output_path="${RUN_DIR}/profiles/${point_id}.tsv"
        profile_base="${RUN_DIR}/profiles/${point_id}"
        sqlite_path="${profile_base}.sqlite"
        make_benchmark_command profile "${point_id}" "${benchmark}" "${gate_kind}" "${qubits}" "${gate_repeats}" "${output_path}"
        record_command nsys profile --trace=cuda,mpi,nvtx,osrt --mpi-impl=openmpi --force-overwrite=true -o "${profile_base}" mpirun -np "${ranks}" "${BENCH_COMMAND[@]}"
        nsys profile --trace=cuda,mpi,nvtx,osrt --mpi-impl=openmpi --force-overwrite=true -o "${profile_base}" mpirun -np "${ranks}" "${BENCH_COMMAND[@]}"
        [ -s "${profile_base}.nsys-rep" ] || die "No Nsight report for ${point_id}."
        export_nsys_sqlite "${profile_base}.nsys-rep" "${sqlite_path}"
        run_logged python3 "${SCRIPT_DIR}/profile_breakdown.py" \
            --sqlite "${sqlite_path}" --point "${point_id}" --benchmark "${benchmark}" \
            --num-qubits "${qubits}" --mpi-ranks "${ranks}" --slurm-nodes 1 --gpus "${gpus}" \
            --rank-output "${RUN_DIR}/procedure_breakdown_rank.tsv" \
            --summary-output "${RUN_DIR}/procedure_breakdown.tsv" \
            --communication-output "${RUN_DIR}/communication_breakdown_rank.tsv" \
            --computation-output "${RUN_DIR}/computation_breakdown_rank.tsv" \
            --lifecycle-output "${RUN_DIR}/lifecycle_breakdown_rank.tsv" \
            --runtime-output "${RUN_DIR}/cuda_runtime_summary_rank.tsv"
    done 3< "${POINT_MANIFEST}"
}

build_for_mode() {
    local markers="$1"
    export QUEST_BENCH_ENABLE_PROFILING_MARKERS="${markers}"
    if [ "${QUEST_REQUIRED_MODE}" = gate-path ]; then
        run_logged "${EXPERIMENTS_DIR}/build.sh" gate_micro gpu_mpi Release
    else
        run_logged "${EXPERIMENTS_DIR}/build.sh" qft gpu_mpi Release
    fi
}

write_checksums() {
    (
        cd "${RUN_DIR}"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    )
}

main() {
    local mode="${QUEST_REQUIRED_MODE:-}"
    local job_id="${SLURM_JOB_ID:-manual}"
    local run_name

    case "${mode}" in repro|qft-sweep|gate-path) ;; *) die "QUEST_REQUIRED_MODE must be repro, qft-sweep, or gate-path." ;; esac
    [ "${SLURM_NNODES:-1}" -eq 1 ] || die "Required experiments are intra-node only."
    [ "${SLURM_NTASKS:-4}" -eq 4 ] || die "Required experiments need a four-task allocation."

    cd "${REPO_ROOT}"
    source_system_profile_if_present
    source_toolchain_env_if_present
    ensure_minimum_cmake 3.21
    if [ "${mode}" = gate-path ]; then
        ensure_nsys_available
    fi

    run_name="${mode}_${QUEST_REQUIRED_ALLOCATION:-0}_${job_id}"
    RUN_DIR="${QUEST_REQUIRED_CAMPAIGN_DIR}/${run_name}"
    POINT_MANIFEST="${RUN_DIR}/point_manifest.tsv"
    COMMAND_LOG="${RUN_DIR}/commands.log"
    GPU_SAMPLES="${RUN_DIR}/gpu_samples.tsv"
    export RUN_DIR POINT_MANIFEST COMMAND_LOG GPU_SAMPLES
    mkdir -p "${RUN_DIR}/timing" "${RUN_DIR}/profiles"
    : > "${COMMAND_LOG}"
    printf 'timestamp\tpoint\tphase\tindex\tuuid\tname\ttemperature_gpu_c\tpower_w\tutilization_gpu_pct\tmemory_used_mib\tmemory_total_mib\n' > "${GPU_SAMPLES}"
    write_point_manifest "${mode}" "${POINT_MANIFEST}"
    write_environment_snapshot

    export BENCH_PLATFORM=cluster OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}" OMP_PLACES=cores OMP_PROC_BIND=close
    build_for_mode 0
    run_timing_points
    if [ "${mode}" = gate-path ]; then
        build_for_mode 1
        run_profile_points
    fi
    write_checksums
    info "Required experiment complete: ${RUN_DIR}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
