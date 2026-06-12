#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_required_experiments.sh"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

# shellcheck source=/dev/null
source "${LAUNCHER}"

calls=()
sbatch() {
    calls+=("$*")
    printf '%s\n' "$((3503000 + ${#calls[@]}))"
}
git() { printf '0123456789abcdef\n'; }
mkdir() { command mkdir "$@"; }

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT
QUEST_REQUIRED_CAMPAIGN_DIR="${tmpdir}/campaign"
submit_all

[ "${#calls[@]}" -eq 5 ] || fail "expected five jobs, got ${#calls[@]}"
[[ "${calls[0]}" == *"--nodelist=landonia01"* ]] || fail "repro 1 not pinned to landonia01"
[[ "${calls[1]}" == *"--nodelist=landonia02"* ]] || fail "repro 2 not pinned to landonia02"
[[ "${calls[2]}" != *"--nodelist="* ]] || fail "repro 3 should be scheduler-selected"
[[ "${calls[1]}" == *"--dependency=afterok:3503001"* ]] || fail "repro 2 dependency missing"
[[ "${calls[4]}" == *"--dependency=afterok:3503004"* ]] || fail "gate-path dependency missing"

printf 'Required experiment launcher tests passed.\n'
