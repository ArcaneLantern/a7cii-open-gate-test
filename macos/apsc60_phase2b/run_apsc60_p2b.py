#!/usr/bin/env python3
"""One-touch single-session Phase 2B runner for Sony A7C II (fw 2.01) APS-C 3:2 Open Gate 4K 60p.

Runs all 5 SHA-256-pinned RAM-only steps inside ONE authenticated Service Mode (054C:0336) session
(zero USB replugs, ~5 seconds total, STOP_SENSER sent ONCE in finally):

  Step 1: apsc60_p2a_v3              APS-C 3:2 4608x3072 @ 59.94p all-4 col 14 modes (0x0821+0x08c1+0x0b21+0x0bc1) sensor/scaler (Phase 2A v3)
  Step 2: outsz2560_c2_v1            movie output size class 3 -> 3840x2560 (level 16/17)
  Step 3: ogfile2560_60p_r240_c2_v1  59.94p 2-core encoder w/ V60 1.20x bitrate boost up to 240M (level 19/20)
          (--menu-rate selects stock-menu-rate ogfile2560_60p_c2_v1 instead)
  Step 4: lcdfill_c2_v2              3:2 rear screen fill with focus frames (level 16/17; --no-lcd32 skips 4 & 5)
  Step 5: lcdcycle_c2_v1             C2 key de-squeeze preview cycle (level 19/20; --no-cycle skips 5)

  python -I -B macos/apsc60_phase2b/run_apsc60_p2b.py --dry-run [--menu-rate] [--putback] [--no-lcd32] [--no-cycle]
  python -I -B macos/apsc60_phase2b/run_apsc60_p2b.py --selftest
  python -I -B macos/apsc60_phase2b/run_apsc60_p2b.py --firmware 2.01 --yes [--apply | --putback | --remove-only] [--menu-rate]

Zero external dependencies beyond Python stdlib and macos/.venv (pyusb, pycryptodomex, libusb-package).
"""
import argparse
import contextlib
import dataclasses
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
P2A_DIR = MACOS_DIR / "apsc60_phase2a"
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

# Exact SHA-256 pins for custom Phase 2A (v3 all-4 col 14 modes) and Phase 2B libraries (kit libraries come from c2_level_run.PINS)
P2A_APPLY_PATH = P2A_DIR / "libJit_c2_apsc60_p2a_apply.so"
P2A_APPLY_SHA = "a7cb928401b748aee8e070c7d1a271cec8948398aaeb4dc0b25b5c6030d74109"
P2A_PUTBACK_PATH = P2A_DIR / "libJit_c2_apsc60_p2a_putback.so"
P2A_PUTBACK_SHA = "ab6511ea26a6ae496c0d5b723a491f0030857635e8684645407e7af3b5b3cc07"
P2A_LEGACY_SHAS = {
    "6853d7da4ff3d7a55c22602a211c212fc97d5338bbc5d982a75168b143d84871",  # v1 apply
    "bc1eee30af52adafb9754f1d32362e02ad501ec2c027f178254d466532d08c73",  # v1 putback
    "a96f0e9bb4629d06a0f22e6f230ad49108bdea5e55fe623c3bc2315e94b4bbd4",  # v2 apply
    "a9ff21491dcd56c258945b7beb31eb214831c9904b10032c0fa34da3f619c0d5",  # v2 putback
}

P2B_ENC_R240_APPLY_PATH = HERE / "libJit_c2_level19_ogfile2560_60p_r240_c2_v1.so"
P2B_ENC_R240_APPLY_SHA = "458f9b93e9fd5039226eac4ffb8960f0c8fc11cae5c28e2918b676abdee6ab3a"
P2B_ENC_R240_PUTBACK_PATH = HERE / "libJit_c2_level20_ogfile2560_60p_r240_c2_v1.so"
P2B_ENC_R240_PUTBACK_SHA = "ec848b50c6eb1b1288c0f6425f7dfbd256e427110afc52e0e6b62064918a6345"

P2B_ENC_MENU_APPLY_PATH = HERE / "libJit_c2_level19_ogfile2560_60p_c2_v1.so"
P2B_ENC_MENU_APPLY_SHA = "fad921634cd72ea8180c2d31297d288b212bbed3027c6432f27e21a7512b8267"
P2B_ENC_MENU_PUTBACK_PATH = HERE / "libJit_c2_level20_ogfile2560_60p_c2_v1.so"
P2B_ENC_MENU_PUTBACK_SHA = "31836347e47f61722399a55bc0eecc05c673e9078891b96a339b9f06229a01d7"


@dataclasses.dataclass(frozen=True)
class StepSpec:
    index: int
    total: int
    kind: str            # "p2a" or "ct6"
    action: str          # "apply" or "putback"
    level: int           # 0 for p2a, or 16/17/19/20 for ct6
    design: str
    facts_key: str       # "p2a", "level1617", or "level1920"
    sha256: str
    path: Path
    what: str


def _base_step_defs(menu_rate=False):
    """Return the 5 canonical (apply_tuple, putback_tuple) definitions for Phase 2B."""
    if menu_rate:
        enc_pair = (
            ("ct6", "apply", 19, "ogfile2560_60p_c2_v1", "level1920",
             P2B_ENC_MENU_APPLY_SHA, P2B_ENC_MENU_APPLY_PATH,
             "59.94p 2-core encoder descriptors/profiles & libmpr (stock menu bitrates: 114 av-cam + 4 libmpr sites)"),
            ("ct6", "putback", 20, "ogfile2560_60p_c2_v1", "level1920",
             P2B_ENC_MENU_PUTBACK_SHA, P2B_ENC_MENU_PUTBACK_PATH,
             "put back: 59.94p 2-core encoder descriptors/profiles & libmpr (stock menu bitrates)"),
        )
    else:
        enc_pair = (
            ("ct6", "apply", 19, "ogfile2560_60p_r240_c2_v1", "level1920",
             P2B_ENC_R240_APPLY_SHA, P2B_ENC_R240_APPLY_PATH,
             "59.94p 2-core encoder w/ V60 1.20x bitrate boost up to 240M (162 av-cam + 4 libmpr sites)"),
            ("ct6", "putback", 20, "ogfile2560_60p_r240_c2_v1", "level1920",
             P2B_ENC_R240_PUTBACK_SHA, P2B_ENC_R240_PUTBACK_PATH,
             "put back: 59.94p 2-core encoder w/ V60 1.20x bitrate boost"),
        )
    return [
        (
            ("p2a", "apply", 0, "apsc60_p2a_v3", "p2a", P2A_APPLY_SHA, P2A_APPLY_PATH,
             "APS-C 3:2 4608x3072 @ 59.94p all-4 col 14 modes (0x0821+0x08c1+0x0b21+0x0bc1) sensor & scaler tables"),
            ("p2a", "putback", 0, "apsc60_p2a_v3", "p2a", P2A_PUTBACK_SHA, P2A_PUTBACK_PATH,
             "put back: APS-C 3:2 4608x3072 @ 59.94p all-4 col 14 modes sensor & scaler tables"),
        ),
        (
            ("ct6", "apply", 16, "outsz2560_c2_v1", "level1617",
             P.PINS[(16, "outsz2560_c2_v1")], P.library_file(16, "outsz2560_c2_v1"),
             "movie output size class 3 -> 3840x2560"),
            ("ct6", "putback", 17, "outsz2560_c2_v1", "level1617",
             P.PINS[(17, "outsz2560_c2_v1")], P.library_file(17, "outsz2560_c2_v1"),
             "put back: movie output size class 3 -> 3840x2160"),
        ),
        enc_pair,
        (
            ("ct6", "apply", 16, "lcdfill_c2_v2", "level1617",
             P.PINS[(16, "lcdfill_c2_v2")], P.library_file(16, "lcdfill_c2_v2"),
             "rear screen 3:2 fill with focus frames"),
            ("ct6", "putback", 17, "lcdfill_c2_v2", "level1617",
             P.PINS[(17, "lcdfill_c2_v2")], P.library_file(17, "lcdfill_c2_v2"),
             "put back: rear screen 3:2 fill with focus frames"),
        ),
        (
            ("ct6", "apply", 19, "lcdcycle_c2_v1", "level1920",
             P.PINS[(19, "lcdcycle_c2_v1")], P.library_file(19, "lcdcycle_c2_v1"),
             "C2 key de-squeeze preview cycle (3:2 -> 2:1 -> 2.4:1 -> 3:2)"),
            ("ct6", "putback", 20, "lcdcycle_c2_v1", "level1920",
             P.PINS[(20, "lcdcycle_c2_v1")], P.library_file(20, "lcdcycle_c2_v1"),
             "put back: C2 key de-squeeze preview cycle"),
        ),
    ]


ALL_KNOWN_SHAS = (
    {t[5] for pair in _base_step_defs(menu_rate=False) for t in pair}
    | {t[5] for pair in _base_step_defs(menu_rate=True) for t in pair}
    | P2A_LEGACY_SHAS
)


def build_steps(putback=False, no_lcd32=False, no_cycle=False, menu_rate=False):
    pairs = _base_step_defs(menu_rate=menu_rate)
    if no_lcd32:
        pairs = [p for p in pairs if p[0][3] not in ("lcdfill_c2_v2", "lcdcycle_c2_v1")]
    elif no_cycle:
        pairs = [p for p in pairs if p[0][3] != "lcdcycle_c2_v1"]
    chosen = [p[1] for p in reversed(pairs)] if putback else [p[0] for p in pairs]
    total = len(chosen)
    return [
        StepSpec(
            index=i, total=total, kind=t[0], action=t[1], level=t[2],
            design=t[3], facts_key=t[4], sha256=t[5], path=t[6], what=t[7],
        )
        for i, t in enumerate(chosen, 1)
    ]


def load_and_verify_steps(steps):
    """Verify every pinned .so exists, is <= LIB_MAX, and matches its exact SHA-256 pin before touching USB."""
    verified = []
    for step in steps:
        if not step.path.is_file():
            raise c.StopTest("Pinned library missing for step %d: %s. Nothing was sent." % (step.index, step.path))
        data = step.path.read_bytes()
        if not (0 < len(data) <= R.LIB_MAX):
            raise c.StopTest(
                "Pinned library %s has invalid size %d bytes (limit %d). Nothing was sent."
                % (step.path.name, len(data), R.LIB_MAX)
            )
        got = hashlib.sha256(data).hexdigest()
        if got != step.sha256:
            raise c.StopTest(
                "SHA-256 mismatch for %s (got %s, expected %s). Nothing was sent."
                % (step.path.name, got, step.sha256)
            )
        verified.append((step, data))
    return verified


# --------------------------------------------------------------------------
# Strict Multi-Step Service Guard
# --------------------------------------------------------------------------

class MultiStepGuard(c.ServiceGuard):
    """Strict per-session guard enforcing monotonic per-step pinned writes and verified-SHA result deletes."""

    def __init__(self, device, session, pinned_steps):
        self._pinned = [(bytes(lib), sha) for lib, sha in pinned_steps]
        self._step_idx = -1
        self._step_armed = False
        self._step_writes = 0
        self.total_writes = 0
        self.allow_result_delete = False
        super().__init__(device, session, self._build_rules())

    def _build_rules(self):
        def _set_file2_ok(payload):
            if not self._step_armed or self._step_idx < 0 or self._step_idx >= len(self._pinned):
                return False
            lib, sha = self._pinned[self._step_idx]
            return payload == b"\x00\xff\x00" + lib and c.sha256(payload[3:]) == sha

        return {
            (c.PFUNC_ADJUST, 0x1001, R.SET_FILE_PATH): ("SET_FILE_PATH", lambda p: p[:1] == b"\x00" and p[1:] in R.PATHS),
            (c.PFUNC_ADJUST, 0x1001, R.GET_FILE2): ("GET_FILE2", lambda p: p == b"\x00\xff\x00"),
            (c.PFUNC_ADJUST, 0x1001, R.SET_FILE2): ("SET_FILE2", _set_file2_ok),
            (c.PFUNC_ADJUST, 0x1001, R.FILE_DELETE2): ("FILE_DELETE2", lambda p: p == b"\x00\xff"),
        }

    def arm_step(self, idx):
        if idx != self._step_idx + 1 or not (0 <= idx < len(self._pinned)):
            raise c.GuardRefusal(
                "MultiStepGuard: non-monotonic or out-of-range step index %d (expected %d)"
                % (idx, self._step_idx + 1)
            )
        self._step_idx = idx
        self._step_armed = True
        self._step_writes = 0

    def disarm_step(self):
        self._step_armed = False

    def _send(self, pFunc, data, _oData):
        if len(data) > 0x1000:
            if not self._step_armed or self._step_idx < 0 or self._step_idx >= len(self._pinned):
                raise c.GuardRefusal("a %d-byte service packet when no step is armed" % len(data))
            expected_len = len(self._pinned[self._step_idx][0]) + 7
            if len(data) != expected_len:
                raise c.GuardRefusal("a %d-byte service packet that is not step %d's pinned library" % (len(data), self._step_idx + 1))
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
            if not self._step_armed or self._step_writes > 0:
                raise c.GuardRefusal("a second or unarmed SET_FILE2 in step %d" % (self._step_idx + 1))
            self._step_writes += 1
            self.total_writes += 1
            try:
                return super().send(pFunc, data, oData)
            finally:
                self._step_armed = False
        return super().send(pFunc, data, oData)


def check_guard_refusals(verified_steps):
    """Verify MultiStepGuard refuses all 15 forbidden CT6 packets (disarmed & armed) plus cross-step tampering."""
    c.use_pmca()
    session = c.Session("refusals", write=False, test="CT6")

    class _DummyDev:
        _sequence = 1
        driver = None

    pinned = [(lib, step.sha256) for step, lib in verified_steps]
    guard = MultiStepGuard(_DummyDev(), session, pinned)

    # Disarmed must refuse even a valid pinned library
    try:
        c.classify_service_packet(
            c.PFUNC_ADJUST,
            struct.pack("<HH", 0x1001, R.SET_FILE2) + b"\x00\xff\x00" + pinned[0][0],
            guard.rules,
        )
        raise AssertionError("MultiStepGuard allowed SET_FILE2 while disarmed!")
    except c.GuardRefusal:
        pass

    checked_names = []
    for idx, (lib, _sha) in enumerate(pinned):
        guard.arm_step(idx)
        tampered = bytearray(lib)
        tampered[len(tampered) // 2] ^= 1
        forbidden = [
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
        if len(pinned) > 1:
            wrong_lib = pinned[(idx + 1) % len(pinned)][0]
            forbidden.append(("another step's pinned library", c.PFUNC_ADJUST, 0x1001, R.SET_FILE2, b"\x00\xff\x00" + wrong_lib))
        for name, pfunc, cat, code, payload in forbidden:
            try:
                c.classify_service_packet(pfunc, struct.pack("<HH", cat, code) + payload, guard.rules)
            except c.GuardRefusal:
                if idx == 0:
                    checked_names.append(name)
                continue
            raise AssertionError("MultiStepGuard allowed forbidden packet on step %d: %s" % (idx + 1, name))
        guard.disarm_step()
    return checked_names


# --------------------------------------------------------------------------
# Result Parsers (Step 1 P2A + Steps 2..5 CT6 Level 16/17/19/20)
# --------------------------------------------------------------------------

def parse_p2a_step_result(data, expected_mode):
    head = re.match(rb"apsc60_p2a 1 mode (apply|putback)\neuid (\d+)\n", data)
    done_foot = re.search(rb"== done, bytes written before this line: (\d+)\n\Z", data)
    mode_got = head.group(1).decode() if head else None
    verb = "apply" if expected_mode == "apply" else "put back"
    stored_records = [
        {"mode": m.group(1).decode(), "addr": m.group(2).decode(), "length": int(m.group(3)), "site_bytes": int(m.group(4))}
        for m in re.finditer(
            rb"^stored record of mode (0x[0-9a-f]{4}) at (0x[0-9a-f]{8}) length (\d+): (\d+) of its bytes are a site$",
            data, re.M,
        )
    ]
    refused = [m.decode("utf-8", "replace") for m in re.findall(rb"^(?:.*!! .*; nothing is written)$", data, re.M)]
    problems = [m.decode("utf-8", "replace") for m in re.findall(rb"^(?:.*!! .*)$", data, re.M)]
    return {
        "bytes": len(data),
        "sha256": c.sha256(data),
        "header_ok": bool(head) and mode_got == expected_mode,
        "euid": int(head.group(2)) if head else None,
        "complete": bool(done_foot) and int(done_foot.group(1)) == done_foot.start(1),
        "p2a": {
            "mode": mode_got,
            "design": "apsc60_p2a_v3",
            "stored_records": stored_records,
            "guards_static_ok": b"\nguards: all static table guards verified\n" in data,
            "guards_record_ok": b"\nguards: all stored record invariants verified\n" in data,
            "as_expected": len(re.findall(rb"^site 0x[0-9a-f]{8} length \d+: as expected$", data, re.M)),
            "already": len(re.findall(rb"^site 0x[0-9a-f]{8} length \d+: already holds the target bytes$", data, re.M)),
            "written": len(re.findall(rb"^site 0x[0-9a-f]{8} length \d+: written and read back$", data, re.M)),
            "buffer_released": data.count(b"\nbuffer released\n"),
            "done": (b"\nDONE: all 45 sites hold the target bytes (" + verb.encode() + b")\n") in data,
            "nothing_to_do": b"\nNOTHING TO DO: every site already holds the target bytes\n" in data,
            "undone": b"\nNOT DONE: a step failed; every site this run touched was put back\n" in data,
            "undo_incomplete": b"UNDO INCOMPLETE" in data,
            "refused": refused,
            "problems": problems,
        },
    }


def parse_step_result(step, data):
    if step.kind == "p2a":
        return parse_p2a_step_result(data, step.action)
    R.LEVEL = step.level
    R.DESIGN = step.design
    return R.check_result(data)


def evaluate_step(step, facts):
    """Return (ok, status_label, detail)."""
    if not facts.get("header_ok"):
        return False, "bad_header", "result header did not match step %d (%s)" % (step.index, step.design)
    if not facts.get("complete"):
        return False, "incomplete", "result file is truncated or missing byte-count footer"
    f = facts.get(step.facts_key) or {}
    if f.get("design") != step.design:
        return False, "bad_design", "result design %r did not match expected %r" % (f.get("design"), step.design)
    if f.get("undo_incomplete"):
        return False, "undo_incomplete", "UNDO INCOMPLETE — COLD BOOT THE CAMERA NOW (battery out 10s)"
    if f.get("done"):
        return True, "done", "done"
    if f.get("nothing_to_do"):
        return True, "nothing_to_do", "already in place, nothing to do"
    why = "; ".join((f.get("refused") or []) + (f.get("problems") or [])) or "helper did not report DONE"
    if f.get("undone"):
        return False, "undone", "step failed and was put back: " + why
    if f.get("refused"):
        return False, "refused", "refused before writing: " + why
    return False, "failed", "not done: " + why


# --------------------------------------------------------------------------
# Single-Session USB Execution
# --------------------------------------------------------------------------

def service_session_p2b(session, inner, pinned_steps, body):
    """Authenticate once on 054C:0336, install MultiStepGuard, run body, send STOP_SENSER ONCE in finally."""
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
        guard = MultiStepGuard(device, session, pinned_steps)
        body(session, SonySenserCamera(device), guard)
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


def body_remove_only(session, camera, _guard):
    out = session.results["p2b"] = {"action": "remove_only", "library_sent": False, "library_on_camera_after": None}
    R.known_answer(session, camera)
    there, data = R.read(camera, R.LIB_PATH)
    if not there:
        out["library_on_camera_after"] = False
        session.say("There is no /tmp/jiritsu/libJit.so on the camera. Nothing to remove.")
        return
    got_sha = c.sha256(data)
    out["found_sha256"] = got_sha
    if got_sha not in ALL_KNOWN_SHAS:
        raise c.StopTest(
            "The /tmp/jiritsu/libJit.so on the camera (%d bytes, SHA-256 %s) is NOT a Phase 2B library. Left untouched."
            % (len(data), got_sha[:16])
        )
    session.say("Found Phase 2B libJit.so on camera (%d bytes, SHA-256 %s). Removing it." % (len(data), got_sha[:16]))
    R.remove(session, camera, out)
    if out.get("library_on_camera_after") is not False:
        raise c.StopTest("STOP: /tmp/jiritsu/libJit.so was NOT confirmed removed.")


def body_p2b(session, camera, guard, action, verified_steps, initial_clear_sha=None):
    out = session.results["p2b"] = {
        "action": action,
        "steps_total": len(verified_steps),
        "steps_completed": 0,
        "library_on_camera_after": False,
        "steps": [],
    }

    R.known_answer(session, camera)
    expected_clear_sha = initial_clear_sha

    for idx, (step, lib_bytes) in enumerate(verified_steps):
        step_rec = {
            "index": step.index,
            "total": step.total,
            "kind": step.kind,
            "level": step.level,
            "design": step.design,
            "what": step.what,
            "library_name": step.path.name,
            "library_bytes": len(lib_bytes),
            "library_sha256": step.sha256,
            "library_sent": False,
            "write_status": None,
            "remove_status": None,
            "library_on_camera_after": None,
            "result_found": False,
            "result_on_camera_after": None,
        }
        out["steps"].append(step_rec)
        session.say("--- Step %d/%d (%s): %s" % (step.index, step.total, step.design, step.what))

        # 1. /tmp/jiritsu/libJit.so must NOT exist.
        there, existing_lib = R.read(camera, R.LIB_PATH)
        if there:
            ours = c.sha256(existing_lib) in ALL_KNOWN_SHAS
            raise c.StopTest(
                "Step %d: a libJit.so is already on the camera (%d bytes, %s). Nothing was written. %s"
                % (step.index, len(existing_lib), "matches a Phase 2B library" if ours else "NOT a Phase 2B library",
                   "Run --remove-only first." if ours else "Left untouched.")
            )

        # 2. /tmp_wbshd/readcheck_result.txt must NOT exist; clear ONLY if SHA-256 matches saved result.
        there, old_res = R.read(camera, R.RESULT_PATH)
        if there and expected_clear_sha and c.sha256(old_res) == expected_clear_sha:
            session.say("Earlier saved result found on RAM disk (%d bytes); clearing before step %d." % (len(old_res), step.index))
            del_st, still_there = _delete_verified_result(
                session, camera, guard, expected_clear_sha, old_res, "pre-step %d" % step.index
            )
            step_rec["pre_clear_status"] = del_st
            there = still_there
        if there:
            raise c.StopTest(
                "Step %d: a result file is already on the RAM disk and does not match an already-saved result. "
                "Cold boot the camera first. Nothing was written." % step.index
            )

        # 3. /tmp/jbusy must NOT exist.
        if R.read(camera, R.BUSY_PATH)[0]:
            raise c.StopTest("Step %d: /tmp/jbusy exists; camera would refuse the library. Nothing was written." % step.index)

        # 4. Arm step, upload libJit.so, ALWAYS remove libJit.so & confirm ST_NO_FILE in finally.
        write_status = None
        write_exc = None
        guard.arm_step(idx)
        try:
            R.select(camera, R.LIB_PATH)
            session.say("Step %d/%d: sending %s (%d bytes)" % (step.index, step.total, step.path.name, len(lib_bytes)))
            step_rec["library_sent"] = True
            try:
                write_status, _ = R.command(camera, R.SET_FILE2, b"\x00\xff\x00" + lib_bytes)
            except c.GuardRefusal:
                step_rec["library_sent"] = False
                raise
            except Exception as exc:
                write_exc = exc
                step_rec["write_error"] = "%s: %s" % (type(exc).__name__, exc)
                session.say("Step %d write got no answer (%s)" % (step.index, step_rec["write_error"]))
        finally:
            guard.disarm_step()
            if step_rec["library_sent"]:
                R.remove(session, camera, step_rec)
                out["library_on_camera_after"] = step_rec.get("library_on_camera_after")

        step_rec["write_status"] = write_status
        if write_exc is not None:
            raise c.StopTest("Step %d write got no answer (%s); result not read." % (step.index, step_rec["write_error"]))
        session.say("Step %d write and load: status %s" % (step.index, R.text(write_status)))

        if step_rec.get("library_on_camera_after") is not False:
            raise c.StopTest("STOPPED at step %d: /tmp/jiritsu/libJit.so was NOT confirmed removed." % step.index)

        # 5. Fetch result file from RAM disk.
        there, result_bytes = R.read(camera, R.RESULT_PATH)
        step_rec["result_found"] = there
        if not there:
            if step.index >= 2:
                raise c.StopTest(
                    "Step %d (%s) produced no result file (write status: %s; possible dlclose caching). "
                    "/tmp/jiritsu/libJit.so was removed and confirmed gone. Cold boot (battery out 10s)."
                    % (step.index, step.design, R.text(write_status))
                )
            raise c.StopTest("Step 1 (%s) produced no result file (write status: %s)." % (step.design, R.text(write_status)))

        result_name = "step%d_%s_result.txt" % (step.index, step.design)
        result_sha = session.save_bytes(result_name, result_bytes)

        # 6. Clear /tmp_wbshd/readcheck_result.txt by verified SHA-256 for next step & clean exit.
        del_st, still_there = _delete_verified_result(
            session, camera, guard, result_sha, result_bytes, "post-step %d" % step.index
        )
        step_rec["result_clear_status"] = del_st
        step_rec["result_on_camera_after"] = still_there
        expected_clear_sha = None if not still_there else result_sha

        # 7. Parse & validate result; maintain compatibility mirror for last_result_sha().
        facts = parse_step_result(step, result_bytes)
        ok, status_label, detail = evaluate_step(step, facts)
        step_rec["status"] = status_label
        step_rec["detail"] = detail
        step_rec["result"] = facts

        session.results["ct6"] = {
            "library_bytes": len(lib_bytes),
            "library_sha256": step.sha256,
            "library_sent": True,
            "write_status": write_status,
            "remove_status": step_rec.get("remove_status"),
            "library_on_camera_after": step_rec.get("library_on_camera_after"),
            "result_found": True,
            "result": {"bytes": facts["bytes"], "sha256": result_sha, "complete": facts["complete"]},
        }

        if ok and still_there and step.index < step.total:
            raise c.StopTest(
                "Step %d succeeded, but /tmp_wbshd/readcheck_result.txt could not be cleared for step %d."
                % (step.index, step.index + 1)
            )
        if not ok:
            raise c.StopTest("STOPPED at step %d (%s): %s" % (step.index, step.design, detail))

        out["steps_completed"] = step.index
        session.say("Step %d/%d (%s): %s" % (step.index, step.total, step.design, detail))


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
# Simulated Multi-Step Camera for --dry-run and --selftest
# --------------------------------------------------------------------------

def make_canned_step_result(step, outcome="done", truncated=False):
    verb = "apply" if step.action == "apply" else "put back"
    if step.kind == "p2a":
        lines = [
            "apsc60_p2a 1 mode %s" % step.action,
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
                lines.append("site 0x010fd000 length 4: already holds the target bytes")
                lines.append("NOTHING TO DO: every site already holds the target bytes")
            elif outcome == "undo_incomplete":
                lines.append("!! UNDO INCOMPLETE. Cold boot the camera (battery out) before anything else.")
            else:
                lines.append("site 0x010fd000 length 4: as expected")
                lines.append("site 0x010fd000 length 4: written and read back")
                lines.append("DONE: all 45 sites hold the target bytes (%s)" % verb)
        lines.append("buffer released")
    elif step.level in (16, 17):
        lines = [
            "readcheck 1 level %d" % step.level,
            "euid 0",
            "== file /proc/version",
            "Linux (simulated)",
            "",
            "== end 18 bytes",
            "== level %d: %s the words of %s" % (step.level, verb, step.design),
        ]
        if outcome == "refused":
            lines.append("!! guard bytes differ; nothing is written")
        elif outcome == "undo_incomplete":
            lines.append("checks passed: the bytes beside the sites are as expected")
            lines.append("!! UNDO INCOMPLETE")
        elif outcome == "nothing_to_do":
            lines.append("checks passed: the bytes beside the sites are as expected")
            lines.append("site 0x01a531c8: already holds the target bytes")
            lines.append("NOTHING TO DO: every site already holds the target bytes")
        else:
            lines.append("checks passed: the bytes beside the sites are as expected")
            lines.append("site 0x01a531c8: as expected")
            lines.append("site 0x01a531c8: written and read back")
            lines.append("DONE: every site holds the target bytes (%s)" % verb)
        lines.append("buffer released")
    elif step.level in (19, 20):
        av_entries = 180 if step.design == "ogfile2560_60p_r240_c2_v1" else 132
        lines = [
            "readcheck 1 level %d" % step.level,
            "euid 0",
            "== file /proc/version",
            "Linux (simulated)",
            "",
            "== end 18 bytes",
            "== level %d: %s the main-processor sites of %s" % (step.level, verb, step.design),
            "av-cam.bin at physical 0x401000000, %d entries; libmpr.so mapped at 0x7f00000000-0x7f00100000 r-xp, 9 entries" % av_entries,
        ]
        if outcome == "refused":
            lines.append("!! guard mismatch in av-cam.bin; nothing is written")
        elif outcome == "undo_incomplete":
            lines.append("checks passed: every guard is as in the file, every site is stock or target")
            lines.append("!! UNDO INCOMPLETE")
        elif outcome == "nothing_to_do":
            lines.append("checks passed: every guard is as in the file, every site is stock or target")
            lines.append("av site 0x38f724: already holds the target bytes")
            lines.append("NOTHING TO DO: every site already holds the target bytes")
        else:
            lines.append("checks passed: every guard is as in the file, every site is stock or target")
            lines.append("av site 0x38f724: as expected")
            lines.append("av site 0x38f724: written and read back")
            lines.append("DONE: every site holds the target bytes (%s)" % verb)
    else:
        raise ValueError("Unsupported step: %r" % (step,))

    body = ("\n".join(lines) + "\n").encode("utf-8")
    lead = b"== done, bytes written before this line: "
    full = body + lead + (b"%d\n" % (len(body) + len(lead)))
    return full[:-5] if truncated else full


def make_p2b_sim(verified_steps, files=None, fault=None, fault_step=None, outcome="done"):
    import ct1_sim

    by_sha = {step.sha256: step for step, _ in verified_steps}
    allowed_set = {(bytes(lib), step.sha256) for step, lib in verified_steps}

    def _sim_rules():
        return {
            (c.PFUNC_ADJUST, 0x1001, R.SET_FILE_PATH): ("SET_FILE_PATH", lambda p: p[:1] == b"\x00" and p[1:] in R.PATHS),
            (c.PFUNC_ADJUST, 0x1001, R.GET_FILE2): ("GET_FILE2", lambda p: p == b"\x00\xff\x00"),
            (c.PFUNC_ADJUST, 0x1001, R.SET_FILE2): ("SET_FILE2", lambda p: p[:3] == b"\x00\xff\x00" and (p[3:], c.sha256(p[3:])) in allowed_set),
            (c.PFUNC_ADJUST, 0x1001, R.FILE_DELETE2): ("FILE_DELETE2", lambda p: p == b"\x00\xff"),
        }

    class P2BSim(ct1_sim.SimDriver):
        def __init__(self):
            super().__init__("senser", rules=_sim_rules())
            self.fs = {"/usr/share/pmbp/DeviceInfo.xml": (c.EXTRACTED / R.KNOWN_FILE).read_bytes()}
            self.fs.update(files or {})
            self.loads = []
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
                step_num = len(self.loads) + 1
                if fault == "dead" and step_num == fault_step:
                    self.dead = True
                    self.fs[path] = body[7:]
                    raise ct1_sim.SimUsbError("the simulated camera died during step %d write" % step_num)
                self.fs[path] = body[7:]
                if path != R.LIB_PATH.decode():
                    return 0, st(R.ST_OK)
                sha = c.sha256(body[7:])
                self.loads.append((step_num, sha, len(body[7:])))
                if fault == "load" and step_num == fault_step:
                    return 0, st(R.ST_LOAD_FAILED)
                if fault == "dlclose_no_result" and step_num == fault_step:
                    return 0, st(R.ST_OK)
                if "/tmp_wbshd/readcheck_result.txt" not in self.fs:
                    step_spec = by_sha[sha]
                    step_outcome = fault if (fault in ("refused", "undo_incomplete") and step_num == fault_step) else outcome
                    self.fs["/tmp_wbshd/readcheck_result.txt"] = make_canned_step_result(
                        step_spec, outcome=step_outcome, truncated=(fault == "truncated" and step_num == fault_step)
                    )
                return 0, st(R.ST_OK)
            if name == "FILE_DELETE2":
                if fault == "sticky_lib" and path == R.LIB_PATH.decode() and len(self.loads) == fault_step:
                    return 0, st(R.ST_REMOVE_FAILED)
                if path not in self.fs:
                    return 0, st(R.ST_REMOVE_FAILED)
                del self.fs[path]
                return 0, st(R.ST_OK)
            raise AssertionError(name)

    return P2BSim()


def simulate_p2b(action, verified_steps, files=None, fault=None, fault_step=None,
                 outcome="done", initial_clear_sha=None, remove_only=False):
    c.use_pmca()
    session = c.Session("simulated_p2b_" + action, write=False, test="CT6")
    sim = make_p2b_sim(verified_steps, files=files, fault=fault, fault_step=fault_step, outcome=outcome)
    pinned = [(lib, st.sha256) for st, lib in verified_steps]
    stopped = None
    body_fn = (
        body_remove_only
        if remove_only
        else (lambda s, cam, g: body_p2b(s, cam, g, action, verified_steps, initial_clear_sha=initial_clear_sha))
    )
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            service_session_p2b(session, sim, pinned, body_fn)
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

    # 1. Full 5-step default (V60 r240) apply in ONE session
    check(len(ALL_KNOWN_SHAS) == 16, "ALL_KNOWN_SHAS tracks all 16 pinned Phase 2B hashes (12 active v3/r240/menu-rate + 4 legacy v1/v2 for --remove-only)")
    v_apply = load_and_verify_steps(build_steps(putback=False, menu_rate=False))
    check(len(v_apply) == 5, "Phase 2B default apply has 5 SHA-256 verified pinned libraries")
    check([s.design for s, _ in v_apply] == [
        "apsc60_p2a_v3", "outsz2560_c2_v1", "ogfile2560_60p_r240_c2_v1", "lcdfill_c2_v2", "lcdcycle_c2_v1"
    ], "Phase 2B default apply order is exact [apsc60_p2a_v3, outsz2560_c2_v1, ogfile2560_60p_r240_c2_v1, lcdfill_c2_v2, lcdcycle_c2_v1]")

    s, sim, err = simulate_p2b("apply", v_apply)
    check(err is None and not sim.unexpected and len(sim.loads) == 5, "5-step default (r240) single-session apply succeeded (5 loads, 0 unexpected)")
    check(sent(s).count("SET_FILE2") == 5 and sent(s).count("FILE_DELETE2") == 10, "5 writes + 5 lib deletes + 5 verified result deletes")
    check(
        sim.vendor_requests.count((1, 0x37FF, 0xD7AA, b"")) == 1
        and sim.vendor_requests.count((1, 0xC800, 0x2855, b"")) == 1
        and sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855),
        "START_SENSER sent once and STOP_SENSER sent once at the very end",
    )
    check(R.LIB_PATH.decode() not in sim.fs and "/tmp_wbshd/readcheck_result.txt" not in sim.fs,
          "camera clean: zero libJit.so and zero readcheck_result.txt left behind")
    check(s.results["p2b"]["steps_completed"] == 5 and s.results["p2b"]["library_on_camera_after"] is False,
          "manifest records 5/5 completed and library confirmed gone")

    # 2. Full 5-step default (r240) putback in EXACT reverse order
    v_pb = load_and_verify_steps(build_steps(putback=True, menu_rate=False))
    check([(s.design, s.level) for s, _ in v_pb] == [
        ("lcdcycle_c2_v1", 20), ("lcdfill_c2_v2", 17), ("ogfile2560_60p_r240_c2_v1", 20), ("outsz2560_c2_v1", 17), ("apsc60_p2a_v3", 0)
    ], "Phase 2B default (r240) putback runs exact reverse order (20/17/20_r240/17/p2a_v3_putback)")
    s, sim, err = simulate_p2b("putback", v_pb)
    check(err is None and not sim.unexpected and len(sim.loads) == 5 and R.LIB_PATH.decode() not in sim.fs,
          "5-step default (r240) single-session putback succeeded cleanly")

    # 2b. Full 5-step --menu-rate apply & reverse putback
    v_menu_apply = load_and_verify_steps(build_steps(putback=False, menu_rate=True))
    check([s.design for s, _ in v_menu_apply] == [
        "apsc60_p2a_v3", "outsz2560_c2_v1", "ogfile2560_60p_c2_v1", "lcdfill_c2_v2", "lcdcycle_c2_v1"
    ], "Phase 2B --menu-rate apply selects stock-menu-rate ogfile2560_60p_c2_v1 at step 3")
    s, sim, err = simulate_p2b("apply", v_menu_apply)
    check(err is None and not sim.unexpected and len(sim.loads) == 5 and R.LIB_PATH.decode() not in sim.fs,
          "5-step --menu-rate single-session apply succeeded cleanly")

    v_menu_pb = load_and_verify_steps(build_steps(putback=True, menu_rate=True))
    check([(s.design, s.level) for s, _ in v_menu_pb] == [
        ("lcdcycle_c2_v1", 20), ("lcdfill_c2_v2", 17), ("ogfile2560_60p_c2_v1", 20), ("outsz2560_c2_v1", 17), ("apsc60_p2a_v3", 0)
    ], "Phase 2B --menu-rate putback runs exact reverse order (20/17/20_menu/17/p2a_v3_putback)")
    s, sim, err = simulate_p2b("putback", v_menu_pb)
    check(err is None and not sim.unexpected and len(sim.loads) == 5 and R.LIB_PATH.decode() not in sim.fs,
          "5-step --menu-rate single-session putback succeeded cleanly")

    # NOTHING TO DO idempotency
    s, sim, err = simulate_p2b("apply", v_apply, outcome="nothing_to_do")
    check(err is None and all(st["status"] == "nothing_to_do" for st in s.results["p2b"]["steps"]),
          "NOTHING TO DO accepted cleanly on all 5 steps")

    # 3. Optional toggles: --no-cycle (4 steps) and --no-lcd32 (3 steps)
    check(len(build_steps(no_cycle=True)) == 4 and len(build_steps(no_lcd32=True)) == 3,
          "--no-cycle gives 4 steps, --no-lcd32 gives 3 steps")

    # 4. Guard refusal checks: all 15 forbidden packets + wrong-step swap for BOTH r240 & menu-rate (apply & putback)
    check(len(check_guard_refusals(v_apply)) == 16, "MultiStepGuard(r240 apply) refuses all 15 forbidden packets + wrong-step library")
    check(len(check_guard_refusals(v_pb)) == 16, "MultiStepGuard(r240 putback) refuses all 15 forbidden packets + wrong-step library")
    check(len(check_guard_refusals(v_menu_apply)) == 16, "MultiStepGuard(menu-rate apply) refuses all 15 forbidden packets + wrong-step library")
    check(len(check_guard_refusals(v_menu_pb)) == 16, "MultiStepGuard(menu-rate putback) refuses all 15 forbidden packets + wrong-step library")

    # 5. Stateful guard checks
    def stateful(fn):
        c.use_pmca()
        sess = c.Session("stateful", write=False, test="CT6")
        sim_drv = make_p2b_sim(v_apply)
        pinned = [(lib, st.sha256) for st, lib in v_apply]
        caught = None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                service_session_p2b(sess, sim_drv, pinned, fn)
            except c.StopTest as exc:
                caught = exc
        return caught, sess

    caught, sess = stateful(lambda s_, cam, g: (g.arm_step(0), R.select(cam, R.RESULT_PATH), R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + v_apply[0][1])))
    check(isinstance(caught, c.GuardRefusal) and "SET_FILE2" not in sent(sess), "guard refuses SET_FILE2 on non-libJit path")

    caught, sess = stateful(lambda s_, cam, g: (g.arm_step(0), R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + v_apply[0][1])))
    check(isinstance(caught, c.GuardRefusal) and "SET_FILE2" not in sent(sess), "guard refuses SET_FILE2 with no path selected")

    caught, sess = stateful(lambda s_, cam, g: (
        g.arm_step(0),
        R.select(cam, R.LIB_PATH), R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + v_apply[0][1]),
        R.select(cam, R.LIB_PATH), R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + v_apply[0][1]),
    ))
    check(isinstance(caught, c.GuardRefusal) and sent(sess).count("SET_FILE2") == 1, "guard refuses second SET_FILE2 in same step")

    caught, sess = stateful(lambda s_, cam, g: (R.select(cam, R.RESULT_PATH), R.command(cam, R.FILE_DELETE2, b"\x00\xff")))
    check(isinstance(caught, c.GuardRefusal) and "FILE_DELETE2" not in sent(sess), "guard refuses unverified FILE_DELETE2 on result path")

    caught, sess = stateful(lambda s_, cam, g: (R.select(cam, R.BUSY_PATH), R.command(cam, R.FILE_DELETE2, b"\x00\xff")))
    check(isinstance(caught, c.GuardRefusal) and "FILE_DELETE2" not in sent(sess), "guard refuses FILE_DELETE2 on /tmp/jbusy")

    caught, sess = stateful(lambda s_, cam, g: g.arm_step(1))
    check(isinstance(caught, c.GuardRefusal), "guard enforces monotonic step cursor (refuses skipping step 0)")

    # 6. Pre-check stops & verified --clear-result
    for label, init_files in (
        ("existing unknown libJit.so", {"/tmp/jiritsu/libJit.so": b"unknown"}),
        ("existing Phase 2B libJit.so", {"/tmp/jiritsu/libJit.so": v_apply[2][1]}),
        ("existing unknown result", {"/tmp_wbshd/readcheck_result.txt": b"foreign"}),
        ("/tmp/jbusy present", {"/tmp/jbusy": b""}),
        ("wrong DeviceInfo.xml", {"/usr/share/pmbp/DeviceInfo.xml": b"<wrong/>"}),
    ):
        s, sim, err = simulate_p2b("apply", v_apply, files=init_files)
        check(err is not None and not sim.loads and "SET_FILE2" not in sent(s) and "FILE_DELETE2" not in sent(s),
              "precheck stops safely on %s (zero writes, zero deletes)" % label)

    prior = b"earlier saved result bytes"
    s, sim, err = simulate_p2b("apply", v_apply, files={"/tmp_wbshd/readcheck_result.txt": prior}, initial_clear_sha=c.sha256(prior))
    check(err is None and len(sim.loads) == 5 and "/tmp_wbshd/readcheck_result.txt" not in sim.fs,
          "verified --clear-result deletes matching prior result and completes all 5 steps")

    # 7. Mid-run failure cases leaving no libJit.so behind + STOP_SENSER sent
    s, sim, err = simulate_p2b("apply", v_apply, fault="dlclose_no_result", fault_step=2)
    check(err is not None and "dlclose" in str(err) and R.LIB_PATH.decode() not in sim.fs
          and s.results["p2b"]["steps_completed"] == 1 and sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855),
          "step-2 dlclose no-result detected cleanly with libJit.so removed & STOP_SENSER sent")

    s, sim, err = simulate_p2b("apply", v_apply, fault="refused", fault_step=3)
    check(err is not None and R.LIB_PATH.decode() not in sim.fs and "/tmp_wbshd/readcheck_result.txt" not in sim.fs
          and s.results["p2b"]["steps_completed"] == 2 and sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855),
          "mid-run step-3 refusal leaves camera clean and sends STOP_SENSER")

    s, sim, err = simulate_p2b("apply", v_apply, fault="load", fault_step=3)
    check(err is not None and R.LIB_PATH.decode() not in sim.fs and sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855),
          "mid-run ST_LOAD_FAILED still removes libJit.so and sends STOP_SENSER")

    s, sim, err = simulate_p2b("apply", v_apply, fault="dead", fault_step=3)
    check(err is not None and sim.vendor_requests[-1][:3] == (1, 0xC800, 0x2855),
          "mid-run USB disconnect still sends STOP_SENSER in finally")

    s, sim, err = simulate_p2b("apply", v_apply, fault="sticky_lib", fault_step=2)
    check(err is not None and len(sim.loads) == 2 and s.results["p2b"]["library_on_camera_after"] is True,
          "sticky libJit.so removal failure halts immediately (never proceeds to step 3)")

    s, sim, err = simulate_p2b("apply", v_apply, fault="undo_incomplete", fault_step=3)
    check(err is not None and "UNDO INCOMPLETE" in str(err) and R.LIB_PATH.decode() not in sim.fs,
          "UNDO INCOMPLETE detected loudly with libJit.so removed")

    # 8. --remove-only tests (recognizes BOTH r240 and menu-rate pinned libs; refuses unknown)
    s, sim, err = simulate_p2b("apply", v_apply, files={"/tmp/jiritsu/libJit.so": v_apply[2][1]}, remove_only=True)
    check(err is None and R.LIB_PATH.decode() not in sim.fs and "SET_FILE2" not in sent(s),
          "--remove-only removes pinned r240 Phase 2B libJit.so without writing")

    s, sim, err = simulate_p2b("apply", v_apply, files={"/tmp/jiritsu/libJit.so": v_menu_apply[2][1]}, remove_only=True)
    check(err is None and R.LIB_PATH.decode() not in sim.fs and "SET_FILE2" not in sent(s),
          "--remove-only removes pinned menu-rate Phase 2B libJit.so without writing")

    s, sim, err = simulate_p2b("apply", v_apply, files={"/tmp/jiritsu/libJit.so": b"foreign"}, remove_only=True)
    check(err is not None and "/tmp/jiritsu/libJit.so" in sim.fs and "FILE_DELETE2" not in sent(s),
          "--remove-only refuses to delete unknown libJit.so")

    s, sim, err = simulate_p2b("apply", v_apply, remove_only=True)
    check(err is None and "FILE_DELETE2" not in sent(s),
          "--remove-only is a safe read-only no-op when no libJit.so is present")

    # Cross-rate guard check: r240 guard on step 3 must refuse menu-rate .so bytes
    c.use_pmca()
    sess_xr = c.Session("cross_rate", write=False, test="CT6")
    sim_xr = make_p2b_sim(v_apply)
    caught_xr = None
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            service_session_p2b(
                sess_xr, sim_xr, [(lib, st.sha256) for st, lib in v_apply],
                lambda s_, cam, g: (
                    g.arm_step(0), g.disarm_step(),
                    g.arm_step(1), g.disarm_step(),
                    g.arm_step(2), R.select(cam, R.LIB_PATH),
                    R.command(cam, R.SET_FILE2, b"\x00\xff\x00" + v_menu_apply[2][1]),
                ),
            )
        except c.StopTest as exc:
            caught_xr = exc
    check(isinstance(caught_xr, c.GuardRefusal) and "SET_FILE2" not in sent(sess_xr),
          "MultiStepGuard(r240) refuses menu-rate Step 3 .so bytes on step 3")

    return passed


def run_dry_run(action, verified_steps):
    refused = check_guard_refusals(verified_steps)
    s, sim, err = simulate_p2b(action, verified_steps)
    if sim.unexpected or err is not None:
        raise SystemExit("Dry run failed: unexpected=%r err=%r" % (sim.unexpected, err))
    print("Phase 2B (%s): %d pinned libraries verified (single Service Mode session):" % (action, len(verified_steps)))
    for step, lib in verified_steps:
        print("  Step %d/%d: %-22s  %5d B  SHA-256 %s  (%s)" % (
            step.index, step.total, step.design, len(lib), step.sha256[:16], step.what))
    print("Simulated 054C:0336: %d loads, %d SET_FILE2, %d FILE_DELETE2, 1 STOP_SENSER" % (
        len(sim.loads),
        sum(1 for e in s.events if e["kind"] == "service_out" and e["name"] == "SET_FILE2"),
        sum(1 for e in s.events if e["kind"] == "service_out" and e["name"] == "FILE_DELETE2"),
    ))
    print("Camera clean after dry run: libJit.so gone=%s, readcheck_result.txt gone=%s" % (
        R.LIB_PATH.decode() not in sim.fs, "/tmp_wbshd/readcheck_result.txt" not in sim.fs))
    print("Guard refusals verified (%d): %s" % (len(refused), ", ".join(refused[:5]) + ", ..."))
    print("Dry run passed. Nothing was sent to any real USB device.")


def run_live(args, action, verified_steps):
    uid, gid = resolve_target_owner(args.owner_uid, args.owner_gid)
    if args.firmware != FIRMWARE:
        raise SystemExit(
            "A live Phase 2B run requires --firmware %s (got %r). Nothing was sent." % (FIRMWARE, args.firmware)
        )
    if not args.yes:
        prompt = (
            "Type YES to remove a Phase 2B libJit.so if present on A7C II (fw 2.01): "
            if args.remove_only
            else "Type YES to run Phase 2B (%s, %d steps, RAM-only) on A7C II (fw 2.01): " % (action, len(verified_steps))
        )
        if input(prompt).strip() != "YES":
            raise SystemExit("Not confirmed. Nothing was sent.")

    # Pre-flight offline safety checks before touching real hardware
    check_guard_refusals(verified_steps)
    _s, _sim, _err = simulate_p2b(action, verified_steps, remove_only=args.remove_only)
    if _err is not None or _sim.unexpected:
        raise SystemExit("Pre-flight simulated check failed: %r" % (_err,))

    clear_sha = args.clear_result or OG.last_result_sha()
    stage = "apsc60_p2b_remove_only" if args.remove_only else ("apsc60_p2b_" + action)
    pinned = [(lib, st.sha256) for st, lib in verified_steps]
    body_fn = (
        body_remove_only
        if args.remove_only
        else (lambda s, cam, g: body_p2b(s, cam, g, action, verified_steps, initial_clear_sha=clear_sha))
    )

    session_dir = None
    try:
        with c.Session(stage, write=True, root=PRIVATE_DIR, test="CT6") as session:
            session_dir = session.dir
            session.results.update(
                firmware_shown_on_camera=args.firmware,
                model=MODEL,
                action="remove_only" if args.remove_only else action,
                rate_profile="menu_rate" if args.menu_rate else "v60_r240",
                oneshot_phase2b=True,
            )
            inner = service.connect(session)
            try:
                service_session_p2b(session, inner, pinned, body_fn)
            finally:
                time.sleep(2.5)
                c.print_pnp(session, "after the stop request")
                out = session.results.get("p2b", {})
                print("Steps completed: %s/%s. libJit.so on camera now: %s." % (
                    out.get("steps_completed", 0),
                    out.get("steps_total", 0),
                    {False: "no (confirmed)", True: "YES", None: "unknown"}[out.get("library_on_camera_after")],
                ))
    finally:
        if session_dir is not None:
            chown_tree(session_dir, uid, gid)

    if session_dir is not None:
        man = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
        if man.get("status") != "completed":
            raise SystemExit(1)
        print()
        print("=" * 78)
        if args.remove_only:
            print("REMOVE-ONLY COMPLETE. Session folder: %s" % session_dir)
        elif action == "putback":
            print("ALL %d PHASE 2B STEPS PUT BACK: camera is stock again." % len(verified_steps))
            print("Session folder: %s" % session_dir)
        else:
            print("ALL %d PHASE 2B STEPS APPLIED IN ONE SESSION!" % len(verified_steps))
            print("Session folder: %s" % session_dir)
            print("=" * 78)
            print("Now:")
            print("  1. Unplug the USB cable (do NOT power the camera off).")
            print("  2. Switch to MOVIE -> APS-C / Super 35 ON -> XAVC HS 4K or XAVC S 4K at 60p ONLY.")
            print("     (Do NOT dial 24p/30p/50p in S35, Full Frame 4K, HD, or S&Q while applied.)")
            if not args.no_lcd32:
                print("  3. Rear screen should fill 3:2 with focus frames.")
            if not args.no_lcd32 and not args.no_cycle:
                print("  4. Press C2 in Movie mode to cycle de-squeeze: 3:2 -> 2:1 (1.33x) -> 2.4:1 (1.6x) -> 3:2.")
            print("  5. Record a 3-second test clip (expected: 3840x2560 @ 59.94p).")
            if not args.menu_rate:
                print("     Bitrate boost active (1.20x): 200M->240M, 150M->180M, 100M->120M, 75M->90M, 45M->54M (use V60/V90 card).")
            print("  6. When done: " + COLD_BOOT)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    act = ap.add_mutually_exclusive_group()
    act.add_argument("--apply", action="store_true", help="Apply all Phase 2B APS-C 4K 60p Open Gate steps (default)")
    act.add_argument("--putback", action="store_true", help="Put back all Phase 2B steps in exact reverse order")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Simulate single-session flow; sends nothing")
    mode.add_argument("--selftest", action="store_true", help="Run full offline safety & guard self-test suite; sends nothing")
    mode.add_argument("--remove-only", action="store_true", help="Safely remove a leftover Phase 2B libJit.so without writing")
    ap.add_argument("--menu-rate", action="store_true",
                    help="Use stock menu bitrates (ogfile2560_60p_c2_v1) instead of the default V60 1.20x bitrate boost (up to 240 Mbps)")
    ap.add_argument("--no-lcd32", action="store_true", help="Omit Step 4 (3:2 rear screen) and Step 5 (C2 cycle)")
    ap.add_argument("--no-cycle", action="store_true", help="Omit Step 5 (C2 de-squeeze preview cycle)")
    ap.add_argument("--firmware", default=None, help="Firmware version shown on camera (must be %s)" % FIRMWARE)
    ap.add_argument("--yes", action="store_true", help="Skip interactive confirmation prompt")
    ap.add_argument("--clear-result", default=None, metavar="SHA256",
                    help="Optional SHA-256 of an earlier saved result file to clear before step 1")
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
        print("All %d Phase 2B self-tests passed. Nothing was sent to any device." % len(checks))
        return 0
    steps = build_steps(putback=args.putback, no_lcd32=args.no_lcd32, no_cycle=args.no_cycle, menu_rate=args.menu_rate)
    verified = load_and_verify_steps(steps)
    if args.dry_run:
        run_dry_run(action, verified)
        return 0
    run_live(args, action, verified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
