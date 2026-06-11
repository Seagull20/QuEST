#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAYLOAD="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_scaling_point.sh"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_TEST}"' EXIT

cp "${PAYLOAD}" "${TMPDIR_TEST}/slurm_script"
SLURM_SUBMIT_DIR="${REPO_ROOT}" bash -c '
    source "$1"
    [ "${SCRIPT_DIR}" = "$2/experiments/scripts" ]
' bash "${TMPDIR_TEST}/slurm_script" "${REPO_ROOT}"

# shellcheck source=/dev/null
source "${PAYLOAD}"
git() { return 1; }
export QUEST_SCALING_GIT_COMMIT=0123456789abcdef
mkdir -p "${TMPDIR_TEST}/git-environment"
write_git_snapshot "${TMPDIR_TEST}/git-environment"
[ "$(cat "${TMPDIR_TEST}/git-environment/git_commit.txt")" = "0123456789abcdef" ] || {
    echo "exported commit was not preserved when git was unavailable" >&2
    exit 1
}
write_point_manifest "${TMPDIR_TEST}/point_manifest.tsv"

rows="$(awk 'END {print NR - 1}' "${TMPDIR_TEST}/point_manifest.tsv")"
shared="$(awk -F '\t' 'NR > 1 && $8 == "strong,weak" {count++} END {print count + 0}' "${TMPDIR_TEST}/point_manifest.tsv")"
profiles="$(awk -F '\t' 'NR > 1 && $11 == "1" {count++} END {print count + 0}' "${TMPDIR_TEST}/point_manifest.tsv")"
qft_profiles="$(awk -F '\t' 'NR > 1 && $2 == "qft" && $11 == "1" {count++} END {print count + 0}' "${TMPDIR_TEST}/point_manifest.tsv")"
h_profiles="$(awk -F '\t' 'NR > 1 && $3 == "h" && $11 == "1" {count++} END {print count + 0}' "${TMPDIR_TEST}/point_manifest.tsv")"
random_profiles="$(awk -F '\t' 'NR > 1 && $2 == "random" && $11 == "1" {count++} END {print count + 0}' "${TMPDIR_TEST}/point_manifest.tsv")"
empty_fields="$(awk -F '\t' 'NR > 1 {for (i = 1; i <= NF; i++) if ($i == "") count++} END {print count + 0}' "${TMPDIR_TEST}/point_manifest.tsv")"

[ "${rows}" -eq 25 ] || { echo "expected 25 timing points, got ${rows}" >&2; exit 1; }
[ "${shared}" -eq 5 ] || { echo "expected 5 shared endpoints, got ${shared}" >&2; exit 1; }
[ "${profiles}" -eq 11 ] || { echo "expected 11 profile points, got ${profiles}" >&2; exit 1; }
[ "${qft_profiles}" -eq 5 ] || { echo "expected 5 QFT profiles, got ${qft_profiles}" >&2; exit 1; }
[ "${h_profiles}" -eq 3 ] || { echo "expected 3 H profiles, got ${h_profiles}" >&2; exit 1; }
[ "${random_profiles}" -eq 3 ] || { echo "expected 3 random profiles, got ${random_profiles}" >&2; exit 1; }
[ "${empty_fields}" -eq 0 ] || { echo "manifest contains ${empty_fields} empty fields" >&2; exit 1; }

printf 'Scaling payload matrix tests passed.\n'
