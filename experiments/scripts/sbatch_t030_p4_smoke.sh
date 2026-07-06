#!/usr/bin/env bash
#SBATCH --job-name=t030-p4-smoke
#SBATCH --time=00:30:00

set -euo pipefail

SCRIPT_PATH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ -f "${SLURM_SUBMIT_DIR}/experiments/build.sh" ]; then
    REPO_ROOT="${SLURM_SUBMIT_DIR}"
else
    REPO_ROOT="${SCRIPT_PATH_ROOT}"
fi
SCRIPT_DIR="${REPO_ROOT}/experiments/scripts"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

detect_nvcomp_root() {
    local candidate
    for candidate in \
        "${NVCOMP_ROOT:-}" \
        "${CONDA_PREFIX:-}" \
        "${HOME}/miniconda3/envs/quest_compression" \
        "${HOME}/miniconda3/envs/quest_env" \
        "/usr/local"; do
        [ -n "${candidate}" ] || continue
        if [ -f "${candidate}/include/nvcomp.hpp" ]; then
            printf '%s' "${candidate}"
            return 0
        fi
    done
    return 1
}

configure_nvcomp_runtime() {
    local root
    if [ -z "${NVCOMP_ROOT:-}" ] && root="$(detect_nvcomp_root)"; then
        export NVCOMP_ROOT="${root}"
    fi
    if [ -n "${NVCOMP_ROOT:-}" ] && [ -d "${NVCOMP_ROOT}/lib" ]; then
        export LD_LIBRARY_PATH="${NVCOMP_ROOT}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    elif [ -n "${NVCOMP_ROOT:-}" ] && [ -d "${NVCOMP_ROOT}/lib64" ]; then
        export LD_LIBRARY_PATH="${NVCOMP_ROOT}/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    fi
}

run_check() {
    local label="$1"
    local compression="$2"
    local verify="$3"
    local output="${RUN_DIR}/${label}.stdout"
    local error="${RUN_DIR}/${label}.stderr"
    local -a launcher=(mpirun)
    local var

    export QUEST_ENABLE_EXCHANGE_COMPRESSION="${compression}"
    export QUEST_EXCHANGE_COMPRESSION_VERIFY="${verify}"
    for var in NVCOMP_ROOT LD_LIBRARY_PATH QUEST_ENABLE_EXCHANGE_COMPRESSION QUEST_EXCHANGE_COMPRESSION_VERIFY QUEST_EXCHANGE_COMPRESSION_STATS; do
        if [ -n "${!var:-}" ]; then
            launcher+=(-x "${var}")
        fi
    done
    "${launcher[@]}" -np 2 "${REPO_ROOT}/experiments/build/compression_check/gpu_mpi/compression_check" 24 \
        >"${output}" 2>"${error}"
}

normalise_output() {
    sed -E '/^(q[0-9]+ ranks=|DONE$)/d' "$1" | sort
}

main() {
    local job_id="${SLURM_JOB_ID:-manual}"
    cd "${REPO_ROOT}"
    ensure_results_dirs
    source_system_profile_if_present
    source_toolchain_env_if_present
    ensure_minimum_cmake 3.21
    configure_nvcomp_runtime

    RUN_DIR="$(current_raw_results_dir)/t030_p4_smoke_${job_id}"
    mkdir -p "${RUN_DIR}"
    {
        printf 'slurm_job_id=%s\n' "${job_id}"
        printf 'hostname=%s\n' "$(hostname)"
        printf 'git_commit=%s\n' "$(git rev-parse HEAD)"
        printf 'NVCOMP_ROOT=%s\n' "${NVCOMP_ROOT:-unset}"
        printf 'LD_LIBRARY_PATH=%s\n' "${LD_LIBRARY_PATH:-unset}"
        printf 'QUEST_EXCHANGE_COMPRESSION_STATS=%s\n' "${QUEST_EXCHANGE_COMPRESSION_STATS:-unset}"
    } > "${RUN_DIR}/environment.txt"

    QUEST_BENCH_ENABLE_NVCOMP=1 "${REPO_ROOT}/experiments/build.sh" compression_check gpu_mpi Release \
        >"${RUN_DIR}/build.stdout" 2>"${RUN_DIR}/build.stderr"

    run_check off 0 0
    run_check on 1 0
    run_check verify 1 1

    normalise_output "${RUN_DIR}/off.stdout" > "${RUN_DIR}/off.normalised"
    normalise_output "${RUN_DIR}/on.stdout" > "${RUN_DIR}/on.normalised"
    normalise_output "${RUN_DIR}/verify.stdout" > "${RUN_DIR}/verify.normalised"
    diff -u "${RUN_DIR}/off.normalised" "${RUN_DIR}/on.normalised" > "${RUN_DIR}/off_vs_on.diff"
    diff -u "${RUN_DIR}/off.normalised" "${RUN_DIR}/verify.normalised" > "${RUN_DIR}/off_vs_verify.diff"
    grep -q '\[quest-nvcomp\] exchange compression ENABLED' "${RUN_DIR}/on.stderr"
    grep -q '\[quest-nvcomp\] exchange compression ENABLED' "${RUN_DIR}/verify.stderr"

    (
        cd "${RUN_DIR}"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    )
    info "T-030 P4 smoke complete: ${RUN_DIR}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
