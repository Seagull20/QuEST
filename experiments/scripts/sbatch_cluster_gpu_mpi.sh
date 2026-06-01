#!/usr/bin/env bash
# Submit the MLS-cluster GPU+MPI QFT smoke job.
#
# Usage, from the repo root on the cluster login node:
#   bash experiments/scripts/sbatch_cluster_gpu_mpi.sh [auto|a40|2080ti] [ranks]
#
# Defaults:
#   GPU type: auto
#   ranks:    QUEST_GPU_MPI_RANKS, or 2
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

usage() {
    cat <<'EOF'
Usage:
  bash experiments/scripts/sbatch_cluster_gpu_mpi.sh [auto|a40|2080ti] [ranks]

Environment overrides:
  QUEST_GPU_MPI_QUBITS          default: 24
  QUEST_GPU_MPI_REPS            default: 1
  QUEST_GPU_MPI_WARMUP          default: 0
  QUEST_GPU_MPI_SYNC_MODE       default: benchmark
  QUEST_GPU_MPI_PREHEAT_MODE    default: off
  QUEST_GPU_MPI_PREHEAT_QUBITS  default: 24

The submitted job runs qft/gpu_mpi with --distribution on. QuEST owns the
rank-to-GPU mapping; this launcher does not set CUDA_VISIBLE_DEVICES.
EOF
}

is_positive_int() {
    case "$1" in
        ''|*[!0-9]*)
            return 1
            ;;
        0)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

state_score() {
    local state_lc

    state_lc="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    case "${state_lc}" in
        *idle*)
            printf '0'
            ;;
        *mix*|*comp*)
            printf '1'
            ;;
        *alloc*)
            printf '2'
            ;;
        *)
            printf '3'
            ;;
    esac
}

state_is_usable() {
    local state_lc

    state_lc="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    case "${state_lc}" in
        *down*|*drain*|*fail*|*maint*|*resv*)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

node_matches_type() {
    local gpu_type="$1"
    local node="$2"
    local gres="$3"

    case "${gpu_type}" in
        a40)
            case "${node}:${gres}" in
                crannog*:*|*:gpu:a40:*|*:gpu:a40|*:a40*)
                    return 0
                    ;;
            esac
            ;;
        2080ti)
            case "${node}:${gres}" in
                damnii*:*|*:gpu:nvidia_geforce_rtx_2080_ti:*|*:gpu:nvidia_geforce_rtx_2080_ti|*:2080*)
                    return 0
                    ;;
            esac
            ;;
    esac

    return 1
}

best_node_for_type() {
    local gpu_type="$1"
    local node gres state score
    local best_node=""
    local best_state=""
    local best_score=99

    if ! command -v sinfo >/dev/null 2>&1; then
        return 1
    fi

    while IFS='|' read -r node gres state; do
        [ -n "${node}" ] || continue
        node_matches_type "${gpu_type}" "${node}" "${gres}" || continue
        state_is_usable "${state}" || continue

        score="$(state_score "${state}")"
        if [ "${score}" -lt "${best_score}" ]; then
            best_node="${node}"
            best_state="${state}"
            best_score="${score}"
        fi
    done < <(sinfo -h -p Teaching -N -o '%N|%G|%T' 2>/dev/null || true)

    [ -n "${best_node}" ] || return 1
    printf '%s|%s|%s' "${best_node}" "${best_state}" "${best_score}"
}

auto_gpu_type() {
    local a40_info ti_info
    local a40_score=99
    local ti_score=99

    a40_info="$(best_node_for_type a40 || true)"
    ti_info="$(best_node_for_type 2080ti || true)"

    if [ -n "${a40_info}" ]; then
        a40_score="$(printf '%s' "${a40_info}" | awk -F'|' '{print $3}')"
    fi
    if [ -n "${ti_info}" ]; then
        ti_score="$(printf '%s' "${ti_info}" | awk -F'|' '{print $3}')"
    fi

    if [ "${a40_score}" -eq 99 ] && [ "${ti_score}" -eq 99 ]; then
        die "No usable A40 or 2080 Ti node found in sinfo."
    fi

    if [ "${a40_score}" -le "${ti_score}" ]; then
        printf 'a40'
    else
        printf '2080ti'
    fi
}

resolve_gpu_config() {
    local gpu_type="$1"
    local node_info node_from_sinfo state_from_sinfo

    case "${gpu_type}" in
        a40)
            GPU_NODE="crannog01"
            GPU_GRES_PREFIX="gpu:a40"
            GPU_MAX_RANKS=4
            GPU_CPUS_PER_TASK=2
            GPU_WALLTIME="${QUEST_GPU_MPI_WALLTIME:-00:45:00}"
            ;;
        2080ti)
            GPU_NODE="damnii07"
            GPU_GRES_PREFIX="gpu:nvidia_geforce_rtx_2080_ti"
            GPU_MAX_RANKS=8
            GPU_CPUS_PER_TASK=1
            GPU_WALLTIME="${QUEST_GPU_MPI_WALLTIME:-00:45:00}"
            ;;
        *)
            die "Unknown GPU type '${gpu_type}'. Use: auto | a40 | 2080ti"
            ;;
    esac

    node_info="$(best_node_for_type "${gpu_type}" || true)"
    if [ -n "${node_info}" ]; then
        node_from_sinfo="$(printf '%s' "${node_info}" | awk -F'|' '{print $1}')"
        state_from_sinfo="$(printf '%s' "${node_info}" | awk -F'|' '{print $2}')"
        GPU_NODE="${node_from_sinfo}"
        info "Selected ${gpu_type} node from sinfo: ${GPU_NODE} (${state_from_sinfo})"
    else
        warn "Could not confirm a usable ${gpu_type} node with sinfo; falling back to ${GPU_NODE}."
    fi
}

main() {
    local requested_gpu_type="${1:-auto}"
    local ranks="${2:-${QUEST_GPU_MPI_RANKS:-2}}"
    local gpu_type
    local export_vars
    local job_id
    local queue_job_id
    local sbatch_script="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_qft_point.sh"

    case "${requested_gpu_type}" in
        -h|--help)
            usage
            return 0
            ;;
    esac

    case "${requested_gpu_type}" in
        auto|a40|2080ti)
            ;;
        *)
            die "Unknown GPU type '${requested_gpu_type}'. Use: auto | a40 | 2080ti"
            ;;
    esac

    is_positive_int "${ranks}" || die "Ranks must be a positive integer, got '${ranks}'."
    command -v sbatch >/dev/null 2>&1 || die "sbatch not found. Run this from the cluster login node."
    [ -f "${sbatch_script}" ] || die "Missing batch script: ${sbatch_script}"

    cd "${REPO_ROOT}"
    ensure_results_dirs

    if [ "${requested_gpu_type}" = "auto" ]; then
        gpu_type="$(auto_gpu_type)"
    else
        gpu_type="${requested_gpu_type}"
    fi

    resolve_gpu_config "${gpu_type}"
    if [ "${ranks}" -gt "${GPU_MAX_RANKS}" ]; then
        die "${gpu_type} supports at most ${GPU_MAX_RANKS} ranks in this smoke launcher; requested ${ranks}."
    fi

    export_vars="ALL"
    export_vars="${export_vars},BENCH_PLATFORM=${BENCH_PLATFORM:-cluster}"
    export_vars="${export_vars},QUEST_GPU_MPI_GPU_TYPE=${gpu_type}"
    export_vars="${export_vars},QUEST_GPU_MPI_RANKS=${ranks}"
    export_vars="${export_vars},QUEST_GPU_MPI_QUBITS=${QUEST_GPU_MPI_QUBITS:-24}"
    export_vars="${export_vars},QUEST_GPU_MPI_REPS=${QUEST_GPU_MPI_REPS:-1}"
    export_vars="${export_vars},QUEST_GPU_MPI_WARMUP=${QUEST_GPU_MPI_WARMUP:-0}"
    export_vars="${export_vars},QUEST_GPU_MPI_SYNC_MODE=${QUEST_GPU_MPI_SYNC_MODE:-benchmark}"
    export_vars="${export_vars},QUEST_GPU_MPI_PREHEAT_MODE=${QUEST_GPU_MPI_PREHEAT_MODE:-off}"
    export_vars="${export_vars},QUEST_GPU_MPI_PREHEAT_QUBITS=${QUEST_GPU_MPI_PREHEAT_QUBITS:-24}"

    info "Submitting QFT gpu_mpi smoke"
    info "GPU type: ${gpu_type}"
    info "Node: ${GPU_NODE}"
    info "Ranks/GPUs: ${ranks}"
    info "Qubits: ${QUEST_GPU_MPI_QUBITS:-24}"

    job_id="$(
        sbatch --parsable \
            --partition=Teaching \
            --nodelist="${GPU_NODE}" \
            --gres="${GPU_GRES_PREFIX}:${ranks}" \
            --nodes=1 \
            --ntasks="${ranks}" \
            --ntasks-per-node="${ranks}" \
            --cpus-per-task="${GPU_CPUS_PER_TASK}" \
            --time="${GPU_WALLTIME}" \
            --job-name="quest-qft-gpu-mpi-${gpu_type}-r${ranks}" \
            --output="experiments/results/raw/qft_gpu_mpi_${gpu_type}_r${ranks}_%j.out" \
            --error="experiments/results/raw/qft_gpu_mpi_${gpu_type}_r${ranks}_%j.err" \
            --export="${export_vars}" \
            "${sbatch_script}"
    )"
    queue_job_id="${job_id%%;*}"

    info "Submitted job: ${job_id}"
    info "Monitor with: squeue -j ${queue_job_id}"
}

main "$@"
