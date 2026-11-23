#!/bin/bash
# Build the A7C II 2.01 write libraries from the A7 IV helper's source (helper/readcheck.c), with this build's tables and constants
# (A7C II port, 2026-11-24). Run gen_c2_write.py first.
# usage: build_c2_write.sh        (writes libJit_c2_level<L>_<design>.so into this folder and prints SHA-256, size, level, design)
# Flags are those of helper/rebuild.sh. Differences from an A7 IV build: the headers of gen_c2_write.py, the A7C II libsencore.so to link
# against, and these constants (modding/A7C2_PORT.md):
#   levels 5/6    IMAGER 0x18aeb8c (0x989d4a returns it), drive-parameter pointer at control +0x684 (as the A7 IV), stored record 0x170 bytes
#   levels 13/14  the DMM server's page of pointers 0x1a8c000: workspace +0x558, unit objects +0x0ac (0xc1c220 = A7 IV 0xafdac6)
#   levels 19/20  av-cam.bin at physical 0x401000000 (kemco.txt: memrsv=0x01000000@0x401000000:ex, as the A7 IV)
set -e
H="$(cd "$(dirname "$0")" && pwd)"
SEN="$H/../../../work/extracted/a7c2_201/0700_part_image/dev/nflasha15/lib/libsencore.so"
cd "$H/../helper"
CC=(../../../work/venv_zig/Scripts/python.exe -m ziglang cc -target aarch64-linux-gnu -std=gnu11 -O1 -Wall -Wextra -Werror -fPIC -shared -nostdlib -ffreestanding
    -fno-stack-protector -fno-builtin -fno-asynchronous-unwind-tables -Wl,-soname,libJit.so -Wl,--no-undefined -Wl,-z,now -g0 -s -Wl,-z,max-page-size=4096)
OUT=()
for D in ogsq_c2_v1 og57n_c2_v1 ogsq880_c2_v1 og57n880_c2_v1; do
  for L in 5 6; do
    "${CC[@]}" -DLEVEL=$L "-DSITES_H=\"expected_sites_$D.h\"" "-DDESIGN_LABEL=\"$D\"" -DIMAGER=0x18aeb8cUL -DCTRL_DRVPARAM=0x684U -DREC=0x170U \
      readcheck.c "$SEN" -o "$H/libJit_c2_level${L}_$D.so"; OUT+=("level${L}_$D")
  done
done
for L in 13 14; do
  "${CC[@]}" -DLEVEL=$L '-DDMM_SITES_H="expected_dmm_dmmwb4688_c2_v1.h"' -DDMM_GLOBALS=0x01a8c000UL -DG_WORKSPACE=0x558U -DG_UNITS=0x0acU \
    readcheck.c "$SEN" -o "$H/libJit_c2_level${L}_dmmwb4688_c2_v1.so"; OUT+=("level${L}_dmmwb4688_c2_v1")
done
for D in outsz2560_c2_v1 og57cp_c2_v3 lcdfill_c2_v2; do
  for L in 16 17; do
    "${CC[@]}" -DLEVEL=$L "-DWORDS_H=\"expected_words_$D.h\"" readcheck.c "$SEN" -o "$H/libJit_c2_level${L}_$D.so"; OUT+=("level${L}_$D")
  done
done
MPS=(ogfile2560_hs_s_c2_v1 ogfile57r300_c2_v1 ogfile57_c2_v1)
if [ -f expected_mp_lcdcycle_c2_v1.h ]; then MPS+=(lcdcycle_c2_v1); fi
for D in "${MPS[@]}"; do
  for L in 19 20; do
    "${CC[@]}" -DLEVEL=$L "-DMP_H=\"expected_mp_$D.h\"" -DAVCAM_PHYS=0x401000000UL readcheck.c -o "$H/libJit_c2_level${L}_$D.so"; OUT+=("level${L}_$D")
  done
done
cd "$H"
for N in "${OUT[@]}"; do echo "$(sha256sum libJit_c2_$N.so | cut -d' ' -f1) $(stat -c %s libJit_c2_$N.so) $N"; done
