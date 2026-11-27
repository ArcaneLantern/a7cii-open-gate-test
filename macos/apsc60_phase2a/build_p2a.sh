#!/bin/bash
# Build A7C II 2.01 Phase 2A apply & putback libJit.so binaries using /tmp/venv_zig.
# Flags match windows/tools/camera_tests/CT6_memory_read_check/helper_c2/build_c2_write.sh exactly.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SEN="$REPO/windows/camera_private/extracted_a7c2_201/0700_part_image/dev/nflasha15/lib/libsencore.so"
ZIG_PY="/tmp/venv_zig/bin/python"

if [ ! -x "$ZIG_PY" ]; then
    echo "ERROR: $ZIG_PY not found. Install ziglang in isolated /tmp/venv_zig first." >&2
    exit 1
fi
if [ ! -f "$SEN" ]; then
    echo "ERROR: $SEN not found." >&2
    exit 1
fi

echo "== Generating and verifying expected_sites_apsc60_p2a.h =="
python3 -B "$HERE/gen_p2a_sites.py"

CC=(
    "$ZIG_PY" -m ziglang cc
    -target aarch64-linux-gnu
    -std=gnu11 -O1 -Wall -Wextra -Werror
    -fPIC -shared -nostdlib -ffreestanding
    -fno-stack-protector -fno-builtin -fno-asynchronous-unwind-tables
    -Wl,-soname,libJit.so
    -Wl,--no-undefined -Wl,-z,now -g0 -s
    -Wl,-z,max-page-size=4096
)

OUT_APPLY="$HERE/libJit_c2_apsc60_p2a_apply.so"
OUT_PUTBACK="$HERE/libJit_c2_apsc60_p2a_putback.so"

echo "== Compiling libJit_c2_apsc60_p2a_apply.so =="
"${CC[@]}" -DP2A_APPLY=1 "$HERE/apsc60_p2a.c" "$SEN" -o "$OUT_APPLY"

echo "== Compiling libJit_c2_apsc60_p2a_putback.so =="
"${CC[@]}" -DP2A_APPLY=0 "$HERE/apsc60_p2a.c" "$SEN" -o "$OUT_PUTBACK"

echo "== Built binaries =="
for f in "$OUT_APPLY" "$OUT_PUTBACK"; do
    sz=$(stat -f %z "$f")
    sha=$(shasum -a 256 "$f" | awk '{print $1}')
    echo "  $sha  ${sz} B  $(basename "$f")"
    if [ "$sz" -gt 65520 ]; then
        echo "ERROR: $(basename "$f") exceeds R.LIB_MAX (65520 B): $sz B" >&2
        exit 1
    fi
done
