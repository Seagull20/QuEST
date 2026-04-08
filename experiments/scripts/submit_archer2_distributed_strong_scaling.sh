#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

ARCHER2_ACCOUNT="${ARCHER2_ACCOUNT:-m25ext}"
BACKEND="${BACKEND:-cpu_mpi}"
BENCH_PLATFORM="${BENCH_PLATFORM:-archer2}"
THREADS_PER_RANK="${THREADS_PER_RANK:-128}"
START_QUBIT="${START_QUBIT:-33}"
MIN_QUBIT="${MIN_QUBIT:-26}"
PREHEAT_MODE="${PREHEAT_MODE:-light}"
PREHEAT_QUBITS="${PREHEAT_QUBITS:-24}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
DEVIATION_THRESHOLD="${DEVIATION_THRESHOLD:-0.02}"
RUN_TAG="${RUN_TAG:-archer2_distributed_strong_scaling_$(date +%Y%m%d_%H%M%S)}"
RUN_RAW_DIR="${RAW_RESULTS_DIR}/${RUN_TAG}"
RUN_PROCESSED_DIR="${PROCESSED_RESULTS_DIR}/$(date +%Y%m%d)_distributed_strong_scaling"
META_FILE="${RUN_RAW_DIR}/submission_meta.txt"

CORE_NODES=(1 2 4 8 16)
EXTENDED_NODES=(32 64 128)

normalize_job_id() {
    printf '%s' "${1%%;*}"
}

submit_job() {
    local script_path="${!#}"
    local prefix=("${@:1:$(($# - 1))}")
    local job_out

    job_out="$("${prefix[@]}" --parsable "${script_path}")" || return 1
    normalize_job_id "${job_out}"
}

wait_for_job_state() {
    local job_id="$1"
    local state=""
    local attempts=0

    while :; do
        state="$(sacct -n -X -j "${job_id}" --format=State 2>/dev/null | awk 'NF {print $1; exit}')"
        case "${state}" in
            COMPLETED|FAILED|TIMEOUT|CANCELLED|OUT_OF_MEMORY|NODE_FAIL|PREEMPTED|BOOT_FAIL)
                printf '%s' "${state}"
                return 0
                ;;
            "")
                attempts=$((attempts + 1))
                if [ "${attempts}" -gt 120 ]; then
                    die "Timed out waiting for sacct state for job ${job_id}"
                fi
                sleep 5
                ;;
            *)
                sleep 10
                ;;
        esac
    done
}

read_probe_result() {
    local path="$1"
    python3 - <<'PY' "${path}"
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("MISSING\t")
    raise SystemExit(0)
with path.open("r", encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
if not rows:
    print("MISSING\t")
    raise SystemExit(0)
row = rows[-1]
print(f"{row.get('status', '')}\t{row.get('max_qubits', '')}")
PY
}

read_qft_total() {
    local path="$1"
    python3 - <<'PY' "${path}"
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
with path.open("r", encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
for row in reversed(rows):
    if row.get("benchmark") == "qft" and row.get("stage_label") == "total" and row.get("warmup") == "0" and row.get("status") == "PASS":
        print(row.get("total_time_s", ""))
        raise SystemExit(0)
raise SystemExit(1)
PY
}

calc_relative_deviation() {
    local t1="$1"
    local t2="$2"
    python3 - <<'PY' "${t1}" "${t2}"
import sys
t1 = float(sys.argv[1])
t2 = float(sys.argv[2])
mean = (t1 + t2) / 2.0
print(abs(t1 - t2) / mean if mean else 0.0)
PY
}

point_walltime() {
    local nodes="$1"
    case "${nodes}" in
        1) printf '00:30:00' ;;
        2) printf '03:00:00' ;;
        4) printf '02:00:00' ;;
        *) printf '01:00:00' ;;
    esac
}

submit_exact_probe() {
    local qubit="$1"
    submit_job sbatch \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos=short \
        --nodes=2 \
        --ntasks-per-node=1 \
        --cpus-per-task="${THREADS_PER_RANK}" \
        --time=00:20:00 \
        --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BACKEND=${BACKEND},BENCH_PLATFORM=${BENCH_PLATFORM},SEARCH_MIN=${qubit},SEARCH_MAX=${qubit},BENCH_QUBIT=${qubit},VALIDATION_KIND=alloc_only,PROBE_LABEL=probe_alloc_only_q${qubit}" \
        "${SCRIPT_DIR}/sbatch_archer2_distributed_probe.sh"
}

submit_qft_point() {
    local nodes="$1"
    local distribution_mode="$2"
    local qubit="$3"
    local walltime="$4"
    local rep_index="$5"
    local output_suffix="$6"
    local label="$7"

    submit_job sbatch \
        --account="${ARCHER2_ACCOUNT}" \
        --partition=standard \
        --qos=standard \
        --nodes="${nodes}" \
        --ntasks-per-node=1 \
        --cpus-per-task="${THREADS_PER_RANK}" \
        --time="${walltime}" \
        --export="ALL,RAW_RESULTS_DIR_OVERRIDE=${RUN_RAW_DIR},BENCHMARK=qft,BACKEND=${BACKEND},BENCH_PLATFORM=${BENCH_PLATFORM},BENCH_QUBIT=${qubit},REPS=1,WARMUP=0,SYNC_MODE=${SYNC_MODE},PREHEAT_MODE=${PREHEAT_MODE},PREHEAT_QUBITS=${PREHEAT_QUBITS},DISTRIBUTION_MODE=${distribution_mode},REP_INDEX=${rep_index},OUTPUT_SUFFIX=${output_suffix},BENCH_LABEL=${label}" \
        "${SCRIPT_DIR}/sbatch_archer2_distributed_point.sh"
}

ensure_results_dirs
mkdir -p "${RUN_RAW_DIR}" "${RUN_PROCESSED_DIR}"

{
    echo "run_tag=${RUN_TAG}"
    echo "backend=${BACKEND}"
    echo "platform=${BENCH_PLATFORM}"
    echo "threads_per_rank=${THREADS_PER_RANK}"
    echo "start_qubit=${START_QUBIT}"
    echo "min_qubit=${MIN_QUBIT}"
    echo "preheat_mode=${PREHEAT_MODE}"
    echo "preheat_qubits=${PREHEAT_QUBITS}"
    echo "deviation_threshold=${DEVIATION_THRESHOLD}"
} > "${META_FILE}"

info "Submitting ARCHER2 distributed strong-scaling workflow"
info "Run tag: ${RUN_TAG}"
info "Raw output dir: ${RUN_RAW_DIR}"

BUILD_JOB="$(submit_job sbatch \
    --account="${ARCHER2_ACCOUNT}" \
    --partition=standard \
    --qos=standard \
    --cpus-per-task=32 \
    "${SCRIPT_DIR}/sbatch_archer2_build_cpu_mpi.sh")" || die "Failed to submit build job."
info "Submitted build job: ${BUILD_JOB}"
BUILD_STATE="$(wait_for_job_state "${BUILD_JOB}")"
[ "${BUILD_STATE}" = "COMPLETED" ] || die "Build job ${BUILD_JOB} ended in state ${BUILD_STATE}"

Q_ALLOC_MAX=""
for qubit in $(seq "${START_QUBIT}" -1 "${MIN_QUBIT}"); do
    info "Checking alloc_only feasibility at q=${qubit} on 2 nodes"
    PROBE_JOB="$(submit_exact_probe "${qubit}")" || die "Failed to submit exact probe for q=${qubit}"
    PROBE_STATE="$(wait_for_job_state "${PROBE_JOB}")"
    PROBE_PATH="${RUN_RAW_DIR}/probe_archer2_${BACKEND}_on_n2_q${qubit}_t${THREADS_PER_RANK}_alloc_only.tsv"
    read -r PROBE_STATUS PROBE_MAX <<EOF
$(read_probe_result "${PROBE_PATH}")
EOF
    {
        echo "alloc_probe_q${qubit}_job=${PROBE_JOB}"
        echo "alloc_probe_q${qubit}_state=${PROBE_STATE}"
        echo "alloc_probe_q${qubit}_status=${PROBE_STATUS}"
        echo "alloc_probe_q${qubit}_max=${PROBE_MAX}"
    } >> "${META_FILE}"
    if [ "${PROBE_STATUS}" = "PASS" ] && [ "${PROBE_MAX}" = "${qubit}" ]; then
        Q_ALLOC_MAX="${qubit}"
        break
    fi
done

[ -n "${Q_ALLOC_MAX}" ] || die "No distributed allocation-only success found between ${START_QUBIT} and ${MIN_QUBIT}"
info "Distributed alloc-only maximum on 2 nodes: q=${Q_ALLOC_MAX}"
echo "q_alloc_max=${Q_ALLOC_MAX}" >> "${META_FILE}"

Q_FIXED=""
REP_MODE=""
PILOT_REL_DEV=""
for qubit in $(seq "${Q_ALLOC_MAX}" -1 "${MIN_QUBIT}"); do
    info "Running 2-node QFT pilot at q=${qubit}"
    PILOT_JOB_1="$(submit_qft_point 2 on "${qubit}" 03:00:00 1 pilot "qft_pilot_n2_q${qubit}")" || die "Failed to submit pilot rep1 at q=${qubit}"
    PILOT_JOB_2="$(submit_qft_point 2 on "${qubit}" 03:00:00 2 pilot "qft_pilot_n2_q${qubit}")" || die "Failed to submit pilot rep2 at q=${qubit}"
    PILOT_STATE_1="$(wait_for_job_state "${PILOT_JOB_1}")"
    PILOT_STATE_2="$(wait_for_job_state "${PILOT_JOB_2}")"
    PILOT_PATH_1="${RUN_RAW_DIR}/qft_archer2_${BACKEND}_on_n2_q${qubit}_t${THREADS_PER_RANK}_rep1_pilot.tsv"
    PILOT_PATH_2="${RUN_RAW_DIR}/qft_archer2_${BACKEND}_on_n2_q${qubit}_t${THREADS_PER_RANK}_rep2_pilot.tsv"
    {
        echo "pilot_q${qubit}_job1=${PILOT_JOB_1}"
        echo "pilot_q${qubit}_job2=${PILOT_JOB_2}"
        echo "pilot_q${qubit}_state1=${PILOT_STATE_1}"
        echo "pilot_q${qubit}_state2=${PILOT_STATE_2}"
    } >> "${META_FILE}"
    if [ "${PILOT_STATE_1}" != "COMPLETED" ] || [ "${PILOT_STATE_2}" != "COMPLETED" ]; then
        info "Pilot at q=${qubit} did not complete on both reps; trying smaller qubit"
        continue
    fi
    if ! T1="$(read_qft_total "${PILOT_PATH_1}")"; then
        info "Pilot rep1 at q=${qubit} did not produce PASS total row; trying smaller qubit"
        continue
    fi
    if ! T2="$(read_qft_total "${PILOT_PATH_2}")"; then
        info "Pilot rep2 at q=${qubit} did not produce PASS total row; trying smaller qubit"
        continue
    fi
    PILOT_REL_DEV="$(calc_relative_deviation "${T1}" "${T2}")"
    Q_FIXED="${qubit}"
    python3 - <<'PY' "${PILOT_REL_DEV}" "${DEVIATION_THRESHOLD}" >/tmp/quest_rep_mode.txt
import sys
rel = float(sys.argv[1])
thr = float(sys.argv[2])
print("one" if rel <= thr else "three_parallel")
PY
    REP_MODE="$(cat /tmp/quest_rep_mode.txt)"
    rm -f /tmp/quest_rep_mode.txt
    {
        echo "q_fixed=${Q_FIXED}"
        echo "pilot_time_rep1=${T1}"
        echo "pilot_time_rep2=${T2}"
        echo "pilot_relative_deviation=${PILOT_REL_DEV}"
        echo "rep_mode=${REP_MODE}"
    } >> "${META_FILE}"
    break
done

[ -n "${Q_FIXED}" ] || die "Could not find a distributed QFT qubit that completes pilot runs"
info "Strong-scaling fixed qubit: q=${Q_FIXED}"
info "Rep mode: ${REP_MODE} (relative deviation=${PILOT_REL_DEV})"

submit_scaling_point() {
    local nodes="$1"
    local distribution_mode="$2"
    local reps_to_submit="$3"
    local walltime
    local rep_index
    local job_id

    walltime="$(point_walltime "${nodes}")"
    for rep_index in $(seq 1 "${reps_to_submit}"); do
        job_id="$(submit_qft_point "${nodes}" "${distribution_mode}" "${Q_FIXED}" "${walltime}" "${rep_index}" "strong_scaling" "qft_ss_n${nodes}")" || return 1
        echo "scaling_n${nodes}_${distribution_mode}_rep${rep_index}_job=${job_id}" >> "${META_FILE}"
        info "Submitted QFT point: nodes=${nodes}, distribution=${distribution_mode}, rep=${rep_index}, job=${job_id}"
    done
}

if [ "${REP_MODE}" = "one" ]; then
    REPS_TO_SUBMIT=1
else
    REPS_TO_SUBMIT=3
fi
echo "reps_to_submit=${REPS_TO_SUBMIT}" >> "${META_FILE}"

for nodes in "${CORE_NODES[@]}"; do
    if [ "${nodes}" -eq 1 ]; then
        submit_scaling_point "${nodes}" off "${REPS_TO_SUBMIT}" || die "Failed to submit core baseline at ${nodes} node"
    else
        submit_scaling_point "${nodes}" on "${REPS_TO_SUBMIT}" || die "Failed to submit core distributed point at ${nodes} nodes"
    fi
done

for nodes in "${EXTENDED_NODES[@]}"; do
    if ! submit_scaling_point "${nodes}" on "${REPS_TO_SUBMIT}"; then
        warn "Stopping extended strong-scaling submission at ${nodes} nodes due to submission failure"
        echo "extended_stop_at=${nodes}" >> "${META_FILE}"
        break
    fi
done

{
    echo "processed_dir=${RUN_PROCESSED_DIR}"
} >> "${META_FILE}"

info "Submitted strong-scaling points for q=${Q_FIXED}"
info "Meta file: ${META_FILE}"
