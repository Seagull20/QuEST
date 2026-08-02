#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${T039_LOCAL_BUILD_DIR:-/private/tmp/t039-quest-local-default-off}"

unset QUEST_GPU_STAGING_MODE QUEST_FORCE_CPU_STAGING QUEST_GPU_STAGING_STATS

cmake -S "${REPO_ROOT}" -B "${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_CUDA=OFF \
    -DENABLE_DISTRIBUTION=OFF \
    -DENABLE_MULTITHREADING=OFF \
    -DENABLE_TESTING=OFF \
    -DBUILD_EXAMPLES=OFF
cmake --build "${BUILD_DIR}" --parallel 4 --target QuEST

/usr/bin/c++ -std=c++17 -fsyntax-only \
    -I"${BUILD_DIR}" -I"${REPO_ROOT}" -I"${REPO_ROOT}/quest/include" \
    "${SCRIPT_DIR}/src/t039_bulk_async_verify.cpp"

extract_exchange_arrays() {
    awk '
        /^void exchangeArrays\(qcomp\* send/ { capture=1 }
        capture { print }
        capture && /^}$/ { exit }
    '
}

hash_stream() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
    else
        shasum -a 256 | awk '{print $1}'
    fi
}

BASE_HASH="$(git -C "${REPO_ROOT}" show 114e021:quest/src/comm/comm_routines.cpp | extract_exchange_arrays | hash_stream)"
CURRENT_HASH="$(extract_exchange_arrays < "${REPO_ROOT}/quest/src/comm/comm_routines.cpp" | hash_stream)"

[ "${BASE_HASH}" = "${CURRENT_HASH}" ] || {
    printf 'T039_DEFAULT_OFF_ERROR: raw exchangeArrays changed (base=%s current=%s)\n' \
        "${BASE_HASH}" "${CURRENT_HASH}" >&2
    exit 1
}

printf 'T039_DEFAULT_OFF_BUILD_PASS build=%s\n' "${BUILD_DIR}"
printf 'T039_VERIFY_SOURCE_SYNTAX_PASS source=%s\n' \
    "${SCRIPT_DIR}/src/t039_bulk_async_verify.cpp"
printf 'T039_DEFAULT_OFF_RAW_FUNCTION_PASS sha256=%s\n' "${CURRENT_HASH}"
