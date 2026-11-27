#!/bin/bash
# Sony A7C II (firmware 2.01): Phase 2B — One-Touch APS-C 3:2 Open Gate 4K 60p (3840x2560 @ 59.94p NTSC).
# STOCK / OLD MENU BITRATES (ogfile2560_60p_c2_v1): keeps stock camera menu bitrates
# (200M, 150M, 140M, 100M, 75M, 45M — V30-friendly; use OpenGate_APSC60.command for V60 1.20x boost).
# Runs all 5 pinned RAM-only steps inside ONE Service Mode session (~5 seconds, zero USB replugs).
# TEMPORARY: writes to volatile RAM only (nothing to flash or settings).
# A 10-second battery pull (cold boot) or --putback reverts 100% to stock.
# Extra options:  --putback  --no-cycle  --no-lcd32  --remove-only  --dry-run  --selftest
cd "$(dirname "$0")" || exit 1
PY="$PWD/.venv/bin/python"
SCRIPT="$PWD/apsc60_phase2b/run_apsc60_p2b.py"
PRIVATE="$PWD/../windows/camera_private"
close() { echo; read -r -p "Press Return to close. "; exit "$1"; }

if [ ! -x "$PY" ]; then
  echo "Python environment missing: run macos/setup.command first."
  close 1
fi
if [ ! -f "$SCRIPT" ]; then
  echo "Phase 2B runner missing: $SCRIPT"
  close 1
fi

case " $* " in
  *" --dry-run "*|*" --selftest "*|*" --help "*|*" -h "*)
    "$PY" -I -B "$SCRIPT" --menu-rate "$@"
    close $?
    ;;
esac

echo "=============================================================================="
echo "  Sony A7C II (fw 2.01) — APS-C 3:2 Open Gate 4K 60p (Stock Menu Bitrates)"
echo "=============================================================================="
echo
echo "TEMPORARY RAM-ONLY PATCH: writes NOTHING to flash or camera settings."
echo "Uses the ORIGINAL / STOCK menu bitrates (200M, 150M, 100M, 75M, 45M — V30-friendly)."
echo "For the V60 1.20x boosted bitrates (200M->240M), use macos/OpenGate_APSC60.command."
echo "A 10-second battery pull (cold boot) reverts the camera 100% to stock."
echo
echo "Before continuing:"
echo "  1. Cold boot the camera: power off, remove battery for 10 seconds, reinsert."
echo "  2. Turn it on with the Still/Movie/S&Q switch on STILLS (not Movie)."
echo "  3. Connect the USB cable (USB Connection Mode: Mass Storage)."
echo
echo "After this script finishes (~5 seconds, zero USB replugs):"
echo "  - Unplug the USB cable (do NOT power off)."
echo "  - Switch to MOVIE -> APS-C / Super 35 ON -> XAVC HS 4K or XAVC S 4K at 60p ONLY."
echo "    (Do NOT dial 24p/30p/50p in S35, Full Frame 4K, HD, or S&Q while applied.)"
echo "  - Check the 3:2 rear screen with focus frames + press C2 to cycle de-squeeze."
echo "  - Record a short 3-second test clip (expected: 3840x2560 @ 59.94p at stock menu bitrate)."
echo "  - When done: power off + remove battery for 10s (or run with --putback)."
echo
read -r -p "Press Return once the camera is connected in Mass Storage mode (Ctrl+C to cancel) ... "
echo
echo "macOS asks for your password: taking the camera over from its own USB driver needs admin rights."
sudo /bin/bash -c 'trap : INT; private=$1; shift; "$@"; status=$?
  [ -d "$private" ] && chown -R "$SUDO_UID:$SUDO_GID" "$private"; exit $status' run "$PRIVATE" "$PY" -I -B "$SCRIPT" --menu-rate --firmware 2.01 --yes "$@"
close $?
