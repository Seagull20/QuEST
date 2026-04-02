#!/usr/bin/env bash
#SBATCH --job-name=quest-probe-cpu
#SBATCH --account=m25ext-s2866920
#SBATCH --partition=standard
#SBATCH --qos=short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --time=00:15:00
#SBATCH --output=experiments/results/raw/archer2_%x_%j.out
#SBATCH --error=experiments/results/raw/archer2_%x_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=common.sh
. "${REPO_ROOT}/experiments/scripts/common.sh"

cd "${REPO_ROOT}"
ensure_results_dirs
source_toolchain_env_if_present

RUN_RAW_DIR="$(current_raw_results_dir)"
PROBE_EXE="${REPO_ROOT}/experiments/build/probe/cpu_mpi/probe"
SEARCH_MIN="${SEARCH_MIN:-1}"
SEARCH_MAX="${SEARCH_MAX:-62}"
SYNC_MODE="${SYNC_MODE:-benchmark}"
DEPLOYMENT="${DEPLOYMENT:-off}"

[ -x "${PROBE_EXE}" ] || die "Missing probe executable: ${PROBE_EXE}"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-32}"
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export BENCH_PLATFORM="${BENCH_PLATFORM:-archer2}"

OUTPUT_PATH="${RUN_RAW_DIR}/probe_archer2_cpu_mpi_${DEPLOYMENT}.tsv"

info "ARCHER2 probe-only job"
info "Node: $(hostname)"
info "Output: ${OUTPUT_PATH}"

"${PROBE_EXE}" \
    --distribution "${DEPLOYMENT}" \
    --sync-mode "${SYNC_MODE}" \
    --search-min "${SEARCH_MIN}" \
    --search-max "${SEARCH_MAX}" \
    --output "${OUTPUT_PATH}"
