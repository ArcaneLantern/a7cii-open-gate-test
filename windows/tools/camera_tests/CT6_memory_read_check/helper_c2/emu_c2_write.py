"""Run the built A7C II 2.01 write libraries (build_c2_write.sh) in Unicorn against a model camera. Offline; no camera. (A7C II port, 2026-11-24)

  work/venv/Scripts/python.exe -I -B emu_c2_write.py

The harness is the A7 IV one (helper/emu_built.py) with this camera's models, as helper_s3/emu_s3_write.py does for the A7S III:
  - coprocessor transfers must be whole 64-byte lines to or from the allocated buffer; a transfer TOWARDS the coprocessor may change only
    bytes of a listed site, and only to that site's stock or new bytes;
  - levels 5/6 (four sensor designs): the sensor object 0x18aeb8c -> control -> +0x684 -> parameter object {vtable, 30, ids, 0, records}
    -> the 0x170-byte stored records, the one of the design's mode holding the emulated stock record;
  - levels 13/14: the memory manager's pages as the A7C II's OWN DMM code leaves them after parsing its DmmCfg.bin
    (build/emu_dmm_server.py --build a7c2_201); after level 13 the same code must hand out units of the new size;
  - levels 16/17: the movie size table as the A7C II's set-up fills it (build/emu_size_table.py), the 16 setting bytes zero, the id rows
    and key lists from the DMM model, and the loaded image for the screen words;
  - levels 19/20: av-cam.bin at its physical address and libmpr.so (or appFw.so) in the process's own mapping.
Cases per library: apply on stock, apply again (nothing to do), put back (byte for byte), a site holding foreign bytes, a lost write
(undo), a guard wrong.
"""
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "helper"))
sys.path.insert(0, str(ROOT / "build"))
import emu_built as E  # noqa: E402
from unicorn import arm64_const as R  # noqa: E402
import emu_dmm_server  # noqa: E402
import emu_size_table  # noqa: E402
import parse_dmmcfg  # noqa: E402

BASE, CP_HI = 0x108000, 0x10000000
DES = ROOT / "modding/designs"
IMAGER, CTRL, OBJ, CTRL_DRVPARAM = 0x18AEB8C, 0x1500000, 0x1500800, 0x684
IDS, RECS, REC, COUNT, VTABLE = 0xDE99D4, 0x25DC04C, 0x170, 30, 0xDEAC48
LM_AT = 0x7F82D15000
RESULT = E.RESULT
DEFAULT_DESIGN = E.DESIGN


def ex(rel):
    return (ROOT / "work/extracted/a7c2_201/0700_part_image/dev" / rel).read_bytes()


def design(name):
    return json.loads((DES / f"{name}.json").read_text(encoding="utf8"))


def lib(name):
    return (HERE / f"libJit_c2_{name}.so").read_bytes()


class Mem:
    """Coprocessor memory: the image at 0x108000, zeros elsewhere, with what the model camera adds."""

    def __init__(self, image):
        self.pages, self.image = {}, image

    def page(self, a):
        a &= ~0xFFF
        if a not in self.pages:
            p = bytearray(4096)
            if BASE <= a < BASE + len(self.image):
                chunk = self.image[a - BASE:a - BASE + 4096]
                p[:len(chunk)] = chunk
            self.pages[a] = p
        return self.pages[a]

    def read(self, a, n):
        return bytes(self.page(a + k)[(a + k) & 0xFFF] for k in range(n))

    def write(self, a, data):
        for k, v in enumerate(data):
            self.page(a + k)[(a + k) & 0xFFF] = v

    def snapshot(self):
        return {a: bytes(p) for a, p in self.pages.items()}


class Run(E.Run):
    """emu_s3_write.py's level-5-rules run: every import is modelled here, against Mem."""

    def __init__(self, libbytes, files, dirs, mem, allowed, lose=None):
        saved, E.DESIGN = E.DESIGN, DEFAULT_DESIGN                 # the A7 IV harness reads a sensor design's sites in its constructor; not used here
        super().__init__(libbytes, 5, files, dirs, cp=b"")
        E.DESIGN = saved
        self.mem, self.allowed, self.lose, self.n_writes = mem, allowed, lose, 0

    def go(self):
        self.uc.reg_write(R.UC_ARM64_REG_SP, E.STACK - 0x1000)
        self.uc.reg_write(R.UC_ARM64_REG_X30, E.RET)
        self.uc.emu_start(self.ctor, E.RET, count=2_000_000_000)
        if not self.bad and self.uc.reg_read(R.UC_ARM64_REG_PC) != E.RET:
            self.bad = "the constructor did not return"
        return self

    def on_import(self, uc, address, size, _):
        x = lambda n: uc.reg_read(getattr(R, "UC_ARM64_REG_X%d" % n))
        name, ret = self.imports.get(address), 0
        if name == "_Z9dmm_allocjPj":
            uc.mem_write(x(1), struct.pack("<I", E.BUF_HANDLE))
            ret = self.buf
        elif name == "_Z8dmm_freej":
            pass
        elif name == "sen_dma_transfer":
            d, src, dst, n = x(0) & 0xFFFFFFFF, x(1), x(2), x(3) & 0xFFFFFFFF
            cp = src if d == 0 else dst
            if d not in (0, 1) or (dst if d == 0 else src) != self.buf or cp & 63 or n & 63 or not 0 < n <= 4096 or cp < BASE or cp + n > CP_HI:
                self.bad = "a transfer that is not whole lines to or from the buffer: dir %d src %#x dst %#x size %d" % (d, src, dst, n)
                uc.emu_stop()
                return
            va = next(v for v, off in self.map_phys.items() if v in self.maps and off <= self.buf < off + self.maps[v]) + self.buf % 4096
            if d == 0:
                uc.mem_write(va, self.mem.read(cp, n))
            else:
                data, now = bytes(uc.mem_read(va, n)), self.mem.read(cp, n)
                for k in range(n):
                    if data[k] != now[k] and data[k] not in self.allowed.get(cp + k, ()):
                        self.bad = "a write that changes %#x to %#x, which is not a listed site's stock or new byte" % (cp + k, data[k])
                        uc.emu_stop()
                        return
                self.n_writes += 1
                if self.lose != self.n_writes:
                    self.mem.write(cp, data)
        else:
            self.bad = "call of an unexpected import %r" % name
            uc.emu_stop()
            return
        uc.reg_write(R.UC_ARM64_REG_X0, ret)
        uc.reg_write(R.UC_ARM64_REG_PC, uc.reg_read(R.UC_ARM64_REG_X30))


def dmm_server(opens=(), frees=()):
    emu_dmm_server.use_build("a7c2_201")
    cfg = parse_dmmcfg.Config(parse_dmmcfg.find_file("a7c2_201"), "a7c2_201")
    srv = emu_dmm_server.Server(cfg.data, cfg)
    assert srv.init() == 0
    for i in opens:
        assert srv.open(i)["ok"]
    for n in frees:
        assert srv.free(n)["ok"]
    return srv


def main():
    fail = []
    ok = lambda cond, text: (print(("PASS  " if cond else "FAIL  ") + text), fail.append(text) if not cond else None)
    image = ex("nflasha8/cpapp-b.bin")
    files = E.tree()
    files["/dev/mem"] = b""
    dirs = {"/tmp_wbshd", "/sys/module/liro/parameters", "/proc/driver/pcie"}

    def go(libbytes, mem, allowed, **kw):
        r = Run(libbytes, files, dirs, mem, allowed, **kw).go()
        return r, r.writes.get(RESULT, b"").decode("utf8", "replace")

    def changed_only(mem, before, allowed):
        for a, p in mem.pages.items():
            b = before.get(a)
            if b is None:
                b = image[a - BASE:a - BASE + 4096].ljust(4096, b"\0") if BASE <= a < BASE + len(image) else bytes(4096)
            if any(p[k] != b[k] and (a + k) not in allowed for k in range(4096)):
                return False
        return True

    # ---- levels 5 and 6: sensor sites and the stored record
    for name in ("ogsq_c2_v1", "og57n_c2_v1", "ogsq880_c2_v1", "og57n880_c2_v1"):
        d = design(name)
        rec = json.loads((ROOT / d["stored_record"]["evidence"]).read_text(encoding="utf8"))
        index = d["mode_index"]
        st, nw = bytes.fromhex(rec["stock_record_10"]), bytes.fromhex(rec["design_record_10"])
        allowed = {}
        for p in d["patches"]:
            a = int(p["addr"], 16)
            for k, (o, n) in enumerate(zip(bytes.fromhex(p["old"]), bytes.fromhex(p["new"]))):
                allowed[a + k] = (o, n)
        for k in range(REC):
            if st[k] != nw[k]:
                allowed[RECS + index * REC + k] = (st[k], nw[k])

        def camera():
            m = Mem(image)
            m.write(IMAGER, struct.pack("<I", CTRL))
            m.write(CTRL + CTRL_DRVPARAM, struct.pack("<I", OBJ))
            m.write(OBJ, struct.pack("<6I", VTABLE, COUNT, IDS, 0, RECS, 0xDE9A4C))
            for i in range(COUNT):
                m.write(RECS + i * REC, bytes([i]) * REC)
            m.write(RECS + index * REC, st)
            return m

        def state(mem):
            vals = [(mem.read(a, 1)[0], pr) for a, pr in allowed.items()]
            return "stock" if all(v == pr[0] for v, pr in vals) else "new" if all(v == pr[1] for v, pr in vals) else "mixed"
        L5, L6 = lib(f"level5_{name}"), lib(f"level6_{name}")
        mem = camera()
        before = mem.snapshot()
        r, t = go(L5, mem, allowed)
        ok(not r.bad and "DONE: all 14 sites hold the target bytes (apply)" in t and state(mem) == "new" and changed_only(mem, before, allowed),
           f"{name} level 5 on a stock camera: 13 sites and the stored record of mode {d['mode_id']} new, nothing else changed ({r.n_writes} line writes)")
        r, t = go(L5, mem, allowed)
        ok(not r.bad and "NOTHING TO DO" in t and r.n_writes == 0, f"{name} level 5 again: nothing to do, no write")
        r, t = go(L6, mem, allowed)
        ok(not r.bad and "(put back)" in t and "DONE" in t and state(mem) == "stock" and changed_only(mem, before, {}), f"{name} level 6: everything stock again, byte for byte")
        mem = camera()
        mem.write(int(d["patches"][0]["addr"], 16), b"\x55")
        r, t = go(L5, mem, allowed)
        ok(not r.bad and r.n_writes == 0 and "holds neither the stock nor the new bytes; nothing is written" in t, f"{name} level 5 with a site holding other bytes: nothing is written")
        for lose in (1, 7, 14):
            mem = camera()
            before = mem.snapshot()
            r, t = go(L5, mem, allowed, lose=lose)
            ok(not r.bad and "NOT DONE: a step failed; every site this run touched was put back" in t and state(mem) == "stock" and changed_only(mem, before, {}),
               f"{name} level 5 with line write {lose} lost: reported, every site put back")
        mem = camera()
        mem.write(OBJ + 0x10, bytes(4))
        r, t = go(L5, mem, allowed)
        ok(not r.bad and r.n_writes == 0 and "the stored record was not found" in t, f"{name} level 5 with a null records pointer: nothing is written")
        other = 9 if index == 10 else 10                      # the design of one mode must not take the other mode's record
        mem = camera()
        m_ids = list(struct.unpack_from("<30I", image, IDS - BASE))
        mem.write(IDS + 4 * index, struct.pack("<I", 0x1234))
        r, t = go(L5, mem, allowed)
        ok(not r.bad and r.n_writes == 0, f"{name} level 5 when mode {d['mode_id']} is not in the id table: nothing is written (not mode {m_ids[other]:#x}'s record)")

    # ---- levels 13 and 14: the memory manager
    dd = design("dmmwb4688_c2_v1")
    E.DESIGN = "dmmwb4688_c2_v1"
    sites = [(int(s["ram_address"], 16), bytes.fromhex(s["old"]), bytes.fromhex(s["new"])) for s in dd["sites"]]
    d_allowed = {a + k: (o[k], n[k]) for a, o, n in sites for k in range(4)}

    def dmm_mem(srv):
        m = Mem(image)
        for a, b in srv.snapshot().values():
            m.write(a, b)
        return m

    srv = dmm_server()
    mem = dmm_mem(srv)
    before = mem.snapshot()
    L13, L14 = lib("level13_dmmwb4688_c2_v1"), lib("level14_dmmwb4688_c2_v1")
    r, t = go(L13, mem, d_allowed)
    ok(not r.bad and "DONE" in t and all(mem.read(a, 4) == n for a, _, n in sites) and changed_only(mem, before, d_allowed),
       f"level 13 on the A7C II's own DMM state (nothing open): the four words new, nothing else changed ({r.n_writes} line writes)")
    srv2 = dmm_server()
    for a, _, n in sites:
        srv2.uc.mem_write(a, n)
    o = srv2.open(0x43E)
    ok(o["ok"] and [e["size"] for e in o["extents"]] == [dd["summary"]["unit_size"]], "after level 13 the A7C II's allocator code hands out a write buffer of %s bytes" % format(dd["summary"]["unit_size"], ","))
    r, t = go(L13, mem, d_allowed)
    ok(not r.bad and r.n_writes == 0 and ("NOTHING TO DO" in t or "already" in t), "level 13 again: no write")
    r, t = go(L14, mem, d_allowed)
    ok(not r.bad and "DONE" in t and all(mem.read(a, 4) == o_ for a, o_, _ in sites) and changed_only(mem, before, {}), "level 14: the four words stock again, byte for byte")
    busy = dmm_server(opens=(0x43E, 0x43E, 0x4B8), frees=(0, 1))
    mem = dmm_mem(busy)
    r, t = go(L13, mem, d_allowed)
    ok(not r.bad and r.n_writes == 0 and "nothing is written" in t, "level 13 while a block is still divided as the old layout (one unit in use): refused, nothing written")
    mem = dmm_mem(dmm_server())
    a0 = sites[0][0]
    mem.write(a0 - 12 + 4, b"\x99")                          # a byte of the record that is not the site: not as in the file
    r, t = go(L13, mem, d_allowed)
    ok(not r.bad and r.n_writes == 0 and "nothing is written" in t, "level 13 with a unit record not as in the file: nothing is written")
    for lose in (1, 3):
        mem = dmm_mem(dmm_server())
        before = mem.snapshot()
        r, t = go(L13, mem, d_allowed, lose=lose)
        ok(not r.bad and "NOT DONE" in t and all(mem.read(a, 4) == o_ for a, o_, _ in sites), f"level 13 with line write {lose} lost: everything put back")

    # ---- levels 16 and 17: RAM words
    t_, srows, _ = emu_size_table.table("a7c2_201")
    srv = dmm_server()

    def words_mem():
        m = Mem(image)
        for k, (w, h, mid) in enumerate(srows):
            m.write(t_ + 8 * k, struct.pack("<2HI", w, h, mid))
        for lo, hi in ((parse_dmmcfg.RAM["a7c2_201"]["id_rows"] & ~0xFFF, parse_dmmcfg.RAM["a7c2_201"]["id_keys"] + 0x2000),):
            for a in range(lo, hi, 0x1000):
                m.write(a, bytes(srv.uc.mem_read(a, 0x1000)))
        m.page(0x1A533A0)                                     # the setting bytes: zero
        return m
    for name in ("outsz2560_c2_v1", "og57cp_c2_v3", "lcdfill_c2_v2"):
        d = design(name)
        w_allowed = {}
        for s in d["sites"]:
            a = int(s["ram_address"], 16)
            for k, (o, n) in enumerate(zip(bytes.fromhex(s["old"]), bytes.fromhex(s["new"]))):
                w_allowed[a + k] = (o, n)
        L16, L17 = lib(f"level16_{name}"), lib(f"level17_{name}")

        def wstate(mem):
            vals = [(mem.read(a, 1)[0], pr) for a, pr in w_allowed.items()]
            return "stock" if all(v == pr[0] for v, pr in vals) else "new" if all(v == pr[1] for v, pr in vals) else "mixed"
        mem = words_mem()
        before = mem.snapshot()
        r, t = go(L16, mem, w_allowed)
        ok(not r.bad and "DONE" in t and wstate(mem) == "new" and changed_only(mem, before, w_allowed),
           f"{name} level 16 on the model: {len(d['sites'])} word(s) new, nothing else changed ({r.n_writes} line writes)")
        r, t = go(L16, mem, w_allowed)
        ok(not r.bad and "NOTHING TO DO" in t and r.n_writes == 0, f"{name} level 16 again: nothing to do")
        r, t = go(L17, mem, w_allowed)
        ok(not r.bad and "DONE" in t and wstate(mem) == "stock" and changed_only(mem, before, {}), f"{name} level 17: stock again, byte for byte")
        for g in d["guards"]:
            mem = words_mem()
            ga = int(g["ram_address"], 16)
            mem.write(ga, bytes([mem.read(ga, 1)[0] ^ 1]))
            r, t = go(L16, mem, w_allowed)
            if r.bad or r.n_writes:
                ok(False, f"{name} level 16 with the guard at {ga:#x} wrong: nothing is written")
                break
        else:
            ok(True, f"{name} level 16 with any one of its {len(d['guards'])} guards wrong: nothing is written")
        mem = words_mem()
        before = mem.snapshot()
        r, t = go(L16, mem, w_allowed, lose=1)
        ok(not r.bad and "NOT DONE" in t and wstate(mem) == "stock", f"{name} level 16 with the first line write lost: everything put back")

    # ---- levels 19 and 20: encoder side (and the C2 routine, if built)
    av, lm = ex("nflasha3/av-cam.bin"), ex("nflasha15/lib/libmpr.so")
    av = bytes([0x5A]) * 0x1000 + av[0x1000:]
    phoff, phentsize, phnum = struct.unpack_from("<Q", lm, 0x20)[0], *struct.unpack_from("<HH", lm, 0x36)
    span = next(struct.unpack_from("<IIQQQQQQ", lm, phoff + k * phentsize) for k in range(phnum) if struct.unpack_from("<I", lm, phoff + k * phentsize)[0] == 1)[5]
    lspan = (span + 0xFFF) & ~0xFFF
    lm0 = lm[:span].ljust(lspan, b"\0")
    mfiles = dict(files)
    mfiles["/proc/self/mem"] = b""
    mfiles["/proc/self/maps"] = (b"00400000-00409000 r-xp 00000000 fc:8f 81970                              /usr/bin/im.elf\n"
                                 + b"%x-%x r-xp 00000000 fc:8f 55354                          /usr/lib/libmpr.so\n" % (LM_AT, LM_AT + lspan)
                                 + b"%x-%x ---p %08x fc:8f 55354                          /usr/lib/libmpr.so\n" % (LM_AT + lspan, LM_AT + lspan + 0x10000, lspan)
                                 + b"7f8b936000-7f8b991000 r-xp 00000000 fc:8f 55344                          /usr/lib/libsencore.so\n")
    for name in ("ogfile2560_hs_s_c2_v1", "ogfile57r300_c2_v1", "ogfile57_c2_v1"):
        fl = design(name)
        sts = {"av-cam.bin": [], "libmpr.so": []}
        for p in fl["patches"]:
            sts[p["binary"]].append((int(p["file_off"], 16), bytes.fromhex(p["old"]), bytes.fromhex(p["new"])))

        def applied(blob, which):
            b = bytearray(blob)
            for o, old, new in sts[which]:
                b[o:o + len(new)] = new
            return bytes(b)

        def mp(L, av_mem, lm_mem, **wrong):
            r = E.Run(lib(f"level{L}_{name}"), L, mfiles, dirs, {E.AVCAM_PHYS: av_mem}, own={LM_AT: lm_mem})
            r.kview_fail = False
            r.stale = {E.AVCAM_PHYS + o for o in wrong.get("stale", ())}
            r.pwrite_lost = {LM_AT + o for o in wrong.get("pwrite_lost", ())}
            r.go()
            return r, r.writes.get(RESULT, b"").decode("utf8", "replace"), bytes(r.phys[E.AVCAM_PHYS]), bytes(r.uc.mem_read(LM_AT, lspan))
        av1, lm1 = applied(av, "av-cam.bin"), applied(lm0, "libmpr.so")
        r, t, a, m = mp(19, av, lm0)
        ok(not r.bad and a == av1 and m == lm1 and "DONE" in t, f"{name} level 19: {len(sts['av-cam.bin'])} + {len(sts['libmpr.so'])} sites new, every other byte unchanged")
        r, t, a, m = mp(19, av1, lm1)
        ok(not r.bad and a == av1 and m == lm1 and not r.pwrites and not r.map_writes, f"{name} level 19 again: no write")
        r, t, a, m = mp(20, av1, lm1)
        ok(not r.bad and a == av and m == lm0 and "DONE" in t, f"{name} level 20: both stock again, byte for byte")
        bad_av = bytearray(av)
        bad_av[0x38389C + 0xDF50 + 0x20] ^= 1
        r, t, a, m = mp(19, bytes(bad_av), lm0)
        ok(not r.bad and a == bytes(bad_av) and m == lm0 and not r.pwrites and not r.map_writes, f"{name} level 19 with a guard of av-cam.bin wrong: nothing is written")
        r, t, a, m = mp(19, av, lm0, pwrite_lost=[sts["libmpr.so"][0][0]])
        ok(not r.bad and a == av and m == lm0 and "NOT DONE" in t, f"{name} level 19 with a libmpr.so write lost: everything put back")
        r, t, a, m = mp(19, av, lm0, stale=[sts["av-cam.bin"][3][0]])
        ok(not r.bad and a == av and m == lm0 and "NOT DONE" in t, f"{name} level 19 with the kernel's view not following a write: everything put back")

    if (HERE / "libJit_c2_level19_lcdcycle_c2_v1.so").is_file():
        lc = design("lcdcycle_c2_v1")
        af = ex("nflasha15/lib/appFw.so")
        phoff, phentsize, phnum = struct.unpack_from("<Q", af, 0x20)[0], *struct.unpack_from("<HH", af, 0x36)
        seg = next(struct.unpack_from("<IIQQQQQQ", af, phoff + k * phentsize) for k in range(phnum) if struct.unpack_from("<I", af, phoff + k * phentsize)[0] == 1)[5]
        AF_AT, AF_LEN = 0x7F5C1E3000, (seg + 0xFFF) & ~0xFFF
        af0 = af[:AF_LEN]
        lc_sites = [(int(q["file_off"], 16), bytes.fromhex(q["old"]), bytes.fromhex(q["new"]), q["kind"]) for q in lc["patches"]]
        hook_off = next(o for o, _, _, kind in lc_sites if kind == "hook")
        afb = bytearray(af0)
        for o, old_b, new_b, _ in lc_sites:
            assert af0[o:o + len(old_b)] == old_b
            afb[o:o + len(new_b)] = new_b
        af1 = bytes(afb)
        afiles = dict(mfiles)
        afiles["/proc/self/maps"] = (b"00400000-00409000 r-xp 00000000 fc:8f 81970                              /usr/bin/im.elf\n"
                                     + b"%x-%x r-xp 00000000 fc:8f 55369                          /usr/lib/appFw.so\n" % (AF_AT, AF_AT + AF_LEN)
                                     + b"%x-%x ---p %08x fc:8f 55369                          /usr/lib/appFw.so\n" % (AF_AT + AF_LEN, AF_AT + AF_LEN + 0xF000, AF_LEN)
                                     + b"7f8b936000-7f8b991000 r-xp 00000000 fc:8f 55344                          /usr/lib/libsencore.so\n")

        def cyc(L, af_mem, **wrong):
            r = E.Run(lib(f"level{L}_lcdcycle_c2_v1"), L, afiles, dirs, {E.AVCAM_PHYS: av}, own={AF_AT: af_mem})
            r.pwrite_lost = {AF_AT + o for o in wrong.get("pwrite_lost", ())}
            r.go()
            return r, r.writes.get(RESULT, b"").decode("utf8", "replace"), bytes(r.uc.mem_read(AF_AT, AF_LEN))
        r, t, m = cyc(19, af0)
        order = [o - AF_AT for o, _ in r.pwrites]
        ok(not r.bad and m == af1 and "DONE" in t, f"lcdcycle_c2_v1 level 19: the routine and the hook in appFw.so's mapping, every other byte unchanged ({len(order)} writes)")
        ok(len(order) == len(lc_sites) and order[-1] == hook_off and hook_off not in order[:-1], "lcdcycle_c2_v1 level 19: the branch into the cave is the last write")
        r, t, m = cyc(19, af1)
        ok(not r.bad and m == af1 and not r.pwrites, "lcdcycle_c2_v1 level 19 again: no write")
        r, t, m = cyc(20, af1)
        order = [o - AF_AT for o, _ in r.pwrites]
        ok(not r.bad and m == af0 and "DONE" in t and order and order[0] == hook_off, "lcdcycle_c2_v1 level 20: the file again, the hook put back first")
        for label, lost in (("a cave write", lc_sites[3][0]), ("the hook's write", hook_off)):
            r, t, m = cyc(19, af0, pwrite_lost=[lost])
            ok(not r.bad and m == af0 and "NOT DONE" in t, f"lcdcycle_c2_v1 level 19 with {label} lost: everything put back")
        bad = bytearray(af0)
        bad[hook_off + 8] ^= 1
        r, t, m = cyc(19, bytes(bad))
        ok(not r.bad and m == bytes(bad) and not r.pwrites, "lcdcycle_c2_v1 level 19 with the bytes after the hook not the file's: nothing is written")
    else:
        print("NOTE  lcdcycle_c2_v1 libraries not built: skipped")
    print("ALL PASS" if not fail else "FAILED: %d" % len(fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
