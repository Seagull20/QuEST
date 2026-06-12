#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAYLOAD="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_required_experiment_point.sh"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_TEST}"' EXIT

# shellcheck source=/dev/null
source "${PAYLOAD}"

for mode in repro qft-sweep gate-path; do
    manifest="${TMPDIR_TEST}/${mode}.tsv"
    write_point_manifest "${mode}" "${manifest}"
done

[ "$(awk 'END {print NR - 1}' "${TMPDIR_TEST}/repro.tsv")" -eq 3 ]
[ "$(awk 'END {print NR - 1}' "${TMPDIR_TEST}/qft-sweep.tsv")" -eq 18 ]
[ "$(awk 'END {print NR - 1}' "${TMPDIR_TEST}/gate-path.tsv")" -eq 4 ]
[ "$(awk -F '\t' 'NR > 1 && $10 == 1 {count++} END {print count + 0}' "${TMPDIR_TEST}/gate-path.tsv")" -eq 4 ]

POINT_MANIFEST="${TMPDIR_TEST}/qft-sweep.tsv"
RUN_DIR="${TMPDIR_TEST}"
calls=0
capture_gpu_snapshot() { :; }
make_benchmark_command() { BENCH_COMMAND=(true); }
run_logged() { calls=$((calls + 1)); cat >/dev/null; }
run_timing_points
[ "${calls}" -eq 18 ] || { echo "expected 18 timing calls, got ${calls}" >&2; exit 1; }

printf 'Required experiment payload tests passed.\n'
