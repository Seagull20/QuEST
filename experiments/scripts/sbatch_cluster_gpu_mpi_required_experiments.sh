#!/usr/bin/env bash
# Submit proposal-required follow-up experiments on one-node RTX 2080 Ti allocations.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

REQUIRED_ACCOUNT="${QUEST_REQUIRED_ACCOUNT:-general-teaching}"
REQUIRED_QOS="${QUEST_REQUIRED_QOS:-teaching}"
REQUIRED_PARTITION="${QUEST_REQUIRED_PARTITION:-Interactive}"
REQUIRED_GRES="gpu:nvidia_geforce_rtx_2080_ti"
REQUIRED_GPUS=4
REQUIRED_CPUS_PER_TASK=2
REQUIRED_WALLTIME="${QUEST_REQUIRED_WALLTIME:-02:00:00}"
SUBMITTED_JOB_ID=""

usage() {
    cat <<'EOF'
Usage:
  bash experiments/scripts/sbatch_cluster_gpu_mpi_required_experiments.sh [--dry-run] all
  bash experiments/scripts/sbatch_cluster_gpu_mpi_required_experiments.sh [--dry-run] repro
  bash experiments/scripts/sbatch_cluster_gpu_mpi_required_experiments.sh [--dry-run] qft-sweep
  bash experiments/scripts/sbatch_cluster_gpu_mpi_required_experiments.sh [--dry-run] gate-path

The all mode submits three independent QFT reproducibility allocations, one
QFT q24-q29 sweep, and one matched H/CPhase timing/profile allocation.
EOF
}

ensure_no_running_gpu_allocation() {
    local allocations
    allocations="$(squeue -h -u "${USER:-$(id -un)}" -t RUNNING,COMPLETING -o '%b' 2>/dev/null || true)"
    ! printf '%s\n' "${allocations}" | grep -Eq '(^|[,[:space:]])gres/gpu(:|=)'
}

preflight_resource() {
    sbatch --test-only \
        --account="${REQUIRED_ACCOUNT}" --qos="${REQUIRED_QOS}" --partition="${REQUIRED_PARTITION}" \
        --nodes=1 --ntasks=4 --ntasks-per-node=4 --cpus-per-task=2 \
        --gres="${REQUIRED_GRES}:4" --time="${REQUIRED_WALLTIME}" --wrap=/bin/true
}

campaign_dir_default() {
    printf '%s/required_experiments_2080ti_%s' "$(current_raw_results_dir)" "$(date +%Y%m%d_%H%M%S)"
}

submit_one() {
    local mode="$1" allocation="$2" dependency="$3" nodelist="$4"
    local payload="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_required_experiment_point.sh"
    local job_name="quest-required-${mode}-${allocation}"
    local output_file="${QUEST_REQUIRED_CAMPAIGN_DIR}/submission-${mode}-${allocation}.jobid"
    local export_vars="ALL,BENCH_PLATFORM=cluster,QUEST_REQUIRED_MODE=${mode},QUEST_REQUIRED_ALLOCATION=${allocation}"
    local args=()

    export_vars="${export_vars},QUEST_REQUIRED_CAMPAIGN_DIR=${QUEST_REQUIRED_CAMPAIGN_DIR}"
    export_vars="${export_vars},QUEST_REQUIRED_GIT_COMMIT=$(git rev-parse HEAD)"
    export_vars="${export_vars},QUEST_BENCH_BUILD_PARALLEL=${QUEST_BENCH_BUILD_PARALLEL:-8}"
    [ -z "${dependency}" ] || args+=("--dependency=afterok:${dependency}")
    [ -z "${nodelist}" ] || args+=("--nodelist=${nodelist}")

    sbatch --parsable \
        --account="${REQUIRED_ACCOUNT}" --qos="${REQUIRED_QOS}" --partition="${REQUIRED_PARTITION}" \
        --gres="${REQUIRED_GRES}:${REQUIRED_GPUS}" --nodes=1 --ntasks=4 --ntasks-per-node=4 \
        --cpus-per-task="${REQUIRED_CPUS_PER_TASK}" --time="${REQUIRED_WALLTIME}" \
        --job-name="${job_name}" \
        --output="${QUEST_REQUIRED_CAMPAIGN_DIR}/slurm/${job_name}_%j.out" \
        --error="${QUEST_REQUIRED_CAMPAIGN_DIR}/slurm/${job_name}_%j.err" \
        --export="${export_vars}" "${args[@]}" "${payload}" > "${output_file}"
    SUBMITTED_JOB_ID="$(cut -d ';' -f 1 < "${output_file}")"
    rm -f "${output_file}"
    printf '%s\t%s\t%s\t%s\t%s\t%s_%s_%s\tSUBMITTED\n' \
        "${mode}" "${allocation}" "${SUBMITTED_JOB_ID}" "${nodelist}" "${dependency}" \
        "${mode}" "${allocation}" "${SUBMITTED_JOB_ID}" >> "${QUEST_REQUIRED_CAMPAIGN_DIR}/submission_manifest.tsv"
    info "Submitted ${mode}/${allocation}: ${SUBMITTED_JOB_ID}"
}

submit_all() {
    local previous=""
    mkdir -p "${QUEST_REQUIRED_CAMPAIGN_DIR}/slurm"
    printf 'mode\tallocation\tjob_id\tnode\tdependency\trun_dir\tstate\n' > "${QUEST_REQUIRED_CAMPAIGN_DIR}/submission_manifest.tsv"

    submit_one repro 1 "${previous}" landonia01; previous="${SUBMITTED_JOB_ID}"
    submit_one repro 2 "${previous}" landonia02; previous="${SUBMITTED_JOB_ID}"
    submit_one repro 3 "${previous}" ""; previous="${SUBMITTED_JOB_ID}"
    submit_one qft-sweep 0 "${previous}" ""; previous="${SUBMITTED_JOB_ID}"
    submit_one gate-path 0 "${previous}" ""; previous="${SUBMITTED_JOB_ID}"
    printf '%s\n' "${previous}" > "${QUEST_REQUIRED_CAMPAIGN_DIR}/final_job_id.txt"
    info "Campaign directory: ${QUEST_REQUIRED_CAMPAIGN_DIR}"
    info "Final dependency job: ${previous}"
}

submit_single_mode() {
    local mode="$1"
    mkdir -p "${QUEST_REQUIRED_CAMPAIGN_DIR}/slurm"
    printf 'mode\tallocation\tjob_id\tnode\tdependency\trun_dir\tstate\n' > "${QUEST_REQUIRED_CAMPAIGN_DIR}/submission_manifest.tsv"
    submit_one "${mode}" 0 "" ""
    printf '%s\n' "${SUBMITTED_JOB_ID}" > "${QUEST_REQUIRED_CAMPAIGN_DIR}/final_job_id.txt"
}

main() {
    local dry_run=0 mode=""
    if [ "${1:-}" = --dry-run ]; then
        dry_run=1
        shift
    fi
    mode="${1:-all}"
    case "${mode}" in all|repro|qft-sweep|gate-path) ;; -h|--help) usage; return 0 ;; *) usage >&2; die "Unknown mode ${mode}." ;; esac

    cd "${REPO_ROOT}"
    ensure_results_dirs
    ensure_no_running_gpu_allocation || die "Release the current GPU allocation before submitting this campaign."
    preflight_resource
    [ "${dry_run}" -eq 0 ] || return 0
    QUEST_REQUIRED_CAMPAIGN_DIR="${QUEST_REQUIRED_CAMPAIGN_DIR:-$(campaign_dir_default)}"
    export QUEST_REQUIRED_CAMPAIGN_DIR
    if [ "${mode}" = all ]; then
        submit_all
    else
        submit_single_mode "${mode}"
    fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
