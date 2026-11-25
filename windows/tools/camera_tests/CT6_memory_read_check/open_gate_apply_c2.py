"""Apply an open-gate state to a Sony A7C II (ILCE-7CM2, firmware 2.01) for this power-on. (A7C II port of open_gate_apply.py, 2026-11-24)

UNTESTED ON AN A7C II. Every step is the A7 IV's proven step (open_gate_apply.py), rebuilt for the A7C II's firmware: the same tables
found at the A7C II's addresses, checked byte for byte, and each library run against a model A7C II (helper_c2/emu_c2_write.py).
Nothing of it has run on an A7C II yet.

Temporary. Everything goes into the camera's memory only; nothing is written to flash. A cold boot (battery out for 10 seconds)
removes it and the camera is stock again.

This script sends nothing to the camera itself. It runs pinned steps of c2_level_run.py (the A7 IV runner with the A7C II
libraries) in order and checks each result before going on.

  (default)   a 3840x2560 file in XAVC HS 4K or XAVC S 4K at 24p (PAL: XAVC S 4K at 25p), with the rear screen at 3:2 and the
              C2 key stepping the preview 3:2 -> 2:1 (1.33x) -> 2.4:1 (1.6x) -> 3:2 in movie mode, focus frames kept:
                step 1  level 13, dmmwb4688_c2_v1        the raw write buffer made taller
                step 2  level 5,  ogsq_c2_v1             the full-height picture, every sensor row (PAL: ogsq880_c2_v1)
                step 3  level 16, outsz2560_c2_v1        the output size 3840x2560
                step 4  level 19, ogfile2560_hs_s_c2_v1  the encoder side for both formats
                step 5  level 16, lcdfill_c2_v2          the rear screen at 3:2 with the focus frames
                step 6  level 19, lcdcycle_c2_v1         the C2 key steps the preview (if this kit has it)
  --real57    a real 5760x3840 file in XAVC HS 4K at 24p (PAL: XAVC S 4K at 25p), 300 Mbps on the 100M record settings:
                step 2  level 5,  og57n_c2_v1            5760x3840 out of the first scaler (PAL: og57n880_c2_v1)
                step 3  level 16, og57cp_c2_v3           four scaler units, the output buffer and its id, the output size
                step 4  level 19, ogfile57r300_c2_v1     the encoder side at 300 Mbps (--rate100: ogfile57_c2_v1, the menu's rate)
  --no-cycle leaves step 6 out; --no-lcd32 leaves steps 5 and 6 out.

NTSC or PAL: the script asks (or take --ntsc / --pal). On the A7C II one pair of image records serves both 24p (mode 0x8c0) and 25p
(mode 0x880); only step 2 differs.

    --dry-run    show what each step would send, on a simulated camera. Nothing is sent, no USB access.
    --selftest   run each step's own self-test on a simulated camera. Nothing is sent.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAP = HERE.parents[1]
RUNNER = HERE / "c2_level_run.py"
LISTER = MAP / "camera_tests" / "CT1_usb_first_contact" / "ct1_stage2_service.py"
PRIVATE = MAP.parent / "camera_private"
sys.path.insert(0, str(HERE))
import c2_level_run as P  # noqa: E402

FIRMWARE = P.FIRMWARE
MODEL_NAME = "A7C II"
KEY = "C2"


def step(level, design, what):
    facts = {5: "level56", 6: "level56", 13: "level1314", 14: "level1314", 16: "level1617", 17: "level1617", 19: "level1920", 20: "level1920"}[level]
    return (level, design, P.PINS[(level, design)], facts, what)


DMM = step(13, "dmmwb4688_c2_v1", "the raw write buffer made taller")
PIC = step(5, "ogsq_c2_v1", "the full-height picture, every sensor row")
PIC_57N = step(5, "og57n_c2_v1", "the full-height picture, 5760x3840 out of the first scaler")
OUT_2560 = step(16, "outsz2560_c2_v1", "the output size 3840x2560")
OUT_57 = step(16, "og57cp_c2_v3", "four scaler units, a buffer for the output picture (30.8 MB units) and its id in the size table, the output size 5760x3840")
ENC_HS_S = step(19, "ogfile2560_hs_s_c2_v1", "the encoder side for XAVC HS 4K and XAVC S 4K")
ENC_57_300 = step(19, "ogfile57r300_c2_v1", "the encoder side for 5760x3840 with the 100M record settings at 300 Mbps")
ENC_57 = step(19, "ogfile57_c2_v1", "the encoder side for 5760x3840 at the menu's own bit rate")
# 2026-11-25, the A7 IV's metadata fix (F987, F989): the MP4 headers (tkhd, stsd and Sony's PROF box) state the real size. Default; --no-meta
# gives the steps above. The 5.7K one is the A7 IV's 5.7K step 4 (ogfile57r300m1_v4): also I and P pictures only (no duplicate frames, F981/F986)
# and the HEVC edit list at 0 (first picture shown). Sony's XML in the file still says 3840x2160, 16:9, as on the A7 IV.
ENC_HS_S_META = step(19, "ogfile2560_hs_s_c2_v2", "the encoder side for XAVC HS 4K and XAVC S 4K, the MP4 headers saying 3840x2560")
ENC_57_META = step(19, "ogfile57r300m1_c2_v4", "the encoder side for 5760x3840 at 300 Mbps on the 100M settings, I and P pictures only, the MP4 headers saying 5760x3840")
LCD_32 = step(16, "lcdfill_c2_v2", "the rear screen at 3:2 with the focus frames")
CYCLE = step(19, "lcdcycle_c2_v1", "the C2 key steps the rear-screen preview 3:2 -> 2:1 -> 2.4:1 -> 3:2 in movie mode") if (19, "lcdcycle_c2_v1") in P.PINS else None
PAL = {"ogsq_c2_v1": step(5, "ogsq880_c2_v1", "the full-height picture, every sensor row, in drive mode 0x880 (camera set to PAL, 25p)"),
       "og57n_c2_v1": step(5, "og57n880_c2_v1", "the full-height picture, 5760x3840 out of the first scaler, in drive mode 0x880 (camera set to PAL, 25p)")}
RATE = {"NTSC": "24p", "PAL": "25p"}
BOTH = "XAVC HS 4K or XAVC S 4K"
SCREEN = [LCD_32] + ([CYCLE] if CYCLE else [])
STATES = {
    "default": {"steps": [DMM, PIC, OUT_2560, ENC_HS_S_META] + SCREEN, "formats": BOTH, "size": "3840x2560",
                "title": "Open gate for the Sony A7C II: a 3840x2560 recording of the whole 3:2 sensor area (UNTESTED on an A7C II)",
                "notes": ["The rear screen shows the whole picture at 3:2 while shooting; every 16:9 picture on the rear screen fills it too. The file is",
                          "3840x2560 and plays back in the camera at 3:2. Its MP4 headers say 3840x2560 (editors show 3:2 without a remux).",
                          "Sony's own XML block in the file still says 3840x2160, 16:9 (as on the A7 IV). --no-meta: headers say 3840x2160."]},
    "real57": {"steps": [DMM, PIC_57N, OUT_57, ENC_57_META] + SCREEN, "formats": "XAVC HS 4K", "size": "5760x3840",
               "title": "Open gate for the Sony A7C II: a real 5760x3840 recording of the whole 3:2 sensor area (UNTESTED on an A7C II)",
               "notes": ["The file is 5760x3840 with real detail (on the A7 IV: F906). Its MP4 headers say 5760x3840 (Sony's XML block still 3840x2160).",
                         "I and P pictures only (no duplicate frames) and the first picture shown, as the A7 IV's 5.7K step. It does not play in the camera.",
                         "--no-meta gives the earlier step (headers 3840x2160, B pictures).",
                         "The record settings marked 100M record at about 300 Mbps (peaks to 340). The menu and the remaining time still say 100M:",
                         "the card fills three times as fast as shown. Use a fast card (about 40 MB a second: SD V60 / V90).",
                         "--rate100 gives the encoder step with the menu's own bit rate.",
                         "IF THE CAMERA RESTARTS OR FREEZES: say so, with the camera_private folders."]},
}
COLD_BOOT = "Cold boot the camera (power off, battery out for 10 seconds). That returns it to stock."


def say(text=""):
    print(text, flush=True)


def for_family(steps, family):
    if family != "PAL":
        return steps
    return [PAL[s[1]] if s[0] == 5 else s for s in steps]


def ask_family():
    say("Is the camera set to NTSC or PAL? (Menu > Setup > Area/Date > NTSC/PAL Selector. NTSC offers 24p/30p/60p, PAL offers 25p/50p.)")
    answer = input("Type NTSC or PAL: ").strip().upper()
    if answer not in RATE:
        raise SystemExit("Neither NTSC nor PAL. Nothing was sent.")
    return answer


def library(level, design):
    return P.library_file(level, design)


def check_libraries(steps):
    """Every pinned library is on this PC and is the file whose hash is written above."""
    for level, design, sha, _, _ in steps:
        path = library(level, design)
        if not path.is_file():
            raise SystemExit("STOP: %s is missing. Nothing was sent." % path)
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != sha:
            raise SystemExit("STOP: %s is not the pinned file (SHA-256 %s). Nothing was sent." % (path.name, got))


def usb_state():
    """What the PC and libusb show now: 'drive' (Mass Storage, ready), 'service', or 'other'. Listing only; nothing is sent."""
    r = subprocess.run([sys.executable, "-I", "-B", str(LISTER), "--list"], capture_output=True, text=True, encoding="utf8", errors="replace", cwd=str(MAP))
    m = re.search(r"libusb sees: (\d+) Sony mass-storage device\(s\).*?, (\d+) Sony service device\(s\)", r.stdout)
    if not m:
        return "other"
    return "drive" if int(m.group(1)) == 1 and int(m.group(2)) == 0 else "service" if int(m.group(2)) else "other"


def wait_for_drive(first, timeout=600):
    asked = False
    end = time.time() + timeout
    while True:
        if usb_state() == "drive":
            return True
        if not asked:
            say()
            if first:
                say("The camera is not showing as a USB drive (Mass Storage).")
                say("  Turn it on, switch on STILLS, and connect the USB cable. USB Connection Mode: Mass Storage.")
            else:
                say(">>> Unplug the USB cable from the camera and plug it in again.")
                say(">>> Do NOT power the camera off: that may undo the steps already done.")
            say("Waiting for the camera (Ctrl+C to stop) ...")
            asked = True
        if time.time() > end:
            return False
        time.sleep(2)


def last_result_sha():
    if not PRIVATE.is_dir():
        return None
    for folder in sorted((p for p in PRIVATE.iterdir() if p.is_dir() and p.name.startswith("CT6_")), reverse=True):
        try:
            man = json.loads((folder / "manifest.json").read_text(encoding="utf8"))
            return man["results"]["ct6"]["result"]["sha256"]
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None


def outcome(folder, facts_key):
    try:
        man = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf8"))
        ct6 = man["results"]["ct6"]
    except (OSError, KeyError, TypeError, ValueError) as e:
        return False, "the session's record could not be read (%s)" % e, None
    res = ct6.get("result") or {}
    f = res.get(facts_key) or {}
    sha = res.get("sha256")
    if man.get("status") != "completed":
        return False, "the run did not complete (%s)" % (man.get("error") or man.get("status")), sha
    if not ct6.get("result_found") or not res.get("complete"):
        return False, "the camera gave no complete result", sha
    if f.get("undo_incomplete"):
        return False, "A STEP FAILED AND ITS UNDO DID NOT COMPLETE", sha
    if f.get("done"):
        return True, "done", sha
    if f.get("nothing_to_do"):
        return True, "already in place, nothing to do", sha
    why = "; ".join((f.get("refused") or []) + (f.get("problems") or [])) or "see the lines above"
    return False, ("a step failed and was put back: " if f.get("undone") else "not done: ") + why, sha


def run_step(n, total, level, design, sha, facts_key, what, firmware, clear):
    say()
    say("=" * 78)
    say("Step %d of %d: level %d, %s: %s" % (n, total, level, design, what))
    say("=" * 78)
    cmd = [sys.executable, "-I", "-B", str(RUNNER), "--go", "--level", str(level), "--design", design, "--firmware-shown", firmware, "--library-sha256", sha]
    if clear:
        cmd += ["--clear-result", clear]
    folder = None
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf8", errors="replace", cwd=str(MAP))
    for line in p.stdout:
        line = line.rstrip("\n")
        say("  " + line)
        m = re.match(r"Session folder: (.+)$", line)
        if m:
            folder = m.group(1).strip()
    p.wait()
    if folder is None:
        return False, "the run ended before a session was saved (exit code %d)" % p.returncode, None
    ok, text, got = outcome(folder, facts_key)
    if p.returncode != 0 and ok:
        ok, text = False, "the run ended with exit code %d" % p.returncode
    return ok, text, got


def offline(mode, steps):
    check_libraries(steps)
    say("The %d pinned libraries are present and have the pinned hashes." % len(steps))
    for n, (level, design, sha, _, what) in enumerate(steps, 1):
        say()
        say("--- step %d: level %d, %s (%s): %s" % (n, level, design, what, mode))
        r = subprocess.run([sys.executable, "-I", "-B", str(RUNNER), mode, "--level", str(level), "--design", design], capture_output=True, text=True,
                           encoding="utf8", errors="replace", cwd=str(MAP))
        lines = r.stdout.strip().splitlines()
        for line in lines[-6:] if mode == "--selftest" else lines[:3] + ["  ..."] + lines[-2:]:
            say("  " + line[:200])
        if r.returncode != 0:
            raise SystemExit("STOP: step %d's %s failed:\n%s" % (n, mode, r.stderr[-800:]))
    say()
    say("All %d steps passed their %s. Nothing was sent to any device." % (len(steps), mode.lstrip("-")))


def putback_steps(steps):
    """The reverse of an apply: each step's other level (5->6, 13->14, 16->17, 19->20), in reverse order."""
    return [(lv + 1, d, P.PINS[(lv + 1, d)], f, "put back: " + w) for lv, d, _, f, w in reversed(steps)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real57", action="store_true", help="the real 5760x3840 state: first scaler 5760x3840, XAVC HS 4K at 24p, 300 Mbps on the 100M settings")
    ap.add_argument("--rate100", action="store_true", help="5.7K: the encoder step at the menu's own bit rate instead of 300 Mbps on the 100M settings")
    ap.add_argument("--no-meta", action="store_true", help="the encoder step without the MP4 header size: the headers say 3840x2160 as the camera's own (4K: ogfile2560_hs_s_c2_v1; 5.7K: ogfile57r300_c2_v1)")
    ap.add_argument("--no-cycle", action="store_true", help="leave out the C2 preview cycle: C2 keeps its own function in movie mode")
    ap.add_argument("--no-lcd32", action="store_true", help="leave out the rear-screen steps: live view squeezed in a 16:9 letterbox")
    ap.add_argument("--putback", action="store_true", help="put the state back step by step instead of a cold boot (the screen preview must be at 3:2)")
    ap.add_argument("--ntsc", action="store_true", help="the camera's NTSC/PAL Selector is on NTSC (24p); without --ntsc or --pal the script asks")
    ap.add_argument("--pal", action="store_true", help="the camera's NTSC/PAL Selector is on PAL (25p, XAVC S 4K): step 2 is the design for drive mode 0x880")
    ap.add_argument("--dry-run", action="store_true", help="show what each step would send, on a simulated camera; nothing is sent")
    ap.add_argument("--selftest", action="store_true", help="run each step's self-test on a simulated camera; nothing is sent")
    args = ap.parse_args()
    st = STATES["real57" if args.real57 else "default"]
    steps, formats, size = st["steps"], st["formats"], st["size"]
    if args.no_lcd32:
        steps = [x for x in steps if x not in (LCD_32, CYCLE)]
    if args.no_cycle:
        steps = [x for x in steps if x != CYCLE]
    if args.no_meta:
        steps = [{ENC_HS_S_META: ENC_HS_S, ENC_57_META: ENC_57_300}.get(x, x) for x in steps]
    if args.rate100:
        steps = [ENC_57 if x in (ENC_57_300, ENC_57_META) else x for x in steps]
    if args.ntsc and args.pal:
        raise SystemExit("--ntsc and --pal do not go together. Nothing was sent.")
    if args.dry_run or args.selftest:
        steps = for_family(steps, "PAL" if args.pal else "NTSC")
        return offline("--dry-run" if args.dry_run else "--selftest", putback_steps(steps) if args.putback else steps)

    say(st["title"] if not args.putback else "Put back the open-gate state of the Sony A7C II, step by step")
    say("-" * 78)
    family = "PAL" if args.pal else "NTSC" if args.ntsc else ask_family()
    steps, rate = for_family(steps, family), RATE[family]
    if args.putback:
        steps = putback_steps(steps)
    if family == "PAL":
        formats = "XAVC S 4K"
    say()
    say("THIS HAS NOT BEEN RUN ON AN A7C II YET. It is the A7 IV's working state rebuilt for the A7C II's firmware 2.01. Every step")
    say("checks that the camera's memory holds exactly the A7C II 2.01 stock bytes before it writes, and writes nothing otherwise.")
    say()
    say("This writes TEMPORARY settings into the camera's memory, in %d steps. Nothing is" % len(steps))
    say("written to flash. A cold boot (battery out for 10 seconds) removes all of it.")
    say()
    if not args.putback:
        say("Before you start:")
        say("  1. The camera is set to %s: in the menu, set File Format to %s and the frame rate to %s." % (family, formats, rate))
        say("     Full frame (APS-C/Super 35 off), 4K, not S&Q.")
        say("  2. Cold boot the camera: power off, battery out for 10 seconds, battery in.")
        say("  3. Turn it on with the Still/Movie/S&Q switch on STILLS (not movie).")
        if CYCLE in steps:
            say("     For the C2 preview cycle nothing needs setting: in movie mode the C2 key itself is taken over (whatever it is")
            say("     assigned to); in stills C2 works as before.")
        say("  4. Connect the USB cable. USB Connection Mode: Mass Storage.")
        say()
        say("While it is applied:")
        say("  - Only %s at %s records properly. Do NOT record in another 4K format or" % (formats, rate))
        say("    frame rate, and stay in full-frame 4K: do not switch to HD, S&Q or to APS-C/Super 35")
        say("    (a mode that is not set up can stop the camera responding; it then needs a cold boot).")
        for line in st["notes"]:
            say("  - " + line if line[:1].isupper() else "    " + line)
        say("  - Do not change the NTSC/PAL Selector while it is applied.")
        say()
    else:
        say("Before you go on: the camera must still hold the state this script applied in this power-on. Press C2 in movie mode until")
        say("the preview is back at 3:2, then switch to stills and connect USB. (A cold boot does the same in one go.)")
    check_libraries(steps)
    shown = input("Type the firmware version the camera shows (Menu > Setup > Version): ").strip()
    if shown != FIRMWARE:
        raise SystemExit("This is only for A7C II firmware %s. Nothing was sent." % FIRMWARE)
    if input("Type YES to write these temporary settings to the camera: ").strip() != "YES":
        raise SystemExit("Not confirmed. Nothing was sent.")

    clear = last_result_sha()
    for n, (level, design, sha, facts_key, what) in enumerate(steps, 1):
        try:
            ready = wait_for_drive(first=(n == 1))
        except KeyboardInterrupt:
            ready = False
        if not ready:
            say()
            say("STOPPED before step %d: the camera did not show as a USB drive." % n)
            if n > 1:
                say("Steps 1 to %d are in the camera's memory, the rest are not. Do not record like this." % (n - 1))
                say(COLD_BOOT)
            raise SystemExit(1)
        ok, text, got = run_step(n, len(steps), level, design, sha, facts_key, what, shown, clear)
        clear = got or clear
        if not ok:
            say()
            say("STOPPED at step %d: %s." % (n, text))
            say("The settings are NOT complete. Do not record like this.")
            if n == 1 and "nothing is written" in text and not args.putback:
                say("Step 1 only works straight after a cold boot with the camera on stills, before movie mode has been used.")
            if level == 5 and "holds neither" in text:
                say("Step 2 was run for a camera set to %s and the camera's stored sensor record is not the expected one. Check Menu > Setup >" % family)
                say("Area/Date > NTSC/PAL Selector, and that the camera is an A7C II on firmware %s. Nothing of step 2 was written." % FIRMWARE)
            say(COLD_BOOT + " Then start again from the list at the top. Please send the newest folders in camera_private.")
            raise SystemExit(1)
        say()
        say("Step %d: %s." % (n, text))

    say()
    say("=" * 78)
    say("ALL %d STEPS ARE IN PLACE." % len(steps) if not args.putback else "ALL %d STEPS ARE PUT BACK: the camera is stock again." % len(steps))
    say("=" * 78)
    if args.putback:
        return
    say("Now:")
    say("  1. Unplug the USB cable. Do not power the camera off.")
    if LCD_32 in steps:
        say("  2. Switch to movie. The rear screen should show the full-height picture at 3:2, filling the screen, with the focus frames.")
    else:
        say("  2. Switch to movie. The rear screen shows the full-height picture, squeezed.")
    if CYCLE in steps:
        say("     Press C2 to step the rear screen 3:2 -> 2:1 (1.33x) -> 2.4:1 (1.6x) -> 3:2 (say what each press shows).")
    say("  3. Record a few seconds in %s at %s, stop, and play the clip back. The file should be %s." % (formats, rate, size))
    say()
    say("Please report (this is the first A7C II run): does it record; the clip's size and frame rate (MediaInfo); does the picture")
    say("show the whole sensor height (compare with a photo); the rear screen in movie mode; what C2 does; anything odd.")
    say("  - Power-off, a restart or a cold boot removes the settings; run this again to get them back.")
    say("  - If the camera misbehaves and will not respond: " + COLD_BOOT)


if __name__ == "__main__":
    main()
