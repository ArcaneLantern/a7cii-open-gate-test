#!/usr/bin/env python3
"""Offline verification suite for A7C II 2.01 Phase 2B (BOTH designs, Gates 2, 3, 4):
  - ogfile2560_60p_c2_v1       (stock menu bitrates, --menu-rate)
  - ogfile2560_60p_r240_c2_v1  (V60-targeted 1.20x bitrate boost <= 240 Mbps, default)

Gate 2: Ground-truth byte-for-byte verification of expected_mp_<design>.h
        against unpacked av-cam.bin (90343f5f...) and libmpr.so (4fb6d886...), bounds,
        page span, PT_LOAD[0] bounds, zero overlaps, and A2-rate <= 240 Mbps invariants.
Gate 3: Compiled AArch64 ELF & machine-code disassembly audit of BOTH
        libJit_c2_level19_<design>.so (apply) and libJit_c2_level20_<design>.so (putback):
          - AArch64 64-bit LSB shared object, stripped, <= 65520 B
          - SONAME libJit.so, ZERO NEEDED libraries, ZERO undefined symbols, INIT_ARRAY 1 entry, BIND_NOW
          - Every svc #0 strictly in allowed level 19/20 syscall set
          - Zero forbidden paths (/cp/, /setting, /dev/nflash)
          - Exact binary table parity: compiled RODATA contains verbatim mp_av[] & mp_lm[] structs
Gate 4: Native C host-harness executing the EXACT readcheck.c lines 2243..2387 (mp_pass1 + mp_run)
        compiled directly with expected_mp_<design>.h against REAL unpacked
        av-cam.bin & libmpr.so images in memory (9/9 behavioral tests per design).
"""

from __future__ import annotations

import ctypes
import hashlib
import re
import struct
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EX = REPO / "windows/camera_private/extracted_a7c2_201/0700_part_image/dev"
AV_PATH = EX / "nflasha3/av-cam.bin"
LM_PATH = EX / "nflasha15/lib/libmpr.so"
READCHECK_C = REPO / "windows/tools/camera_tests/CT6_memory_read_check/helper/readcheck.c"

AV_SHA256 = "90343f5f2edc0393ad8809d907cfe648dfd3a82ce0ef39cf06167929d8955096"
LM_SHA256 = "4fb6d886c1ac620399d09c9c2ffc074315968cf0ada764062913c4e0783fc7f9"

DESIGNS = [
    {
        "name": "ogfile2560_60p_c2_v1",
        "av_sites": 114,
        "av_bytes": 492,
        "av_guards": 18,
        "lm_sites": 4,
        "lm_bytes": 8,
        "lm_guards": 5,
        "mp_max": 160,
        "mid_av_write": 57,
        "has_r240": False,
    },
    {
        "name": "ogfile2560_60p_r240_c2_v1",
        "av_sites": 162,
        "av_bytes": 876,
        "av_guards": 18,
        "lm_sites": 4,
        "lm_bytes": 8,
        "lm_guards": 5,
        "mp_max": 192,
        "mid_av_write": 81,
        "has_r240": True,
    },
]


def _find_tool(name: str) -> str:
    clt = Path("/Library/Developer/CommandLineTools/usr/bin") / name
    return str(clt) if clt.exists() else name


def parse_mp_header(header_path: Path):
    txt = header_path.read_text(encoding="utf8")
    design = re.search(r'#define MP_DESIGN "([^"]+)"', txt).group(1)
    av_first = int(re.search(r"#define MP_AV_FIRST (0x[0-9a-fA-F]+)UL", txt).group(1), 16)
    av_bytes = int(re.search(r"#define MP_AV_BYTES (0x[0-9a-fA-F]+)UL", txt).group(1), 16)
    mp_max = int(re.search(r"#define MP_MAX (\d+)", txt).group(1))

    def parse_arr(name: str):
        blk = re.search(rf"static const struct mp_site {name}\[\] = \{{(.*?)\}};", txt, re.S).group(1)
        rows = []
        for m in re.finditer(
            r"\{\s*(0x[0-9a-fA-F]+),\s*(\d+),\s*(\d+),\s*\{([^}]*)\},\s*\{([^}]*)\}\s*\}", blk
        ):
            off, ln, kind = int(m.group(1), 16), int(m.group(2)), int(m.group(3))
            stock = bytes(int(x.strip(), 16) for x in m.group(4).split(",") if x.strip())
            changed = bytes(int(x.strip(), 16) for x in m.group(5).split(",") if x.strip())
            assert len(stock) == 16 and len(changed) == 16
            rows.append((off, ln, kind, stock, changed))
        return rows

    return design, av_first, av_bytes, mp_max, parse_arr("mp_av"), parse_arr("mp_lm")


def pack_mp_sites(rows) -> bytes:
    out = bytearray()
    for off, ln, kind, stock, changed in rows:
        out += struct.pack("<IBB", off, ln, kind) + stock + changed + b"\x00\x00"
    return bytes(out)


def run_gate2(av: bytes, lm: bytes, spec: dict):
    name = spec["name"]
    header_path = HERE / f"expected_mp_{name}.h"
    print(f"== [{name}] Gate 2: Ground-Truth Stock-Byte, Alignment & Overlap Audit ==")
    assert hashlib.sha256(av).hexdigest() == AV_SHA256
    assert hashlib.sha256(lm).hexdigest() == LM_SHA256
    design, av_first, av_bytes, mp_max, mp_av, mp_lm = parse_mp_header(header_path)
    assert design == name
    assert av_first == 0x38F000 and av_bytes == 0x3F000
    assert mp_max == spec["mp_max"] and mp_max >= max(len(mp_av), len(mp_lm))

    av_sites = [r for r in mp_av if r[2] == 1]
    av_guards = [r for r in mp_av if r[2] == 0]
    lm_sites = [r for r in mp_lm if r[2] == 1]
    lm_guards = [r for r in mp_lm if r[2] == 0]
    assert len(av_sites) == spec["av_sites"] and len(av_guards) == spec["av_guards"]
    assert sum(r[1] for r in av_sites) == spec["av_bytes"]
    assert len(lm_sites) == spec["lm_sites"] and len(lm_guards) == spec["lm_guards"]
    assert sum(r[1] for r in lm_sites) == spec["lm_bytes"]

    for tab, blob, lo_bound, hi_bound, label in (
        (mp_av, av, av_first, av_first + av_bytes, "av-cam.bin"),
        (mp_lm, lm, 0, len(lm), "libmpr.so"),
    ):
        for off, ln, kind, stock, changed in tab:
            assert 0 < ln <= 16 and kind in (0, 1)
            assert lo_bound <= off and off + ln <= hi_bound, f"{label} out of range 0x{off:x}"
            assert blob[off : off + ln] == stock[:ln], f"{label} stock mismatch at 0x{off:x}"
            if kind == 1:
                assert stock[:ln] != changed[:ln]
        for (a, aln, *_), (b, *_) in zip(tab, tab[1:]):
            assert a + aln <= b, f"{label} overlap at 0x{a:x} and 0x{b:x}"

    if spec["has_r240"]:
        # Verify all 48 A2-rate sites (8-byte sites >= 0x3a9000) satisfy rate <= 240 Mbps
        rate_sites = [r for r in av_sites if r[0] >= 0x3A9000 and r[1] == 8]
        assert len(rate_sites) == 48, f"expected 48 A2-rate sites, got {len(rate_sites)}"
        for i in range(0, 48, 3):
            vals = []
            for k in range(3):
                off, ln, _, stock, changed = rate_sites[i + k]
                if k > 0:
                    assert off == rate_sites[i + k - 1][0] + 8
                sh, val = struct.unpack("<II", changed[:8])
                exp_base = 6 if k < 2 else 4
                vals.append((val + 1) * (1 << (sh + exp_base)))
            assert vals[0] <= vals[1] <= vals[2] <= 240_000_000, f"rate violation {vals}"

    print(
        f"  PASS: av-cam.bin ({len(av_sites)} sites / {spec['av_bytes']} B + {len(av_guards)} guards) & "
        f"libmpr.so ({len(lm_sites)} sites / {spec['lm_bytes']} B + {len(lm_guards)} guards) 100% verified."
    )
    return mp_av, mp_lm


def run_gate3(mp_av, mp_lm, spec: dict):
    name = spec["name"]
    apply_so = HERE / f"libJit_c2_level19_{name}.so"
    putback_so = HERE / f"libJit_c2_level20_{name}.so"
    print(f"== [{name}] Gate 3: Compiled AArch64 ELF & Machine-Code Disassembly Audit ==")
    allowed_nrs = {56, 57, 61, 63, 64, 67, 68, 175, 215, 222}
    forbidden_strings = ["/cp/", "/setting", "/dev/nflash", "ioctl", "/bin/sh", "execve"]
    objdump = _find_tool("llvm-objdump")
    nm_tool = _find_tool("llvm-nm")
    av_blob = pack_mp_sites(mp_av)
    lm_blob = pack_mp_sites(mp_lm)

    for so in (apply_so, putback_so):
        assert so.exists(), f"missing {so}"
        raw = so.read_bytes()
        sz = len(raw)
        assert sz <= 65520, f"{so.name} exceeds 65520 B: {sz}"

        f_out = subprocess.check_output(["file", str(so)], text=True)
        assert "ELF 64-bit LSB shared object" in f_out and "ARM aarch64" in f_out and "stripped" in f_out, f_out

        ro = subprocess.check_output([objdump, "-p", str(so)], text=True)
        assert "SONAME" in ro and "libJit.so" in ro, ro
        assert "NEEDED" not in ro, f"unexpected NEEDED library in level 19/20 binary:\n{ro}"
        assert "INIT_ARRAY" in ro and "INIT_ARRAYSZ" in ro and "0x0000000000000008" in ro, ro
        m_fl = re.search(r"\bFLAGS\s+(0x[0-9a-fA-F]+)", ro)
        m_fl1 = re.search(r"\bFLAGS_1\s+(0x[0-9a-fA-F]+)", ro)
        assert (m_fl and (int(m_fl.group(1), 16) & 0x8)) or (m_fl1 and (int(m_fl1.group(1), 16) & 0x1)), ro

        nm = subprocess.check_output([nm_tool, "-D", "-u", str(so)], text=True)
        undef = {line.split()[-1] for line in nm.splitlines() if line.strip()}
        assert undef == set(), f"unexpected undefined symbols: {undef}"

        for bad in forbidden_strings:
            assert bad.encode() not in raw, f"forbidden string {bad!r} in {so.name}"
        assert b"/tmp_wbshd/readcheck_result.txt\x00" in raw
        assert b"/dev/mem\x00" in raw
        assert b"/proc/self/mem\x00" in raw
        assert name.encode() in raw

        # Exact binary RODATA table parity!
        assert av_blob in raw, f"mp_av[] binary bytes not found verbatim in {so.name}"
        assert lm_blob in raw, f"mp_lm[] binary bytes not found verbatim in {so.name}"

        dis = subprocess.check_output([objdump, "-d", str(so)], text=True)
        lines = dis.splitlines()
        svc_count = 0
        seen_nrs = set()
        for idx, line in enumerate(lines):
            if "\tsvc\t#0" in line or "svc #0" in line:
                svc_count += 1
                nr = None
                for back in range(idx - 1, max(-1, idx - 14), -1):
                    m = re.search(r"mov\s+[wx]8,\s+#(0x[0-9a-fA-F]+|\d+)", lines[back])
                    if m:
                        nr = int(m.group(1), 0)
                        break
                assert nr in allowed_nrs, f"disallowed/untraced syscall {nr} near {line} in {so.name}"
                seen_nrs.add(nr)
        assert svc_count > 0 and {56, 57, 64, 67, 68, 215, 222}.issubset(seen_nrs)

        sha = hashlib.sha256(raw).hexdigest()
        print(
            f"  PASS: {so.name} ({sz} B, sha256={sha[:16]}..., 0 imports, {svc_count} audited svcs, RODATA tables exact)"
        )


def build_readcheck_mp_host_lib(tmpdir: Path, design_name: str, level: int) -> ctypes.CDLL:
    """Extract the exact unmodified mp_pass1 + mp_run engine from readcheck.c and compile natively."""
    rc_lines = READCHECK_C.read_text(encoding="utf8").splitlines()
    slice_helpers = "\n".join(rc_lines[1627:1640])
    slice_engine = "\n".join(rc_lines[2242:2387])
    assert "static void mp_run(" in slice_engine and "static int mp_pass1(" in slice_engine

    harness_c = f"""
#include <stdint.h>
#include <stddef.h>
#include <string.h>

typedef uint8_t  u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t  i64;

#define LEVEL {level}
#define AVCAM_PHYS 0x401000000UL
#include "expected_mp_{design_name}.h"

typedef i64 (*sc_cb_t)(i64 nr, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f);
static sc_cb_t g_sc_cb = NULL;
static char g_log[262144];
static size_t g_log_len = 0;

static void puts_(const char *s) {{
    size_t n = strlen(s);
    if (g_log_len + n < sizeof(g_log) - 1) {{
        memcpy(g_log + g_log_len, s, n);
        g_log_len += n;
        g_log[g_log_len] = 0;
    }}
}}
static void puthex(const u8 *b, u64 n) {{
    static const char hx[] = "0123456789abcdef";
    for (u64 i = 0; i < n; i++) {{
        char pair[3] = {{ hx[b[i] >> 4], hx[b[i] & 15], 0 }};
        puts_(pair);
    }}
}}
static i64 sc(i64 nr, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f) {{
    return g_sc_cb(nr, a, b, c, d, e, f);
}}

{slice_helpers}
{slice_engine}
#endif /* LEVEL == 19 || LEVEL == 20 */

void sim_run_mp(u8 *av_mapped, u64 lm_base, u64 lm_end, sc_cb_t cb, char *out_buf, size_t out_cap) {{
    g_sc_cb = cb;
    g_log_len = 0;
    g_log[0] = 0;
    mp_run((volatile u8 *)av_mapped, 100 /* fd */, 200 /* pm */, lm_base, lm_end);
    size_t cp = g_log_len < out_cap - 1 ? g_log_len : out_cap - 1;
    memcpy(out_buf, g_log, cp);
    out_buf[cp] = 0;
}}
"""
    src = tmpdir / f"mp_host_{design_name}_{level}.c"
    src.write_text(harness_c, encoding="utf8")
    out_dylib = tmpdir / f"libmp_host_{design_name}_{level}.dylib"
    subprocess.check_call([
        "clang", "-O1", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
        f"-I{HERE}", str(src), "-o", str(out_dylib),
    ])
    lib = ctypes.CDLL(str(out_dylib))
    SC_CB = ctypes.CFUNCTYPE(
        ctypes.c_int64,
        ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
        ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
    )
    lib.sim_run_mp.argtypes = [
        ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, SC_CB, ctypes.c_char_p, ctypes.c_size_t
    ]
    lib._SC_CB = SC_CB
    return lib


def exec_mp(lib, av_buf: bytearray, lm_buf: bytearray, n_av_entries: int, *,
            kview_mismatch_off: int | None = None,
            fail_av_kview_on_write: int | None = None,
            fail_lm_pwrite_num: int | None = None):
    av_arr = (ctypes.c_uint8 * len(av_buf)).from_buffer(av_buf)
    lm_arr = (ctypes.c_uint8 * len(lm_buf)).from_buffer(lm_buf)
    av_first = 0x38F000
    av_phys = 0x401000000
    av_ptr = ctypes.addressof(av_arr) + av_first
    lm_base = ctypes.addressof(lm_arr)
    lm_end = lm_base + len(lm_buf)

    kview_after_pass1 = [0]
    pwrite_calls = [0]
    pread_calls = [0]

    def sc_cb(nr: int, a: int, b: int, c: int, d: int, _e: int, _f: int) -> int:
        if nr == 67:  # pread64(fd, out, len, phys)
            assert a == 100
            off = d - av_phys
            assert av_first <= off and off + c <= av_first + 0x3F000
            pread_calls[0] += 1
            data = bytes(av_buf[off : off + c])
            if pread_calls[0] <= n_av_entries:
                if kview_mismatch_off is not None and off == kview_mismatch_off:
                    data = bytes([data[0] ^ 0xFF]) + data[1:]
            else:
                kview_after_pass1[0] += 1
                if fail_av_kview_on_write is not None and kview_after_pass1[0] == fail_av_kview_on_write:
                    data = bytes([data[0] ^ 0xFF]) + data[1:]
            ctypes.memmove(b, data, c)
            return c
        elif nr == 68:  # pwrite64(pm, bytes, len, addr)
            assert a == 200
            pwrite_calls[0] += 1
            if fail_lm_pwrite_num is not None and pwrite_calls[0] == fail_lm_pwrite_num:
                return -1
            assert lm_base <= d and d + c <= lm_end
            ctypes.memmove(d, b, c)
            return c
        raise AssertionError(f"unexpected syscall {nr}")

    out_buf = ctypes.create_string_buffer(262144)
    cb = lib._SC_CB(sc_cb)
    lib.sim_run_mp(av_ptr, lm_base, lm_end, cb, out_buf, len(out_buf))
    return out_buf.value.decode("utf8"), pread_calls[0], pwrite_calls[0]


def expected_applied(av_stock: bytes, lm_stock: bytes, mp_av, mp_lm):
    av_new = bytearray(av_stock)
    lm_new = bytearray(lm_stock)
    for off, ln, kind, _, changed in mp_av:
        if kind == 1:
            av_new[off : off + ln] = changed[:ln]
    for off, ln, kind, _, changed in mp_lm:
        if kind == 1:
            lm_new[off : off + ln] = changed[:ln]
    return bytes(av_new), bytes(lm_new)


def run_gate4(av_stock: bytes, lm_stock: bytes, mp_av, mp_lm, spec: dict):
    name = spec["name"]
    n_av_entries = len(mp_av)
    n_av_sites = spec["av_sites"]
    mid_w = spec["mid_av_write"]
    print(f"== [{name}] Gate 4: Offline Behavioral Simulation (Exact readcheck.c Level 19/20 Engine) ==")
    av_exp, lm_exp = expected_applied(av_stock, lm_stock, mp_av, mp_lm)

    with tempfile.TemporaryDirectory(prefix=f"p2b_sim_{name}_") as td:
        tmp = Path(td)
        lib19 = build_readcheck_mp_host_lib(tmp, name, 19)
        lib20 = build_readcheck_mp_host_lib(tmp, name, 20)

        # 1. Apply on stock
        av_b, lm_b = bytearray(av_stock), bytearray(lm_stock)
        log, _, pw = exec_mp(lib19, av_b, lm_b, n_av_entries)
        assert "DONE: every site holds the target bytes (apply)" in log, log
        assert pw == 4 and bytes(av_b) == av_exp and bytes(lm_b) == lm_exp
        print(f"  [1/9] PASS: Apply (L19) on stock -> {n_av_sites} av + 4 lm sites updated, 0 unlisted bytes touched")

        # 2. Apply again (idempotent)
        log, _, pw = exec_mp(lib19, av_b, lm_b, n_av_entries)
        assert "NOTHING TO DO: every site already holds the target bytes (apply)" in log, log
        assert pw == 0 and bytes(av_b) == av_exp and bytes(lm_b) == lm_exp
        print("  [2/9] PASS: Apply again (idempotent) -> NOTHING TO DO, 0 writes")

        # 3. Putback on applied
        log, _, pw = exec_mp(lib20, av_b, lm_b, n_av_entries)
        assert "DONE: every site holds the target bytes (put back)" in log, log
        assert pw == 4 and bytes(av_b) == av_stock and bytes(lm_b) == lm_stock
        print("  [3/9] PASS: Putback (L20) on applied -> 100% byte-for-byte stock restored")

        # 4. Putback again (idempotent)
        log, _, pw = exec_mp(lib20, av_b, lm_b, n_av_entries)
        assert "NOTHING TO DO: every site already holds the target bytes (put back)" in log, log
        assert pw == 0 and bytes(av_b) == av_stock and bytes(lm_b) == lm_stock
        print("  [4/9] PASS: Putback again (idempotent) -> NOTHING TO DO, 0 writes")

        # 5. Pass 1 refusal on 1-bit flip in EVERY guard (18 av + 5 lm)
        for off, ln, kind, _, _ in mp_av:
            if kind != 0:
                continue
            av_bad, lm_bad = bytearray(av_stock), bytearray(lm_stock)
            av_bad[off] ^= 0x01
            snap = bytes(av_bad)
            log, _, pw = exec_mp(lib19, av_bad, lm_bad, n_av_entries)
            assert "is not as in the file; nothing is written" in log and pw == 0
            assert bytes(av_bad) == snap and bytes(lm_bad) == lm_stock
        for off, ln, kind, _, _ in mp_lm:
            if kind != 0:
                continue
            av_bad, lm_bad = bytearray(av_stock), bytearray(lm_stock)
            lm_bad[off] ^= 0x01
            snap = bytes(lm_bad)
            log, _, pw = exec_mp(lib19, av_bad, lm_bad, n_av_entries)
            assert "is not as in the file; nothing is written" in log and pw == 0
            assert bytes(av_bad) == av_stock and bytes(lm_bad) == snap
        print("  [5/9] PASS: Pass 1 refusal on 1-bit flip in ALL 18 av + 5 lm guards -> 0 writes")

        # 6. Pass 1 refusal on corrupted sample av sites (incl. A2-rate) & ALL 4 lm sites
        av_site_samples = [r for r in mp_av if r[2] == 1]
        for off, ln, _, stock, changed in (av_site_samples[0], av_site_samples[1], av_site_samples[mid_w], av_site_samples[-1]):
            av_bad, lm_bad = bytearray(av_stock), bytearray(lm_stock)
            # Pick a byte value guaranteed != stock[0] and != changed[0]
            bad_b = next(v for v in (0x5A, 0xA5, 0x3C) if v != stock[0] and v != changed[0])
            av_bad[off] = bad_b
            snap = bytes(av_bad)
            log, _, pw = exec_mp(lib19, av_bad, lm_bad, n_av_entries)
            assert "holds neither set of bytes; nothing is written" in log and pw == 0
            assert bytes(av_bad) == snap and bytes(lm_bad) == lm_stock
        for off, ln, kind, _, _ in mp_lm:
            if kind != 1:
                continue
            av_bad, lm_bad = bytearray(av_stock), bytearray(lm_stock)
            lm_bad[off] ^= 0x04
            snap = bytes(lm_bad)
            log, _, pw = exec_mp(lib19, av_bad, lm_bad, n_av_entries)
            assert "holds neither set of bytes; nothing is written" in log and pw == 0
            assert bytes(av_bad) == av_stock and bytes(lm_bad) == snap
        print("  [6/9] PASS: Pass 1 refusal on corrupted av & lm sites -> 0 writes")

        # 7. Pass 1 refusal on kernel-view mismatch
        av_bad, lm_bad = bytearray(av_stock), bytearray(lm_stock)
        log, _, pw = exec_mp(lib19, av_bad, lm_bad, n_av_entries, kview_mismatch_off=0x38F724)
        assert "the kernel's own view differs from the mapping; nothing is written" in log and pw == 0
        assert bytes(av_bad) == av_stock and bytes(lm_bad) == lm_stock
        print("  [7/9] PASS: Pass 1 refusal on kernel-view mismatch -> 0 writes")

        # 8. Pass 2 lost-write auto-undo on 1st, middle, and last av-cam write
        for wnum in (1, mid_w, n_av_sites):
            av_u, lm_u = bytearray(av_stock), bytearray(lm_stock)
            log, _, pw = exec_mp(lib19, av_u, lm_u, n_av_entries, fail_av_kview_on_write=wnum)
            assert "NOT DONE: a step failed; every site this run touched was put back" in log, log
            assert pw == 0 and bytes(av_u) == av_stock and bytes(lm_u) == lm_stock
        print(f"  [8/9] PASS: Pass 2 av-cam auto-undo on 1st, {mid_w}th, and {n_av_sites}th write -> 100% stock restored")

        # 9. Pass 2 libmpr write failure auto-undo (restores BOTH libmpr AND all av-cam sites!)
        for wnum in (1, 4):
            av_u, lm_u = bytearray(av_stock), bytearray(lm_stock)
            log, _, _ = exec_mp(lib19, av_u, lm_u, n_av_entries, fail_lm_pwrite_num=wnum)
            assert "NOT DONE: a step failed; every site this run touched was put back" in log, log
            assert bytes(av_u) == av_stock and bytes(lm_u) == lm_stock
        print(f"  [9/9] PASS: Pass 2 libmpr auto-undo on 1st & 4th write -> BOTH libmpr & all {n_av_sites} av-cam restored")


def main() -> None:
    av = AV_PATH.read_bytes()
    lm = LM_PATH.read_bytes()
    for spec in DESIGNS:
        mp_av, mp_lm = run_gate2(av, lm, spec)
        run_gate3(mp_av, mp_lm, spec)
        run_gate4(av, lm, mp_av, mp_lm, spec)
    print("\nALL PHASE 2B GATES (2, 3, 4) PASSED 100% FOR BOTH DESIGNS!")


if __name__ == "__main__":
    main()
