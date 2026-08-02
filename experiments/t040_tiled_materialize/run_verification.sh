#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
T039_SCRIPT="${SCRIPT_DIR}/../t039_bulk_async/run_verification.sh"
BUILD_DIR="${T040_BUILD_DIR:-${SCRIPT_DIR}/build}"
RESULT_ROOT="${T040_RESULT_DIR:-${SCRIPT_DIR}/results/$(date -u +%Y%m%dT%H%M%SZ)}"

[ -x "${T039_SCRIPT}" ] || {
    printf 'T040_VERIFY_ERROR: missing T-039 driver at %s\n' "${T039_SCRIPT}" >&2
    exit 1
}

run_tile_case() {
    local label="$1"
    local variable="$2"
    local value="$3"
    local result_dir="${RESULT_ROOT}/${label}"

    mkdir -p "${result_dir}"
    (
        export T039_WINDOW_MODE=tiled_materialize
        export T039_EXPECT_WINDOW_MODE=tiled_materialize
        export T039_BUILD_DIR="${BUILD_DIR}"
        export T039_RESULT_DIR="${result_dir}"
        unset QUEST_GPU_STAGING_TILE_BYTES QUEST_GPU_STAGING_TILE_MB
        if [ "${variable}" = bytes ]; then
            export QUEST_GPU_STAGING_TILE_BYTES="${value}"
        else
            export QUEST_GPU_STAGING_TILE_MB="${value}"
        fi
        bash "${T039_SCRIPT}"
    )
}

# T-073's frozen point and its two comparison points. The final bytes-sized
# case is intentionally not a power-of-two tile: it exercises the descriptor
# and offset tail without changing the full gpuCommBuffer postcondition.
run_tile_case tile-4MiB mb 4
run_tile_case tile-16MiB mb 16
run_tile_case tile-64MiB mb 64
run_tile_case partial-tail bytes 20000000

printf 'T040_TILED_MATERIALIZE_RAW_OFF_PASS results=%s\n' "${RESULT_ROOT}"
