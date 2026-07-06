#!/usr/bin/env bash
# Submit the complete T-030 P4 Slurm batch: smoke -> compression-off -> compression-on.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

T030_ACCOUNT="${QUEST_T030_ACCOUNT:-general-teaching}"
T030_QOS="${QUEST_T030_QOS:-teaching}"
T030_PARTITION="${QUEST_T030_PARTITION:-Interactive}"
T030_GPU_GRES="${QUEST_T030_GPU_GRES:-gpu:nvidia_geforce_rtx_2080_ti}"
T030_FULL_WALLTIME="${QUEST_T030_FULL_WALLTIME:-03:30:00}"
T030_SMOKE_WALLTIME="${QUEST_T030_SMOKE_WALLTIME:-00:30:00}"
T030_CPUS_PER_TASK="${QUEST_T030_CPUS_PER_TASK:-2}"
T030_BUILD_PARALLEL="${QUEST_BENCH_BUILD_PARALLEL:-8}"

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  bash experiments/scripts/submit_t030_p4_batch.sh [manifest.tsv]

Submits three jobs immediately:
  smoke -> compression-off -> compression-on
All jobs force RTX 2080 Ti resources.
EOF
}

full_export_vars() {
    local mode="$1"
    local commit="$2"
    local enabled=0

    [ "${mode}" = "on" ] && enabled=1
    printf 'ALL,BENCH_PLATFORM=cluster'
    printf ',QUEST_SCALING_PARTITION=%s' "${T030_PARTITION}"
    printf ',QUEST_SCALING_GPU_TYPE=2080ti'
    printf ',QUEST_SCALING_GPU_GRES=%s' "${T030_GPU_GRES}"
    printf ',QUEST_SCALING_GPU_CHOICE=2080ti'
    printf ',QUEST_SCALING_COMPRESSION_MODE=%s' "${mode}"
    printf ',QUEST_SCALING_GIT_COMMIT=%s' "${commit}"
    printf ',QUEST_BENCH_BUILD_PARALLEL=%s' "${T030_BUILD_PARALLEL}"
    printf ',QUEST_BENCH_ENABLE_NVCOMP=1'
    printf ',QUEST_ENABLE_EXCHANGE_COMPRESSION=%s' "${enabled}"
    printf ',QUEST_EXCHANGE_COMPRESSION_VERIFY=0'
}

submit_t030_batch() {
    local manifest_path="${1:-experiments/results/raw/t030_p4_batch_manifest.tsv}"
    local smoke_payload="${SCRIPT_DIR}/sbatch_t030_p4_smoke.sh"
    local full_payload="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_scaling_point.sh"
    local commit
    local smoke_job off_job on_job

    cd "${REPO_ROOT}"
    mkdir -p "$(dirname "${manifest_path}")" experiments/results/raw
    [ -f "${smoke_payload}" ] || die "missing smoke payload: ${smoke_payload}"
    [ -f "${full_payload}" ] || die "missing scaling payload: ${full_payload}"
    commit="$(git rev-parse HEAD)"

    smoke_job="$(
        sbatch --parsable \
            --account="${T030_ACCOUNT}" \
            --qos="${T030_QOS}" \
            --partition="${T030_PARTITION}" \
            --gres="${T030_GPU_GRES}:2" \
            --nodes=1 \
            --ntasks=2 \
            --ntasks-per-node=2 \
            --cpus-per-task="${T030_CPUS_PER_TASK}" \
            --time="${T030_SMOKE_WALLTIME}" \
            --job-name=t030-p4-smoke \
            --output=experiments/results/raw/t030_p4_smoke_%j.out \
            --error=experiments/results/raw/t030_p4_smoke_%j.err \
            --export="ALL,QUEST_SCALING_GIT_COMMIT=${commit},QUEST_BENCH_BUILD_PARALLEL=${T030_BUILD_PARALLEL}" \
            "${smoke_payload}"
    )"
    smoke_job="${smoke_job%%;*}"

    off_job="$(
        sbatch --parsable \
            --dependency="afterok:${smoke_job}" \
            --account="${T030_ACCOUNT}" \
            --qos="${T030_QOS}" \
            --partition="${T030_PARTITION}" \
            --gres="${T030_GPU_GRES}:4" \
            --nodes=1 \
            --ntasks=4 \
            --ntasks-per-node=4 \
            --cpus-per-task="${T030_CPUS_PER_TASK}" \
            --time="${T030_FULL_WALLTIME}" \
            --job-name=t030-p4-off \
            --output=experiments/results/raw/gpu_mpi_scaling_2080ti_off_%j.out \
            --error=experiments/results/raw/gpu_mpi_scaling_2080ti_off_%j.err \
            --export="$(full_export_vars off "${commit}")" \
            "${full_payload}"
    )"
    off_job="${off_job%%;*}"

    on_job="$(
        sbatch --parsable \
            --dependency="afterok:${off_job}" \
            --account="${T030_ACCOUNT}" \
            --qos="${T030_QOS}" \
            --partition="${T030_PARTITION}" \
            --gres="${T030_GPU_GRES}:4" \
            --nodes=1 \
            --ntasks=4 \
            --ntasks-per-node=4 \
            --cpus-per-task="${T030_CPUS_PER_TASK}" \
            --time="${T030_FULL_WALLTIME}" \
            --job-name=t030-p4-on \
            --output=experiments/results/raw/gpu_mpi_scaling_2080ti_on_%j.out \
            --error=experiments/results/raw/gpu_mpi_scaling_2080ti_on_%j.err \
            --export="$(full_export_vars on "${commit}")" \
            "${full_payload}"
    )"
    on_job="${on_job%%;*}"

    {
        printf 'key\tvalue\n'
        printf 'git_commit\t%s\n' "${commit}"
        printf 'smoke_job_id\t%s\n' "${smoke_job}"
        printf 'compression_off_job_id\t%s\n' "${off_job}"
        printf 'compression_on_job_id\t%s\n' "${on_job}"
        printf 'partition\t%s\n' "${T030_PARTITION}"
        printf 'gpu_gres\t%s\n' "${T030_GPU_GRES}"
    } > "${manifest_path}"

    printf 'Submitted T-030 P4 batch:\n'
    printf '  smoke: %s\n' "${smoke_job}"
    printf '  off:   %s (afterok:%s)\n' "${off_job}" "${smoke_job}"
    printf '  on:    %s (afterok:%s)\n' "${on_job}" "${off_job}"
    printf '\nMonitor:\n'
    printf '  squeue -j %s,%s,%s\n' "${smoke_job}" "${off_job}" "${on_job}"
    printf '\nCollect after completion:\n'
    printf '  bash experiments/scripts/collect_cluster_gpu_mpi_scaling.sh %s\n' "${off_job}"
    printf '  bash experiments/scripts/collect_cluster_gpu_mpi_scaling.sh %s\n' "${on_job}"
    printf '\nManifest: %s\n' "${manifest_path}"
}

main() {
    case "${1:-}" in
        -h|--help)
            usage
            return 0
            ;;
        *)
            submit_t030_batch "${1:-experiments/results/raw/t030_p4_batch_manifest.tsv}"
            ;;
    esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
