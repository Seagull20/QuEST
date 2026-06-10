#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../common.sh
. "${SCRIPT_DIR}/common.sh"

temp_root="$(mktemp -d)"
trap 'rm -rf "${temp_root}"' EXIT

expected="${temp_root}/targets/x86_64-linux/include"
mkdir -p "${expected}/nvtx3"
: > "${expected}/nvtx3/nvToolsExt.h"

actual="$(find_nvtx_include_dir "${temp_root}")"
[ "${actual}" = "${expected}" ] || {
    printf 'expected %s, got %s\n' "${expected}" "${actual}" >&2
    exit 1
}
