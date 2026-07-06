#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMITTER="${SCRIPT_DIR}/submit_t030_p4_batch.sh"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_TEST}"' EXIT

# shellcheck source=/dev/null
source "${SUBMITTER}"

SBATCH_LOG="${TMPDIR_TEST}/sbatch.log"
sbatch() {
    printf '%s\n' "$*" >> "${SBATCH_LOG}"
    case "$(wc -l < "${SBATCH_LOG}" | tr -d ' ')" in
        1) printf '101\n' ;;
        2) printf '102\n' ;;
        3) printf '103\n' ;;
        *) fail "unexpected extra sbatch call" ;;
    esac
}
git() {
    case "$*" in
        "rev-parse HEAD")
            printf 'abcdef123456\n'
            ;;
        *)
            command git "$@"
            ;;
    esac
}

submit_t030_batch "${TMPDIR_TEST}/manifest.tsv" >/tmp/t030_submit_output.txt

jobs=()
while IFS= read -r line; do
    jobs+=("${line}")
done < "${SBATCH_LOG}"
[ "${#jobs[@]}" -eq 3 ] || fail "expected 3 sbatch submissions, got ${#jobs[@]}"
printf '%s\n' "${jobs[0]}" | grep -q -- '--job-name=t030-p4-smoke' || fail "smoke job name missing: ${jobs[0]}"
printf '%s\n' "${jobs[0]}" | grep -q -- '--gres=gpu:nvidia_geforce_rtx_2080_ti:2' || fail "smoke job did not request 2 RTX 2080 Ti GPUs: ${jobs[0]}"
printf '%s\n' "${jobs[1]}" | grep -q -- '--dependency=afterok:101' || fail "off job dependency missing: ${jobs[1]}"
printf '%s\n' "${jobs[1]}" | grep -q 'QUEST_SCALING_COMPRESSION_MODE=off' || fail "off job mode missing: ${jobs[1]}"
printf '%s\n' "${jobs[1]}" | grep -q 'QUEST_SCALING_GPU_CHOICE=2080ti' || fail "off job GPU choice missing: ${jobs[1]}"
printf '%s\n' "${jobs[2]}" | grep -q -- '--dependency=afterok:102' || fail "on job dependency missing: ${jobs[2]}"
printf '%s\n' "${jobs[2]}" | grep -q 'QUEST_SCALING_COMPRESSION_MODE=on' || fail "on job mode missing: ${jobs[2]}"
printf '%s\n' "${jobs[2]}" | grep -q 'QUEST_SCALING_GPU_CHOICE=2080ti' || fail "on job GPU choice missing: ${jobs[2]}"

grep -q $'smoke_job_id\t101' "${TMPDIR_TEST}/manifest.tsv" || fail "manifest missing smoke job id"
grep -q 'collect_cluster_gpu_mpi_scaling.sh 102' /tmp/t030_submit_output.txt || fail "output missing off collector command"
grep -q 'collect_cluster_gpu_mpi_scaling.sh 103' /tmp/t030_submit_output.txt || fail "output missing on collector command"

printf 'T030 batch submit tests passed.\n'
