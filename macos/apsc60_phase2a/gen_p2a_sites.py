#!/usr/bin/env python3
"""Generate expected_sites_apsc60_p2a.h for A7C II (ILCE-7CM2) fw 2.01 Phase 2A v3.

Phase 2A v3 (`apsc60_p2a_v3`, 4-mode ALL `col 14` modes: `m23 0x0821` + `m24 0x08c1` +
`m26 0x0b21` + `m27 0x0bc1`, 41 static sites + 34 static guards + 4 dynamic stored_records
= 45 sites total, per #79):
  - Widens ALL 4 APS-C `col 14` modes (`m23 0x0821` active 4K 59.94p, `m24 0x08c1` 4K 24/30p,
    `m26 0x0b21` FHD/S&Q 1687-line, `m27 0x0bc1` S&Q 2100-line) from 4608x2592 (16:9) to
    4608x3072 (3:2 full APS-C, 1.52x crop), updates shared `SENSOR_MATRIX col 14` (7 B)
    and shared Lrc split pair (`0xd564a8` / `0xd57060`), and patches ALL 4 dynamic
    `stored_record`s with `v_pixel_aspect = 27/32` and exact Sony double-precision timings:
      * 1687-line pair (`0x0821` @0x025de15c, `0x0b21` @0x025de5ac): `lt = 2531.673582295989`
      * 2100-line pair (`0x08c1` @0x025de2cc, `0x0bc1` @0x025de71c): `lt = 2542.222222222222`
  - Strictly volatile coprocessor RAM only (zero flash, NVRAM, DMM, or main-SoC writes).
  - Every stock byte is verified byte-for-byte against:
      1) unpacked A7C II 2.01 cpapp-b.bin (SHA-256 f5a47758...),
      2) live stock probe CT6_20261126T092116Z_apsc60_probe/probe_decoded.json,
      3) live post-P2A v1 probe CT6_20261126T143606Z_apsc60_probe/probe_decoded.json, and
      4) live post-P2A v2 probe CT6_20261127T061322Z_apsc60_probe/probe_decoded.json.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CPAPP_PATH = REPO / "windows/camera_private/extracted_a7c2_201/0700_part_image/dev/nflasha8/cpapp-b.bin"
PROBE_STOCK_JSON = REPO / "windows/camera_private/CT6_20261126T092116Z_apsc60_probe/probe_decoded.json"
PROBE_V1_JSON = REPO / "windows/camera_private/CT6_20261126T143606Z_apsc60_probe/probe_decoded.json"
PROBE_V2_JSON = REPO / "windows/camera_private/CT6_20261127T061322Z_apsc60_probe/probe_decoded.json"
OUT_H = HERE / "expected_sites_apsc60_p2a.h"

CPAPP_SHA256 = "f5a477588698ef965748903b8959a39ce4f59ffd4aee4d4234ef1670f56a1428"
BIAS = 0x108000
CP_LO = 0x108000
CP_HI = 0x10000000
CP_PAGE = 4096
REC_SIZE = 0x170

# Exact 28 changed byte offsets inside every 0x170-byte stored_record (zero overlap with boot-varying bytes)
EXPECTED_REC_OFFS = [
    0x6, 0x7, 0x64, 0x66, 0x67, 0x68, 0x8C, 0xBC, 0xBD, 0xE8, 0xE9, 0xEA,
    0xF2, 0xF4, 0xF5, 0xF6, 0xF8, 0xF9, 0xFE, 0x10A, 0x148, 0x149,
    0x154, 0x155, 0x160, 0x164, 0x16A, 0x16B,
]

# Lrc APS-C 16:9 split-record pair (shared by m23/m24) and factory 3072-row APS-C still ref pair
LRC_L_ADDR = 0x00D564A8
LRC_R_ADDR = 0x00D57060
LRC_SIZE = 0x0BB8  # 3000 bytes (1500 u16s)
REF3072_L_ADDR = 0x00D64F08
REF3072_R_ADDR = 0x00D65AC0

LRC_DELTAS: list[tuple[int, list[int]]] = [
    (-244, [0x95E]),
    (+4, [0x012, 0x096]),
    (+4, [0x036, 0x05A, 0x072, 0x0BA, 0x0D2, 0x0EA, 0x10A, 0x122, 0x13A, 0x156, 0x16E]),
    (+10, [0x1DE, 0x1E6, 0x1EE, 0x208, 0x210, 0x218, 0x290, 0x2B6, 0x310, 0x314, 0x318, 0x320]),
    (+122, [0x590, 0x594]),
    (+244, [
        0x49C, 0x4AC, 0x4BC,
        0x698, 0x6B4, 0x6BC,
        0xA48, 0xA64, 0xA6C,
        0xAE8, 0xB04, 0xB0C,
        0xB88, 0xBA4, 0xBAC,
    ]),
    (+480, [
        0x016, 0x03A, 0x05E, 0x076, 0x09A,
        0x0BE, 0x0D6, 0x0EE, 0x10E, 0x126, 0x13E, 0x15A, 0x172,
    ]),
    (+488, [
        0x00E, 0x02A, 0x032, 0x04E, 0x056, 0x066, 0x06E, 0x092, 0x48E,
        0x006, 0x08A, 0x238, 0x384, 0x394, 0x39C, 0x3A0,
        0x3FC, 0x404, 0x40C, 0x414, 0x41C, 0x424, 0x42C, 0x430, 0x434, 0x438, 0x43C, 0x440, 0x448, 0x44C, 0x450,
        0x0AE, 0x0B6, 0x0C6, 0x0CE, 0x0DE, 0x0E6, 0x0FE, 0x106,
        0x116, 0x11E, 0x12E, 0x136, 0x14A, 0x152, 0x162, 0x16A,
        0x4C0, 0x4C8, 0x4D0, 0x4D8, 0x4E0, 0x4E8, 0x4F8, 0x4FC,
        0x550, 0x558, 0x560, 0x564, 0x568, 0x56C, 0x570, 0x582,
        0x62C, 0x634, 0x63C, 0x644, 0x64C, 0x650, 0x658, 0x660, 0x668,
        0x6C8, 0x6D0, 0x6D8, 0x6DC, 0x6E0, 0x6E4, 0x70A, 0x71A,
        0x724, 0x72C, 0x734, 0x738, 0x73C, 0x752, 0x75E, 0x77C,
        0x9DC, 0x9E4, 0x9EC, 0x9F4, 0x9FC, 0xA00, 0xA08, 0xA10, 0xA18,
        0xA7C, 0xA84, 0xA8C, 0xA94, 0xA9C, 0xAA0, 0xAA8, 0xAB0, 0xAB8,
        0xB1C, 0xB24, 0xB2C, 0xB34, 0xB3C, 0xB40, 0xB44, 0xB48, 0xB50, 0xB58,
    ]),
    (+498, [0x2A0, 0x300, 0x30C, 0x31C, 0x32E]),
]

LRC_STEP_V_OFF = 0x784
LRC_START_V_OFF = 0x78C
LRC_STOCK_STEP_V = 4355
LRC_NEW_STEP_V = 5159
LRC_STOCK_START_V = 1776
LRC_NEW_START_V = ((3132 - 1) * 4096 - (2486 - 1) * LRC_NEW_STEP_V) // 2
assert LRC_NEW_START_V == 2230


def hx(b: bytes) -> str:
    return ", ".join(f"0x{x:02x}" for x in b)


def build_lrc_record(cp: bytes, addr: int, ref3072_addr: int) -> tuple[bytes, bytes, int]:
    stock = cp[addr - BIAS : addr - BIAS + LRC_SIZE]
    ref3072 = cp[ref3072_addr - BIAS : ref3072_addr - BIAS + LRC_SIZE]
    words = list(struct.unpack(f"<{LRC_SIZE // 2}H", stock))
    ref_w = struct.unpack(f"<{LRC_SIZE // 2}H", ref3072)

    seen_offs: set[int] = set()
    for delta, offs in LRC_DELTAS:
        for off in offs:
            assert off % 2 == 0 and 0 <= off < LRC_SIZE, f"bad Lrc offset 0x{off:x}"
            assert off not in seen_offs, f"duplicate Lrc offset 0x{off:x}"
            seen_offs.add(off)
            idx = off // 2
            nv = words[idx] + delta
            assert 0 <= nv <= 0xFFFF
            if ref_w[idx] != 0:
                assert nv == ref_w[idx], (
                    f"Lrc +0x{off:03x} mismatch vs factory 3072 (0x{ref3072_addr:x}): "
                    f"got {nv}, factory {ref_w[idx]} (stock {words[idx]})"
                )
            words[idx] = nv

    assert words[LRC_STEP_V_OFF // 2] == LRC_STOCK_STEP_V
    assert words[LRC_START_V_OFF // 2] == LRC_STOCK_START_V
    words[LRC_STEP_V_OFF // 2] = LRC_NEW_STEP_V
    words[LRC_START_V_OFF // 2] = LRC_NEW_START_V
    seen_offs.add(LRC_STEP_V_OFF)
    seen_offs.add(LRC_START_V_OFF)

    new_bytes = struct.pack(f"<{LRC_SIZE // 2}H", *words)
    assert len(seen_offs) == 179
    return stock, new_bytes, len(seen_offs)


def build_2100_diff(probe_stock: dict, probe_v1: dict | None, probe_v2: dict | None) -> tuple[bytes, bytes]:
    """Build & cross-verify the 28-byte stored_record diff for 2100-line modes (0x08c1, 0x0bc1)."""
    rec_info = probe_stock["drvparam"]["stored_records"]["0x0bc1"]
    assert rec_info["bytes"] == REC_SIZE and rec_info["index"] == 27 and rec_info["addr"].lower() == "0x025de71c"
    r_stock = bytearray(bytes.fromhex(rec_info["raw_hex"]))
    r_new = bytearray(r_stock)

    frame_ticks = struct.unpack_from("<I", r_stock, 0x0D0)[0]
    total_lines = struct.unpack_from("<H", r_stock, 0x0C6)[0]
    lt = (320_000_000 * 1001 / 60_000) / total_lines  # exact 22880/9 = 2542.222222 ticks/line
    assert frame_ticks == round(total_lines * lt) == 5338667 and total_lines == 2100
    assert struct.unpack_from("<I", r_stock, 0x0BC)[0] == round(79 * lt)
    assert struct.unpack_from("<I", r_stock, 0x0E8)[0] == round(1296 * lt)
    assert struct.unpack_from("<H", r_stock, 0x0F2)[0] == round(87 * lt / 1000)
    assert struct.unpack_from("<H", r_stock, 0x0F4)[0] == round(3294720 / 1000)
    assert struct.unpack_from("<I", r_stock, 0x154)[0] == round(27.5 * lt)
    assert struct.unpack_from("<H", r_stock, 0x160)[0] == round(69911 / 1000)

    new_bc = round(86 * lt)             # 218631
    new_e8 = round(1536 * lt)           # 3904853
    new_f2 = round(94 * lt / 1000)      # 239
    new_f4 = round(new_e8 / 1000)       # 3905
    new_154 = round(32.5 * lt)          # 82622
    new_160 = round(new_154 / 1000)     # 83
    assert (new_bc, new_e8, new_f2, new_f4, new_154, new_160) == (218631, 3904853, 239, 3905, 82622, 83)

    r_new[0x006] = 27
    r_new[0x007] = 32
    struct.pack_into("<HHH", r_new, 0x064, 30, 3072, 30)
    struct.pack_into("<H", r_new, 0x08C, 65)
    struct.pack_into("<I", r_new, 0x0BC, new_bc)
    struct.pack_into("<I", r_new, 0x0E8, new_e8)
    struct.pack_into("<HHHH", r_new, 0x0F2, new_f2, new_f4, new_f2, new_f4)
    struct.pack_into("<H", r_new, 0x0FE, 778)
    struct.pack_into("<H", r_new, 0x10A, 778)
    struct.pack_into("<H", r_new, 0x148, 1600)
    struct.pack_into("<I", r_new, 0x154, new_154)
    struct.pack_into("<H", r_new, 0x160, new_160)
    struct.pack_into("<H", r_new, 0x164, new_160)
    struct.pack_into("<H", r_new, 0x16A, 787)

    offs = [k for k in range(REC_SIZE) if r_stock[k] != r_new[k]]
    assert offs == EXPECTED_REC_OFFS
    stock28 = bytes(r_stock[k] for k in offs)
    new28 = bytes(r_new[k] for k in offs)

    # Cross-verify against 0x08c1 in stock probe AND against v1 (143606Z) & v2 (061322Z) live probes!
    for label, probe, exp_08c1, exp_0bc1 in (
        ("092116Z", probe_stock, stock28, stock28),
        ("143606Z", probe_v1,    stock28, new28),
        ("061322Z", probe_v2,    new28,   new28),
    ):
        if probe is None:
            continue
        for key, want in (("0x08c1", exp_08c1), ("0x0bc1", exp_0bc1)):
            raw = bytes.fromhex(probe["drvparam"]["stored_records"][key]["raw_hex"])
            got = bytes(raw[k] for k in offs)
            assert got == want, f"{label} {key} mismatch: got={got.hex()} want={want.hex()}"
    return stock28, new28


def build_1687_diff(probe_stock: dict, probe_v1: dict | None, probe_v2: dict | None) -> tuple[bytes, bytes]:
    """Build & cross-verify the 28-byte stored_record diff for 1687-line modes (0x0821, 0x0b21) per #79."""
    rec_info = probe_stock["drvparam"]["stored_records"]["0x0b21"]
    assert rec_info["bytes"] == REC_SIZE and rec_info["index"] == 26 and rec_info["addr"].lower() == "0x025de5ac"
    r_stock = bytearray(bytes.fromhex(rec_info["raw_hex"]))
    r_new = bytearray(r_stock)

    # Exact Sony 0x98deb4 double-precision line period for m23/m26 (t1 = 356, 3374, r0=+1, r1=241, r2=4, r12=36):
    d8 = ((241 - 4) * (356.0 / 36.0) + 4 * (357.0 / 36.0)) / 241.0
    lt = d8 * 256.0  # 2531.673582295989 ticks/line
    frame_ticks = struct.unpack_from("<I", r_stock, 0x0D0)[0]
    total_lines = struct.unpack_from("<H", r_stock, 0x0C6)[0]
    assert total_lines == 1687 and frame_ticks == math.ceil(total_lines * lt) == 4270934
    assert struct.unpack_from("<H", r_stock, 0x02C)[0] == 3374
    assert struct.unpack_from("<I", r_stock, 0x0BC)[0] == math.ceil(79 * lt) == 200003
    assert struct.unpack_from("<I", r_stock, 0x0E8)[0] == round(1296 * lt) == 3281049
    assert struct.unpack_from("<H", r_stock, 0x0F2)[0] == round(87 * lt / 1000) == 220
    assert struct.unpack_from("<H", r_stock, 0x0F4)[0] == round(1296 * lt / 1000) == 3281
    assert struct.unpack_from("<I", r_stock, 0x154)[0] == round(27.5 * lt) == 69621
    assert struct.unpack_from("<H", r_stock, 0x160)[0] == round(69621 / 1000) == 70

    new_bc = math.ceil(86 * lt)             # 217724 (0x03527c)
    new_e8 = round(1536 * lt)               # 3888651 (0x3b560b)
    new_f2 = round(94 * lt / 1000)          # 238 (0x00ee)
    new_f4 = round(1536 * lt / 1000)        # 3889 (0x0f31)
    new_154 = round(32.5 * lt)              # 82279 (0x014167)
    new_160 = round(new_154 / 1000)         # 82 (0x0052)
    assert (new_bc, new_e8, new_f2, new_f4, new_154, new_160) == (217724, 3888651, 238, 3889, 82279, 82)

    r_new[0x006] = 27
    r_new[0x007] = 32
    struct.pack_into("<HHH", r_new, 0x064, 30, 3072, 30)
    struct.pack_into("<H", r_new, 0x08C, 65)
    struct.pack_into("<I", r_new, 0x0BC, new_bc)
    struct.pack_into("<I", r_new, 0x0E8, new_e8)
    struct.pack_into("<HHHH", r_new, 0x0F2, new_f2, new_f4, new_f2, new_f4)
    struct.pack_into("<H", r_new, 0x0FE, 778)
    struct.pack_into("<H", r_new, 0x10A, 778)
    struct.pack_into("<H", r_new, 0x148, 1600)
    struct.pack_into("<I", r_new, 0x154, new_154)
    struct.pack_into("<H", r_new, 0x160, new_160)
    struct.pack_into("<H", r_new, 0x164, new_160)
    struct.pack_into("<H", r_new, 0x16A, 787)

    offs = [k for k in range(REC_SIZE) if r_stock[k] != r_new[k]]
    assert offs == EXPECTED_REC_OFFS
    stock28 = bytes(r_stock[k] for k in offs)
    new28 = bytes(r_new[k] for k in offs)

    # Assert exact match to #79 specification bytes
    want_stock = bytes([
        0x01, 0x01, 0x1A, 0x20, 0x0A, 0x1A, 0x37, 0x43, 0x0D, 0x99, 0x10, 0x32,
        0xDC, 0xD1, 0x0C, 0xDC, 0xD1, 0x0C, 0xFE, 0xFE, 0x4C, 0x05, 0xF5, 0x0F,
        0x46, 0x46, 0x03, 0x04,
    ])
    want_new = bytes([
        0x1B, 0x20, 0x1E, 0x00, 0x0C, 0x1E, 0x41, 0x7C, 0x52, 0x0B, 0x56, 0x3B,
        0xEE, 0x31, 0x0F, 0xEE, 0x31, 0x0F, 0x0A, 0x0A, 0x40, 0x06, 0x67, 0x41,
        0x52, 0x52, 0x13, 0x03,
    ])
    assert stock28 == want_stock, f"1687 stock mismatch: {stock28.hex()}"
    assert new28 == want_new, f"1687 new mismatch: {new28.hex()}"

    # Verify identical across ALL 3 live probes (0x0b21 was stock in all 3!)
    for label, probe in (("092116Z", probe_stock), ("143606Z", probe_v1), ("061322Z", probe_v2)):
        if probe is None:
            continue
        raw = bytes.fromhex(probe["drvparam"]["stored_records"]["0x0b21"]["raw_hex"])
        assert bytes(raw[k] for k in offs) == stock28, f"{label} 0x0b21 mismatch"
        assert struct.unpack_from("<I", raw, 0x024)[0] == 0x0B21
        assert struct.unpack_from("<H", raw, 0x02C)[0] == 3374
        assert struct.unpack_from("<H", raw, 0x0C6)[0] == 1687
        assert struct.unpack_from("<I", raw, 0x0D0)[0] == 4270934
        assert struct.unpack_from("<H", raw, 0x168)[0] == 989
    return stock28, new28


def main() -> None:
    cp = CPAPP_PATH.read_bytes()
    cp_sha = hashlib.sha256(cp).hexdigest()
    assert cp_sha == CPAPP_SHA256, f"cpapp-b.bin SHA-256 mismatch: {cp_sha}"
    cp_end = BIAS + len(cp)

    probe_stock = json.loads(PROBE_STOCK_JSON.read_text(encoding="utf8"))
    assert probe_stock["probe_ok"] is True and probe_stock["complete"] is True and probe_stock["problems"] == []
    probe_v1 = json.loads(PROBE_V1_JSON.read_text(encoding="utf8")) if PROBE_V1_JSON.exists() else None
    probe_v2 = json.loads(PROBE_V2_JSON.read_text(encoding="utf8")) if PROBE_V2_JSON.exists() else None
    for p in (probe_v1, probe_v2):
        if p is not None:
            assert p["probe_ok"] is True and p["complete"] is True and p["problems"] == []

    # Verify m23 (0x0821) and m26 (0x0b21) have 100% identical timing & sensor tables in cpapp-b.bin
    assert cp[0xDEA154 + 23 * 8 - BIAS : 0xDEA154 + 24 * 8 - BIAS] == cp[0xDEA154 + 26 * 8 - BIAS : 0xDEA154 + 27 * 8 - BIAS]
    assert cp[0xDE9A4C + 23 * 20 - BIAS : 0xDE9A4C + 24 * 20 - BIAS] == cp[0xDE9A4C + 26 * 20 - BIAS : 0xDE9A4C + 27 * 20 - BIAS]
    assert cp[0xEE01D6 + 23 * 38 - BIAS : 0xEE01D6 + 24 * 38 - BIAS] == cp[0xEE01D6 + 26 * 38 - BIAS : 0xEE01D6 + 27 * 38 - BIAS]
    assert cp[0xEE064A + 23 * 38 - BIAS : 0xEE064A + 24 * 38 - BIAS] == cp[0xEE064A + 26 * 38 - BIAS : 0xEE064A + 27 * 38 - BIAS]
    assert cp[0xEE0ABE + 23 * 14 - BIAS : 0xEE0ABE + 24 * 14 - BIAS] == cp[0xEE0ABE + 26 * 14 - BIAS : 0xEE0ABE + 27 * 14 - BIAS]
    assert cp[0xEE0C62 + 23 * 20 - BIAS : 0xEE0C62 + 24 * 20 - BIAS] == cp[0xEE0C62 + 26 * 20 - BIAS : 0xEE0C62 + 27 * 20 - BIAS]

    lrc_l_old, lrc_l_new, lrc_l_n = build_lrc_record(cp, LRC_L_ADDR, REF3072_L_ADDR)
    lrc_r_old, lrc_r_new, lrc_r_n = build_lrc_record(cp, LRC_R_ADDR, REF3072_R_ADDR)
    assert lrc_l_n == lrc_r_n == 179

    col14_stock = bytes.fromhex(probe_stock["sensor_matrix"]["col_14"]["raw_hex"])
    col14_v1 = bytes.fromhex(probe_v1["sensor_matrix"]["col_14"]["raw_hex"]) if probe_v1 else None
    col14_v2 = bytes.fromhex(probe_v2["sensor_matrix"]["col_14"]["raw_hex"]) if probe_v2 else None
    col14_edits = [
        (3,  0x4F, 0x5B, "SENSOR_MATRIX col14 r003 reg 0x06 vstart_lo (1103->859)"),
        (4,  0x04, 0x03, "SENSOR_MATRIX col14 r004 reg 0x07 vstart_hi (1103->859)"),
        (5,  0x2C, 0x20, "SENSOR_MATRIX col14 r005 reg 0x08 lines_lo (1324->1568)"),
        (6,  0x05, 0x06, "SENSOR_MATRIX col14 r006 reg 0x09 lines_hi (1324->1568)"),
        (7,  0x15, 0x10, "SENSOR_MATRIX col14 r007 reg 0x0e aux_geom"),
        (8,  0x1C, 0x21, "SENSOR_MATRIX col14 r008 reg 0x10 aux_geom"),
        (10, 0x26, 0x2B, "SENSOR_MATRIX col14 r010 reg 0x19 pdaf_aux (=S9 38->43)"),
    ]

    # Per-mode static site helper (S4/S2/S1/S10/S7/S8/S9/S3) for all 4 col-14 modes (m23, m24, m26, m27)
    #   (mode_idx, mode_id, mode_key, s4_row_addr)
    MODE_ROWS = [
        (23, 0x0821, "0x0821", 0x00D995F0),
        (24, 0x08C1, "0x08c1", 0x00D995FC),
        (26, 0x0B21, "0x0b21", 0x00D99614),
        (27, 0x0BC1, "0x0bc1", 0x00D99620),
    ]

    sites: list[tuple[int, bytes, bytes, str]] = [
        (LRC_L_ADDR, lrc_l_old, lrc_l_new, "Lrc L split record (shared m23/m24, 179 u16s: 3132->2486 step=5159 start=2230)"),
        (LRC_R_ADDR, lrc_r_old, lrc_r_new, "Lrc R split record (shared m23/m24, 179 u16s: 3132->2486 step=5159 start=2230)"),
    ]
    guards: list[tuple[int, bytes, str]] = [
        (0x010FDCF8, struct.pack("<I", 0x011F82EA), "SENSOR_MATRIX r003 shadow_ptr == 0x011f82ea (shadow+0x52, reg 0x06)"),
        (0x010FDD28, struct.pack("<I", 0x011F82F2), "SENSOR_MATRIX r005 shadow_ptr == 0x011f82f2 (shadow+0x5a, reg 0x08)"),
    ]

    for m, mid, mkey, s4_row in MODE_ROWS:
        s2_row = 0x00DE9A4C + m * 20
        s1_row = 0x00EE01D6 + m * 38
        s10_row = 0x00EE064A + m * 38
        s7_row = 0x00EE0ABE + m * 14
        s9_row = 0x00EE0C62 + m * 20
        ids_addr = 0x00DE99D4 + m * 4
        colmap_addr = 0x010FE8E0 + m * 4

        sites += [
            (s4_row + 10, struct.pack("<H", 1022), struct.pack("<H", 778), f"S4 geometry row m{m} ({mkey}) v_off 1022->778"),
            (s2_row + 6,  struct.pack("<H", 1022), struct.pack("<H", 778), f"S2 crop row m{m} ({mkey}) v_off 1022->778"),
            (s1_row + 24, struct.pack("<HHH", 26, 2592, 26), struct.pack("<HHH", 30, 3072, 30), f"S5_12/S1/S5_14 stream-0 m{m} ({mkey}) (26,2592,26)->(30,3072,30)"),
            (s10_row + 26, struct.pack("<H", 55), struct.pack("<H", 65), f"S10 stream-1 aux/PDAF height m{m} ({mkey}) 55->65"),
            (s7_row + 8,  struct.pack("<H", 1022), struct.pack("<H", 778), f"S7 LC table m{m} ({mkey}) v_off 1022->778"),
            (s7_row + 12, struct.pack("<H", 1027), struct.pack("<H", 787), f"S8 LC table m{m} ({mkey}) field 6 1027->787"),
            (s9_row + 8,  struct.pack("<H", 38), struct.pack("<H", 43), f"S9 readout m{m} ({mkey}) field 4 (reg 0x19) 38->43"),
            (s9_row + 16, struct.pack("<H", 1356), struct.pack("<H", 1600), f"S3 readout m{m} ({mkey}) field 8 (line periods) 1356->1600"),
        ]
        guards += [
            (s4_row, struct.pack("<HHHHH", mid, 0, 0, 292, 774), f"S4 row m{m} ({mkey}) header"),
            (ids_addr, struct.pack("<I", mid), f"IDS[{m}] == {mkey}"),
            (s2_row, struct.pack("<HHH", 1, 0, 1168), f"S2 row m{m} ({mkey}) header"),
            (s1_row, struct.pack("<12H", 0, 16, 188, 44, 4608, 44, 188, 0, 0, 34, 24, 2), f"S1 row m{m} ({mkey}) header"),
            (s7_row, struct.pack("<HHHH", 8, 257, 2, 1168), f"S7 row m{m} ({mkey}) header"),
            (s7_row + 10, struct.pack("<H", 989), f"S7 row m{m} ({mkey}) lc5 == 989"),
            (s9_row, struct.pack("<HHHH", 24, 1, 1, 2), f"S9/S3 row m{m} ({mkey}) header"),
            (colmap_addr, struct.pack("<I", 14), f"SENSOR_COLMAP[{m}] == 14"),
        ]
        assert probe_stock["sensor_matrix"]["colmap"][m] == 14

    for row, old_b, new_b, desc in col14_edits:
        addr = 0x010FDCB0 + row * 24 + 4 + 14
        assert col14_stock[row] == old_b
        if col14_v1 is not None:
            assert col14_v1[row] == new_b
        if col14_v2 is not None:
            assert col14_v2[row] == new_b
        sites.append((addr, bytes([old_b]), bytes([new_b]), desc))

    sites.sort(key=lambda x: x[0])
    guards.sort(key=lambda x: x[0])
    assert len(sites) == 41 and sum(len(o) for _, o, _, _ in sites) == 6087
    assert len(guards) == 34

    for a, old, new, desc in sites:
        assert len(old) == len(new) > 0 and old != new
        assert CP_LO <= a and a + len(old) <= cp_end
        assert cp[a - BIAS : a - BIAS + len(old)] == old, f"stock mismatch vs cpapp-b.bin at 0x{a:x} ({desc})"
        lo, hi = a & ~63, (a + len(old) + 63) & ~63
        assert hi - lo <= CP_PAGE and 0x00B11000 <= lo and hi <= cp_end
    for (a, o, _, _), (b, _, _, _) in zip(sites, sites[1:]):
        assert a + len(o) <= b, f"overlapping sites: 0x{a:x}+{len(o)} > 0x{b:x}"

    for ga, gb, gdesc in guards:
        assert 0 < len(gb) <= 24 and CP_LO <= ga and ga + len(gb) <= cp_end
        actual = cp[ga - BIAS : ga - BIAS + len(gb)]
        assert actual == gb, f"guard mismatch at 0x{ga:x} ({gdesc}): file={actual.hex()} want={gb.hex()}"
        lo, hi = ga & ~63, (ga + len(gb) + 63) & ~63
        assert hi - lo <= CP_PAGE
        for sa, so, _, _ in sites:
            assert not (ga < sa + len(so) and sa < ga + len(gb)), f"guard 0x{ga:x} overlaps site 0x{sa:x}"
    for (a, b, _), (c, _, _) in zip(guards, guards[1:]):
        assert a + len(b) <= c, f"overlapping guards: 0x{a:x} and 0x{c:x}"

    # Build & verify BOTH timing families for the 4 dynamic stored_records
    rec_stock_1687, rec_new_1687 = build_1687_diff(probe_stock, probe_v1, probe_v2)
    rec_stock_2100, rec_new_2100 = build_2100_diff(probe_stock, probe_v1, probe_v2)

    rec_specs = [
        (0x0821, "0x0821", "rec_stock_1687", "rec_new_1687", [
            (0x024, struct.pack("<I", 0x0821), "rec 0x0821 mode_id == 0x0821"),
            (0x02C, struct.pack("<H", 3374),   "rec 0x0821 total_v_pix == 3374"),
            (0x0C6, struct.pack("<H", 1687),   "rec 0x0821 total_lines == 1687"),
            (0x0D0, struct.pack("<I", 4270934),"rec 0x0821 frame_ticks == 4270934"),
            (0x168, struct.pack("<H", 989),    "rec 0x0821 lc5 == 989"),
        ]),
        (0x08C1, "0x08c1", "rec_stock_2100", "rec_new_2100", [
            (0x024, struct.pack("<I", 0x08C1), "rec 0x08c1 mode_id == 0x08c1"),
            (0x02C, struct.pack("<H", 4200),   "rec 0x08c1 total_v_pix == 4200"),
            (0x0C6, struct.pack("<H", 2100),   "rec 0x08c1 total_lines == 2100"),
            (0x0D0, struct.pack("<I", 5338667),"rec 0x08c1 frame_ticks == 5338667"),
            (0x168, struct.pack("<H", 989),    "rec 0x08c1 lc5 == 989"),
        ]),
        (0x0B21, "0x0b21", "rec_stock_1687", "rec_new_1687", [
            (0x024, struct.pack("<I", 0x0B21), "rec 0x0b21 mode_id == 0x0b21"),
            (0x02C, struct.pack("<H", 3374),   "rec 0x0b21 total_v_pix == 3374"),
            (0x0C6, struct.pack("<H", 1687),   "rec 0x0b21 total_lines == 1687"),
            (0x0D0, struct.pack("<I", 4270934),"rec 0x0b21 frame_ticks == 4270934"),
            (0x168, struct.pack("<H", 989),    "rec 0x0b21 lc5 == 989"),
        ]),
        (0x0BC1, "0x0bc1", "rec_stock_2100", "rec_new_2100", [
            (0x024, struct.pack("<I", 0x0BC1), "rec 0x0bc1 mode_id == 0x0bc1"),
            (0x02C, struct.pack("<H", 4200),   "rec 0x0bc1 total_v_pix == 4200"),
            (0x0C6, struct.pack("<H", 2100),   "rec 0x0bc1 total_lines == 2100"),
            (0x0D0, struct.pack("<I", 5338667),"rec 0x0bc1 frame_ticks == 5338667"),
            (0x168, struct.pack("<H", 989),    "rec 0x0bc1 lc5 == 989"),
        ]),
    ]
    offs_set = set(EXPECTED_REC_OFFS)
    for _, _, _, _, rgs in rec_specs:
        assert len(rgs) == 5
        for goff, gb, _ in rgs:
            for k in range(goff, goff + len(gb)):
                assert k not in offs_set

    n_recs = len(rec_specs)
    lines = [
        "/* Generated by macos/apsc60_phase2a/gen_p2a_sites.py (A7C II 2.01 Phase 2A v3, all 4 col-14 modes: 0x0821/0x08c1/0x0b21/0x0bc1). Do not edit. */",
        f"/* {len(sites)} static sites ({sum(len(o) for _, o, _, _ in sites)} bytes), "
        f"{len(guards)} static guards, {n_recs} dynamic stored_records ({len(EXPECTED_REC_OFFS)} changed bytes & 5 guards each). */",
        f"/* Verified against cpapp-b.bin {cp_sha[:16]} and live probes 092116Z / 143606Z / 061322Z. */",
        "",
        '#define DESIGN_LABEL "apsc60_p2a_v3"',
        "#define IMAGER_ADDR 0x018aeb8cUL",
        "#define CTRL_DRVPARAM 0x684U",
        "#define EXPECTED_MODES 30U",
        f"#define REC_SIZE 0x{REC_SIZE:x}U",
        f"#define REC_BYTES {len(EXPECTED_REC_OFFS)}U",
        f'#define REC_BYTES_TEXT "{len(EXPECTED_REC_OFFS)}"',
        f"#define N_RECS {n_recs}U",
        '#define N_RECS_TEXT "4"',
        "#define N_REC_GUARDS_EACH 5U",
        f'#define SITES_TEXT "{len(sites)}"',
        f'#define ALL_TEXT "{len(sites) + n_recs}"',
        "",
    ]

    site_rows = []
    for k, (a, old, new, desc) in enumerate(sites):
        lines.append(f"/* site {k}: 0x{a:08x} ({len(old)} B) - {desc} */")
        lines.append(f"static const u8 site{k}_old[{len(old)}] = {{ {hx(old)} }};")
        lines.append(f"static const u8 site{k}_new[{len(new)}] = {{ {hx(new)} }};")
        site_rows.append(f"    {{ 0x{a:08x}UL, {len(old)}U, site{k}_old, site{k}_new, 0, 0 }},")

    lines += [
        "",
        "/* Changed byte offsets inside each 0x170-byte stored drive-parameter record */",
        f"static const u16 rec_offs[{len(EXPECTED_REC_OFFS)}] = {{ {', '.join(f'0x{k:x}' for k in EXPECTED_REC_OFFS)} }};",
        "/* 1687-line timing family (m23 0x0821 & m26 0x0b21: lt=2531.673582 ticks/line) */",
        f"static const u8 rec_stock_1687[{len(rec_stock_1687)}] = {{ {hx(rec_stock_1687)} }};",
        f"static const u8 rec_new_1687[{len(rec_new_1687)}] = {{ {hx(rec_new_1687)} }};",
        "/* 2100-line timing family (m24 0x08c1 & m27 0x0bc1: lt=2542.222222 ticks/line) */",
        f"static const u8 rec_stock_2100[{len(rec_stock_2100)}] = {{ {hx(rec_stock_2100)} }};",
        f"static const u8 rec_new_2100[{len(rec_new_2100)}] = {{ {hx(rec_new_2100)} }};",
        "",
        "struct cp_site { u64 address; u32 length; const u8 *stock; const u8 *changed; const u16 *offs; u32 noffs; };",
        "static const struct cp_site cp_sites[] = {",
        *site_rows,
        "};",
        "",
        "/* Static Pass-1 guard bytes beside key tables (must match stock before ANY write) */",
        "struct cp_guard { u64 address; u32 length; u8 bytes[24]; };",
        "static const struct cp_guard cp_guards[] = {",
    ]
    for ga, gb, gdesc in guards:
        lines.append(f"    {{ 0x{ga:08x}UL, {len(gb)}U, {{ {hx(gb.ljust(24, bytes([0])))} }} }}, /* {gdesc} */")
    lines += [
        "};",
        "",
        "/* Dynamic Pass-1 invariant guards & per-mode timing tables for all 4 stored_records */",
        "struct rec_guard { u16 off; u8 length; u8 bytes[8]; };",
        "struct rec_target { u32 mode_id; const char *mode_text; const u8 *stock; const u8 *changed; struct rec_guard guards[N_REC_GUARDS_EACH]; };",
        "static const struct rec_target rec_targets[N_RECS] = {",
    ]
    for mode_id, mode_key, stock_sym, new_sym, rguards in rec_specs:
        lines.append(f'    {{ 0x{mode_id:04x}U, "{mode_key}", {stock_sym}, {new_sym}, {{')
        for goff, gb, gdesc in rguards:
            lines.append(f"        {{ 0x{goff:03x}U, {len(gb)}U, {{ {hx(gb.ljust(8, bytes([0])))} }} }}, /* {gdesc} */")
        lines.append("    } },")
    lines += [
        "};",
        "",
    ]

    OUT_H.write_text("\n".join(lines), encoding="utf8", newline="\n")
    print(
        f"Wrote {OUT_H.name}: {len(sites)} static sites ({sum(len(o) for _, o, _, _ in sites)} B), "
        f"{len(guards)} static guards, {n_recs} dynamic stored_records (0x0821/0x08c1/0x0b21/0x0bc1, 28 B & 5 guards each)."
    )


if __name__ == "__main__":
    main()
