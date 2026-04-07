#!/usr/bin/env bash
#SBATCH --job-name=quest-dist-probe
#SBATCH --account=m25ext-s2866920
#SBATCH --partition=standard
#SBATCH --qos=short
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:20:00
#SBATCH --output=experiments/results/raw/archer2_%x_%j.out
#SBATCH --error=experiments/results/raw/archer2_%x_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present
ensure_minimum_cmake 3.21

BACKEND="${BACKEND:-cpu_mpi}"
BENCH_PLATFORM="${BENCH_PLATFORM:-archer2}"
SEARCH_MIN="${SEARCH_MIN:-32}"
SEARCH_MAX="${SEARCH_MAX:-35}"
RUN_RAW_DIR="$(current_raw_results_dir)"
OUTPUT_PATH="${RUN_RAW_DIR}/probe_archer2_${BACKEND}_on_n${SLURM_JOB_NUM_NODES:-4}_t${SLURM_CPUS_PER_TASK:-128}.tsv"
EXE="${REPO_ROOT}/experiments/build/probe/${BACKEND}/probe"

[ -x "${EXE}" ] || die "Missing executable: ${EXE}"

export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-128}"
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export BENCH_PLATFORM

info "ARCHER2 distributed H-last probe"
info "Node count: ${SLURM_JOB_NUM_NODES:-unknown}"
info "OMP_NUM_THREADS=${OMP_NUM_THREADS}"
info "Search range: ${SEARCH_MIN}..${SEARCH_MAX}"
info "Output: ${OUTPUT_PATH}"

srun --hint=nomultithread --cpu-bind=cores \
    "${EXE}" \
    --distribution on \
    --sync-mode benchmark \
    --preheat-mode off \
    --search-min "${SEARCH_MIN}" \
    --search-max "${SEARCH_MAX}" \
    --validation-kind h_last \
    --label "probe_h_last" \
    --output "${OUTPUT_PATH}"
