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
# NixOS and Nix, as setup.sh: re-exec this script inside this folder's nix shell (shell.nix),
# which Python needs to find libusb (no ldconfig cache there). IN_NIX_SHELL stops a second entry.
if [ -z "$IN_NIX_SHELL" ] && [ -e /nix/store ] && command -v nix-shell >/dev/null 2>&1; then
  exec nix-shell "$PWD/shell.nix" --run "$(printf '%q ' "$PWD/$(basename "$0")" "$@")"
fi
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
# sudo strips LD_LIBRARY_PATH (its env_reset), and NixOS has no ldconfig cache: without the path
# pyusb could not find libusb in the root process even inside the nix shell. `env` sets it again
# after sudo's own stripping; on other distributions the array stays empty and nothing passes.
ASROOT=(env)
[ -n "$LD_LIBRARY_PATH" ] && ASROOT+=("LD_LIBRARY_PATH=$LD_LIBRARY_PATH")
# The sessions are saved as root; the same root shell gives them back to you afterwards, so a run
# longer than sudo's 5-minute grace period doesn't ask for the password a second time (as on
# macOS). `trap : INT` keeps that shell alive through Ctrl+C (the child still stops), so the
# sessions are given back then too.
sudo "${ASROOT[@]}" /bin/bash -c 'trap : INT; private=$1; shift; "$@"; status=$?
  [ -d "$private" ] && chown -R "$SUDO_UID:$SUDO_GID" "$private"; exit $status' run "$PRIVATE" "$PY" -I -B "$SCRIPT" "$@"
close $?