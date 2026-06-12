#!/usr/bin/env bash
# Finalize and checksum one required-experiments campaign on the cluster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

main() {
    local campaign_dir="${1:-}"
    local manifest temp_manifest state job_id run_dir node
    [ -n "${campaign_dir}" ] || die "Usage: bash experiments/scripts/collect_cluster_gpu_mpi_required_experiments.sh CAMPAIGN_DIR"
    [ -d "${campaign_dir}" ] || die "Campaign directory not found: ${campaign_dir}"
    campaign_dir="$(cd "${campaign_dir}" && pwd)"
    manifest="${campaign_dir}/submission_manifest.tsv"
    [ -f "${manifest}" ] || die "Submission manifest missing: ${manifest}"

    temp_manifest="${manifest}.tmp"
    printf 'mode\tallocation\tjob_id\tnode\tdependency\trun_dir\tstate\n' > "${temp_manifest}"
    while IFS=$'\t' read -r mode allocation job_id node dependency run_dir old_state; do
        [ "${mode}" != mode ] || continue
        state="$(sacct -n -j "${job_id}" --format=JobIDRaw,State -P | awk -F '|' -v id="${job_id}" '$1 == id {print $2; exit}')"
        case "${state}" in
            COMPLETED) ;;
            RUNNING|PENDING|CONFIGURING|COMPLETING|SUSPENDED) die "Job ${job_id} is not terminal: ${state}" ;;
            *) die "Job ${job_id} did not complete successfully: ${state:-unknown}" ;;
        esac
        run_dir="$(find "${campaign_dir}" -maxdepth 1 -type d -name "${mode}_${allocation}_${job_id}" -exec basename {} \;)"
        [ -n "${run_dir}" ] || die "Run directory missing for job ${job_id}."
        node="$(awk -F= '$1 == "hostname" {print $2}' "${campaign_dir}/${run_dir}/campaign_metadata.txt")"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${mode}" "${allocation}" "${job_id}" "${node}" "${dependency}" "${run_dir}" "${state}" >> "${temp_manifest}"
    done < "${manifest}"
    mv "${temp_manifest}" "${manifest}"

    python3 "${SCRIPT_DIR}/required_experiments_analysis.py" --campaign-dir "${campaign_dir}" --validate
    sacct -j "$(awk -F '\t' 'NR > 1 {ids = ids (ids ? "," : "") $3} END {print ids}' "${manifest}")" \
        --format=JobIDRaw,JobName,Partition,Account,QOS,State,ExitCode,Elapsed,AllocTRES,NodeList -P \
        > "${campaign_dir}/sacct_final.tsv"
    (
        cd "${campaign_dir}"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
        sha256sum -c SHA256SUMS >/dev/null
    )
    info "Collected required experiments: ${campaign_dir}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
