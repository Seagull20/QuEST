#!/usr/bin/env bash
#SBATCH --job-name=quest-comp-nvtx
#SBATCH --partition=Interactive
#SBATCH --account=general-teaching
#SBATCH --qos=teaching
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=1
#SBATCH --time=01:00:00
#SBATCH --output=experiments/results/raw/compression_toy_nvtx_%j_slurm.out

set -euo pipefail

if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ -d "${SLURM_SUBMIT_DIR}/experiments/compression_toy_benchmark" ]; then
    QUEST_ROOT="$(cd "${SLURM_SUBMIT_DIR}" && pwd)"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    QUEST_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
fi
BENCH_ROOT="${QUEST_ROOT}/experiments/compression_toy_benchmark"
COMMON_SH="${QUEST_ROOT}/experiments/scripts/common.sh"

# shellcheck disable=SC1090
. "${COMMON_SH}"

activate_quest_compression_env() {
    local env_dir="${QUEST_COMPRESSION_CONDA_PREFIX:-${HOME}/miniconda3/envs/quest_compression}"
    [ -d "${env_dir}" ] || die "quest_compression env directory not found: ${env_dir}"
    export CONDA_PREFIX="${env_dir}"
    export PATH="${CONDA_PREFIX}/bin:${PATH}"
    export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
    export CMAKE_PREFIX_PATH="${CONDA_PREFIX}:${CMAKE_PREFIX_PATH:-}"
}

resolve_nvcomp_root() {
    local nvcomp_header=""
    if [ -n "${NVCOMP_ROOT:-}" ] && [ -f "${NVCOMP_ROOT}/include/nvcomp.hpp" ]; then
        return 0
    fi
    if [ -n "${CONDA_PREFIX:-}" ]; then
        nvcomp_header="$(find "${CONDA_PREFIX}" -path '*/include/nvcomp.hpp' -print -quit 2>/dev/null || true)"
    fi
    if [ -z "${nvcomp_header}" ]; then
        nvcomp_header="$(find "${HOME}" -path '*/include/nvcomp.hpp' -print -quit 2>/dev/null || true)"
    fi
    [ -n "${nvcomp_header}" ] || die "nvCOMP header include/nvcomp.hpp not found; set NVCOMP_ROOT"
    export NVCOMP_ROOT="$(cd "$(dirname "${nvcomp_header}")/.." && pwd)"
}

main() {
    cd "${QUEST_ROOT}"
    source_system_profile_if_present
    if type module >/dev/null 2>&1; then
        module load cuda/12.8.0 >/dev/null 2>&1 || module load cuda >/dev/null 2>&1 || true
    fi
    ensure_minimum_cmake 3.21
    ensure_nsys_available
    activate_quest_compression_env
    resolve_nvcomp_root

    export QUEST_COMPRESSION_CUDA_ARCH="${QUEST_COMPRESSION_CUDA_ARCH:-75}"
    export QUEST_COMPRESSION_ENABLE_NVTX=1
    export QUEST_BENCH_BUILD_PARALLEL="${QUEST_BENCH_BUILD_PARALLEL:-${SLURM_CPUS_ON_NODE:-4}}"

    local job_id="${SLURM_JOB_ID:-manual}"
    local raw_dir="${QUEST_ROOT}/experiments/results/raw/compression_toy_nvtx_${job_id}"
    local processed_dir="${QUEST_ROOT}/experiments/results/processed/compression_toy_nvtx_${job_id}"
    mkdir -p "${raw_dir}" "${processed_dir}" "${raw_dir}/logs"

    {
        printf 'timestamp\t%s\n' "$(date -Is)"
        printf 'hostname\t%s\n' "$(hostname)"
        printf 'whoami\t%s\n' "$(whoami)"
        printf 'pwd\t%s\n' "$(pwd)"
        printf 'branch\t%s\n' "$(git branch --show-current)"
        git log --oneline -3
        printf '\n[nvcc]\n'
        nvcc --version || true
        printf '\n[mpirun]\n'
        mpirun --version || true
        printf '\n[nsys]\n'
        nsys --version || true
        printf '\n[paths]\n'
        printf 'CONDA_PREFIX=%s\n' "${CONDA_PREFIX:-}"
        printf 'NVCOMP_ROOT=%s\n' "${NVCOMP_ROOT:-}"
        printf 'QUEST_NVTX_INCLUDE_DIR=%s\n' "${QUEST_NVTX_INCLUDE_DIR:-}"
        printf 'QUEST_COMPRESSION_CUDA_ARCH=%s\n' "${QUEST_COMPRESSION_CUDA_ARCH}"
        printf '\n[slurm]\n'
        env | sort | grep -E '^(SLURM|CUDA|OMPI|NVCOMP|QUEST_)' || true
    } > "${raw_dir}/environment.log"

    "${BENCH_ROOT}/build.sh" all 2>&1 | tee "${raw_dir}/logs/build_nvtx.log"

    python3 "${BENCH_ROOT}/scripts/run_nvtx_profile.py" \
        --raw-dir "${raw_dir}" \
        --processed-dir "${processed_dir}" \
        --ranks 4 \
        --qubits 28 \
        --payload-amps 0 \
        --allocation-id "${job_id}" \
        --warmup 0 \
        --reps 3 \
        2>&1 | tee "${raw_dir}/logs/run_nvtx_profile.log"

    info "NVTX raw_dir=${raw_dir}"
    info "NVTX processed_dir=${processed_dir}"
}

main "$@"
