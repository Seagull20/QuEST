#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

ARCHER2_ACCOUNT="${ARCHER2_ACCOUNT:-m25ext-s2866920}"
BACKEND="${BACKEND:-cpu_mpi}"
DEPLOYMENT="${DEPLOYMENT:-off}"
BASE_QUBITS="${BASE_QUBITS:-26}"
REPS="${REPS:-3}"
WARMUP="${WARMUP:-1}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
SEARCH_MIN="${SEARCH_MIN:-1}"
SEARCH_MAX="${SEARCH_MAX:-62}"
POLL_SECONDS="${POLL_SECONDS:-15}"
CANCEL_EXISTING_JOBS="${CANCEL_EXISTING_JOBS:-1}"
RUN_TAG="${RUN_TAG:-archer2_parallel_$(date +%Y%m%d_%H%M%S)}"
RUN_RAW_DIR="${RAW_RESULTS_DIR}/${RUN_TAG}"
META_FILE="${RUN_RAW_DIR}/submission_meta.txt"

normalize_job_id() {
    printf '%s' "${1%%;*}"
}

job_state() {
    local job_id="$1"
    sacct -j "${job_id}" --format=State -n -P 2>/dev/null | awk -F'|' 'NF {print $1; exit}'
}

wait_for_job() {
    local job_id="$1"
    local state=""

    while true; do
        state="$(job_state "${job_id}")"
        case "${state}" in
            COMPLETED)
                info "Job ${job_id} completed"
                return 0
                ;;
            FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL|BOOT_FAIL|PREEMPTED)
                die "Job ${job_id} finished with state ${state}"
                ;;
            "")
                ;;
            *)
                info "Job ${job_id} state=${state}; waiting ${POLL_SECONDS}s"
                ;;
        esac
        sleep "${POLL_SECONDS}"
    done
}

probe_max_qubits() {
    local probe_file="$1"
    python3 - "$probe_file" <<'PY'
import csv
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))

if not rows:
    raise SystemExit("probe file is empty")

print(rows[-1]["max_qubits"])
PY
}

sample_points_csv() {
    local base="$1"
    local maximum="$2"
    python3 - "$base" "$maximum" <<'PY'
import sys

base = int(sys.argv[1])
maximum = int(sys.argv[2])
values = sorted({base, maximum, (base + maximum) // 2})
print(",".join(str(v) for v in values if base <= v <= maximum))
PY
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

h_qos_for_qubit() {
    local qubit="$1"
    if [ "${qubit}" -ge 33 ]; then
        printf 'standard'
    else
        printf 'short'
    fi
}

h_time_limit_for_qubit() {
    local qubit="$1"
    if [ "${qubit}" -ge 33 ]; then
        # Previous q=33 run wrote 66/132 rows before timing out at 00:10:15.
        # Doubling that partial progress with a small buffer gives ~00:25:00.
        printf '00:25:00'
    else
        printf '00:10:00'
    fi
}

random_qos_for_qubit() {
    local qubit="$1"
    if [ "${qubit}" -ge 33 ]; then
        printf 'standard'
    else
        printf 'standard'
    fi
}

random_time_limit_for_qubit() {
    local qubit="$1"
    if [ "${qubit}" -ge 33 ]; then
        # Previous q=33 run completed warmup + one measured rep in ~5.5h.
        # Splitting by rep still keeps warmup, so budget ~7h for one task.
        printf '07:00:00'
    elif [ "${qubit}" -ge 29 ]; then
        printf '01:00:00'
    else
        printf '00:20:00'
    fi
}

submit_build_job() {
    sbatch --parsable \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos=standard \
        "${SCRIPT_DIR}/sbatch_archer2_build_cpu_mpi.sh"
}

submit_probe_job() {
    local dependency="$1"
    sbatch --parsable \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos=short \
        --dependency="afterok:${dependency}" \
        --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},SEARCH_MIN=${SEARCH_MIN},SEARCH_MAX=${SEARCH_MAX},SYNC_MODE=${SYNC_MODE},DEPLOYMENT=${DEPLOYMENT},BENCH_PLATFORM=archer2" \
        "${SCRIPT_DIR}/sbatch_archer2_probe_cpu_mpi.sh"
}

submit_qft_array() {
    local maximum="$1"
    sbatch --parsable \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos=standard \
        --job-name=quest-qft-cpu \
        --time=03:00:00 \
        --array="${BASE_QUBITS}-${maximum}" \
        --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BENCHMARK=qft,BACKEND=${BACKEND},DEPLOYMENT=${DEPLOYMENT},REPS=${REPS},WARMUP=${WARMUP},SYNC_MODE=${SYNC_MODE},BENCH_PLATFORM=archer2" \
        "${SCRIPT_DIR}/sbatch_archer2_point.sh"
}

submit_h_jobs() {
    local points_csv="$1"
    local points=()
    local job_ids=()
    local qubit
    local qos
    local walltime

    IFS=, read -r -a points <<< "${points_csv}"
    for qubit in "${points[@]}"; do
        qos="$(h_qos_for_qubit "${qubit}")"
        walltime="$(h_time_limit_for_qubit "${qubit}")"
        job_ids+=("$(normalize_job_id "$(sbatch --parsable \
            --account="${ARCHER2_ACCOUNT}" \
            --partition=standard \
            --qos="${qos}" \
            --job-name=quest-h-cpu \
            --time="${walltime}" \
            --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BENCHMARK=h_sweep,BACKEND=${BACKEND},DEPLOYMENT=${DEPLOYMENT},REPS=${REPS},WARMUP=${WARMUP},SYNC_MODE=${SYNC_MODE},BENCH_PLATFORM=archer2,BENCH_QUBIT=${qubit}" \
            "${SCRIPT_DIR}/sbatch_archer2_point.sh")")")
    done

    join_by_comma "${job_ids[@]}"
}

submit_random_jobs() {
    local points_csv="$1"
    local points=()
    local job_ids=()
    local qubit
    local qos
    local walltime

    IFS=, read -r -a points <<< "${points_csv}"
    for qubit in "${points[@]}"; do
        qos="$(random_qos_for_qubit "${qubit}")"
        walltime="$(random_time_limit_for_qubit "${qubit}")"
        job_ids+=("$(normalize_job_id "$(sbatch --parsable \
            --account="${ARCHER2_ACCOUNT}" \
            --partition=standard \
            --qos="${qos}" \
            --job-name=quest-random-cpu \
            --time="${walltime}" \
            --array="1-${REPS}" \
            --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BENCHMARK=random,BACKEND=${BACKEND},DEPLOYMENT=${DEPLOYMENT},REPS=${REPS},WARMUP=${WARMUP},POINT_REPS=1,POINT_WARMUP=1,RANDOM_SPLIT_BY_REP=1,SYNC_MODE=${SYNC_MODE},SEED=20260402,BENCH_PLATFORM=archer2,BENCH_QUBIT=${qubit}" \
            "${SCRIPT_DIR}/sbatch_archer2_point.sh")")")
    done

    join_by_comma "${job_ids[@]}"
}

cancel_existing_jobs_if_requested() {
    local job_ids=""

    [ "${CANCEL_EXISTING_JOBS}" = "1" ] || return 0

    job_ids="$(squeue -u "${USER}" -h -o '%A %j' | awk '$2 ~ /^quest-(suite|build|probe|qft|h|random)-cpu$/ {print $1}')"
    if [ -n "${job_ids}" ]; then
        info "Cancelling existing ARCHER2 CPU benchmark jobs: ${job_ids}"
        # shellcheck disable=SC2086
        scancel ${job_ids} || true
        sleep 2
    fi
}

write_meta() {
    local build_job="$1"
    local probe_job="$2"
    local qft_job="$3"
    local h_job="$4"
    local random_job="$5"
    local maximum="$6"
    local points_csv="$7"

    cat > "${META_FILE}" <<EOF
run_tag=${RUN_TAG}
run_raw_dir=${RUN_RAW_DIR}
build_job=${build_job}
probe_job=${probe_job}
qft_job=${qft_job}
h_job=${h_job}
random_job=${random_job}
max_qubits=${maximum}
sample_points=${points_csv}
parser_cmd=python3 experiments/scripts/parse_results.py --raw-dir ${RUN_RAW_DIR} --out-dir ${RUN_RAW_DIR}/processed
EOF
}

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present
ensure_minimum_cmake 3.21

mkdir -p "${RUN_RAW_DIR}"
cancel_existing_jobs_if_requested

info "ARCHER2 parallel submission"
info "Run tag: ${RUN_TAG}"
info "Run raw dir: ${RUN_RAW_DIR}"

build_job="$(normalize_job_id "$(submit_build_job)")"
probe_job="$(normalize_job_id "$(submit_probe_job "${build_job}")")"

info "Submitted build job ${build_job}"
info "Submitted probe job ${probe_job}"

wait_for_job "${probe_job}"

PROBE_FILE="${RUN_RAW_DIR}/probe_archer2_${BACKEND}_${DEPLOYMENT}.tsv"
[ -f "${PROBE_FILE}" ] || die "Probe file not found: ${PROBE_FILE}"

maximum="$(probe_max_qubits "${PROBE_FILE}")"
[ "${maximum}" -ge "${BASE_QUBITS}" ] || die "probe max_qubits=${maximum} is below base qubits ${BASE_QUBITS}"

points_csv="$(sample_points_csv "${BASE_QUBITS}" "${maximum}")"

info "Probe maximum qubits: ${maximum}"
info "Sample points: ${points_csv}"

qft_job="$(normalize_job_id "$(submit_qft_array "${maximum}")")"
h_job="$(submit_h_jobs "${points_csv}")"
random_job="$(submit_random_jobs "${points_csv}")"

write_meta "${build_job}" "${probe_job}" "${qft_job}" "${h_job}" "${random_job}" "${maximum}" "${points_csv}"

info "Submitted QFT array job ${qft_job}"
info "Submitted H sweep array job ${h_job}"
info "Submitted random array job ${random_job}"
info "Metadata written to ${META_FILE}"
