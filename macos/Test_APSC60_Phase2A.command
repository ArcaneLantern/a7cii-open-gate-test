#!/bin/bash
# Sony A7C II (firmware 2.01): Phase 2A — APS-C 3:2 4K 60p Coprocessor Sensor/Scaler Test (RAM-only).
# TEMPORARY: writes to volatile coprocessor RAM only (nothing to flash or settings).
# A 10-second battery pull (cold boot) or --putback reverts 100% to stock.
# Extra options are passed on, e.g.  --putback  --remove-only  --dry-run  --selftest
cd "$(dirname "$0")" || exit 1
PY="$PWD/.venv/bin/python"
SCRIPT="$PWD/apsc60_phase2a/run_apsc60_p2a.py"
PRIVATE="$PWD/../windows/camera_private"
close() { echo; read -r -p "Press Return to close. "; exit "$1"; }

if [ ! -x "$PY" ]; then
  echo "Python environment missing: run macos/setup.command first."
  close 1
fi
if [ ! -f "$SCRIPT" ]; then
  echo "Phase 2A runner missing: $SCRIPT"
  close 1
fi

case " $* " in
  *" --dry-run "*|*" --selftest "*|*" --help "*|*" -h "*)
    "$PY" -I -B "$SCRIPT" "$@"
    close $?
    ;;
esac

echo "=============================================================================="
echo "  Sony A7C II (fw 2.01) — Phase 2A APS-C 3:2 4K 60p Test (RAM-only)"
echo "=============================================================================="
echo
echo "TEMPORARY RAM-ONLY TEST: writes NOTHING to flash or camera settings."
echo "A 10-second battery pull (cold boot) reverts the camera 100% to stock."
echo
echo "Before continuing:"
echo "  1. Cold boot the camera: power off, remove battery for 10 seconds, reinsert."
echo "  2. Turn it on with the Still/Movie/S&Q switch on STILLS (not Movie)."
echo "  3. Connect the USB cable (USB Connection Mode: Mass Storage)."
echo
echo "After this script finishes:"
echo "  - Unplug the USB cable (do NOT power off)."
echo "  - Switch to MOVIE -> APS-C / Super 35 ON -> XAVC S 4K or XAVC HS 4K at 60p ONLY."
echo "  - IMPORTANT: Stay in S35 4K 60p during this Phase 2A test (do NOT dial 24p/30p/50p"
echo "    in S35, as col 14 is shared and other S35 frame rates are not patched in Phase 2A)."
echo "  - Check for a tall full-height 3:2 picture squeezed into the 16:9 frame at 60p."
echo "  - When done (or if the camera ever freezes): power off + remove battery for 10s"
echo "    (cold boot) or run with --putback to return 100% to stock."
echo
read -r -p "Press Return once the camera is connected in Mass Storage mode (Ctrl+C to cancel) ... "
echo
echo "macOS asks for your password: taking the camera over from its own USB driver needs admin rights."
sudo /bin/bash -c 'trap : INT; private=$1; shift; "$@"; status=$?
  [ -d "$private" ] && chown -R "$SUDO_UID:$SUDO_GID" "$private"; exit $status' run "$PRIVATE" "$PY" -I -B "$SCRIPT" --firmware 2.01 --yes "$@"
close $?
