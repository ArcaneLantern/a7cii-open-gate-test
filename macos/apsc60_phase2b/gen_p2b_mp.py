#!/usr/bin/env python3
"""Generate Phase 2B encoder-side headers for A7C II (ILCE-7CM2) fw 2.01.

Generates BOTH:
  1) expected_mp_ogfile2560_60p_c2_v1.h        (stock menu bitrates, --menu-rate)
  2) expected_mp_ogfile2560_60p_r240_c2_v1.h   (V60-targeted 1.20x bitrate boost, <= 240 Mbps, default)

Both match the exact format of helper/expected_mp_ogfile2560_hs_s_c2_v1.h so that the
proven unmodified helper/readcheck.c compiles verbatim with -DLEVEL=19 / -DLEVEL=20
and -DAVCAM_PHYS=0x401000000UL (zero new C engine code, zero external imports).
Every stock byte and guard byte is verified byte-for-byte against unpacked A7C II 2.01
av-cam.bin (SHA-256 90343f5f...) and libmpr.so (SHA-256 4fb6d886...).
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EX = REPO / "windows/camera_private/extracted_a7c2_201/0700_part_image/dev"
AV_PATH = EX / "nflasha3/av-cam.bin"
LM_PATH = EX / "nflasha15/lib/libmpr.so"

AV_SHA256 = "90343f5f2edc0393ad8809d907cfe648dfd3a82ce0ef39cf06167929d8955096"
LM_SHA256 = "4fb6d886c1ac620399d09c9c2ffc074315968cf0ada764062913c4e0783fc7f9"

# Exact A7C II 2.01 table bases (matching gen_c2_write.py)
AV_SHIFT = 0xDF50
DESC_BASE = 0x38393C + AV_SHIFT       # 0x39188c, stride 0x90
UNIT_BASE = 0x38389C + AV_SHIFT       # 0x3917ec
CLIP_BASE = 0x38F710
COMB_BASE = 0x2D32B8 - 0x57B0         # 0x2cdb08, stride 0x18
PROFILE_END = 0x3B32CC + AV_SHIFT + 316 * 0x100  # 0x3d4e1c

# All 16 NTSC 59.94p (rc=0, ir=0, f0c=0) descriptors on A7C II 2.01 (per #59 spec):
#   (desc_idx, codec, desc_addr, profile_key, prof_addr)
DESCRIPTORS_60P: list[tuple[int, str, int, int, int]] = [
    # 7 AVC (codec=4)
    (3,   "avc",  0x391A3C, 40,     0x3A9E1C),
    (24,  "avc",  0x39260C, 102400, 0x3AB31C),
    (38,  "avc",  0x392DEC, 102402, 0x3AC11C),
    (70,  "avc",  0x393FEC, 102404, 0x3AE11C),
    (72,  "avc",  0x39410C, 102406, 0x3AE31C),
    (80,  "avc",  0x39458C, 102807, 0x3AEB1C),
    (82,  "avc",  0x3946AC, 102809, 0x3AED1C),
    # 9 HEVC (codec=14)
    (404, "hevc", 0x39FBCC, 101502, 0x3C311C),
    (431, "hevc", 0x3A0AFC, 101800, 0x3C4C1C),
    (432, "hevc", 0x3A0B8C, 102100, 0x3C4D1C),
    (443, "hevc", 0x3A11BC, 101514, 0x3C581C),
    (473, "hevc", 0x3A229C, 102106, 0x3C761C),
    (477, "hevc", 0x3A24DC, 101820, 0x3C7A1C),
    (554, "hevc", 0x3A502C, 101516, 0x3CC71C),
    (572, "hevc", 0x3A5A4C, 101804, 0x3CD91C),
    (574, "hevc", 0x3A5B6C, 102104, 0x3CDB1C),
]

# Exact V60-targeted 1.20x A2-rate table for ogfile2560_60p_r240_c2_v1 (per #65 spec):
#   desc_idx -> ((br_sh, br_val), (mb_sh, mb_val), (sb_sh, sb_val))
#   at pbase + ro + {0, 8, 16} as <II, where ro = 0xb0 (AVC) or 0xac (HEVC).
#   Formula: br/max = (val + 1) * 2^(sh + 6) bps; cpb = (val + 1) * 2^(sh + 4) bits.
R240_RATES: dict[int, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]] = {
    3:   ((2, 703124),  (8, 11717), (6, 187499)),  # AVC  150/156/160 -> 180.0/192.0/192.0
    24:  ((8, 14647),   (8, 14647), (6, 234374)),  # AVC  200/200/200 -> 240.0/240.0/240.0
    38:  ((0, 937499),  (7, 8788),  (5, 149999)),  # AVC   50/60/64   ->  60.0/72.0/76.8
    70:  ((8, 10252),   (8, 10252), (6, 164061)),  # AVC  140/140/140 -> 168.0/168.0/168.0
    72:  ((0, 618749),  (6, 10252), (4, 164061)),  # AVC   33/35/35   ->  39.6/42.0/42.0
    80:  ((0, 937499),  (7, 8788),  (5, 149999)),  # AVC   50/60/64   ->  60.0/72.0/76.8
    82:  ((0, 459374),  (5, 15233), (4, 131249)),  # AVC 24.5/26/28   ->  29.4/31.2/33.6
    404: ((1, 909374),  (7, 14647), (5, 234374)),  # HEVC  97/100/100 -> 116.4/120.0/120.0
    431: ((2, 703124),  (8, 11717), (6, 187499)),  # HEVC 150/160/160 -> 180.0/192.0/192.0
    432: ((2, 703124),  (8, 11717), (6, 187499)),  # HEVC 150/160/160 -> 180.0/192.0/192.0
    443: ((8, 14647),   (8, 14647), (6, 234374)),  # HEVC 200/204/204 -> 240.0/240.0/240.0
    473: ((0, 843749),  (6, 14159), (4, 226561)),  # HEVC  45/48/48   ->  54.0/58.0/58.0
    477: ((0, 843749),  (6, 14159), (4, 226561)),  # HEVC  45/48/48   ->  54.0/58.0/58.0
    554: ((0, 1068749), (7, 8788),  (5, 156249)),  # HEVC  57/60/66   ->  68.4/72.0/80.0
    572: ((0, 1406249), (7, 11717), (5, 187499)),  # HEVC  75/80/80   ->  90.0/96.0/96.0
    574: ((0, 1406249), (7, 11717), (5, 187499)),  # HEVC  75/80/80   ->  90.0/96.0/96.0
}

# libmpr.so g_videoParamComb height sites (2160 -> 2560), identical to ogfile2560_hs_s_c2_v1
LM_SITES: list[tuple[int, int]] = [
    (0x2CDB0E, 0),  # row 0: AVC 4:2:0 8-bit
    (0x2CDB3E, 2),  # row 2: AVC 4:2:2 10-bit
    (0x2CDB86, 5),  # row 5: HEVC 4:2:0 10-bit
    (0x2CDBB6, 7),  # row 7: HEVC 4:2:2 10-bit
]


def hx(b: bytes) -> str:
    return ", ".join(f"0x{x:02x}" for x in b)


def h16(b: bytes) -> str:
    assert len(b) <= 16
    return hx(b + bytes(16 - len(b)))


def first_load_memsz(lib: bytes) -> int:
    phoff = struct.unpack_from("<Q", lib, 0x20)[0]
    phentsize, phnum = struct.unpack_from("<HH", lib, 0x36)
    for k in range(phnum):
        p_type, _flags, p_off, p_vaddr, _paddr, p_filesz, p_memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", lib, phoff + k * phentsize
        )
        if p_type == 1:
            assert p_off == 0 and p_vaddr == 0
            return p_filesz
    raise AssertionError("no PT_LOAD segment in libmpr.so")


def generate_design(av: bytes, lm: bytes, av_sha: str, lm_sha: str, *, boost_r240: bool) -> None:
    design = "ogfile2560_60p_r240_c2_v1" if boost_r240 else "ogfile2560_60p_c2_v1"
    out_h = HERE / f"expected_mp_{design}.h"

    av_sites: list[tuple[int, bytes, bytes]] = []
    av_guards: list[tuple[int, bytes]] = []

    # Global av-cam.bin guards (identical to gen_c2_write.py av_guards())
    g_unit = av[UNIT_BASE + 0x20 : UNIT_BASE + 0x30]
    g_clip = av[CLIP_BASE + 0x50 : CLIP_BASE + 0x60]
    assert struct.unpack("<4I", g_unit) == (0x486, 0x474, 0x477, 0x47A)
    assert struct.unpack("<4I", g_clip) == (1, 3840, 2160, 23)
    av_guards.append((UNIT_BASE + 0x20, g_unit))  # 0x39180c
    av_guards.append((CLIP_BASE + 0x50, g_clip))  # 0x38f760

    # Global av-cam.bin sites: A3-clip (0x38f724) and A0-unit row 3 (0x39181c)
    av_sites.append((
        0x38F724,
        bytes.fromhex("00200000e010000003000000"),
        bytes.fromhex("000f0000000a000017000000"),
    ))
    av_sites.append((
        0x39181C,
        bytes.fromhex("00000000730400007604000079040000"),
        bytes.fromhex("8604000074040000770400007a040000"),
    ))

    # All 16 NTSC 59.94p descriptors + their 16 unique profiles (UseCore=2, rows_c0=1280, Level 6.0)
    seen_keys: set[int] = set()
    seen_profs: set[int] = set()
    for desc_idx, codec, dbase, pkey, pbase in DESCRIPTORS_60P:
        assert dbase == DESC_BASE + desc_idx * 0x90, f"desc #{desc_idx} addr mismatch"
        assert pkey not in seen_keys and pbase not in seen_profs
        seen_keys.add(pkey)
        seen_profs.add(pbase)

        # Descriptor guard at +0x28: profile_key
        assert struct.unpack_from("<I", av, dbase + 0x28)[0] == pkey, f"desc #{desc_idx} profile_key mismatch"
        av_guards.append((dbase + 0x28, struct.pack("<I", pkey)))

        # Descriptor sites: +0x38 (2-core mode pair 0,1 -> 2,6) & +0x78 (UseCore 0->2, height 2160->2560)
        av_sites.append((
            dbase + 0x38,
            bytes.fromhex("0000000001000000"),
            bytes.fromhex("0200000006000000"),
        ))
        av_sites.append((
            dbase + 0x78,
            bytes.fromhex("000001000000000f7008"),
            bytes.fromhex("020001000000000f000a"),
        ))

        # Profile sites (5 base per profile): +0x06, +0x38, +0x56, (+0x8c HEVC / +0x90 AVC), (+0x9e HEVC / +0xa7 AVC)
        av_sites.append((pbase + 0x06, struct.pack("<H", 2160), struct.pack("<H", 2560)))
        av_sites.append((pbase + 0x38, struct.pack("<I", 2160), struct.pack("<I", 2560)))
        av_sites.append((pbase + 0x56, struct.pack("<H", 0), struct.pack("<H", 1280)))
        if codec == "hevc":
            av_sites.append((pbase + 0x8C, struct.pack("<H", 2160), struct.pack("<H", 2560)))
            av_sites.append((pbase + 0x9E, bytes([153]), bytes([180])))  # HEVC Level 5.1 -> 6.0
            ro = 0xAC
        elif codec == "avc":
            av_sites.append((pbase + 0x90, struct.pack("<H", 2160), struct.pack("<H", 2560)))
            av_sites.append((pbase + 0xA7, bytes([52]), bytes([60])))    # AVC Level 5.2 -> 6.0
            ro = 0xB0
        else:
            raise AssertionError(codec)

        # Optional V60-targeted A2-rate sites (3 x 8B = 24B per profile: br, max_rate, cpb_size)
        if boost_r240:
            br_pair, mb_pair, sb_pair = R240_RATES[desc_idx]
            br_bps = (br_pair[1] + 1) * (1 << (br_pair[0] + 6))
            mb_bps = (mb_pair[1] + 1) * (1 << (mb_pair[0] + 6))
            sb_bits = (sb_pair[1] + 1) * (1 << (sb_pair[0] + 4))
            assert br_bps <= mb_bps <= sb_bits <= 240_000_000, (
                f"desc #{desc_idx} rate invariant failed: br={br_bps}, max={mb_bps}, cpb={sb_bits}"
            )
            # Verify stock rates are strictly lower than boosted rates and sensible
            for k, (sh_new, val_new) in enumerate((br_pair, mb_pair, sb_pair)):
                off = pbase + ro + k * 8
                sh_old, val_old = struct.unpack_from("<II", av, off)
                exp_base = 6 if k < 2 else 4
                old_rate = (val_old + 1) * (1 << (sh_old + exp_base))
                new_rate = (val_new + 1) * (1 << (sh_new + exp_base))
                assert 20_000_000 <= old_rate < new_rate <= 240_000_000, (
                    f"desc #{desc_idx} rate[{k}] @0x{off:x}: old={old_rate} new={new_rate}"
                )
                ratio = new_rate / old_rate
                assert 1.15 <= ratio <= 1.25, f"desc #{desc_idx} rate[{k}] ratio={ratio:.4f}"
                av_sites.append((off, struct.pack("<II", sh_old, val_old), struct.pack("<II", sh_new, val_new)))

    # Verify every av-cam.bin site & guard against unpacked av-cam.bin
    for off, old, new in av_sites:
        assert 0 < len(old) == len(new) <= 16 and old != new
        actual = av[off : off + len(old)]
        assert actual == old, f"av-cam.bin site mismatch at 0x{off:x}: file={actual.hex()} want={old.hex()}"
    for off, gb in av_guards:
        assert 0 < len(gb) <= 16
        assert av[off : off + len(gb)] == gb, f"av-cam.bin guard mismatch at 0x{off:x}"

    # libmpr.so sites & guards
    lm_sites: list[tuple[int, bytes, bytes]] = []
    lm_guards: list[tuple[int, bytes]] = [(0, lm[:16])]
    for off, row_idx in LM_SITES:
        assert off == COMB_BASE + row_idx * 0x18 + 6
        assert struct.unpack_from("<H", lm, off - 2)[0] == 3840
        old = struct.pack("<H", 2160)
        new = struct.pack("<H", 2560)
        assert lm[off : off + 2] == old
        lm_sites.append((off, old, new))
        lm_guards.append((off - 6, lm[off - 6 : off]))

    def build_table(sites, guards, name):
        for ga, gb in guards:
            for sa, so, _ in sites:
                assert not (ga < sa + len(so) and sa < ga + len(gb)), f"{name}: guard 0x{ga:x} overlaps site 0x{sa:x}"
        tab = sorted([(a, 1, o, n) for a, o, n in sites] + [(a, 0, b, b"") for a, b in guards], key=lambda x: x[0])
        for (a, _, ao, _), (b, _, _, _) in zip(tab, tab[1:]):
            assert a + len(ao) <= b, f"{name}: entries overlap at 0x{a:x} and 0x{b:x}"
        return tab

    av_tab = build_table(av_sites, av_guards, "av-cam.bin")
    lm_tab = build_table(lm_sites, lm_guards, "libmpr.so")

    if boost_r240:
        assert len(av_sites) == 162 and len(av_guards) == 18 and len(av_tab) == 180
        assert sum(len(o) for _, o, _ in av_sites) == 876
    else:
        assert len(av_sites) == 114 and len(av_guards) == 18 and len(av_tab) == 132
        assert sum(len(o) for _, o, _ in av_sites) == 492
    assert len(lm_sites) == 4 and len(lm_guards) == 5 and len(lm_tab) == 9
    assert sum(len(o) for _, o, _ in lm_sites) == 8

    first = av_tab[0][0] & ~0xFFF
    last = max(a + len(o) for a, _, o, _ in av_tab)
    span = ((last - first) + 0xFFF) & ~0xFFF
    assert first == 0x38F000 and span == 0x3F000, f"unexpected av span: first=0x{first:x} span=0x{span:x}"
    assert (CLIP_BASE & ~0xFFF) <= first and first + span <= ((PROFILE_END + 0xFFF) & ~0xFFF)
    assert max(a + len(o) for a, _, o, _ in lm_tab) <= first_load_memsz(lm)

    longest = max(len(av_tab), len(lm_tab))
    mp_max = (longest + 31) & ~31
    assert mp_max == (192 if boost_r240 else 160)

    out = [
        f"/* Generated by macos/apsc60_phase2b/gen_p2b_mp.py (A7C II 2.01 Phase 2B, NTSC 59.94p 3840x2560{', V60 r240' if boost_r240 else ''}). Do not edit. */",
        f"/* av-cam.bin {av_sha[:16]}..., libmpr.so {lm_sha[:16]}... (2.01). kind 1: site; kind 0: guard. */",
        f"/* av-cam.bin: {len(av_sites)} sites ({sum(len(o) for _, o, _ in av_sites)} B) + {len(av_guards)} guards = {len(av_tab)} entries; "
        f"libmpr.so: {len(lm_sites)} sites ({sum(len(o) for _, o, _ in lm_sites)} B) + {len(lm_guards)} guards = {len(lm_tab)} entries. */",
        f'#define MP_DESIGN "{design}"',
        f"#define MP_AV_FIRST 0x{first:x}UL                 /* file offset of the first page read of av-cam.bin */",
        f"#define MP_AV_BYTES 0x{span:x}UL                  /* whole pages: every entry of mp_av lies inside */",
        f"#define MP_MAX {mp_max}                          /* room for the entries of the longer table (levels 19 and 20) */",
        "struct mp_site { u32 off; u8 len; u8 kind; u8 stock[16]; u8 changed[16]; };",
    ]
    for var, tab, label in (("mp_av", av_tab, "av-cam.bin"), ("mp_lm", lm_tab, "libmpr.so")):
        out.append(f"static const struct mp_site {var}[] = {{      /* {label}: file offsets */")
        for a, kind, o, n in tab:
            out.append(f"    {{ 0x{a:x}, {len(o)}, {kind}, {{ {h16(o)} }}, {{ {h16(n)} }} }},")
        out.append("};")

    out_h.write_text("\n".join(out) + "\n", encoding="utf8", newline="\n")
    print(
        f"Wrote {out_h.name}: av-cam.bin {len(av_sites)} sites ({sum(len(o) for _, o, _ in av_sites)} B) + {len(av_guards)} guards "
        f"(first=0x{first:x}, span=0x{span:x}, MP_MAX={mp_max}), libmpr.so {len(lm_sites)} sites ({sum(len(o) for _, o, _ in lm_sites)} B) + {len(lm_guards)} guards."
    )


def main() -> None:
    av = AV_PATH.read_bytes()
    lm = LM_PATH.read_bytes()
    av_sha = hashlib.sha256(av).hexdigest()
    lm_sha = hashlib.sha256(lm).hexdigest()
    assert av_sha == AV_SHA256, f"av-cam.bin SHA-256 mismatch: {av_sha}"
    assert lm_sha == LM_SHA256, f"libmpr.so SHA-256 mismatch: {lm_sha}"

    generate_design(av, lm, av_sha, lm_sha, boost_r240=False)
    generate_design(av, lm, av_sha, lm_sha, boost_r240=True)


if __name__ == "__main__":
    main()
