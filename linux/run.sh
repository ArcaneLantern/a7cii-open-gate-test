#!/bin/bash
# Shared by the .sh launchers: runs the Windows kit's own tools (open_gate_apply_c2.py) with this
# folder's Python.
#   run.sh apply <open_gate_apply_c2.py arguments>
# Linux keeps its own usb-storage driver on the camera's memory card, and both unmounting the card
# and taking that driver over need root rights, so a live run goes through sudo. --dry-run and
# --selftest send nothing and run without it.
cd "$(dirname "$0")" || exit 1
PY="$PWD/.venv/bin/python"
TOOLS="$PWD/../windows/tools/camera_tests/CT6_memory_read_check"
PRIVATE="$PWD/../windows/camera_private"
close() { echo; read -r -p "Press Return to close. "; exit "$1"; }
case "$1" in
  apply) SCRIPT="$TOOLS/open_gate_apply_c2.py" ;;
  *) echo "run.sh: first argument must be apply"; close 2 ;;
esac
shift
if [ ! -x "$PY" ]; then
  echo "Python environment missing: run setup.sh first."
  close 1
fi
case " $* " in
  *" --dry-run "*|*" --selftest "*) "$PY" -I -B "$SCRIPT" "$@"; close $? ;;
esac
echo "Linux asks for your password: unmounting the camera's memory card and taking its usb-storage"
echo "driver over need admin rights."
# The sessions are saved as root; the same root shell gives them back to you afterwards, so a run
# longer than sudo's 5-minute grace period doesn't ask for the password a second time (as on
# macOS). `trap : INT` keeps that shell alive through Ctrl+C (the child still stops), so the
# sessions are given back then too.
sudo /bin/bash -c 'trap : INT; private=$1; shift; "$@"; status=$?
  [ -d "$private" ] && chown -R "$SUDO_UID:$SUDO_GID" "$private"; exit $status' run "$PRIVATE" "$PY" -I -B "$SCRIPT" "$@"
close $?