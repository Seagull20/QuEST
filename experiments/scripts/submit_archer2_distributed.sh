#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

ARCHER2_ACCOUNT="${ARCHER2_ACCOUNT:-m25ext}"
BACKEND="${BACKEND:-cpu_mpi}"
BENCH_PLATFORM="${BENCH_PLATFORM:-archer2}"
SMOKE_QUBIT="${SMOKE_QUBIT:-26}"
SEARCH_MIN="${SEARCH_MIN:-32}"
SEARCH_MAX="${SEARCH_MAX:-35}"
PREHEAT_MODE="${PREHEAT_MODE:-light}"
PREHEAT_QUBITS="${PREHEAT_QUBITS:-24}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
SMOKE_REPS="${SMOKE_REPS:-1}"
SMOKE_WARMUP="${SMOKE_WARMUP:-0}"
QFT_REPS="${QFT_REPS:-3}"
H_REPS="${H_REPS:-3}"
RANDOM_REPS="${RANDOM_REPS:-1}"
WARMUP="${WARMUP:-0}"
THREADS_PER_RANK="${THREADS_PER_RANK:-128}"
NODES="${NODES:-4}"
RUN_TAG="${RUN_TAG:-archer2_distributed_$(date +%Y%m%d_%H%M%S)}"
RUN_RAW_DIR="${RAW_RESULTS_DIR}/${RUN_TAG}"
RUN_PROCESSED_DIR="${PROCESSED_RESULTS_DIR}/$(date +%Y%m%d)_distributed"
META_FILE="${RUN_RAW_DIR}/submission_meta.txt"

normalize_job_id() {
    printf '%s' "${1%%;*}"
}

wait_submit() {
    local script_path="${!#}"
    local prefix=("${@:1:$(($# - 1))}")
    local job_out=""

    if ! job_out="$("${prefix[@]}" --wait --parsable "${script_path}")"; then
        return 1
    fi
    normalize_job_id "${job_out}"
}

submit_array() {
    local benchmark="$1"
    local manifest_path="$2"
    local qos="$3"
    local walltime="$4"
    local reps="$5"
    local count=""

    count="$(wc -l < "${manifest_path}" | tr -d '[:space:]')"
    [ "${count}" -gt 0 ] || die "Manifest ${manifest_path} is empty"

    sbatch --parsable \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos="${qos}" \
        --job-name="quest-dist-${benchmark}" \
        --nodes="${NODES}" \
        --ntasks-per-node=1 \
        --cpus-per-task="${THREADS_PER_RANK}" \
        --time="${walltime}" \
        --array="1-${count}" \
        --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BENCHMARK=${benchmark},BACKEND=${BACKEND},REPS=${reps},WARMUP=${WARMUP},SYNC_MODE=${SYNC_MODE},PREHEAT_MODE=${PREHEAT_MODE},PREHEAT_QUBITS=${PREHEAT_QUBITS},BENCH_PLATFORM=${BENCH_PLATFORM},MANIFEST_PATH=${manifest_path}" \
        "${SCRIPT_DIR}/sbatch_archer2_distributed_point.sh"
}

ensure_results_dirs
mkdir -p "${RUN_RAW_DIR}" "${RUN_PROCESSED_DIR}"

{
    echo "run_tag=${RUN_TAG}"
    echo "backend=${BACKEND}"
    echo "platform=${BENCH_PLATFORM}"
    echo "nodes=${NODES}"
    echo "threads_per_rank=${THREADS_PER_RANK}"
    echo "smoke_qubit=${SMOKE_QUBIT}"
    echo "search_min=${SEARCH_MIN}"
    echo "search_max=${SEARCH_MAX}"
    echo "preheat_mode=${PREHEAT_MODE}"
    echo "preheat_qubits=${PREHEAT_QUBITS}"
} > "${META_FILE}"

info "Submitting ARCHER2 distributed workflow"
info "Run tag: ${RUN_TAG}"
info "Raw output dir: ${RUN_RAW_DIR}"

BUILD_JOB="$(wait_submit sbatch \
    --account="${ARCHER2_ACCOUNT}" \
    --partition=standard \
    --qos=standard \
    --cpus-per-task=32 \
    "${SCRIPT_DIR}/sbatch_archer2_build_cpu_mpi.sh")" || die "Build job failed."
info "Build completed: ${BUILD_JOB}"

SMOKE_JOB="$(wait_submit sbatch \
    --account="${ARCHER2_ACCOUNT}" \
    --partition=standard \
    --qos=short \
    --nodes="${NODES}" \
    --ntasks-per-node=1 \
    --cpus-per-task="${THREADS_PER_RANK}" \
    --time=00:20:00 \
    --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BACKEND=${BACKEND},BENCH_PLATFORM=${BENCH_PLATFORM},BENCH_QUBIT=${SMOKE_QUBIT},REPS=${SMOKE_REPS},WARMUP=${SMOKE_WARMUP},SYNC_MODE=${SYNC_MODE},PREHEAT_MODE=${PREHEAT_MODE},PREHEAT_QUBITS=${PREHEAT_QUBITS}" \
    "${SCRIPT_DIR}/sbatch_archer2_distributed_smoke.sh")" || die "Distributed smoke failed."
info "Smoke completed: ${SMOKE_JOB}"

PROBE_JOB="$(wait_submit sbatch \
    --account="${ARCHER2_ACCOUNT}" \
    --partition=standard \
    --qos=short \
    --nodes="${NODES}" \
    --ntasks-per-node=1 \
    --cpus-per-task="${THREADS_PER_RANK}" \
    --time=00:20:00 \
    --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BACKEND=${BACKEND},BENCH_PLATFORM=${BENCH_PLATFORM},SEARCH_MIN=${SEARCH_MIN},SEARCH_MAX=${SEARCH_MAX}" \
    "${SCRIPT_DIR}/sbatch_archer2_distributed_probe.sh")" || die "Distributed probe failed."
info "Probe completed: ${PROBE_JOB}"

PROBE_PATH="${RUN_RAW_DIR}/probe_archer2_${BACKEND}_on_n${NODES}_t${THREADS_PER_RANK}.tsv"
[ -f "${PROBE_PATH}" ] || die "Probe output missing: ${PROBE_PATH}"

read -r DIST_MAX PROBE_STATUS <<EOF
$(python3 - <<'PY' "${PROBE_PATH}"
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
with path.open("r", encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
if not rows:
    raise SystemExit("nan MISSING")
row = rows[-1]
print(f"{row.get('max_qubits', '')} {row.get('status', '')}")
PY
)
EOF

[ "${PROBE_STATUS}" = "PASS" ] || die "Distributed probe did not PASS: status=${PROBE_STATUS}"
[ -n "${DIST_MAX}" ] || die "Distributed probe did not return max_qubits."
[ "${DIST_MAX}" -ge 2 ] || die "Distributed max qubits is unexpectedly small: ${DIST_MAX}"

DIST_MINUS_ONE="$((DIST_MAX - 1))"
QUBIT_MANIFEST="${RUN_RAW_DIR}/manifest_distributed_qubits.txt"
printf '%s\n%s\n' "${DIST_MINUS_ONE}" "${DIST_MAX}" > "${QUBIT_MANIFEST}"

{
    echo "build_job=${BUILD_JOB}"
    echo "smoke_job=${SMOKE_JOB}"
    echo "probe_job=${PROBE_JOB}"
    echo "distributed_max=${DIST_MAX}"
    echo "suite_qubits=${DIST_MINUS_ONE},${DIST_MAX}"
} >> "${META_FILE}"

QFT_JOB="$(normalize_job_id "$(submit_array qft "${QUBIT_MANIFEST}" standard 03:00:00 "${QFT_REPS}")")"
H_JOB="$(normalize_job_id "$(submit_array h_sweep "${QUBIT_MANIFEST}" short 00:30:00 "${H_REPS}")")"
RANDOM_JOB="$(normalize_job_id "$(submit_array random "${QUBIT_MANIFEST}" standard 06:00:00 "${RANDOM_REPS}")")"

{
    echo "qft_job=${QFT_JOB}"
    echo "h_job=${H_JOB}"
    echo "random_job=${RANDOM_JOB}"
} >> "${META_FILE}"

info "Distributed max qubits: ${DIST_MAX}"
info "Submitted distributed suite QFT array: ${QFT_JOB}"
info "Submitted distributed suite H array: ${H_JOB}"
info "Submitted distributed suite Random array: ${RANDOM_JOB}"
info "Processed output target: ${RUN_PROCESSED_DIR}"
