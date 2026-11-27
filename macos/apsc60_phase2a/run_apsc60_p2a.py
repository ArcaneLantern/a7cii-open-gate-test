#!/usr/bin/env python3
"""Guarded single-session runner for Sony A7C II (fw 2.01) Phase 2A APS-C 3:2 4K 60p test (RAM-only).

Runs either the SHA-256-pinned `apply` or `putback` library (`apsc60_p2a_v3`, all 4 `col 14` modes `0x0821`+`0x08c1`+`0x0b21`+`0x0bc1`)
inside ONE authenticated Service Mode (054C:0336) session:
  - Verifies exact SHA-256 of the pinned .so and vendored PMCA-RE before touching USB
  - Checks DeviceInfo.xml known-answer and pre-conditions (no libJit.so, no stale result, no /tmp/jbusy)
  - Strict P2AGuard allowing ONLY the pinned .so on /tmp/jiritsu/libJit.so (once) and verified-SHA result deletes
  - ALWAYS deletes /tmp/jiritsu/libJit.so and confirms ST_NO_FILE in finally
  - Fetches & parses /tmp_wbshd/readcheck_result.txt, clears it by verified SHA-256, and saves the full
    session + p2a_decoded.json into windows/camera_private/ with ownership restored
  - ALWAYS sends STOP_SENSER once in finally

  python -I -B macos/apsc60_phase2a/run_apsc60_p2a.py --dry-run [--putback]
  python -I -B macos/apsc60_phase2a/run_apsc60_p2a.py --selftest
  python -I -B macos/apsc60_phase2a/run_apsc60_p2a.py --firmware 2.01 --yes [--apply | --putback | --remove-only]

Zero external dependencies beyond Python stdlib and macos/.venv (pyusb, pycryptodomex, libusb-package).
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import posixpath
import re
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MACOS_DIR = HERE.parent
REPO_ROOT = MACOS_DIR.parent
TOOLS_ROOT = REPO_ROOT / "windows" / "tools"
CT1_DIR = TOOLS_ROOT / "camera_tests" / "CT1_usb_first_contact"
CT6_DIR = TOOLS_ROOT / "camera_tests" / "CT6_memory_read_check"
PRIVATE_DIR = REPO_ROOT / "windows" / "camera_private"

for _p in (str(CT1_DIR), str(CT6_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ct1_common as c  # noqa: E402
import ct1_compare  # noqa: E402
import ct1_stage2_service as service  # noqa: E402
import ct6_level1_run as R  # noqa: E402
import c2_level_run as P  # noqa: E402
import open_gate_apply_c2 as OG  # noqa: E402

P.install()

FIRMWARE = P.FIRMWARE
MODEL = P.MODEL
COLD_BOOT = OG.COLD_BOOT

P2A_PINS = {
    "apply": (
        HERE / "libJit_c2_apsc60_p2a_apply.so",
        "a7cb928401b748aee8e070c7d1a271cec8948398aaeb4dc0b25b5c6030d74109",
    ),
    "putback": (
        HERE / "libJit_c2_apsc60_p2a_putback.so",
        "ab6511ea26a6ae496c0d5b723a491f0030857635e8684645407e7af3b5b3cc07",
    ),
}
P2A_LEGACY_SHAS = {
    "6853d7da4ff3d7a55c22602a211c212fc97d5338bbc5d982a75168b143d84871",  # v1 apply
    "bc1eee30af52adafb9754f1d32362e02ad501ec2c027f178254d466532d08c73",  # v1 putback
    "a96f0e9bb4629d06a0f22e6f230ad49108bdea5e55fe623c3bc2315e94b4bbd4",  # v2 apply
    "a9ff21491dcd56c258945b7beb31eb214831c9904b10032c0fa34da3f619c0d5",  # v2 putback
}
KNOWN_P2A_SHAS = {sha for _, sha in P2A_PINS.values()} | P2A_LEGACY_SHAS


# --------------------------------------------------------------------------
# Library Verification & Strict Per-Session Guard
# --------------------------------------------------------------------------

def load_pinned_p2a(action):
    """Load and SHA-256-verify either the pinned apply or putback Phase 2A library."""
    if action not in P2A_PINS:
        raise c.StopTest("Unknown Phase 2A action %r (expected 'apply' or 'putback')." % (action,))
    path, want_sha = P2A_PINS[action]
    if not path.is_file():
        raise c.StopTest("Pinned Phase 2A library missing: %s. Nothing was sent." % path)
    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != want_sha or not (0 < len(data) <= R.LIB_MAX):
        raise c.StopTest(
            "%s is not the pinned Phase 2A %s build (got SHA-256 %s, %d bytes; expected %s). Nothing was sent."
            % (path.name, action, got, len(data), want_sha)
        )
    return path, want_sha, data


def p2a_rules(lib, want_sha):
    return {
        (c.PFUNC_ADJUST, 0x1001, R.SET_FILE_PATH): ("SET_FILE_PATH", lambda p: p[:1] == b"\x00" and p[1:] in R.PATHS),
        (c.PFUNC_ADJUST, 0x1001, R.GET_FILE2): ("GET_FILE2", lambda p: p == b"\x00\xff\x00"),
        (c.PFUNC_ADJUST, 0x1001, R.SET_FILE2): (
            "SET_FILE2",
            lambda p: p == b"\x00\xff\x00" + lib and c.sha256(p[3:]) == want_sha,
        ),
        (c.PFUNC_ADJUST, 0x1001, R.FILE_DELETE2): ("FILE_DELETE2", lambda p: p == b"\x00\xff"),
    }


class P2AGuard(c.ServiceGuard):
    """Strict guard allowing ONLY the single pinned Phase 2A library on LIB_PATH and verified result deletes."""

    def __init__(self, device, session, lib, want_sha):
        self._big = len(lib) + 7
        self.writes = 0
        self.allow_result_delete = False
        super().__init__(device, session, p2a_rules(lib, want_sha))

    def _send(self, pFunc, data, _oData):
        if len(data) > 0x1000 and len(data) != self._big:
            raise c.GuardRefusal("a %d-byte service packet that is not the pinned Phase 2A library" % len(data))
        device = self._device
        header = struct.pack(c.SENSER_HEADER, len(data), pFunc, device._sequence, 0, 0, 0, 0)
        device.driver.write(header + bytes(data))
        response, out = c.read_service_answer(device.driver, device._sequence, self.session.log)
        device._sequence += 1
        return response, out

    def send(self, pFunc, data, oData=None):
        name = c.classify_service_packet(pFunc, data, self.rules)
        if oData is not None:
            raise c.GuardRefusal("%s: output redirection is not allowed" % name)
        if name == "FILE_DELETE2" and self._path_set == R.RESULT_PATH and self.allow_result_delete:
            self.allow_result_delete = False
            return super().send(pFunc, data, oData)
        if name in ("SET_FILE2", "FILE_DELETE2") and self._path_set != R.LIB_PATH:
            raise c.GuardRefusal("%s when the path selected just before is not %s" % (name, R.LIB_PATH.decode()))
        if name == "SET_FILE2":
            if self.writes:
                raise c.GuardRefusal("a second file write in one session")
            self.writes += 1
        return super().send(pFunc, data, oData)


def check_refusals(lib, want_sha, other_lib=None):
    """Verify all 15 forbidden CT6 packets (plus tampered/swapped libraries) are refused by p2a_rules."""
    tampered = bytearray(lib)
    tampered[len(tampered) // 2] ^= 1
    cases = [
        ("the library with one bit changed", c.PFUNC_ADJUST, 0x1001, R.SET_FILE2, b"\x00\xff\x00" + bytes(tampered)),
        ("any other file content", c.PFUNC_ADJUST, 0x1001, R.SET_FILE2, b"\x00\xff\x00hello"),
        ("a write with the code in its short form", c.PFUNC_ADJUST, 0x1001, 0x0010, b"\x00\xff\x00" + lib),
        ("a write to the memory card", c.PFUNC_ADJUST, 0x1001, R.SET_FILE2, b"\x01\xff\x00" + lib),
        ("a path off the list", c.PFUNC_ADJUST, 0x1001, R.SET_FILE_PATH, b"\x00../../etc/passwd"),
        ("delete with another selector", c.PFUNC_ADJUST, 0x1001, R.FILE_DELETE2, b"\x00\x04"),
        ("the older delete command", c.PFUNC_ADJUST, 0x1001, 0x4009, b"\x00\x02"),
        ("format of the service partition", c.PFUNC_ADJUST, 0x1001, 0x4003, b"\x00"),
        ("the mode command", c.PFUNC_ADJUST, 0x1001, 0x4001, b"\x01\x01"),
        ("the start command", c.PFUNC_ADJUST, 0x1001, 0x4004, b"\x00"),
        ("firmware through the service route", c.PFUNC_ADJUST, 0x1001, 0x4006, b"\x00"),
        ("backup write", c.PFUNC_ADJUST, 0x0603, 2, b"\xf2\x1b\xcf\x02\x02"),
        ("coprocessor flash write", c.PFUNC_ADJUST, 0x060B, 2, b"\x00" * 8),
        ("coprocessor debug write", c.PFUNC_ADJUST, 0x060B, 0x82, b"\x00" * 8),
        ("firmware update", 0x20, 0, 0, b""),
    ]
    if other_lib is not None:
        cases.append(("the opposite Phase 2A library", c.PFUNC_ADJUST, 0x1001, R.SET_FILE2, b"\x00\xff\x00" + other_lib))
    refused = []
    rules = p2a_rules(lib, want_sha)
    for name, pfunc, cat, code, payload in cases:
        try:
            c.classify_service_packet(pfunc, struct.pack("<HH", cat, code) + payload, rules)
        except c.GuardRefusal:
            refused.append(name)
            continue
        raise AssertionError("P2AGuard allowed forbidden packet: %s" % name)
    return refused


# --------------------------------------------------------------------------
# Result Parser & Outcome Evaluation
# --------------------------------------------------------------------------

def parse_p2a_result(data, expected_mode):
    """Parse `/tmp_wbshd/readcheck_result.txt` emitted by `apsc60_p2a.c`."""
    head = re.match(rb"apsc60_p2a 1 mode (apply|putback)\neuid (\d+)\n", data)
    done_foot = re.search(rb"== done, bytes written before this line: (\d+)\n\Z", data)
    mode_got = head.group(1).decode() if head else None
    verb_expected = "apply" if expected_mode == "apply" else "put back"

    stored_records = [
        {
            "mode": m.group(1).decode(),
            "addr": m.group(2).decode(),
            "length": int(m.group(3)),
            "site_bytes": int(m.group(4)),
        }
        for m in re.finditer(
            rb"^stored record of mode (0x[0-9a-f]{4}) at (0x[0-9a-f]{8}) length (\d+): (\d+) of its bytes are a site$",
            data, re.M,
        )
    ]
    buf_m = re.search(rb"^buffer physical (0x[0-9a-f]+) handle (\d+)$", data, re.M)

    refused = [m.decode("utf-8", "replace") for m in re.findall(rb"^(?:.*!! .*; nothing is written)$", data, re.M)]
    problems = [m.decode("utf-8", "replace") for m in re.findall(rb"^(?:.*!! .*)$", data, re.M)]

    facts = {
        "bytes": len(data),
        "sha256": c.sha256(data),
        "design": "apsc60_p2a_v3",
        "mode_expected": expected_mode,
        "mode_got": mode_got,
        "header_ok": bool(head) and mode_got == expected_mode,
        "euid": int(head.group(2)) if head else None,
        "complete": bool(done_foot) and int(done_foot.group(1)) == done_foot.start(1),
        "buffer_physical": buf_m.group(1).decode() if buf_m else None,
        "buffer_handle": int(buf_m.group(2)) if buf_m else None,
        "stored_records": stored_records,
        "stored_record": stored_records[-1] if stored_records else None,
        "guards_static_ok": b"\nguards: all static table guards verified\n" in data,
        "guards_record_ok": b"\nguards: all stored record invariants verified\n" in data,
        "as_expected": len(re.findall(rb"^site 0x[0-9a-f]{8} length \d+: as expected$", data, re.M)),
        "already": len(re.findall(rb"^site 0x[0-9a-f]{8} length \d+: already holds the target bytes$", data, re.M)),
        "written": len(re.findall(rb"^site 0x[0-9a-f]{8} length \d+: written and read back$", data, re.M)),
        "undone_sites": len(re.findall(rb"^undo 0x[0-9a-f]{8} length \d+: put back and read back$", data, re.M)),
        "buffer_released": data.count(b"\nbuffer released\n"),
        "done": (b"\nDONE: all 45 sites hold the target bytes (" + verb_expected.encode() + b")\n") in data,
        "nothing_to_do": b"\nNOTHING TO DO: every site already holds the target bytes\n" in data,
        "undone": b"\nNOT DONE: a step failed; every site this run touched was put back\n" in data,
        "undo_incomplete": b"UNDO INCOMPLETE" in data,
        "refused": refused,
        "problems": problems,
    }
    return facts


def evaluate_p2a(facts):
    """Return (ok, status_label, detail)."""
    if not facts["header_ok"]:
        return False, "bad_header", "result header mismatch (expected mode=%s, got=%r)" % (
            facts["mode_expected"], facts["mode_got"])
    if not facts["complete"]:
        return False, "incomplete", "result file is truncated or missing byte-count footer"
    if facts["undo_incomplete"]:
        return False, "undo_incomplete", "UNDO INCOMPLETE: coprocessor memory may be partly changed — COLD BOOT NOW (battery out 10s)"
    if facts["done"]:
        return True, "done", "DONE: all 45 sites hold the target bytes (%s)" % facts["mode_expected"]
    if facts["nothing_to_do"]:
        return True, "nothing_to_do", "NOTHING TO DO: every site already holds the target bytes"
    why = "; ".join(facts["refused"] or facts["problems"]) or "helper did not report DONE"
    if facts["undone"]:
        return False, "undone", "NOT DONE (all touched sites were safely put back): " + why
    if facts["refused"]:
        return False, "refused", "REFUSED before any write: " + why
    return False, "failed", "NOT DONE: " + why


# --------------------------------------------------------------------------
# Single-Session USB Execution
# --------------------------------------------------------------------------

def service_session_p2a(session, inner, lib, want_sha, body):
    """Authenticate once on 054C:0336, install P2AGuard, run body, always send STOP_SENSER in finally."""
    from pmca.usb.sony import SonySenserAuthDevice, SonySenserCamera, SonySenserDevice

    driver = c.GuardedDriver(inner, session, "senser")
    session.results["service_usb_id"] = "%04x:%04x" % driver.getId()
    auth = SonySenserAuthDevice(driver)
    session.say("Authenticating Service Mode session (%s)" % session.results["service_usb_id"])
    auth.start()
    try:
        try:
            auth.authenticate()
        finally:
            session.results["auth_service"] = ct1_compare.auth_summary(session.events, "senser")
        device = SonySenserDevice(driver)
        guard = P2AGuard(device, session, lib, want_sha)
        body(session, SonySenserCamera(device), guard, lib)
    finally:
        auth.stop()
        session.say("Stop request sent")


def _delete_verified_result(session, camera, guard, expected_sha, actual_data, label):
    got_sha = c.sha256(actual_data)
    if not expected_sha or got_sha != expected_sha:
        raise c.StopTest(
            "Refusing to delete %s (%s): SHA-256 %s does not match expected %s."
            % (R.RESULT_PATH.decode(), label, got_sha[:16], (expected_sha or "none")[:16])
        )
    guard.allow_result_delete = True
    R.select(camera, R.RESULT_PATH)
    del_st, _ = R.command(camera, R.FILE_DELETE2, b"\x00\xff")
    still_there = R.read(camera, R.RESULT_PATH)[0]
    session.say("Cleared %s (%s): status %s, result file now %s" % (
        R.RESULT_PATH.decode(), label, R.text(del_st), "STILL THERE" if still_there else "gone"))
    return del_st, still_there


def body_remove_only(session, camera, guard, _lib):
    out = session.results["p2a"] = {"action": "remove_only", "library_sent": False, "library_on_camera_after": None}
    R.known_answer(session, camera)
    there, data = R.read(camera, R.LIB_PATH)
    if not there:
        out["library_on_camera_after"] = False
        session.say("There is no /tmp/jiritsu/libJit.so on the camera. Nothing to remove.")
        return
    got_sha = c.sha256(data)
    out["found_sha256"] = got_sha
    if got_sha not in KNOWN_P2A_SHAS:
        raise c.StopTest(
            "The /tmp/jiritsu/libJit.so on the camera (%d bytes, SHA-256 %s) is NOT a Phase 2A library. Left untouched."
            % (len(data), got_sha[:16])
        )
    session.say("Found Phase 2A libJit.so on camera (%d bytes, SHA-256 %s). Removing it." % (len(data), got_sha[:16]))
    R.remove(session, camera, out)
    if out.get("library_on_camera_after") is not False:
        raise c.StopTest("STOP: /tmp/jiritsu/libJit.so was NOT confirmed removed.")


def body_p2a(session, camera, guard, lib, action, lib_path, want_sha, clear_sha=None):
    out = session.results["p2a"] = {
        "action": action,
        "design": "apsc60_p2a_v3",
        "library_name": lib_path.name,
        "library_bytes": len(lib),
        "library_sha256": want_sha,
        "library_sent": False,
        "library_on_camera_after": None,
        "result_found": False,
        "result_on_camera_after": None,
    }

    R.known_answer(session, camera)

    # 1. /tmp/jiritsu/libJit.so must NOT exist.
    there, existing_lib = R.read(camera, R.LIB_PATH)
    if there:
        ours = c.sha256(existing_lib) in KNOWN_P2A_SHAS
        raise c.StopTest(
            "A libJit.so is already on the camera service partition (%d bytes, %s). Nothing was written. %s"
            % (len(existing_lib), "matches a Phase 2A library" if ours else "NOT a Phase 2A library",
               "Run with --remove-only first." if ours else "Left untouched.")
        )

    # 2. /tmp_wbshd/readcheck_result.txt must NOT exist; clear ONLY if SHA-256 matches saved result.
    there, old_res = R.read(camera, R.RESULT_PATH)
    if there and clear_sha and c.sha256(old_res) == clear_sha:
        session.say("An earlier saved result is on the RAM disk (%d bytes). Clearing it." % len(old_res))
        del_st, still_there = _delete_verified_result(session, camera, guard, clear_sha, old_res, "pre-run")
        out["pre_clear_status"] = del_st
        there = still_there
    if there:
        raise c.StopTest(
            "A result file is already on the camera RAM disk and does not match an already-saved result. "
            "Cold boot the camera or pass --clear-result SHA256. Nothing was written."
        )

    # 3. /tmp/jbusy must NOT exist.
    if R.read(camera, R.BUSY_PATH)[0]:
        raise c.StopTest("/tmp/jbusy exists; the camera would refuse the library. Nothing was written.")
    session.say("Pre-checks passed: no libJit.so, no stale result, not busy")

    # 4. Upload pinned Phase 2A library, ALWAYS removing /tmp/jiritsu/libJit.so in finally.
    write_status = None
    write_exc = None
    R.select(camera, R.LIB_PATH)
    session.say("Sending Phase 2A %s library %s (%d bytes)" % (action, lib_path.name, len(lib)))
    out["library_sent"] = True
    try:
        write_status, _ = R.command(camera, R.SET_FILE2, b"\x00\xff\x00" + lib)
    except c.GuardRefusal:
        out["library_sent"] = False
        raise
    except Exception as exc:
        write_exc = exc
        out["write_error"] = "%s: %s" % (type(exc).__name__, exc)
        session.say("The write got no answer (%s)" % out["write_error"])
    finally:
        if out["library_sent"]:
            R.remove(session, camera, out)

    out["write_status"] = write_status
    if write_exc is None:
        session.say("Write and load: status %s" % R.text(write_status))
    else:
        raise c.StopTest("The write got no answer; result not read.")

    if out.get("library_on_camera_after") is not False:
        raise c.StopTest("STOP: /tmp/jiritsu/libJit.so was NOT confirmed removed after upload.")

    # 5. Fetch result file from RAM disk.
    there, result_bytes = R.read(camera, R.RESULT_PATH)
    out["result_found"] = there
    if not there:
        raise c.StopTest("No result file created by Phase 2A helper (write status: %s)." % R.text(write_status))

    result_sha = session.save_bytes("readcheck_result.txt", result_bytes)

    # 6. Clear /tmp_wbshd/readcheck_result.txt by verified SHA-256.
    del_st, still_there = _delete_verified_result(session, camera, guard, result_sha, result_bytes, "post-run")
    out["result_clear_status"] = del_st
    out["result_on_camera_after"] = still_there

    # 7. Parse result & save p2a_decoded.json + compatibility mirror for last_result_sha().
    facts = parse_p2a_result(result_bytes, action)
    session.save_json("p2a_decoded.json", facts)
    ok, status_label, detail = evaluate_p2a(facts)
    out["status"] = status_label
    out["detail"] = detail
    out["result"] = facts

    session.results["ct6"] = {
        "library_bytes": len(lib),
        "library_sha256": want_sha,
        "library_sent": True,
        "write_status": write_status,
        "remove_status": out.get("remove_status"),
        "library_on_camera_after": out.get("library_on_camera_after"),
        "result_found": True,
        "result": {"bytes": facts["bytes"], "sha256": result_sha, "complete": facts["complete"]},
    }

    session.say(
        "Phase 2A (%s): %s. Guards: static=%s, record=%s. Pass 1: %d expected, %d already; written=%d; buffer_released=%d"
        % (
            action, detail,
            "ok" if facts["guards_static_ok"] else "FAILED",
            "ok" if facts["guards_record_ok"] else "FAILED",
            facts["as_expected"], facts["already"], facts["written"], facts["buffer_released"],
        )
    )
    if facts["undo_incomplete"]:
        session.say("!! UNDO INCOMPLETE: COLD BOOT THE CAMERA NOW (battery out for 10 seconds) BEFORE DOING ANYTHING ELSE.")
    if not ok:
        raise c.StopTest("Phase 2A %s did not succeed: %s" % (action, detail))


# --------------------------------------------------------------------------
# Ownership Restoration
# --------------------------------------------------------------------------

def resolve_target_owner(owner_uid=None, owner_gid=None):
    uid = owner_uid
    gid = owner_gid
    if uid is None and os.environ.get("SUDO_UID", "").isdigit():
        uid = int(os.environ["SUDO_UID"])
    if gid is None and os.environ.get("SUDO_GID", "").isdigit():
        gid = int(os.environ["SUDO_GID"])
    if uid is not None and uid < 0:
        raise ValueError("owner_uid must be >= 0")
    if gid is not None and gid < 0:
        raise ValueError("owner_gid must be >= 0")
    return uid, gid


def chown_tree(path, uid, gid):
    if uid is None or gid is None or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    p = Path(path)
    if not p.exists():
        return
    try:
        os.chown(p, uid, gid)
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                for name in dirs:
                    os.chown(os.path.join(root, name), uid, gid)
                for name in files:
                    os.chown(os.path.join(root, name), uid, gid)
    except OSError as exc:
        print("Warning: could not restore ownership of %s: %s" % (p, exc), file=sys.stderr)


# --------------------------------------------------------------------------
# Simulated Camera for --dry-run and --selftest
# --------------------------------------------------------------------------

def make_canned_p2a_result(action, outcome="done", truncated=False):
    verb = "apply" if action == "apply" else "put back"
    lines = [
        "apsc60_p2a 1 mode %s" % action,
        "euid 0",
        "== apsc60_p2a: %s the 41 sites of apsc60_p2a_v3 and the 4 stored records" % verb,
        "buffer physical 0x000000043a000000 handle 42",
        "stored record of mode 0x0821 at 0x025de15c length 368: 28 of its bytes are a site",
        "stored record of mode 0x08c1 at 0x025de2cc length 368: 28 of its bytes are a site",
        "stored record of mode 0x0b21 at 0x025de5ac length 368: 28 of its bytes are a site",
        "stored record of mode 0x0bc1 at 0x025de71c length 368: 28 of its bytes are a site",
        "pass 1: read every guard and site; nothing is written unless all are as expected",
    ]
    if outcome == "refused":
        lines.append("!! guard mismatch at 0x010fdcb0; nothing is written")
    else:
        lines.append("guards: all static table guards verified")
        lines.append("guards: all stored record invariants verified")
        if outcome == "nothing_to_do":
            for i in range(45):
                lines.append("site 0x%08x length 4: already holds the target bytes" % (0x010FD000 + i * 4))
            lines.append("NOTHING TO DO: every site already holds the target bytes")
        elif outcome == "undone":
            for i in range(45):
                lines.append("site 0x%08x length 4: as expected" % (0x010FD000 + i * 4))
            lines.append("pass 2: write, and read each site back")
            lines.append("site 0x010fd000 length 4: written and read back")
            lines.append("site 0x010fd004 length 4: !! not as written on read-back")
            lines.append("undo: every site this run touched gets its earlier bytes back")
            lines.append("undo 0x010fd004 length 4: put back and read back")
            lines.append("undo 0x010fd000 length 4: put back and read back")
            lines.append("NOT DONE: a step failed; every site this run touched was put back")
        elif outcome == "undo_incomplete":
            lines.append("site 0x010fd000 length 4: as expected")
            lines.append("pass 2: write, and read each site back")
            lines.append("site 0x010fd000 length 4: !! write failed")
            lines.append("undo: every site this run touched gets its earlier bytes back")
            lines.append("undo 0x010fd000 length 4: !! NOT PUT BACK")
            lines.append("!! UNDO INCOMPLETE. Cold boot the camera (battery out) before anything else.")
        else:
            for i in range(45):
                lines.append("site 0x%08x length 4: as expected" % (0x010FD000 + i * 4))
            lines.append("pass 2: write, and read each site back")
            for i in range(45):
                lines.append("site 0x%08x length 4: written and read back" % (0x010FD000 + i * 4))
            lines.append("pass 3: read every site once more")
            lines.append("DONE: all 45 sites hold the target bytes (%s)" % verb)
    lines.append("buffer released")
    body = ("\n".join(lines) + "\n").encode("utf-8")
    lead = b"== done, bytes written before this line: "
    full = body + lead + (b"%d\n" % (len(body) + len(lead)))
    return full[:-5] if truncated else full


def make_p2a_sim(lib, want_sha, action, files=None, fault=None, outcome="done"):
    import ct1_sim

    class P2ASim(ct1_sim.SimDriver):
        def __init__(self):
            super().__init__("senser", rules=p2a_rules(lib, want_sha))
            self.fs = {"/usr/share/pmbp/DeviceInfo.xml": (c.EXTRACTED / R.KNOWN_FILE).read_bytes()}
            self.fs.update(files or {})
            self.loads = 0
            self.dead = False

        def write(self, data, ep=0):
            if self.dead:
                raise ct1_sim.SimUsbError("the simulated camera no longer answers")
            return super().write(data, ep)

        def _handle(self, pfunc, body):
            try:
                name = c.classify_service_packet(pfunc, body, self.rules)
            except c.GuardRefusal as exc:
                self.unexpected.append(("service packet", pfunc, body[:16].hex(), str(exc)))
                return 0xEE, b""
            st = lambda v: struct.pack("<I", v)
            if name == "SET_FILE_PATH":
                p = body[5:].decode()
                self._path = p if p.startswith("/tmp/jiritsu") else posixpath.normpath("/tmp/jiritsu/" + p)
                return 0, st(R.ST_OK)
            path, self._path = self._path, None
            if path is None:
                return 0, st(0x10000201)
            if name == "GET_FILE2":
                return (0, st(R.ST_OK) + self.fs[path]) if path in self.fs else (0, st(R.ST_NO_FILE))
            if name == "SET_FILE2":
                if body[4:7] != b"\x00\xff\x00":
                    return 0, st(0x10000201)
                if path == R.LIB_PATH.decode() and "/tmp/jbusy" in self.fs:
                    return 0, st(R.ST_BUSY)
                if fault == "dead":
                    self.dead = True
                    self.fs[path] = body[7:]
                    raise ct1_sim.SimUsbError("the simulated camera died during write")
                self.fs[path] = body[7:]
                if path != R.LIB_PATH.decode():
                    return 0, st(R.ST_OK)
                self.loads += 1
                if fault == "load":
                    return 0, st(R.ST_LOAD_FAILED)
                if "/tmp_wbshd/readcheck_result.txt" not in self.fs:
                    res_mode = ("putback" if action == "apply" else "apply") if fault == "wrong_mode" else action
                    self.fs["/tmp_wbshd/readcheck_result.txt"] = make_canned_p2a_result(
                        res_mode, outcome=outcome, truncated=(fault == "truncated")
                    )
                return 0, st(R.ST_OK)
            if name == "FILE_DELETE2":
                if fault == "sticky" and path == R.LIB_PATH.decode():
                    return 0, st(R.ST_REMOVE_FAILED)
                if path not in self.fs:
                    return 0, st(R.ST_REMOVE_FAILED)
                del self.fs[path]
                return 0, st(R.ST_OK)
            raise AssertionError(name)

    return P2ASim()


def simulate_p2a(action, lib_path, want_sha, lib, files=None, fault=None, outcome="done", clear_sha=None, remove_only=False):
    c.use_pmca()
    session = c.Session("simulated_p2a_" + action, write=False, test="CT6")
    sim = make_p2a_sim(lib, want_sha, action, files=files, fault=fault, outcome=outcome)
    stopped = None
    body_fn = (
        body_remove_only
        if remove_only
        else (lambda s, cam, g, l: body_p2a(s, cam, g, l, action, lib_path, want_sha, clear_sha=clear_sha))
    )
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            service_session_p2a(session, sim, lib, want_sha, body_fn)
        except c.StopTest as exc:
            stopped = exc
    return session, sim, stopped


def run_selftest():
    passed = []

    def check(cond, what):
        if not cond:
            raise AssertionError("SELFTEST FAILED: " + what)
        passed.append(what)

    def sent(sess):
        return [e["name"] for e in sess.events if e["kind"] == "service_out"]

    ap_path, ap_sha, ap_lib = load_pinned_p2a("apply")
    pb_path, pb_sha, pb_lib = load_pinned_p2a("putback")
    check(len(KNOWN_P2A_SHAS) == 6, "KNOWN_P2A_SHAS includes v3 + legacy v1/v2 apply/putback hashes for --remove-only")
    check(ap_sha != pb_sha and len(ap_lib) <= R.LIB_MAX and len(pb_lib) <= R.LIB_MAX,
          "both apply and putback Phase 2A v3 .so binaries verified against pinned SHA-256 and <= 65520 B")

    # 1. Normal apply & normal putback runs
    for action, lpath, lsha, lbytes in (("apply", ap_path, ap_sha, ap_lib), ("putback", pb_path, pb_sha, pb_lib)):
        s, sim, stopped = simulate_p2a(action, lpath, lsha, lbytes)
        r = s.results["p2a"]
        check(stopped is None and not sim.unexpected and sim.loads == 1,
              "%s: normal run succeeded (1 load, zero unexpected packets)" % action)
        check(sent(s).count("SET_FILE2") == 1 and sent(s).count("FILE_DELETE2") == 2,
              "%s: 1 write, 1 libJit.so delete, 1 verified post-run result delete" % action)
        check(R.LIB_PATH.decode() not in sim.fs and "/tmp_wbshd/readcheck_result.txt" not in sim.fs,
              "%s: both libJit.so and readcheck_result.txt confirmed gone" % action)
        check(r["status"] == "done" and r["result"]["guards_static_ok"] and r["result"]["guards_record_ok"]
              and len(r["result"]["stored_records"]) == 4
              and r["result"]["written"] == 45 and r["result"]["buffer_released"] == 1,
              "%s: parsed DONE with 45 sites written, 4 stored records (0x0821+0x08c1+0x0b21+0x0bc1), all guards verified, buffer released" % action)
        check(sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855), "%s: STOP_SENSER sent at end" % action)

        # Idempotent NOTHING TO DO
        s, sim, stopped = simulate_p2a(action, lpath, lsha, lbytes, outcome="nothing_to_do")
        check(stopped is None and s.results["p2a"]["status"] == "nothing_to_do",
              "%s: NOTHING TO DO accepted cleanly" % action)

    # 2. Guard refusals: all 15 forbidden packets + tampered + cross-mode swap for BOTH apply & putback
    check(len(check_refusals(ap_lib, ap_sha, other_lib=pb_lib)) == 16,
          "P2AGuard(apply) refuses all 15 forbidden packets + putback library swap")
    check(len(check_refusals(pb_lib, pb_sha, other_lib=ap_lib)) == 16,
          "P2AGuard(putback) refuses all 15 forbidden packets + apply library swap")

    # 3. Stateful guard checks
    def stateful(fn):
        c.use_pmca()
        sess = c.Session("stateful", write=False, test="CT6")
        sim_drv = make_p2a_sim(ap_lib, ap_sha, "apply")
        caught = None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                service_session_p2a(sess, sim_drv, ap_lib, ap_sha, fn)
            except c.StopTest as exc:
                caught = exc
        return caught, sess

    caught, sess = stateful(lambda s_, cam, g, l: (R.select(cam, R.RESULT_PATH), R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + l)))
    check(isinstance(caught, c.GuardRefusal) and "SET_FILE2" not in sent(sess), "guard refuses SET_FILE2 on non-libJit path")

    caught, sess = stateful(lambda s_, cam, g, l: R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + l))
    check(isinstance(caught, c.GuardRefusal) and "SET_FILE2" not in sent(sess), "guard refuses SET_FILE2 with no path selected")

    caught, sess = stateful(lambda s_, cam, g, l: (
        R.select(cam, R.LIB_PATH), R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + l),
        R.select(cam, R.LIB_PATH), R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + l),
    ))
    check(isinstance(caught, c.GuardRefusal) and sent(sess).count("SET_FILE2") == 1, "guard refuses second SET_FILE2 in one session")

    caught, sess = stateful(lambda s_, cam, g, l: (R.select(cam, R.RESULT_PATH), R.command(cam, R.FILE_DELETE2, b"\x00\xff")))
    check(isinstance(caught, c.GuardRefusal) and "FILE_DELETE2" not in sent(sess), "guard refuses unverified FILE_DELETE2 on result path")

    caught, sess = stateful(lambda s_, cam, g, l: (R.select(cam, R.BUSY_PATH), R.command(cam, R.FILE_DELETE2, b"\x00\xff")))
    check(isinstance(caught, c.GuardRefusal) and "FILE_DELETE2" not in sent(sess), "guard refuses FILE_DELETE2 on /tmp/jbusy")

    # 4. Pre-check stops
    for label, init_files in (
        ("existing unknown libJit.so", {"/tmp/jiritsu/libJit.so": b"other"}),
        ("existing Phase 2A libJit.so", {"/tmp/jiritsu/libJit.so": ap_lib}),
        ("existing unknown result", {"/tmp_wbshd/readcheck_result.txt": b"foreign"}),
        ("/tmp/jbusy present", {"/tmp/jbusy": b""}),
        ("wrong DeviceInfo.xml", {"/usr/share/pmbp/DeviceInfo.xml": b"<wrong/>"}),
    ):
        s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, files=init_files)
        check(stopped is not None and sim.loads == 0 and "SET_FILE2" not in sent(s) and "FILE_DELETE2" not in sent(s),
              "pre-check stops safely on %s (zero writes, zero deletes)" % label)

    # 5. Verified --clear-result
    prior = b"prior saved result bytes"
    s, sim, stopped = simulate_p2a(
        "apply", ap_path, ap_sha, ap_lib,
        files={"/tmp_wbshd/readcheck_result.txt": prior}, clear_sha=c.sha256(prior),
    )
    check(stopped is None and sim.loads == 1 and "/tmp_wbshd/readcheck_result.txt" not in sim.fs,
          "verified --clear-result deletes matching prior result and completes")

    # 6. Helper outcomes & fault injections
    for outcome_kind in ("refused", "undone", "undo_incomplete"):
        s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, outcome=outcome_kind)
        check(stopped is not None and s.results["p2a"]["status"] == outcome_kind
              and R.LIB_PATH.decode() not in sim.fs and "/tmp_wbshd/readcheck_result.txt" not in sim.fs,
              "helper outcome '%s' stops cleanly with libJit.so & result cleaned up" % outcome_kind)

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, fault="load")
    check(stopped is not None and R.LIB_PATH.decode() not in sim.fs and sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855),
          "ST_LOAD_FAILED still deletes libJit.so and sends STOP_SENSER")

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, fault="sticky")
    check(stopped is not None and s.results["p2a"]["library_on_camera_after"] is True,
          "sticky libJit.so remove failure halts loudly and records library_on_camera_after=True")

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, fault="dead")
    check(stopped is not None and sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855),
          "USB disconnect during write still sends STOP_SENSER in finally")

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, fault="truncated")
    check(stopped is not None and s.results["p2a"]["status"] == "incomplete" and R.LIB_PATH.decode() not in sim.fs,
          "truncated result rejected cleanly after libJit.so cleanup")

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, fault="wrong_mode")
    check(stopped is not None and s.results["p2a"]["status"] == "bad_header",
          "mismatched mode header rejected cleanly")

    # 7. --remove-only tests (matching apply, matching putback, unknown refused, absent no-op)
    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, files={"/tmp/jiritsu/libJit.so": ap_lib}, remove_only=True)
    check(stopped is None and R.LIB_PATH.decode() not in sim.fs and "SET_FILE2" not in sent(s),
          "--remove-only removes pinned apply libJit.so without writing")

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, files={"/tmp/jiritsu/libJit.so": pb_lib}, remove_only=True)
    check(stopped is None and R.LIB_PATH.decode() not in sim.fs and "SET_FILE2" not in sent(s),
          "--remove-only removes pinned putback libJit.so without writing")

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, files={"/tmp/jiritsu/libJit.so": b"foreign"}, remove_only=True)
    check(stopped is not None and "/tmp/jiritsu/libJit.so" in sim.fs and "FILE_DELETE2" not in sent(s),
          "--remove-only refuses to delete unknown libJit.so")

    s, sim, stopped = simulate_p2a("apply", ap_path, ap_sha, ap_lib, remove_only=True)
    check(stopped is None and "FILE_DELETE2" not in sent(s),
          "--remove-only is a safe read-only no-op when no libJit.so is present")

    return passed


def run_dry_run(action):
    lpath, lsha, lbytes = load_pinned_p2a(action)
    other_action = "putback" if action == "apply" else "apply"
    _, _, other_bytes = load_pinned_p2a(other_action)
    refused = check_refusals(lbytes, lsha, other_lib=other_bytes)
    s, sim, stopped = simulate_p2a(action, lpath, lsha, lbytes)
    if sim.unexpected or stopped is not None:
        raise SystemExit("Dry run failed: unexpected=%r stopped=%r" % (sim.unexpected, stopped))
    print("Phase 2A (%s): %s (%d bytes, SHA-256 %s)" % (action, lpath.name, len(lbytes), lsha))
    print("Single-session packet trace on simulated 054C:0336:")
    for e in s.events:
        if e["kind"] != "service_out":
            continue
        data = bytes.fromhex(e["hex"])
        if e["name"] == "SET_FILE2":
            shown = "%s + %d bytes (%s, SHA-256 %s)" % (data[:7].hex(" "), len(data) - 7, lpath.name, lsha)
        else:
            shown = data.hex(" ")
        note = ": " + data[5:].decode() if e["name"] == "SET_FILE_PATH" else ""
        print("  0x%02x  %s  (%s%s)" % (e["pfunc"], shown, e["name"], note))
    print("  vendor request out: (1, 0xc800, 0x2855)  (STOP_SENSER)")
    print("Simulated camera: loads=%d, libJit.so gone=%s, readcheck_result.txt gone=%s" % (
        sim.loads, R.LIB_PATH.decode() not in sim.fs, "/tmp_wbshd/readcheck_result.txt" not in sim.fs))
    print("Guard refusals verified (%d): %s" % (len(refused), ", ".join(refused[:5]) + ", ..."))
    print("Dry run passed. Nothing was sent to any real USB device.")


def run_live(args, action):
    uid, gid = resolve_target_owner(args.owner_uid, args.owner_gid)
    if args.firmware != FIRMWARE:
        raise SystemExit(
            "A live Phase 2A run requires --firmware %s (got %r). Nothing was sent." % (FIRMWARE, args.firmware)
        )
    lpath, lsha, lbytes = load_pinned_p2a(action)
    if not args.yes:
        prompt = (
            "Type YES to remove Phase 2A libJit.so if present on A7C II (fw 2.01): "
            if args.remove_only
            else "Type YES to run Phase 2A (%s, RAM-only) on A7C II (fw 2.01): " % action
        )
        if input(prompt).strip() != "YES":
            raise SystemExit("Not confirmed. Nothing was sent.")

    # Pre-flight offline self-test before touching real hardware
    run_selftest()

    clear_sha = args.clear_result or OG.last_result_sha()
    stage = "apsc60_p2a_remove_only" if args.remove_only else ("apsc60_p2a_" + action)
    body_fn = (
        body_remove_only
        if args.remove_only
        else (lambda s, cam, g, l: body_p2a(s, cam, g, l, action, lpath, lsha, clear_sha=clear_sha))
    )
    session_dir = None
    try:
        with c.Session(stage, write=True, root=PRIVATE_DIR, test="CT6") as session:
            session_dir = session.dir
            session.results.update(
                firmware_shown_on_camera=args.firmware,
                model=MODEL,
                design="apsc60_p2a_v3",
                action="remove_only" if args.remove_only else action,
            )
            inner = service.connect(session)
            try:
                service_session_p2a(session, inner, lbytes, lsha, body_fn)
            finally:
                time.sleep(2.5)
                c.print_pnp(session, "after the stop request")
                out = session.results.get("p2a", {})
                print("Library sent: %s. libJit.so on camera now: %s. Result on camera now: %s." % (
                    "yes" if out.get("library_sent") else "no",
                    {False: "no (confirmed)", True: "YES", None: "unknown"}[out.get("library_on_camera_after")],
                    {False: "no (confirmed)", True: "YES", None: "n/a" if args.remove_only else "unknown"}[out.get("result_on_camera_after")],
                ))
    finally:
        if session_dir is not None:
            chown_tree(session_dir, uid, gid)

    if session_dir is not None:
        man = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
        if man.get("status") != "completed":
            raise SystemExit(1)
        print()
        print("Saved full Phase 2A session logs to:")
        print("  %s" % session_dir)
        if not args.remove_only and action == "apply":
            print()
            print("Next:")
            print("  1. Unplug the USB cable (do NOT power the camera off).")
            print("  2. Switch to MOVIE -> APS-C / Super 35 ON -> XAVC S 4K or XAVC HS 4K at 60p ONLY.")
            print("     (Do NOT dial 24p/30p/50p in S35 during this Phase 2A test.)")
            print("  3. Check for a tall full-height 3:2 image (squeezed horizontally into 16:9) at 60p.")
            print("  4. When done: " + COLD_BOOT)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    act = ap.add_mutually_exclusive_group()
    act.add_argument("--apply", action="store_true", help="Apply Phase 2A APS-C 3:2 4K 60p sensor/scaler patch (default)")
    act.add_argument("--putback", action="store_true", help="Put back Phase 2A stock bytes")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Simulate single-session flow; sends nothing")
    mode.add_argument("--selftest", action="store_true", help="Run full offline safety & guard self-test suite; sends nothing")
    mode.add_argument("--remove-only", action="store_true", help="Safely remove a leftover Phase 2A libJit.so without writing")
    ap.add_argument("--firmware", default=None, help="Firmware version shown on camera (must be %s)" % FIRMWARE)
    ap.add_argument("--yes", action="store_true", help="Skip interactive confirmation prompt")
    ap.add_argument("--clear-result", default=None, metavar="SHA256",
                    help="Optional SHA-256 of an earlier saved result file to clear before running")
    ap.add_argument("--owner-uid", "--chown-uid", dest="owner_uid", type=int, default=None,
                    help="UID to chown windows/camera_private/ session folder back to when running as root")
    ap.add_argument("--owner-gid", "--chown-gid", dest="owner_gid", type=int, default=None,
                    help="GID to chown windows/camera_private/ session folder back to when running as root")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    action = "putback" if args.putback else "apply"
    if args.selftest:
        checks = run_selftest()
        for line in checks:
            print("PASS  " + line)
        print("All %d Phase 2A self-tests passed. Nothing was sent to any device." % len(checks))
        return 0
    if args.dry_run:
        run_dry_run(action)
        return 0
    run_live(args, action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
