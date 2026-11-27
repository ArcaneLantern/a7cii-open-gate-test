"""Sony A7C II (ILCE-7CM2) firmware 2.01: run ONE pinned write library of the A7C II open-gate kit (A7C II port, 2026-11-24).

  python -I -B c2_level_run.py --go --level <L> --design <name> --firmware-shown 2.01 --library-sha256 <the pinned hash>
  python -I -B c2_level_run.py --dry-run | --selftest --level <L> --design <name>

It is the A7 IV runner (ct6_level1_run.py: the fixed packet list and guard, the simulated camera, the session steps, the result
reader) with the A7C II's libraries and pins, its firmware version, and the simulated camera's helper answering with a canned result
(the libraries themselves were run against a model A7C II by helper_c2/emu_c2_write.py). open_gate_apply_c2.py runs it once per step.

The libraries are the A7 IV helper's write levels (helper/readcheck.c) built with the A7C II's tables (helper_c2/build_c2_write.sh).
Each refuses to write unless every byte it will change, and its guards, hold the A7C II 2.01 stock values: on any other camera or
firmware it writes nothing. DeviceInfo.xml is byte-identical on the A7 IV 6.02 and the A7C II 2.01, so the known-answer read does
not tell them apart; the libraries' own checks do.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "CT1_usb_first_contact"))
sys.path.insert(0, str(HERE))
import ct1_common as c  # noqa: E402
import ct6_level1_run as R  # noqa: E402

FIRMWARE, MODEL = "2.01", "ILCE-7CM2"
# (level, design) -> SHA-256 of libJit_c2_level<level>_<design>.so in helper_c2 (build_c2_write.sh prints them)
PINS = {
    (13, "dmmwb4688_c2_v1"): "de6403f9c3fcb81bb751c74866433e154513bd89269066ad123acd37efb8b00e",
    (14, "dmmwb4688_c2_v1"): "92e8557dcd4caa3a4e868639b4824e6bed29b71c12cec5955c6c492026c4dec6",
    (5, "ogsq_c2_v1"): "ad5a6eebdd5c5486dc5d16a5a53b4425c44d22374153daacd87e7fad81b94153",
    (6, "ogsq_c2_v1"): "4b016996f460da15ab9cd1e55c09dd709792bbc3c5f01e4c1a86bf6ebcdd5ff0",
    (5, "og57n_c2_v1"): "3ef3e13dd50d23bfc30a374bfc8f42aa7bc4884f2f7b6056f9a5345a78befb65",
    (6, "og57n_c2_v1"): "47dd4be4f1439eca28154456341dd5ab7bf8b9435322aa097ce97b168988dfec",
    (5, "ogsq880_c2_v1"): "b924fe09e74c69176f3786678976e2073f5bf3a195c482dc72c16312450d0a73",
    (6, "ogsq880_c2_v1"): "8e552d4da3b0440b72d7a55bce6462074ce261f345190ce8dbdd8ab784195ff1",
    (5, "og57n880_c2_v1"): "b5af7af4aaf9b45e151d18ca09131fd31f15ad8b3e5db7b3816ad4f8c49bf082",
    (6, "og57n880_c2_v1"): "a6e1f2bc182c00a7c7d8142da7175434596c98fabb4c476c512bc57a5ebc6904",
    (16, "outsz2560_c2_v1"): "a8b391809475f3742c05935970328677bd210e8ae69edc82f42355f032e83dc3",
    (17, "outsz2560_c2_v1"): "3925b3cd6afcd7138117bdf098c1f832f2d2bc077ca912a1ba27a7646dd32be2",
    (16, "og57cp_c2_v3"): "3b22b7cff09c1420099a79cfaf16ed8d8cdb65b6860401ab6e786c0ada4dedf2",
    (17, "og57cp_c2_v3"): "209e046c47f9e0add8c2f9463fd6fc3e5f18a7606181a6e9684ce72a40772d05",
    (16, "lcdfill_c2_v2"): "4f0903afb480622e558ca163f0a07ac31d922e35b91cc30c1131cf8c81a90dfe",
    (17, "lcdfill_c2_v2"): "1ff0f53b1505212b0704bc1db6af3866c583da7488cfbb943e3e08700be9abb9",
    (19, "ogfile2560_hs_s_c2_v1"): "b00a8a6ac52d44f513568f1ffa29df5330965ab46c29dab058110298cf5c233a",
    (20, "ogfile2560_hs_s_c2_v1"): "8c11be5a3da84069ee9acbee636c7104934a7ddcde74cfb6b00919b989aeffdb",
    (19, "ogfile57r300_c2_v1"): "736407c4b08677eee3315fa06e06480430f25811432d9837c303fdbb2545e21b",
    (20, "ogfile57r300_c2_v1"): "e84ddbb014041f301c45c9af6ef3e83fbeed5ca8212eacf8c7fedcfb49e66a58",
    (19, "ogfile57_c2_v1"): "d6f67cfe7d78ec5f5ccca2591ce2f4de263096a8dba644fd8c3d01b80198b12f",
    (20, "ogfile57_c2_v1"): "2639d39605249ebde472c1866c72cf62ef950220e41890027c8f9c67f131e98b",
    # 2026-11-25: the A7 IV's metadata fix (F987/F989): the MP4 headers (tkhd, stsd, Sony's PROF) state 3840x2560 / 5760x3840. The 5.7K step is the
    # A7 IV's ogfile57r300m1_v4 (also I and P pictures only, F981/F986, and the HEVC edit list at 0). build_ogfile.py / build_og57.py --meta --build a7c2_201.
    (19, "ogfile2560_hs_s_c2_v2"): "d2187e06bd17ac3befc8b9a26802e84c46f388d4de2a7c5aa54da11d58893839",
    (20, "ogfile2560_hs_s_c2_v2"): "0e1a536e28344b4eecd5792591b43a068f52f54889f2122004dd12ed3e2feb2d",
    (19, "ogfile57r300m1_c2_v4"): "352829b1dd5eb85c3b7b67a37b496b4cff8c5935d5f846e39c4fd2f32bc49b44",
    (20, "ogfile57r300m1_c2_v4"): "1cb2149c38aea617514bf299534724d265811d78bdf96fc844dcfe089568a37f",
    # the C2 preview routine in appFw.so (build/build_lcdcycle_c2.py; its cave is in .rela.dyn: modding/A7C2_PORT_LCDCYCLE.md)
    (19, "lcdcycle_c2_v1"): "15f0bcacb624779a3009c83f67ff732b87fcb4da5d0c39359db9d9cd35c3b1a3",
    (20, "lcdcycle_c2_v1"): "c5006d19529ad9bc30c7216dd4e5f02695bc8f01a509a0ec7caf21f434f5a212",
}
C2 = HERE / "helper_c2"
# 2026-11-27, the FX3 kit's fix (2026-11-26): a session cut off after its write (camera restart, cable, Ctrl+C) leaves its library on the
# service partition, which is flash: it survives a cold boot, and every later step refused to go on. Every library of this kit is one a run
# may delete (R.KNOWN_LIBS) before it goes on; --remove-library names one more file by its SHA-256 (the stopped run prints it).
EXTRA_LIBS = {}
REMOVE_HINT = "run the launcher again with --remove-library and that SHA-256"


def library_file(level, design):
    return C2 / ("libJit_c2_level%d_%s.so" % (level, design))


def known_libs():
    out = {sha: "this kit's level %d library (%s)" % (level, design) for (level, design), sha in PINS.items()}
    out.update(EXTRA_LIBS)
    return out


def install():
    """Point the A7 IV runner at the A7C II: its designs and pins, its firmware version, a canned helper on the simulated camera."""
    global _selftest
    R.DESIGN_LIBS = {}                               # only the A7C II's: an A7 IV design name is refused by --design
    for (level, design), sha in PINS.items():
        R.DESIGN_LIBS.setdefault(design, {})[level] = ("../helper_c2/" + library_file(level, design).name, sha)
    c.EXPECTED_FIRMWARE = FIRMWARE
    R.run_helper = lambda data: (R.canned_result(), "canned")
    R.KNOWN_LIBS = known_libs()
    R.REMOVE_HINT = REMOVE_HINT
    if R.selftest is not selftest:
        _selftest, R.selftest = R.selftest, selftest


_selftest = None


def selftest(lib):
    """The A7 IV runner's self-test, then the left-library cases on the simulated camera (a live run runs it first, as R.main does).
    The runner's own cases expect a library already on the camera to stop the run, as it does with no list: they run with the list empty."""
    saved = dict(R.KNOWN_LIBS)
    R.KNOWN_LIBS.clear()
    try:
        done = _selftest(lib)
    finally:
        R.KNOWN_LIBS.update(saved)

    def check(cond, what):
        if not cond:
            raise AssertionError("self-test failed: " + what)
        done.append(what)

    W, D = "SET_FILE2", "FILE_DELETE2"
    lp = R.LIB_PATH.decode()
    other = next(library_file(lv, d).read_bytes() for (lv, d), sha in sorted(PINS.items()) if sha != R.LIB_SHA256)
    for left, what in ((other, "another step's library"), (lib, "this step's own library")):
        s, sim, stopped = R.simulate(lib, R.body_run, files={lp: left})
        names, lr = R.sent(s), s.results["ct6"].get("leftover_removed") or {}
        check(not stopped and not sim.unexpected and names.count(D) == 2 and names.count(W) == 1 and names.index(D) < names.index(W) and lp not in sim.fs
              and sim.loads == 1 and lr.get("gone") and lr.get("sha256") == c.sha256(left) and s.results["ct6"]["library_on_camera_after"] is False,
              "%s left on the camera: deleted first, then the normal run (one write, loaded once, gone afterwards)" % what)
    s, sim, stopped = R.simulate(lib, R.body_run, files={lp: other}, fault="sticky")
    check(stopped and W not in R.sent(s) and R.sent(s).count(D) == 1 and sim.fs.get(lp) == other, "a left library the camera will not delete: stops, nothing written")
    s, sim, stopped = R.simulate(lib, R.body_run, files={lp: b"someone else's"})
    check(stopped and W not in R.sent(s) and D not in R.sent(s) and c.sha256(b"someone else's") in stopped and "--remove-library" in stopped,
          "a file that is none of this kit's: stops, nothing deleted, its SHA-256 and --remove-library are shown")
    named = b"a file named with --remove-library"
    saved = dict(R.KNOWN_LIBS)
    try:
        R.KNOWN_LIBS[c.sha256(named)] = "the file named with --remove-library"
        s, sim, stopped = R.simulate(lib, R.body_run, files={lp: named})
        check(not stopped and R.sent(s).count(D) == 2 and R.sent(s).count(W) == 1 and lp not in sim.fs, "--remove-library with the file's SHA-256: deleted, then the normal run")
    finally:
        R.KNOWN_LIBS.clear()
        R.KNOWN_LIBS.update(saved)
    s, sim, stopped = R.simulate(lib, R.body_remove_only, files={lp: other})
    check(not stopped and R.sent(s).count(D) == 1 and W not in R.sent(s) and lp not in sim.fs, "--remove-only: deletes another step's library of this kit too, writes nothing")
    check(all(sha in R.KNOWN_LIBS for sha in PINS.values()), "every library of this kit (%d) is one a run may delete" % len(PINS))
    return done


def take_remove_library(argv):
    """--remove-library SHA256 out of argv (the A7 IV runner has no such option): that file becomes one a run may delete."""
    import re
    if "--remove-library" not in argv:
        return argv
    i = argv.index("--remove-library")
    value = argv[i + 1] if i + 1 < len(argv) else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise SystemExit("--remove-library takes the 64 hexadecimal digits of the file's SHA-256 (the stopped run prints them). Nothing was sent.")
    EXTRA_LIBS[value.lower()] = "the file named with --remove-library"
    return argv[:i] + argv[i + 2:]


if __name__ == "__main__":
    sys.argv = take_remove_library(sys.argv)
    install()
    if "--design" not in sys.argv or sys.argv[sys.argv.index("--design") + 1:][:1] == ["og4k_data_v1"]:
        raise SystemExit("--design must name one of the A7C II designs: %s. Nothing was sent." % ", ".join(sorted({d for _, d in PINS})))
    R.main()
