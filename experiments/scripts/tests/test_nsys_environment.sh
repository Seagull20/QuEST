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

fake_bin="${temp_root}/bin"
report="${temp_root}/qft.nsys-rep"
sqlite_output="${temp_root}/profiles/qft.sqlite"
args_output="${temp_root}/nsys-args.txt"
mkdir -p "${fake_bin}" "$(dirname "${sqlite_output}")"
: > "${report}"
cat > "${fake_bin}/nsys" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "${NSYS_TEST_ARGS_OUTPUT}"
while [ "$#" -gt 0 ]; do
    if [ "$1" = "--output" ]; then
        shift
        printf 'sqlite' > "$1"
        exit 0
    fi
    shift
done
exit 1
EOF
chmod +x "${fake_bin}/nsys"

PATH="${fake_bin}:${PATH}" NSYS_TEST_ARGS_OUTPUT="${args_output}" \
    export_nsys_sqlite "${report}" "${sqlite_output}"
[ -s "${sqlite_output}" ]
grep -Fx -- '--output' "${args_output}" >/dev/null
grep -Fx -- "${sqlite_output}" "${args_output}" >/dev/null
