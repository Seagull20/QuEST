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
BENCH_QUBIT="${BENCH_QUBIT:-}"
VALIDATION_KIND="${VALIDATION_KIND:-h_last}"
PROBE_LABEL="${PROBE_LABEL:-probe_${VALIDATION_KIND}}"
RUN_RAW_DIR="$(current_raw_results_dir)"
NODES="${SLURM_JOB_NUM_NODES:-4}"
THREADS="${SLURM_CPUS_PER_TASK:-128}"
if [ -n "${BENCH_QUBIT}" ]; then
    SEARCH_MIN="${BENCH_QUBIT}"
    SEARCH_MAX="${BENCH_QUBIT}"
    OUTPUT_PATH="${RUN_RAW_DIR}/probe_archer2_${BACKEND}_on_n${NODES}_q${BENCH_QUBIT}_t${THREADS}_${VALIDATION_KIND}.tsv"
else
    OUTPUT_PATH="${RUN_RAW_DIR}/probe_archer2_${BACKEND}_on_n${NODES}_t${THREADS}_${VALIDATION_KIND}.tsv"
fi
EXE="${REPO_ROOT}/experiments/build/probe/${BACKEND}/probe"

[ -x "${EXE}" ] || die "Missing executable: ${EXE}"

export OMP_NUM_THREADS="${THREADS}"
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export BENCH_PLATFORM

info "ARCHER2 distributed probe"
info "Node count: ${NODES}"
info "OMP_NUM_THREADS=${OMP_NUM_THREADS}"
info "Validation kind: ${VALIDATION_KIND}"
if [ -n "${BENCH_QUBIT}" ]; then
    info "Exact probe qubit: ${BENCH_QUBIT}"
fi
info "Search range: ${SEARCH_MIN}..${SEARCH_MAX}"
info "Output: ${OUTPUT_PATH}"

srun --hint=nomultithread --cpu-bind=cores \
    "${EXE}" \
    --distribution on \
    --sync-mode benchmark \
    --preheat-mode off \
    --search-min "${SEARCH_MIN}" \
    --search-max "${SEARCH_MAX}" \
    --validation-kind "${VALIDATION_KIND}" \
    --label "${PROBE_LABEL}" \
    --output "${OUTPUT_PATH}"
