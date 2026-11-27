#!/usr/bin/env python3
"""Offline verification suite for A7C II 2.01 Phase 2A v3 (`apsc60_p2a_v3`, Gates 2, 3, and 4).

Gate 2: Ground-truth stock-byte, alignment, bounds & non-overlap audit of
        expected_sites_apsc60_p2a.h (41 static sites [6087 B], 34 static guards,
        4 dynamic stored_records `0x0821`, `0x08c1`, `0x0b21`, `0x0bc1` with 28 changed
        bytes & 5 guards each = 45 total sites) against cpapp-b.bin AND ALL THREE live
        probes (092116Z stock, 143606Z post-P2A v1, 061322Z post-P2A v2).
Gate 3: Compiled AArch64 ELF & machine-code disassembly audit of BOTH
        libJit_c2_apsc60_p2a_apply.so and libJit_c2_apsc60_p2a_putback.so
        (including verbatim RODATA byte parity).
Gate 4: Native C host-test execution of the exact apsc60_p2a.c engine against
        a full 48 MB coprocessor RAM image built from real cpapp-b.bin + ALL 4
        stored_records (`0x0821` @0x025de15c, `0x08c1` @0x025de2cc, `0x0b21` @0x025de5ac,
        `0x0bc1` @0x025de71c), covering 13 comprehensive test cases including rollback on
        the 45th site (`0x0bc1`) and partial `v2->v3` upgrade!
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CPAPP_PATH = REPO / "windows/camera_private/extracted_a7c2_201/0700_part_image/dev/nflasha8/cpapp-b.bin"
PROBE_STOCK_JSON = REPO / "windows/camera_private/CT6_20261126T092116Z_apsc60_probe/probe_decoded.json"
PROBE_V1_JSON = REPO / "windows/camera_private/CT6_20261126T143606Z_apsc60_probe/probe_decoded.json"
PROBE_V2_JSON = REPO / "windows/camera_private/CT6_20261127T061322Z_apsc60_probe/probe_decoded.json"
HEADER_PATH = HERE / "expected_sites_apsc60_p2a.h"
APPLY_SO = HERE / "libJit_c2_apsc60_p2a_apply.so"
PUTBACK_SO = HERE / "libJit_c2_apsc60_p2a_putback.so"

BIAS = 0x108000
RAM_SIZE = 0x03000000
REC_ADDRS = {
    0x0821: 0x025DE15C,
    0x08C1: 0x025DE2CC,
    0x0B21: 0x025DE5AC,
    0x0BC1: 0x025DE71C,
}

HOST_SHIM_C = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>

typedef uint8_t  u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t  i64;

extern void p2a_host_run(void);

typedef u32 (*dma_cb_t)(u32 dir, u64 src, u64 dst, u32 size);

static char g_result_path[512];
static u8  *g_bounce_mem = NULL;
static u64  g_dmm_phys = 0x47e67b080ULL;
static u32  g_dmm_handle = 42;
static int  g_alloc_count = 0;
static int  g_free_count = 0;
static int  g_mem_open_count = 0;
static int  g_mem_close_count = 0;
static int  g_mmap_count = 0;
static int  g_munmap_count = 0;
static int  g_last_mem_flags = 0;
static dma_cb_t g_dma_cb = NULL;

void sim_setup(const char *result_path, u8 *bounce_mem, u64 dmm_phys, dma_cb_t cb) {
    strncpy(g_result_path, result_path, sizeof(g_result_path) - 1);
    g_result_path[sizeof(g_result_path) - 1] = 0;
    g_bounce_mem = bounce_mem;
    g_dmm_phys = dmm_phys;
    g_dma_cb = cb;
    g_alloc_count = g_free_count = 0;
    g_mem_open_count = g_mem_close_count = 0;
    g_mmap_count = g_munmap_count = 0;
    g_last_mem_flags = 0;
}

void sim_stats(int *out6) {
    out6[0] = g_alloc_count;
    out6[1] = g_free_count;
    out6[2] = g_mem_open_count;
    out6[3] = g_mem_close_count;
    out6[4] = g_mmap_count;
    out6[5] = g_munmap_count;
}

int sim_last_mem_flags(void) { return g_last_mem_flags; }

i64 host_open_result(void) {
    return open(g_result_path, O_WRONLY | O_CREAT | O_EXCL, 0600);
}
i64 host_open_mem(int flags) {
    g_mem_open_count++;
    g_last_mem_flags = flags;
    return 9999;
}
i64 host_write(int fd, const void *b, u64 n) {
    return write(fd, b, (size_t)n);
}
i64 host_close(int fd) {
    if (fd == 9999) {
        g_mem_close_count++;
        return 0;
    }
    return close(fd);
}
i64 host_geteuid(void) { return 0; }
i64 host_mmap_rw(int fd, u64 len, u64 off) {
    (void)fd; (void)len;
    g_mmap_count++;
    u64 page_base = g_dmm_phys & ~0xfffULL;
    if (off != page_base) return -1;
    return (i64)(uintptr_t)g_bounce_mem;
}
i64 host_munmap(void *p, u64 len) {
    (void)p; (void)len;
    g_munmap_count++;
    return 0;
}
u64 host_dmm_alloc(u32 size, u32 *handle) {
    (void)size;
    g_alloc_count++;
    if (handle) *handle = g_dmm_handle;
    return g_dmm_phys;
}
u64 host_dmm_free(u32 handle) {
    if (handle == g_dmm_handle) g_free_count++;
    return 0;
}
u32 host_dma_transfer(u32 dir, u64 src, u64 dst, u32 size) {
    return g_dma_cb(dir, src, dst, size);
}
void sim_run(void) {
    p2a_host_run();
}
"""


def _find_tool(name: str) -> str:
    clt = Path("/Library/Developer/CommandLineTools/usr/bin") / name
    return str(clt) if clt.exists() else name


def parse_header():
    txt = HEADER_PATH.read_text(encoding="utf8")
    design = re.search(r'#define DESIGN_LABEL "([^"]+)"', txt).group(1)
    arrays = {}
    for m in re.finditer(r"static const u8 ((?:site\d+|rec_stock_\d+|rec_new_\d+)(?:_(?:old|new))?)\[\d+\] = \{([^}]*)\};", txt):
        arrays[m.group(1)] = bytes(int(x.strip(), 16) for x in m.group(2).split(",") if x.strip())
    sites = []
    block = re.search(r"static const struct cp_site cp_sites\[\] = \{(.*?)\};", txt, re.S).group(1)
    for m in re.finditer(r"\{\s*(0x[0-9a-fA-F]+)UL,\s*(\d+)U,\s*(site\d+_old),\s*(site\d+_new),", block):
        sites.append((int(m.group(1), 16), int(m.group(2)), arrays[m.group(3)], arrays[m.group(4)]))
    guards = []
    gblock = re.search(r"static const struct cp_guard cp_guards\[\] = \{(.*?)\};", txt, re.S).group(1)
    for m in re.finditer(r"\{\s*(0x[0-9a-fA-F]+)UL,\s*(\d+)U,\s*\{([^}]*)\}\s*\}", gblock):
        addr, ln = int(m.group(1), 16), int(m.group(2))
        b = bytes(int(x.strip(), 16) for x in m.group(3).split(",") if x.strip())[:ln]
        guards.append((addr, b))
    rec_offs = [int(x.strip(), 16) for x in re.search(r"rec_offs\[\d+\] = \{([^}]*)\};", txt).group(1).split(",") if x.strip()]

    rec_targets = []
    rtblock = re.search(r"static const struct rec_target rec_targets\[N_RECS\] = \{(.*?)\n\};", txt, re.S).group(1)
    for tm in re.finditer(
        r'\{\s*(0x[0-9a-fA-F]+)U,\s*"([^"]+)",\s*(\w+),\s*(\w+),\s*\{(.*?)\n    \}\s*\}',
        rtblock, re.S,
    ):
        mode_id = int(tm.group(1), 16)
        mode_text = tm.group(2)
        stock_b = arrays[tm.group(3)]
        new_b = arrays[tm.group(4)]
        rgs = []
        for gm in re.finditer(r"\{\s*(0x[0-9a-fA-F]+)U,\s*(\d+)U,\s*\{([^}]*)\}\s*\}", tm.group(5)):
            off, ln = int(gm.group(1), 16), int(gm.group(2))
            b = bytes(int(x.strip(), 16) for x in gm.group(3).split(",") if x.strip())[:ln]
            rgs.append((off, b))
        rec_targets.append((mode_id, mode_text, stock_b, new_b, rgs))
    return design, sites, guards, rec_offs, rec_targets, arrays


def run_gate2(cp: bytes, probe_stock: dict, probe_v1: dict | None, probe_v2: dict | None):
    print("== Gate 2: Ground-Truth Stock-Byte, Alignment & Overlap Audit (apsc60_p2a_v3) ==")
    design, sites, guards, rec_offs, rec_targets, arrays = parse_header()
    assert design == "apsc60_p2a_v3", design
    assert len(sites) == 41, f"expected 41 static sites, got {len(sites)}"
    assert sum(ln for _, ln, _, _ in sites) == 6087
    assert len(guards) == 34, f"expected 34 static guards, got {len(guards)}"
    assert len(rec_offs) == 28
    assert len(rec_targets) == 4 and [t[0] for t in rec_targets] == [0x0821, 0x08C1, 0x0B21, 0x0BC1]

    for a, ln, old, new in sites:
        assert len(old) == len(new) == ln and old != new
        assert 0x108000 <= a and a + ln <= 0x10000000
        assert cp[a - BIAS : a - BIAS + ln] == old
        lo, hi = a & ~63, (a + ln + 63) & ~63
        assert hi - lo <= 4096
    for (a, ln, _, _), (b, _, _, _) in zip(sites, sites[1:]):
        assert a + ln <= b, "site overlap"

    for ga, gb in guards:
        assert cp[ga - BIAS : ga - BIAS + len(gb)] == gb
        for sa, sln, _, _ in sites:
            assert not (ga < sa + sln and sa < ga + len(gb)), "guard/site overlap"

    col14_stock = bytes.fromhex(probe_stock["sensor_matrix"]["col_14"]["raw_hex"])
    col14_v1 = bytes.fromhex(probe_v1["sensor_matrix"]["col_14"]["raw_hex"]) if probe_v1 else None
    col14_v2 = bytes.fromhex(probe_v2["sensor_matrix"]["col_14"]["raw_hex"]) if probe_v2 else None
    for a, ln, old, new in sites:
        if 0x010FDCB0 <= a < 0x010FDCB0 + 129 * 24:
            row = (a - 0x010FDCB0 - 4 - 14) // 24
            assert ln == 1 and col14_stock[row] == old[0]
            if col14_v1 is not None:
                assert col14_v1[row] == new[0]
            if col14_v2 is not None:
                assert col14_v2[row] == new[0]

    for mode_id, mode_text, stock_b, new_b, rgs in rec_targets:
        assert len(stock_b) == len(new_b) == 28 and len(rgs) == 5
        for off, gb in rgs:
            for k in range(off, off + len(gb)):
                assert k not in rec_offs
        if mode_text in probe_stock["drvparam"]["stored_records"]:
            rec_s = bytes.fromhex(probe_stock["drvparam"]["stored_records"][mode_text]["raw_hex"])
            assert bytes(rec_s[k] for k in rec_offs) == stock_b
            for off, gb in rgs:
                assert rec_s[off : off + len(gb)] == gb
        if probe_v1 is not None and mode_text in probe_v1["drvparam"]["stored_records"]:
            rec_1 = bytes.fromhex(probe_v1["drvparam"]["stored_records"][mode_text]["raw_hex"])
            want_1 = new_b if mode_id == 0x0BC1 else stock_b
            assert bytes(rec_1[k] for k in rec_offs) == want_1
        if probe_v2 is not None and mode_text in probe_v2["drvparam"]["stored_records"]:
            rec_2 = bytes.fromhex(probe_v2["drvparam"]["stored_records"][mode_text]["raw_hex"])
            want_2 = new_b if mode_id in (0x08C1, 0x0BC1) else stock_b
            assert bytes(rec_2[k] for k in rec_offs) == want_2

    print(
        "  PASS: 41 static sites (6087 B), 34 static guards, 4 dynamic stored_records "
        "(0x0821/0x08c1/0x0b21/0x0bc1, 28 B & 5 guards each) 100% verified vs cpapp-b.bin + all 3 live probes."
    )
    return sites, guards, rec_offs, rec_targets, arrays


def run_gate3(arrays: dict[str, bytes]):
    print("== Gate 3: Compiled AArch64 ELF & Machine-Code Disassembly Audit ==")
    allowed_nrs = {56, 57, 64, 175, 215, 222}
    forbidden_strings = ["/cp/", "/setting", "/dev/nflash", "/proc/self/mem", "ioctl", "/bin/sh", "execve"]
    objdump = _find_tool("llvm-objdump")
    nm_tool = _find_tool("llvm-nm")

    for so in (APPLY_SO, PUTBACK_SO):
        assert so.exists(), f"missing {so}"
        raw = so.read_bytes()
        sz = len(raw)
        assert sz <= 65520, f"{so.name} too large: {sz}"

        f_out = subprocess.check_output(["file", str(so)], text=True)
        assert "ELF 64-bit LSB shared object" in f_out and "ARM aarch64" in f_out and "stripped" in f_out, f_out

        ro = subprocess.check_output([objdump, "-p", str(so)], text=True)
        assert "SONAME" in ro and "libJit.so" in ro, ro
        assert "NEEDED" in ro and "libsencore.so" in ro, ro
        assert "INIT_ARRAY" in ro and "INIT_ARRAYSZ" in ro and "0x0000000000000008" in ro, ro
        m_fl = re.search(r"\bFLAGS\s+(0x[0-9a-fA-F]+)", ro)
        m_fl1 = re.search(r"\bFLAGS_1\s+(0x[0-9a-fA-F]+)", ro)
        assert (m_fl and (int(m_fl.group(1), 16) & 0x8)) or (m_fl1 and (int(m_fl1.group(1), 16) & 0x1)), ro

        nm = subprocess.check_output([nm_tool, "-D", "-u", str(so)], text=True)
        undef = {line.split()[-1] for line in nm.splitlines() if line.strip()}
        assert undef == {"_Z9dmm_allocjPj", "_Z8dmm_freej", "sen_dma_transfer"}, f"unexpected imports: {undef}"

        for bad in forbidden_strings:
            assert bad.encode() not in raw, f"forbidden string {bad!r} in {so.name}"
        assert b"/tmp_wbshd/readcheck_result.txt\x00" in raw
        assert b"/dev/mem\x00" in raw
        assert b"apsc60_p2a_v3" in raw
        for mtxt in (b"0x0821\x00", b"0x08c1\x00", b"0x0b21\x00", b"0x0bc1\x00"):
            assert mtxt in raw
        for sym in ("rec_stock_1687", "rec_new_1687", "rec_stock_2100", "rec_new_2100"):
            assert arrays[sym] in raw, f"missing {sym} in RODATA of {so.name}"

        dis = subprocess.check_output([objdump, "-d", str(so)], text=True)
        lines = dis.splitlines()

        svc_count = 0
        for idx, line in enumerate(lines):
            if "\tsvc\t#0" in line or "svc #0" in line:
                svc_count += 1
                nr = None
                for back in range(idx - 1, max(-1, idx - 12), -1):
                    m = re.search(r"mov\s+[wx]8,\s+#(0x[0-9a-fA-F]+|\d+)", lines[back])
                    if m:
                        nr = int(m.group(1), 0)
                        break
                assert nr in allowed_nrs, f"disallowed/untraced syscall {nr} near {line} in {so.name}"
        assert svc_count > 0

        plt_calls = [i for i, l in enumerate(lines) if re.search(r"\b(?:bl|b)\s+0x[0-9a-fA-F]+\s+<sen_dma_transfer@plt>", l)]
        assert len(plt_calls) == 2
        dir0_calls = dir1_calls = 0
        for ci in plt_calls:
            dval = None
            for back in range(ci - 1, max(-1, ci - 14), -1):
                if re.search(r"mov\s+w0,\s+wzr", lines[back]):
                    dval = 0
                    break
                m = re.search(r"mov\s+w0,\s+#(0x[0-9a-fA-F]+|\d+)", lines[back])
                if m:
                    dval = int(m.group(1), 0)
                    break
            assert dval in (0, 1)
            if dval == 0:
                dir0_calls += 1
            else:
                dir1_calls += 1
        assert dir0_calls == 1 and dir1_calls == 1

        sha = hashlib.sha256(raw).hexdigest()
        print(f"  PASS: {so.name} ({sz} B, sha256={sha[:16]}..., {svc_count} audited svcs, 1 dir=0 + 1 dir=1 DMA site)")


def build_host_lib(tmpdir: Path, apply_flag: int) -> ctypes.CDLL:
    shim_c = tmpdir / f"shim_{apply_flag}.c"
    shim_c.write_text(HOST_SHIM_C, encoding="utf8")
    out_dylib = tmpdir / f"libhost_p2a_{apply_flag}.dylib"
    subprocess.check_call([
        "clang", "-O1", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
        "-DHOST_TEST", f"-DP2A_APPLY={apply_flag}",
        f"-I{HERE}", str(HERE / "apsc60_p2a.c"), str(shim_c),
        "-o", str(out_dylib),
    ])
    lib = ctypes.CDLL(str(out_dylib))
    DMA_CB = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32)
    lib.sim_setup.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint64, DMA_CB]
    lib.sim_stats.argtypes = [ctypes.POINTER(ctypes.c_int)]
    lib.sim_last_mem_flags.restype = ctypes.c_int
    lib._DMA_CB = DMA_CB
    return lib


def make_stock_ram(cp: bytes, probe_stock: dict) -> bytearray:
    ram = bytearray(RAM_SIZE)
    ram[BIAS : BIAS + len(cp)] = cp
    imager = 0x018AEB8C
    ctrl = 0x018AEE1C
    obj = 0x018AF4A8
    ids = 0x00DE99D4
    recs = 0x025DC04C
    for idx, mid in ((23, 0x0821), (24, 0x08C1), (26, 0x0B21), (27, 0x0BC1)):
        assert recs + idx * 0x170 == REC_ADDRS[mid]

    ram[imager : imager + 4] = ctrl.to_bytes(4, "little")
    ram[ctrl + 0x684 : ctrl + 0x688] = obj.to_bytes(4, "little")
    ram[obj + 4 : obj + 8] = (30).to_bytes(4, "little")
    ram[obj + 8 : obj + 12] = ids.to_bytes(4, "little")
    ram[obj + 0x10 : obj + 0x14] = recs.to_bytes(4, "little")

    # Populate 0x08c1, 0x0b21, 0x0bc1 directly from live stock probe, and 0x0821 from 0x0b21 with mode_id=0x0821
    for key, mid in (("0x08c1", 0x08C1), ("0x0b21", 0x0B21), ("0x0bc1", 0x0BC1)):
        raw = bytes.fromhex(probe_stock["drvparam"]["stored_records"][key]["raw_hex"])
        ram[REC_ADDRS[mid] : REC_ADDRS[mid] + 0x170] = raw
    raw_0821 = bytearray(bytes.fromhex(probe_stock["drvparam"]["stored_records"]["0x0b21"]["raw_hex"]))
    struct.pack_into("<I", raw_0821, 0x024, 0x0821)
    ram[REC_ADDRS[0x0821] : REC_ADDRS[0x0821] + 0x170] = raw_0821
    return ram


def run_sim(lib, ram: bytearray, result_path: Path, *, dmm_phys: int = 0x47E67B080,
            drop_write_num: int | None = None, corrupt_neighbor_on_write: int | None = None,
            check_footer: bool = True):
    bounce = (ctypes.c_uint8 * 8192)()
    lead = dmm_phys & 0xFFF
    dma_log = []
    write_count = [0]

    def dma_cb(direction: int, src: int, dst: int, size: int) -> int:
        dma_log.append((direction, src, dst, size))
        assert size > 0 and size <= 4096 and (size & 63) == 0
        if direction == 0:
            assert (src & 63) == 0 and dst == dmm_phys
            assert 0x108000 <= src and src + size <= len(ram)
            chunk = ram[src : src + size]
            for i in range(size):
                bounce[lead + i] = chunk[i]
            return 0
        elif direction == 1:
            assert (dst & 63) == 0 and src == dmm_phys
            assert 0x108000 <= dst and dst + size <= len(ram)
            write_count[0] += 1
            if drop_write_num is not None and write_count[0] == drop_write_num:
                return 0
            for i in range(size):
                ram[dst + i] = bounce[lead + i]
            if corrupt_neighbor_on_write is not None and write_count[0] == corrupt_neighbor_on_write:
                ram[dst] ^= 0x01
            return 0
        raise AssertionError(f"invalid DMA dir {direction}")

    cb = lib._DMA_CB(dma_cb)
    lib.sim_setup(str(result_path).encode(), ctypes.cast(bounce, ctypes.c_void_p), dmm_phys, cb)
    lib.sim_run()

    stats = (ctypes.c_int * 6)()
    lib.sim_stats(stats)
    s = {
        "alloc": stats[0], "free": stats[1],
        "mem_open": stats[2], "mem_close": stats[3],
        "mmap": stats[4], "munmap": stats[5],
        "mem_flags": lib.sim_last_mem_flags(),
    }
    assert s["alloc"] == s["free"], f"dmm_alloc/free leak: {s}"
    assert s["mem_open"] == s["mem_close"], f"/dev/mem fd leak: {s}"
    assert s["mmap"] == s["munmap"], f"mmap/munmap leak: {s}"
    if s["mem_open"] > 0:
        assert s["mem_flags"] == (2 | 0x101000)

    text = result_path.read_text(encoding="utf8") if result_path.exists() else ""
    if text and check_footer:
        m = re.search(r"== done, bytes written before this line: (\d+)\n$", text)
        assert m, f"missing/bad footer in result:\n{text[-200:]}"
        assert int(m.group(1)) == len(text[: m.start(1)].encode("utf8")), "footer byte count mismatch"
    writes = [x for x in dma_log if x[0] == 1]
    return text, dma_log, writes, s


def compute_expected_applied_ram(stock_ram: bytearray, sites, rec_offs, rec_targets) -> bytearray:
    exp = bytearray(stock_ram)
    for a, ln, _, new in sites:
        exp[a : a + ln] = new
    for mode_id, _, _, new_b, _ in rec_targets:
        raddr = REC_ADDRS[mode_id]
        for off, b in zip(rec_offs, new_b):
            exp[raddr + off] = b
    return exp


def run_gate4(cp: bytes, probe_stock: dict, sites, guards, rec_offs, rec_targets):
    print("== Gate 4: Offline Behavioral Simulation (Native C Engine, apsc60_p2a_v3) ==")
    stock_ref = make_stock_ram(cp, probe_stock)
    applied_ref = compute_expected_applied_ram(stock_ref, sites, rec_offs, rec_targets)

    with tempfile.TemporaryDirectory(prefix="p2a_v3_sim_") as td:
        tmp = Path(td)
        lib_apply = build_host_lib(tmp, 1)
        lib_putback = build_host_lib(tmp, 0)

        # 1. Apply on stock (45 sites = 41 static + 4 stored records)
        ram = bytearray(stock_ref)
        txt, _, writes, _ = run_sim(lib_apply, ram, tmp / "t1_apply.txt")
        assert "DONE: all 45 sites hold the target bytes (apply)" in txt, txt
        for mid, mtxt in ((0x0821, "0x0821"), (0x08C1, "0x08c1"), (0x0B21, "0x0b21"), (0x0BC1, "0x0bc1")):
            assert f"stored record of mode {mtxt} at 0x{REC_ADDRS[mid]:08x} length 368: 28 of its bytes are a site" in txt
        assert len(writes) == 45, f"expected 45 dir=1 writes, got {len(writes)}"
        assert ram == applied_ref
        print("  [1/13] PASS: Apply on stock -> 45 writes, exact target bytes (all 4 col-14 modes), 0 unlisted bytes touched")

        # 2. Apply again (idempotent)
        txt, _, writes, _ = run_sim(lib_apply, ram, tmp / "t2_idem.txt")
        assert "NOTHING TO DO: every site already holds the target bytes" in txt
        assert len(writes) == 0 and ram == applied_ref
        print("  [2/13] PASS: Apply again (idempotent) -> NOTHING TO DO, 0 writes")

        # 3. Putback on applied
        txt, _, writes, _ = run_sim(lib_putback, ram, tmp / "t3_putback.txt")
        assert "DONE: all 45 sites hold the target bytes (put back)" in txt
        assert len(writes) == 45 and ram == stock_ref
        print("  [3/13] PASS: Putback on applied -> 45 writes, 100% byte-for-byte stock restored")

        # 4. Putback again (idempotent)
        txt, _, writes, _ = run_sim(lib_putback, ram, tmp / "t4_pb_idem.txt")
        assert "NOTHING TO DO: every site already holds the target bytes" in txt
        assert len(writes) == 0 and ram == stock_ref
        print("  [4/13] PASS: Putback again (idempotent) -> NOTHING TO DO, 0 writes")

        # 5. Pass 1 refusal: 1-bit flip in EVERY ONE of the 41 static sites
        for idx, (a, ln, _, _) in enumerate(sites):
            ram_bad = bytearray(stock_ref)
            ram_bad[a + ln // 2] ^= 0x01
            snap = bytes(ram_bad)
            txt, _, writes, _ = run_sim(lib_apply, ram_bad, tmp / f"t5_site_{idx}.txt")
            assert len(writes) == 0 and bytes(ram_bad) == snap and "nothing is written" in txt
        print("  [5/13] PASS: Pass 1 refusal on 1-bit flip in ALL 41 static sites -> 0 writes")

        # 6. Pass 1 refusal: 1-bit flip in EVERY ONE of the 34 static guards
        ids_guard_addrs = {0x00DE9A30, 0x00DE9A34, 0x00DE9A3C, 0x00DE9A40}
        for idx, (ga, gb) in enumerate(guards):
            ram_bad = bytearray(stock_ref)
            ram_bad[ga + len(gb) // 2] ^= 0x01
            snap = bytes(ram_bad)
            txt, _, writes, _ = run_sim(lib_apply, ram_bad, tmp / f"t6_guard_{idx}.txt")
            assert len(writes) == 0 and bytes(ram_bad) == snap and "nothing is written" in txt
            assert "guard mismatch" in txt or (ga in ids_guard_addrs and "not found" in txt), txt
        print("  [6/13] PASS: Pass 1 refusal on 1-bit flip in ALL 34 static guards -> 0 writes")

        # 7. Pass 1 refusal: 1-bit flip in ALL 20 record guards (5 x 4 modes) & site flips on ALL 4 records
        for mode_id, mode_text, _, _, rgs in rec_targets:
            raddr = REC_ADDRS[mode_id]
            for gi, (goff, gb) in enumerate(rgs):
                ram_bad = bytearray(stock_ref)
                ram_bad[raddr + goff] ^= 0x01
                snap = bytes(ram_bad)
                txt, _, writes, _ = run_sim(lib_apply, ram_bad, tmp / f"t7_rg_{mode_text}_{gi}.txt")
                assert len(writes) == 0 and bytes(ram_bad) == snap
                assert "stored record guard mismatch" in txt and "nothing is written" in txt
            ram_bad = bytearray(stock_ref)
            ram_bad[raddr + rec_offs[5]] ^= 0x01
            snap = bytes(ram_bad)
            txt, _, writes, _ = run_sim(lib_apply, ram_bad, tmp / f"t7_rs_{mode_text}.txt")
            assert len(writes) == 0 and bytes(ram_bad) == snap and "nothing is written" in txt
        print("  [7/13] PASS: Pass 1 refusal on ALL 20 record guards & all 4 stored_record site flips -> 0 writes")

        # 8. Pass 1 refusal: null/bad RECS & mismatched IDS[23/24/26/27]
        for label, patch_fn in [
            ("null_recs", lambda r: r.__setitem__(slice(0x018AF4B8, 0x018AF4BC), (0).to_bytes(4, "little"))),
            ("bad_recs",  lambda r: r.__setitem__(slice(0x018AF4B8, 0x018AF4BC), (0x20000000).to_bytes(4, "little"))),
            ("bad_ids23", lambda r: r.__setitem__(slice(0x00DE9A30, 0x00DE9A34), (0x08C0).to_bytes(4, "little"))),
            ("bad_ids26", lambda r: r.__setitem__(slice(0x00DE9A3C, 0x00DE9A40), (0x08C0).to_bytes(4, "little"))),
        ]:
            ram_bad = bytearray(stock_ref)
            patch_fn(ram_bad)
            snap = bytes(ram_bad)
            txt, _, writes, _ = run_sim(lib_apply, ram_bad, tmp / f"t8_{label}.txt")
            assert len(writes) == 0 and bytes(ram_bad) == snap and "nothing is written" in txt
        print("  [8/13] PASS: Pass 1 refusal on null/bad RECS & mismatched IDS[23]/IDS[26] -> 0 writes")

        # 9. Bad dmm_alloc phys
        for label, phys in [("unaligned", 0x47E67B020), ("low", 0x3FFFFF000), ("high", 0x500000000)]:
            ram_bad = bytearray(stock_ref)
            txt, dlog, writes, _ = run_sim(lib_apply, ram_bad, tmp / f"t9_phys_{label}.txt", dmm_phys=phys)
            assert len(dlog) == 0 and len(writes) == 0 and ram_bad == stock_ref
            assert "nothing is written" in txt and "buffer released" in txt
        print("  [9/13] PASS: Bad dmm_alloc phys refused cleanly with 0 DMA & balanced dmm_free")

        # 10. Pass 2 lost-write auto-undo on 1st, 22nd, 42nd (0x0821), 44th (0x0b21), and 45th (0x0bc1 -> full 45-site rollback!)
        for wnum in (1, 22, 42, 44, 45):
            ram_u = bytearray(stock_ref)
            txt, _, _, _ = run_sim(lib_apply, ram_u, tmp / f"t10_drop_{wnum}.txt", drop_write_num=wnum)
            assert "NOT DONE: a step failed; every site this run touched was put back" in txt, txt
            assert ram_u == stock_ref, f"auto-undo failed on drop_write_num={wnum}!"
        print("  [10/13] PASS: Pass 2 lost-write auto-undo on 1st, 22nd, 42nd, 44th, and 45th write -> 100% stock restored")

        # 11. Pass 2 neighbor-byte corruption auto-undo
        ram_n = bytearray(stock_ref)
        # Write #3 is site 2 (0x00d995fa, 2B; line starts at 0x00d995c0 so byte 0 is a neighbor byte)
        txt, _, _, _ = run_sim(lib_apply, ram_n, tmp / "t11_neighbor.txt", corrupt_neighbor_on_write=3)
        assert "neighbor byte changed on read-back" in txt
        assert "NOT DONE: a step failed; every site this run touched was put back" in txt
        ram_n[0x00D995C0] ^= 0x01
        assert ram_n == stock_ref
        print("  [11/13] PASS: Pass 2 neighbor-byte corruption detected & auto-undone")

        # 12. Existing readcheck_result.txt -> O_EXCL immediate exit
        existing = tmp / "t12_existing.txt"
        existing.write_text("SENTINEL\n", encoding="utf8")
        ram_e = bytearray(stock_ref)
        txt, dlog, writes, stats = run_sim(lib_apply, ram_e, existing, check_footer=False)
        assert len(dlog) == 0 and len(writes) == 0 and stats["alloc"] == 0 and stats["mem_open"] == 0
        assert existing.read_text(encoding="utf8") == "SENTINEL\n"
        print("  [12/13] PASS: Existing readcheck_result.txt -> O_EXCL immediate exit, 0 DMA/alloc")

        # 13. Partial v2->v3 upgrade (Lrc+col14+m24+m27 already applied, m23+m26 still stock -> writes ONLY 18 sites!)
        m23_m26_addrs = {
            0x00D995FA, 0x00DE9C1E, 0x00EE0558, 0x00EE09CE, 0x00EE0C08, 0x00EE0C0C, 0x00EE0E36, 0x00EE0E3E,
            0x00D9961E, 0x00DE9C5A, 0x00EE05CA, 0x00EE0A40, 0x00EE0C32, 0x00EE0C36, 0x00EE0E72, 0x00EE0E7A,
        }
        ram_v2 = bytearray(stock_ref)
        for a, ln, _, new in sites:
            if a not in m23_m26_addrs:
                ram_v2[a : a + ln] = new
        for mode_id, _, _, new_b, _ in rec_targets:
            if mode_id in (0x08C1, 0x0BC1):
                for off, b in zip(rec_offs, new_b):
                    ram_v2[REC_ADDRS[mode_id] + off] = b
        txt, _, writes, _ = run_sim(lib_apply, ram_v2, tmp / "t13_upgrade.txt")
        assert "DONE: all 45 sites hold the target bytes (apply)" in txt
        assert len(writes) == 18, f"expected 18 m23/m26 writes on v2->v3 upgrade, got {len(writes)}"
        assert ram_v2 == applied_ref
        print("  [13/13] PASS: Partial v2->v3 upgrade -> wrote ONLY the 18 remaining m23+m26 sites -> 45/45 target!")


def main() -> None:
    cp = CPAPP_PATH.read_bytes()
    probe_stock = json.loads(PROBE_STOCK_JSON.read_text(encoding="utf8"))
    probe_v1 = json.loads(PROBE_V1_JSON.read_text(encoding="utf8")) if PROBE_V1_JSON.exists() else None
    probe_v2 = json.loads(PROBE_V2_JSON.read_text(encoding="utf8")) if PROBE_V2_JSON.exists() else None
    sites, guards, rec_offs, rec_targets, arrays = run_gate2(cp, probe_stock, probe_v1, probe_v2)
    run_gate3(arrays)
    run_gate4(cp, probe_stock, sites, guards, rec_offs, rec_targets)
    print("\nALL PHASE 2A v3 GATES (2, 3, 4) PASSED 100%!")


if __name__ == "__main__":
    main()
