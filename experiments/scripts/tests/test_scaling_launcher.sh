#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCALING_SCRIPT="${SCRIPT_DIR}/sbatch_cluster_gpu_mpi_scaling.sh"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

test_existing_gpu_allocation_is_rejected() (
    # shellcheck source=/dev/null
    source "${SCALING_SCRIPT}"
    squeue() { printf 'gres/gpu:nvidia_geforce_rtx_2080_ti:1\n'; }
    if ensure_no_running_gpu_allocation >/dev/null 2>&1; then
        fail "running GPU allocation was accepted"
    fi
)

test_near_term_a6000_is_preferred() (
    # shellcheck source=/dev/null
    source "${SCALING_SCRIPT}"
    export QUEST_SCALING_NOW_EPOCH=1781197200
    squeue() { :; }
    sbatch() {
        case " $* " in
            *" gpu:nvidia_rtx_a6000:4 "*)
                printf 'sbatch: Job 10 to start at 2026-06-11T18:02:00 using 8 processors\n'
                ;;
            *)
                printf 'sbatch: Job 11 to start at 2026-06-11T18:00:00 using 8 processors\n'
                ;;
        esac
    }

    select_scaling_resource

    [ "${SCALING_PARTITION}" = "Teaching" ] || fail "expected Teaching, got ${SCALING_PARTITION}"
    [ "${SCALING_GPU_TYPE}" = "a6000" ] || fail "expected a6000, got ${SCALING_GPU_TYPE}"
)

test_delayed_a6000_falls_back_to_interactive_2080ti() (
    # shellcheck source=/dev/null
    source "${SCALING_SCRIPT}"
    export QUEST_SCALING_NOW_EPOCH=1781197200
    squeue() { :; }
    sbatch() {
        case " $* " in
            *" gpu:nvidia_rtx_a6000:4 "*)
                printf 'sbatch: Job 10 to start at 2026-06-12T18:00:00 using 8 processors\n'
                ;;
            *" --partition=Interactive "*)
                printf 'sbatch: Job 11 to start at 2026-06-11T18:01:00 using 8 processors\n'
                ;;
            *)
                return 1
                ;;
        esac
    }

    select_scaling_resource

    [ "${SCALING_PARTITION}" = "Interactive" ] || fail "expected Interactive, got ${SCALING_PARTITION}"
    [ "${SCALING_GPU_TYPE}" = "2080ti" ] || fail "expected 2080ti, got ${SCALING_GPU_TYPE}"
)

test_gpu_choice_2080ti_skips_a6000_probe() (
    # shellcheck source=/dev/null
    source "${SCALING_SCRIPT}"
    export QUEST_SCALING_GPU_CHOICE=2080ti
    a6000_probed=0
    squeue() { :; }
    sbatch() {
        case " $* " in
            *" gpu:nvidia_rtx_a6000:4 "*)
                a6000_probed=1
                return 1
                ;;
            *" --partition=Interactive "*"--gres=gpu:nvidia_geforce_rtx_2080_ti:4"*)
                printf 'sbatch: Job 11 to start at 2026-06-11T18:01:00 using 8 processors\n'
                ;;
            *)
                return 1
                ;;
        esac
    }

    select_scaling_resource

    [ "${a6000_probed}" -eq 0 ] || fail "A6000 was probed despite QUEST_SCALING_GPU_CHOICE=2080ti"
    [ "${SCALING_GPU_TYPE}" = "2080ti" ] || fail "expected 2080ti, got ${SCALING_GPU_TYPE}"
)

test_compression_mode_configures_exported_environment() (
    # shellcheck source=/dev/null
    source "${SCALING_SCRIPT}"
    export QUEST_SCALING_COMPRESSION_MODE=on
    build_scaling_export_vars >/tmp/scaling_exports.txt
    grep -q 'QUEST_SCALING_COMPRESSION_MODE=on' /tmp/scaling_exports.txt || fail "compression mode not exported"
    grep -q 'QUEST_BENCH_ENABLE_NVCOMP=1' /tmp/scaling_exports.txt || fail "nvCOMP build flag not exported"
    grep -q 'QUEST_ENABLE_EXCHANGE_COMPRESSION=1' /tmp/scaling_exports.txt || fail "runtime compression env not exported"

    export QUEST_SCALING_COMPRESSION_MODE=off
    build_scaling_export_vars >/tmp/scaling_exports.txt
    grep -q 'QUEST_ENABLE_EXCHANGE_COMPRESSION=0' /tmp/scaling_exports.txt || fail "compression-off env not exported"
)

test_existing_gpu_allocation_is_rejected
test_near_term_a6000_is_preferred
test_delayed_a6000_falls_back_to_interactive_2080ti
test_gpu_choice_2080ti_skips_a6000_probe
test_compression_mode_configures_exported_environment
printf 'Scaling launcher tests passed.\n'
