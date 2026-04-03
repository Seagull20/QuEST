#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

ARCHER2_ACCOUNT="${ARCHER2_ACCOUNT:-m25ext-s2866920}"
BACKEND="${BACKEND:-cpu_mpi}"
DEPLOYMENT="${DEPLOYMENT:-off}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
PREHEAT_MODE="${PREHEAT_MODE:-light}"
PREHEAT_QUBITS="${PREHEAT_QUBITS:-24}"
THREADS_LIST="${THREADS_LIST:-32 64 128}"
QFT_QUBITS="${QFT_QUBITS:-26 29 31 33}"
H_QUBITS="${H_QUBITS:-26 33}"
RANDOM_SMALL_QUBITS="${RANDOM_SMALL_QUBITS:-26 29}"
RANDOM_LARGE_QUBITS="${RANDOM_LARGE_QUBITS:-33}"
QFT_REPS="${QFT_REPS:-3}"
H_REPS="${H_REPS:-3}"
RANDOM_REPS="${RANDOM_REPS:-1}"
WARMUP="${WARMUP:-0}"
RUN_TAG="${RUN_TAG:-archer2_threads_$(date +%Y%m%d_%H%M%S)}"
RUN_RAW_DIR="${RAW_RESULTS_DIR}/${RUN_TAG}"
META_FILE="${RUN_RAW_DIR}/submission_meta.txt"

normalize_job_id() {
    printf '%s' "${1%%;*}"
}

join_by_comma() {
    local first=1
    local value

    for value in "$@"; do
        if [ "${first}" -eq 1 ]; then
            printf '%s' "${value}"
            first=0
        else
            printf ',%s' "${value}"
        fi
    done
}

write_manifest() {
    local path="$1"
    shift
    printf '%s\n' "$@" > "${path}"
}

submit_build_job() {
    sbatch --parsable \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos=standard \
        "${SCRIPT_DIR}/sbatch_archer2_build_cpu_mpi.sh"
}

submit_thread_array() {
    local benchmark="$1"
    local threads="$2"
    local manifest_path="$3"
    local qos="$4"
    local walltime="$5"
    local reps="$6"
    local dependency="$7"
    local array_limit="${8:-}"
    local count
    local array_spec

    count="$(wc -l < "${manifest_path}" | tr -d '[:space:]')"
    [ "${count}" -gt 0 ] || die "Manifest ${manifest_path} is empty"

    array_spec="1-${count}"
    if [ -n "${array_limit}" ]; then
        array_spec="${array_spec}%${array_limit}"
    fi

    sbatch --parsable \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos="${qos}" \
        --job-name="quest-${benchmark}-t${threads}" \
        --time="${walltime}" \
        --cpus-per-task="${threads}" \
        --dependency="afterok:${dependency}" \
        --array="${array_spec}" \
        --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BENCHMARK=${benchmark},BACKEND=${BACKEND},DEPLOYMENT=${DEPLOYMENT},REPS=${reps},WARMUP=${WARMUP},SYNC_MODE=${SYNC_MODE},PREHEAT_MODE=${PREHEAT_MODE},PREHEAT_QUBITS=${PREHEAT_QUBITS},BENCH_PLATFORM=archer2,BENCH_THREADS=${threads},MANIFEST_PATH=${manifest_path}" \
        "${SCRIPT_DIR}/sbatch_archer2_thread_point.sh"
}

ensure_results_dirs
mkdir -p "${RUN_RAW_DIR}"

BUILD_JOB="$(normalize_job_id "$(submit_build_job)")"

{
    echo "run_tag=${RUN_TAG}"
    echo "backend=${BACKEND}"
    echo "deployment=${DEPLOYMENT}"
    echo "sync_mode=${SYNC_MODE}"
    echo "preheat_mode=${PREHEAT_MODE}"
    echo "preheat_qubits=${PREHEAT_QUBITS}"
    echo "threads=${THREADS_LIST}"
    echo "qft_qubits=${QFT_QUBITS}"
    echo "h_qubits=${H_QUBITS}"
    echo "random_small_qubits=${RANDOM_SMALL_QUBITS}"
    echo "random_large_qubits=${RANDOM_LARGE_QUBITS}"
    echo "build_job=${BUILD_JOB}"
} > "${META_FILE}"

info "Submitted build job ${BUILD_JOB}"
info "Run tag: ${RUN_TAG}"
info "Raw output dir: ${RUN_RAW_DIR}"

QFT_JOB_IDS=()
H_JOB_IDS=()
RANDOM_SMALL_JOB_IDS=()
RANDOM_LARGE_JOB_IDS=()

for threads in ${THREADS_LIST}; do
    qft_manifest="${RUN_RAW_DIR}/manifest_qft_t${threads}.txt"
    h_manifest="${RUN_RAW_DIR}/manifest_h_t${threads}.txt"
    random_small_manifest="${RUN_RAW_DIR}/manifest_random_small_t${threads}.txt"
    random_large_manifest="${RUN_RAW_DIR}/manifest_random_large_t${threads}.txt"

    write_manifest "${qft_manifest}" ${QFT_QUBITS}
    write_manifest "${h_manifest}" ${H_QUBITS}
    write_manifest "${random_small_manifest}" ${RANDOM_SMALL_QUBITS}
    write_manifest "${random_large_manifest}" ${RANDOM_LARGE_QUBITS}

    QFT_JOB_IDS+=("$(normalize_job_id "$(submit_thread_array qft "${threads}" "${qft_manifest}" standard 03:00:00 "${QFT_REPS}" "${BUILD_JOB}")")")
    H_JOB_IDS+=("$(normalize_job_id "$(submit_thread_array h_sweep "${threads}" "${h_manifest}" short 00:18:00 "${H_REPS}" "${BUILD_JOB}" 1)")")")
done

H_DEPENDENCY="$(join_by_comma "${H_JOB_IDS[@]}")"

for threads in ${THREADS_LIST}; do
    random_small_manifest="${RUN_RAW_DIR}/manifest_random_small_t${threads}.txt"
    random_large_manifest="${RUN_RAW_DIR}/manifest_random_large_t${threads}.txt"

    RANDOM_SMALL_JOB_IDS+=("$(normalize_job_id "$(submit_thread_array random "${threads}" "${random_small_manifest}" short 00:20:00 "${RANDOM_REPS}" "${H_DEPENDENCY}" 1)")")")
    RANDOM_LARGE_JOB_IDS+=("$(normalize_job_id "$(submit_thread_array random "${threads}" "${random_large_manifest}" standard 06:00:00 "${RANDOM_REPS}" "${BUILD_JOB}")")")
done

{
    echo "qft_jobs=$(join_by_comma "${QFT_JOB_IDS[@]}")"
    echo "h_jobs=$(join_by_comma "${H_JOB_IDS[@]}")"
    echo "random_small_jobs=$(join_by_comma "${RANDOM_SMALL_JOB_IDS[@]}")"
    echo "random_large_jobs=$(join_by_comma "${RANDOM_LARGE_JOB_IDS[@]}")"
} >> "${META_FILE}"

info "Submitted QFT thread arrays: $(join_by_comma "${QFT_JOB_IDS[@]}")"
info "Submitted H sweep thread arrays: $(join_by_comma "${H_JOB_IDS[@]}")"
info "Submitted Random small thread arrays: $(join_by_comma "${RANDOM_SMALL_JOB_IDS[@]}")"
info "Submitted Random q33 thread arrays: $(join_by_comma "${RANDOM_LARGE_JOB_IDS[@]}")"
