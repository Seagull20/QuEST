#!/usr/bin/env bash
# Submit one four-GPU allocation containing the complete intra-node scaling campaign.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

SCALING_ACCOUNT="${QUEST_SCALING_ACCOUNT:-general-teaching}"
SCALING_QOS="${QUEST_SCALING_QOS:-teaching}"
SCALING_GPUS=4
SCALING_CPUS_PER_TASK=2
SCALING_WALLTIME="${QUEST_SCALING_WALLTIME:-03:30:00}"
# Explicit host memory: q30p4's ~37 GB host staging thrashes under the default
# 8000M/GPU (32 GB cgroup) — proven by canary 3538010 (T-024). q<=28 unaffected.
SCALING_MEM="${QUEST_SCALING_MEM:-96G}"
SCALING_A6000_MAX_WAIT_S="${QUEST_SCALING_A6000_MAX_WAIT_S:-300}"
SCALING_GPU_CHOICE="${QUEST_SCALING_GPU_CHOICE:-auto}"
SCALING_COMPRESSION_MODE="${QUEST_SCALING_COMPRESSION_MODE:-native}"
# Optional Slurm dependency (e.g. afterany:<jobid>) to chain sequential 4-GPU
# jobs under the single-running-allocation QoS limit. When set, the local
# "already running" guard is skipped because queuing behind a peer is intended.
SCALING_DEPENDENCY="${QUEST_SCALING_DEPENDENCY:-}"
SCALING_DEP_FLAG=""
[ -n "${SCALING_DEPENDENCY}" ] && SCALING_DEP_FLAG="--dependency=${SCALING_DEPENDENCY}"

SCALING_PARTITION=""
SCALING_GPU_TYPE=""
SCALING_GPU_GRES=""
SCALING_TEST_OUTPUT=""

usage() {
    cat <<'EOF'
Usage:
  bash experiments/scripts/sbatch_cluster_gpu_mpi_scaling.sh [--dry-run]

Selection order:
  1. Teaching A6000 when Slurm predicts a start within five minutes.
  2. Interactive RTX 2080 Ti.
  3. Teaching RTX 2080 Ti.

The job allocates four GPUs once, then runs 1/2/4-rank strong and weak scaling
points sequentially on the same node.

Environment:
  QUEST_SCALING_GPU_CHOICE=auto|2080ti|a6000
  QUEST_SCALING_COMPRESSION_MODE=native|off|on|raw   (raw = ON minus the codec, T-075)
EOF
}

ensure_no_running_gpu_allocation() {
    local user_name="${USER:-$(id -un)}"
    local allocations

    allocations="$(squeue -h -u "${user_name}" -t RUNNING,COMPLETING -o '%b' 2>/dev/null || true)"
    if printf '%s\n' "${allocations}" | grep -Eq '(^|[,[:space:]])gres/gpu(:|=)'; then
        warn "User ${user_name} already has a running GPU allocation; four-GPU scaling would exceed the QoS limit."
        return 1
    fi
}

scheduler_start_epoch() {
    local output="$1"
    local timestamp

    timestamp="$(printf '%s\n' "${output}" | sed -n 's/.* to start at \([^ ]*\).*/\1/p' | head -n 1)"
    [ -n "${timestamp}" ] || return 1
    python3 - "${timestamp}" <<'PY'
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

value = datetime.fromisoformat(sys.argv[1]).replace(tzinfo=ZoneInfo("Europe/London"))
print(int(value.timestamp()))
PY
}

test_candidate() {
    local partition="$1"
    local gres="$2"
    local output

    if ! output="$(
        sbatch --test-only \
            --account="${SCALING_ACCOUNT}" \
            --qos="${SCALING_QOS}" \
            --partition="${partition}" \
            --nodes=1 \
            --ntasks="${SCALING_GPUS}" \
            --ntasks-per-node="${SCALING_GPUS}" \
            --cpus-per-task="${SCALING_CPUS_PER_TASK}" \
            --gres="${gres}:${SCALING_GPUS}" \
            --time="${SCALING_WALLTIME}" \
            --wrap=/bin/true 2>&1
    )"; then
        SCALING_TEST_OUTPUT="${output}"
        return 1
    fi
    SCALING_TEST_OUTPUT="${output}"
}

a6000_starts_soon() {
    local start_epoch
    local now_epoch="${QUEST_SCALING_NOW_EPOCH:-$(date +%s)}"

    start_epoch="$(scheduler_start_epoch "${SCALING_TEST_OUTPUT}" || true)"
    [ -n "${start_epoch}" ] || return 1
    [ "$((start_epoch - now_epoch))" -le "${SCALING_A6000_MAX_WAIT_S}" ]
}

select_scaling_resource() {
    SCALING_GPU_CHOICE="${QUEST_SCALING_GPU_CHOICE:-${SCALING_GPU_CHOICE:-auto}}"
    case "${SCALING_GPU_CHOICE}" in
        auto)
            if test_candidate Teaching gpu:nvidia_rtx_a6000 && a6000_starts_soon; then
                SCALING_PARTITION="Teaching"
                SCALING_GPU_TYPE="a6000"
                SCALING_GPU_GRES="gpu:nvidia_rtx_a6000"
                return 0
            fi
            ;;
        a6000)
            if test_candidate Teaching gpu:nvidia_rtx_a6000; then
                SCALING_PARTITION="Teaching"
                SCALING_GPU_TYPE="a6000"
                SCALING_GPU_GRES="gpu:nvidia_rtx_a6000"
                return 0
            fi
            die "No accessible four-GPU A6000 allocation passed Slurm validation."
            ;;
        2080ti)
            ;;
        *)
            die "QUEST_SCALING_GPU_CHOICE must be auto, 2080ti, or a6000."
            ;;
    esac

    if [ "${SCALING_GPU_CHOICE}" = "auto" ] || [ "${SCALING_GPU_CHOICE}" = "2080ti" ]; then
        if test_candidate Interactive gpu:nvidia_geforce_rtx_2080_ti; then
            SCALING_PARTITION="Interactive"
            SCALING_GPU_TYPE="2080ti"
            SCALING_GPU_GRES="gpu:nvidia_geforce_rtx_2080_ti"
            return 0
        fi

        if test_candidate Teaching gpu:nvidia_geforce_rtx_2080_ti; then
            SCALING_PARTITION="Teaching"
            SCALING_GPU_TYPE="2080ti"
            SCALING_GPU_GRES="gpu:nvidia_geforce_rtx_2080_ti"
            return 0
        fi
    fi

    die "No accessible four-GPU A6000 or RTX 2080 Ti allocation passed Slurm validation."
}

build_scaling_export_vars() {
    local export_vars="ALL"
    SCALING_COMPRESSION_MODE="${QUEST_SCALING_COMPRESSION_MODE:-${SCALING_COMPRESSION_MODE:-native}}"
    SCALING_GPU_CHOICE="${QUEST_SCALING_GPU_CHOICE:-${SCALING_GPU_CHOICE:-auto}}"
    SCALING_STAGING_MODE="${QUEST_SCALING_STAGING_MODE:-none}"

    # T-044 ladder arm A1: window transport (T-039, pin 910b9ad) as an axis
    # orthogonal to the compression mode. Double-exported (value here, arm
    # re-derivation in the payload) like the compression knobs.
    case "${SCALING_STAGING_MODE}" in
        none)
            ;;
        bulk_async)
            export_vars="${export_vars},QUEST_GPU_STAGING_MODE=bulk_async"
            export_vars="${export_vars},QUEST_GPU_STAGING_STATS=1"
            ;;
        *)
            die "QUEST_SCALING_STAGING_MODE must be none or bulk_async."
            ;;
    esac
    export_vars="${export_vars},QUEST_SCALING_STAGING_MODE=${SCALING_STAGING_MODE}"

    case "${SCALING_COMPRESSION_MODE}" in
        native)
            ;;
        off)
            export_vars="${export_vars},QUEST_BENCH_ENABLE_NVCOMP=1"
            export_vars="${export_vars},QUEST_ENABLE_EXCHANGE_COMPRESSION=0"
            export_vars="${export_vars},QUEST_EXCHANGE_COMPRESSION_VERIFY=0"
            ;;
        on)
            export_vars="${export_vars},QUEST_BENCH_ENABLE_NVCOMP=1"
            export_vars="${export_vars},QUEST_ENABLE_EXCHANGE_COMPRESSION=1"
            export_vars="${export_vars},QUEST_EXCHANGE_COMPRESSION_VERIFY=0"
            # T-024 tranche 2 acceptance: ON arms MUST emit the per-rank
            # raw/sent/control byte counters. These are the counter-level second
            # source for the per-exchange compressibility trend and the coverage
            # share, both of which are currently single-source.
            export_vars="${export_vars},QUEST_EXCHANGE_COMPRESSION_STATS=1"
            export_vars="${export_vars},QUEST_EXCHANGE_COMPRESSION_FORCE_RAW=0"
            ;;
        raw)
            # T-075: the ON arm minus the codec. Identical plumbing (pinned
            # staging, 64 MiB chunk loop, size handshake, sync structure), so
            # RAW-vs-ON isolates encoding and OFF-vs-RAW isolates plumbing.
            export_vars="${export_vars},QUEST_BENCH_ENABLE_NVCOMP=1"
            export_vars="${export_vars},QUEST_ENABLE_EXCHANGE_COMPRESSION=1"
            export_vars="${export_vars},QUEST_EXCHANGE_COMPRESSION_VERIFY=0"
            export_vars="${export_vars},QUEST_EXCHANGE_COMPRESSION_STATS=1"
            export_vars="${export_vars},QUEST_EXCHANGE_COMPRESSION_FORCE_RAW=1"
            ;;
        *)
            die "QUEST_SCALING_COMPRESSION_MODE must be native, off, on, or raw."
            ;;
    esac

    export_vars="${export_vars},BENCH_PLATFORM=cluster"
    export_vars="${export_vars},QUEST_SCALING_PARTITION=${SCALING_PARTITION}"
    export_vars="${export_vars},QUEST_SCALING_GPU_TYPE=${SCALING_GPU_TYPE}"
    export_vars="${export_vars},QUEST_SCALING_GPU_GRES=${SCALING_GPU_GRES}"
    export_vars="${export_vars},QUEST_SCALING_GPU_CHOICE=${SCALING_GPU_CHOICE}"
    export_vars="${export_vars},QUEST_SCALING_COMPRESSION_MODE=${SCALING_COMPRESSION_MODE}"
    # Passed by NAME so sbatch forwards the current value. QUEST_SCALING_POINTS
    # is ';'-separated rather than newline-separated: --export cannot carry a
    # multi-line value. The payload splits it back into lines.
    if [ -n "${QUEST_SCALING_POINTS:-}" ]; then
        export_vars="${export_vars},QUEST_SCALING_POINTS"
    fi
    if [ -n "${QUEST_SCALING_PROFILE_QUBITS:-}" ]; then
        export_vars="${export_vars},QUEST_SCALING_PROFILE_QUBITS"
    fi
    if [ -n "${QUEST_SCALING_WORKLOADS:-}" ]; then
        export_vars="${export_vars},QUEST_SCALING_WORKLOADS"
    fi
    if [ -n "${QUEST_SCALING_RANDOM_DEPTH:-}" ]; then
        export_vars="${export_vars},QUEST_SCALING_RANDOM_DEPTH"
    fi
    export_vars="${export_vars},QUEST_SCALING_GIT_COMMIT=$(git rev-parse HEAD)"
    export_vars="${export_vars},QUEST_BENCH_BUILD_PARALLEL=${QUEST_BENCH_BUILD_PARALLEL:-8}"
    printf '%s\n' "${export_vars}"
}

main() {
    local dry_run=0
    local payload="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_scaling_point.sh"
    local job_id
    local queue_job_id
    local export_vars

    case "${1:-}" in
        "")
            ;;
        --dry-run)
            dry_run=1
            ;;
        -h|--help)
            usage
            return 0
            ;;
        *)
            usage >&2
            die "Unknown argument '$1'."
            ;;
    esac

    cd "${REPO_ROOT}"
    ensure_results_dirs
    [ -f "${payload}" ] || die "Missing scaling payload: ${payload}"
    if [ -z "${SCALING_DEPENDENCY}" ]; then
        ensure_no_running_gpu_allocation || die "Release the existing GPU allocation before submitting scaling."
    else
        info "Dependency set (${SCALING_DEPENDENCY}); skipping running-allocation guard."
    fi
    select_scaling_resource

    info "Selected partition: ${SCALING_PARTITION}"
    info "Selected GPU: ${SCALING_GPU_TYPE}"
    info "Slurm estimate: ${SCALING_TEST_OUTPUT}"
    if [ "${dry_run}" -eq 1 ]; then
        return 0
    fi

    # Runs in a command substitution, so name-tag resolution cannot live inside
    # build_scaling_export_vars (a subshell variable never reaches sbatch).
    SCALING_COMPRESSION_MODE="${QUEST_SCALING_COMPRESSION_MODE:-${SCALING_COMPRESSION_MODE:-native}}"
    SCALING_STAGING_MODE="${QUEST_SCALING_STAGING_MODE:-none}"
    SCALING_ARM_TAG="${SCALING_COMPRESSION_MODE}"
    [ "${SCALING_STAGING_MODE}" = "none" ] || SCALING_ARM_TAG="${SCALING_COMPRESSION_MODE}_window"
    export_vars="$(build_scaling_export_vars)"

    job_id="$(
        sbatch --parsable \
            --account="${SCALING_ACCOUNT}" \
            --qos="${SCALING_QOS}" \
            --partition="${SCALING_PARTITION}" \
            --gres="${SCALING_GPU_GRES}:${SCALING_GPUS}" \
            --nodes=1 \
            --ntasks="${SCALING_GPUS}" \
            --ntasks-per-node="${SCALING_GPUS}" \
            --cpus-per-task="${SCALING_CPUS_PER_TASK}" \
            --mem="${SCALING_MEM}" \
            ${SCALING_DEP_FLAG} \
            --time="${SCALING_WALLTIME}" \
            --job-name="quest-scaling-${SCALING_GPU_TYPE}-${SCALING_ARM_TAG:-${SCALING_COMPRESSION_MODE}}" \
            --output="experiments/results/raw/gpu_mpi_scaling_${SCALING_GPU_TYPE}_${SCALING_ARM_TAG:-${SCALING_COMPRESSION_MODE}}_%j.out" \
            --error="experiments/results/raw/gpu_mpi_scaling_${SCALING_GPU_TYPE}_${SCALING_ARM_TAG:-${SCALING_COMPRESSION_MODE}}_%j.err" \
            --export="${export_vars}" \
            "${payload}"
    )"
    queue_job_id="${job_id%%;*}"
    info "Submitted job: ${job_id}"
    info "Monitor with: squeue -j ${queue_job_id}"
    info "Collect with: bash experiments/scripts/collect_cluster_gpu_mpi_scaling.sh ${queue_job_id}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
