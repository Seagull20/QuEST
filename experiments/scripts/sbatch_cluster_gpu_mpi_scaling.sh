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
SCALING_A6000_MAX_WAIT_S="${QUEST_SCALING_A6000_MAX_WAIT_S:-300}"

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
    if test_candidate Teaching gpu:nvidia_rtx_a6000 && a6000_starts_soon; then
        SCALING_PARTITION="Teaching"
        SCALING_GPU_TYPE="a6000"
        SCALING_GPU_GRES="gpu:nvidia_rtx_a6000"
        return 0
    fi

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

    die "No accessible four-GPU A6000 or RTX 2080 Ti allocation passed Slurm validation."
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
    ensure_no_running_gpu_allocation || die "Release the existing GPU allocation before submitting scaling."
    select_scaling_resource

    info "Selected partition: ${SCALING_PARTITION}"
    info "Selected GPU: ${SCALING_GPU_TYPE}"
    info "Slurm estimate: ${SCALING_TEST_OUTPUT}"
    if [ "${dry_run}" -eq 1 ]; then
        return 0
    fi

    export_vars="ALL"
    export_vars="${export_vars},BENCH_PLATFORM=cluster"
    export_vars="${export_vars},QUEST_SCALING_PARTITION=${SCALING_PARTITION}"
    export_vars="${export_vars},QUEST_SCALING_GPU_TYPE=${SCALING_GPU_TYPE}"
    export_vars="${export_vars},QUEST_SCALING_GPU_GRES=${SCALING_GPU_GRES}"
    export_vars="${export_vars},QUEST_SCALING_GIT_COMMIT=$(git rev-parse HEAD)"
    export_vars="${export_vars},QUEST_BENCH_BUILD_PARALLEL=${QUEST_BENCH_BUILD_PARALLEL:-8}"

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
            --time="${SCALING_WALLTIME}" \
            --job-name="quest-scaling-${SCALING_GPU_TYPE}" \
            --output="experiments/results/raw/gpu_mpi_scaling_${SCALING_GPU_TYPE}_%j.out" \
            --error="experiments/results/raw/gpu_mpi_scaling_${SCALING_GPU_TYPE}_%j.err" \
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
