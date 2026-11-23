r"""Read-only: where the appFw.so pieces of lcdcycle_v6 (A7 IV 6.02) are in another build (A7C II 2.01 by default).

  work\venv\Scripts\python.exe -I -B build/lcdcycle_c2/find_pieces.py [build]

Prints, for the target build (and, as a check of the method, for the A7S III 5.01, whose answers are known from
build/build_lcdcycle_s3.py):
  hook     presenter_controller_manager::process_input_event (pretty_function name), its first instruction
  dlsym    the .plt stub whose GOT slot the JUMP_SLOT relocation of `dlsym` names (parsed from the ELF, not from the index)
  mode     the system-mode accessor (A7 IV 0x28ecdf8): votes by call position in the same-named callers of the A7 IV's accessor
  layout   the vdf_set_yuv_layout wrapper: the callers of infra_wrapper_bb::detail::vdf_set_yuv_layout whose code is the A7 IV
           wrapper's with only the branch targets masked
  cave     the end of the first PT_LOAD segment, the page end, and whether the bytes between are zero
"""
import difflib
import io
import struct
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import capstone  # noqa: E402
from elftools.elf.elffile import ELFFile  # noqa: E402

import common  # noqa: E402
import locate_file  # noqa: E402

A7IV = {"hook": 0x1F06594, "dlsym": 0x17166A0, "mode": 0x28ECDF8, "layout": 0x35994DC}
PIE = "presenter_controller_manager::process_input_event(const ds_event&, bool, bool)"
VDF = "infra_wrapper_bb::detail::vdf_set_yuv_layout(const infra_wrapper_bb::vdf_set_yuv_layout_in_t&, infra_wrapper_bb::vdf_set_yuv_layout_out_t&)"
MD = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)


class Build:
    def __init__(self, build):
        self.build = build
        self.img = locate_file.locate(build, "appFw.so").read_bytes()
        self.db = common.connect(common.code_db_path(build), readonly=True)
        self.aid = self.db.execute("SELECT id FROM artifact_ref WHERE name='appFw.so' AND path NOT LIKE '%unpacked%' AND path NOT LIKE '0400%'").fetchone()[0]
        self.size = {r[0]: r[1] for r in self.db.execute("SELECT addr, size FROM function WHERE artifact=?", (self.aid,))}
        self.names, self.pretty, self.by_name = {}, {}, {}
        for a, n, s in self.db.execute("SELECT addr, name, source FROM label WHERE artifact=? AND target='func' AND source IN ('pretty_function', 'plt', 'symbol')", (self.aid,)):
            self.names.setdefault(a, n)
            if s == "pretty_function":
                self.pretty[a] = n
                self.by_name.setdefault(n, set()).add(a)

    def func_named(self, name):
        s = self.by_name.get(name, set())
        return next(iter(s)) if len(s) == 1 else None

    def calls(self, f):
        out = []
        for i in MD.disasm(self.img[f:f + (self.size.get(f) or 0)], f):
            if i.mnemonic == "bl":
                out.append((i.address, int(i.op_str.lstrip("#"), 16)))
        return out

    def callers(self, target):
        return sorted({r[0] for r in self.db.execute("SELECT src_func FROM xref WHERE artifact=? AND dst=? AND kind='c'", (self.aid, target)) if r[0]})

    def plt_of(self, sym):
        e = ELFFile(io.BytesIO(self.img))
        dynsym, rela, plt = e.get_section_by_name(".dynsym"), e.get_section_by_name(".rela.plt"), e.get_section_by_name(".plt")
        got = next(r["r_offset"] for r in rela.iter_relocations() if dynsym.get_symbol(r["r_info_sym"]).name == sym)
        hits = []
        for k in range(plt["sh_addr"] + 0x20, plt["sh_addr"] + plt["sh_size"], 16):       # 32-byte PLT0, then 16-byte entries
            ins = list(MD.disasm(self.img[k:k + 16], k))
            if len(ins) == 4 and ins[0].mnemonic == "adrp" and ins[1].mnemonic == "ldr" and ins[3].op_str == "x17":
                page = int(ins[0].op_str.split("#")[1], 16)
                lo = int(ins[1].op_str.split("#")[1].rstrip("]"), 16) if "#" in ins[1].op_str else 0
                if page + lo == got:
                    hits.append(k)
        return got, hits

    def load0(self):
        e = ELFFile(io.BytesIO(self.img))
        seg = next(s for s in e.iter_segments() if s["p_type"] == "PT_LOAD")
        assert seg["p_offset"] == 0 and seg["p_vaddr"] == 0
        end = seg["p_filesz"]
        return end, (end + 0xFFF) & ~0xFFF


def token(b, t):
    return b.names.get(t, "anon")


def vote_mode(ref, b):
    """A7 IV callers of the accessor that have a unique pretty_function name in both builds: align their call sequences by callee
    name and count which callee of the other build sits where the A7 IV calls the accessor."""
    votes, used = Counter(), 0
    for f in ref.callers(A7IV["mode"]):
        n = ref.pretty.get(f)
        if not n or ref.func_named(n) != f:
            continue
        g = b.func_named(n)
        if g is None:
            continue
        ca, cb = ref.calls(f), b.calls(g)
        ta = [token(ref, t) if t != A7IV["mode"] else "anon" for _, t in ca]
        tb = [token(b, t) for _, t in cb]
        sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
        used += 1
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op in ("equal", "replace") and i2 - i1 == j2 - j1:
                for k in range(i2 - i1):
                    if ca[i1 + k][1] == A7IV["mode"]:
                        votes[cb[j1 + k][1]] += 1
    return votes, used


def masked(img, a, n):
    out = []
    for i in MD.disasm(img[a:a + 4 * n], a):
        out.append(i.mnemonic + " " + (i.op_str if i.mnemonic not in ("bl", "b") else "T"))
    return out


def report(build):
    ref, b = Build("a7iv_602"), Build(build)
    print("== %s" % build)
    hook = b.func_named(PIE)
    first = next(MD.disasm(b.img[hook:hook + 4], hook))
    print("hook    process_input_event %#x, first instruction `%s %s` (A7 IV %#x)" % (hook, first.mnemonic, first.op_str, A7IV["hook"]))
    got, plts = b.plt_of("dlsym")
    print("dlsym   JUMP_SLOT GOT %#x -> .plt %s" % (got, [hex(x) for x in plts]))
    votes, used = vote_mode(ref, b)
    print("mode    %d same-named callers, votes %s" % (used, [(hex(a), n) for a, n in votes.most_common(4)]))
    best = votes.most_common(1)[0][0]
    for i in MD.disasm(b.img[best:best + 20], best):
        print("          %#x: %s %s" % (i.address, i.mnemonic, i.op_str))
    vdf = b.func_named(VDF)
    want = masked(ref.img, A7IV["layout"], 16)
    wr = [f for f in b.callers(vdf) if masked(b.img, f, 16) == want]
    print("layout  detail::vdf_set_yuv_layout %#x; wrappers with the A7 IV wrapper's code (branch targets masked): %s" % (vdf, [hex(x) for x in wr]))
    end, page = b.load0()
    cave = (end + 15) & ~15
    print("cave    first segment ends %#x, page end %#x, zero between: %s; 16-aligned cave %#x..%#x = %d bytes"
          % (end, page, not any(b.img[end:page]), cave, page, page - cave))


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else "a7c2_201")
    if len(sys.argv) <= 1:
        report("a7s3_501")
