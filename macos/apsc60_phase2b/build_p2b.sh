#!/bin/bash
# Build A7C II 2.01 Phase 2B encoder-side apply (level 19) & putback (level 20) libJit.so binaries
# for BOTH designs:
#   - ogfile2560_60p_c2_v1       (stock menu bitrates, --menu-rate)
#   - ogfile2560_60p_r240_c2_v1  (V60-targeted 1.20x bitrate boost <= 240 Mbps, default)
# Compiles the proven windows/tools/camera_tests/CT6_memory_read_check/helper/readcheck.c VERBATIM
# using /tmp/venv_zig (exact build_c2_write.sh flags).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
READCHECK="$REPO/windows/tools/camera_tests/CT6_memory_read_check/helper/readcheck.c"
ZIG_PY="/tmp/venv_zig/bin/python"

if [ ! -x "$ZIG_PY" ]; then
    echo "ERROR: $ZIG_PY not found. Install ziglang in isolated /tmp/venv_zig first." >&2
    exit 1
fi
if [ ! -f "$READCHECK" ]; then
    echo "ERROR: $READCHECK not found." >&2
    exit 1
fi

echo "== Generating and verifying Phase 2B headers =="
python3 -B "$HERE/gen_p2b_mp.py"

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

BUILT=()
for DESIGN in ogfile2560_60p_c2_v1 ogfile2560_60p_r240_c2_v1; do
    for L in 19 20; do
        OUT="$HERE/libJit_c2_level${L}_${DESIGN}.so"
        echo "== Compiling $(basename "$OUT") =="
        "${CC[@]}" -I"$HERE" -DLEVEL=$L "-DMP_H=\"expected_mp_${DESIGN}.h\"" -DAVCAM_PHYS=0x401000000UL \
            "$READCHECK" -o "$OUT"
        BUILT+=("$OUT")
    done
done

echo "== Built binaries =="
for f in "${BUILT[@]}"; do
    sz=$(stat -f %z "$f")
    sha=$(shasum -a 256 "$f" | awk '{print $1}')
    echo "  $sha  ${sz} B  $(basename "$f")"
    if [ "$sz" -gt 65520 ]; then
        echo "ERROR: $(basename "$f") exceeds R.LIB_MAX (65520 B): $sz B" >&2
        exit 1
    fi
done
