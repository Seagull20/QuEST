#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COLLECTOR="${SCRIPT_DIR}/collect_cluster_gpu_mpi_scaling.sh"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_TEST}"' EXIT

# shellcheck source=/dev/null
source "${COLLECTOR}"
printf 'alpha\n' > "${TMPDIR_TEST}/a.txt"
mkdir -p "${TMPDIR_TEST}/nested"
printf 'beta\n' > "${TMPDIR_TEST}/nested/b.txt"

write_campaign_checksums "${TMPDIR_TEST}"
(
    cd "${TMPDIR_TEST}"
    sha256sum -c SHA256SUMS >/dev/null
)

entries="$(wc -l < "${TMPDIR_TEST}/SHA256SUMS" | tr -d ' ')"
[ "${entries}" -eq 2 ] || { echo "expected 2 checksum entries, got ${entries}" >&2; exit 1; }
printf 'Scaling collector tests passed.\n'
