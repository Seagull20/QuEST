#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

BENCHMARK="${1:?benchmark is required}"
BACKEND="${2:?backend is required}"
shift 2

EXE="${EXPERIMENTS_DIR}/build/${BENCHMARK}/${BACKEND}/${BENCHMARK}"
[ -x "${EXE}" ] || die "Benchmark executable not found: ${EXE}"
command -v ncu >/dev/null 2>&1 || die "ncu not found"

PROFILE_OUT="${RAW_RESULTS_DIR}/${BENCHMARK}_${BACKEND}_$(date +%Y%m%d_%H%M%S)"
info "Profiling with Nsight Compute -> ${PROFILE_OUT}.ncu-rep"

BENCH_PLATFORM="${BENCH_PLATFORM:-$(hostname)}" \
ncu --set full --force-overwrite --export "${PROFILE_OUT}" \
    "${EXE}" --sync-mode profile "$@"
