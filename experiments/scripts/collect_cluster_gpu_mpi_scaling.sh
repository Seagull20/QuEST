#!/usr/bin/env bash
# Finalize one completed scaling campaign inside the cluster repository.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

write_campaign_checksums() {
    local campaign_dir="$1"
    (
        cd "${campaign_dir}"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    )
}

resolve_campaign_dir() {
    local job_id="$1"
    local matches=()

    while IFS= read -r path; do
        matches+=("${path}")
    done < <(find "$(current_raw_results_dir)" -maxdepth 1 -type d -name "gpu_mpi_scaling_*_${job_id}" -print)
    [ "${#matches[@]}" -eq 1 ] || die "Expected one campaign directory for job ${job_id}, found ${#matches[@]}."
    printf '%s' "${matches[0]}"
}

main() {
    local job_id="${1:-}"
    local campaign_dir
    local gpu_type
    local state
    local log_prefix

    [ -n "${job_id}" ] || die "Usage: bash experiments/scripts/collect_cluster_gpu_mpi_scaling.sh JOB_ID"
    cd "${REPO_ROOT}"
    ensure_results_dirs
    campaign_dir="$(resolve_campaign_dir "${job_id}")"
    gpu_type="$(basename "${campaign_dir}" | sed -E "s/^gpu_mpi_scaling_(.*)_${job_id}$/\1/")"
    state="$(sacct -n -j "${job_id}" --format=JobIDRaw,State -P | awk -F '|' -v id="${job_id}" '$1 == id {print $2; exit}')"
    case "${state}" in
        RUNNING|PENDING|CONFIGURING|COMPLETING|SUSPENDED)
            die "Job ${job_id} is not terminal: ${state}."
            ;;
        "")
            die "No accounting state found for job ${job_id}."
            ;;
    esac

    log_prefix="$(current_raw_results_dir)/gpu_mpi_scaling_${gpu_type}_${job_id}"
    [ -f "${log_prefix}.out" ] && cp "${log_prefix}.out" "${campaign_dir}/slurm.out"
    [ -f "${log_prefix}.err" ] && cp "${log_prefix}.err" "${campaign_dir}/slurm.err"
    sacct -j "${job_id}" --format=JobIDRaw,JobName,Partition,Account,QOS,State,ExitCode,Elapsed,AllocTRES,NodeList -P \
        > "${campaign_dir}/environment/sacct_final.tsv"

    if [ -f "${campaign_dir}/point_manifest.tsv" ]; then
        python3 "${SCRIPT_DIR}/scaling_analysis.py" \
            --campaign-dir "${campaign_dir}" \
            --manifest "${campaign_dir}/point_manifest.tsv"
    fi
    write_campaign_checksums "${campaign_dir}"
    info "Collected campaign: ${campaign_dir}"
    info "Job state: ${state}"
    if [ "${state}" != "COMPLETED" ]; then
        warn "Campaign was collected from non-success state ${state}."
        return 1
    fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
