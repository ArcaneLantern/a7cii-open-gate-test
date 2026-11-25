#!/bin/bash
# Creates linux/.venv with the two packages the launchers need (pyusb, pycryptodomex). Needs
# Python 3.9 or later, and the distribution's own libusb (pyusb loads it; no wheel carries it on
# Linux, as libusb-package does on macOS and Windows).
cd "$(dirname "$0")" || exit 1
close() { echo; read -r -p "Press Return to close. "; exit "$1"; }
# NixOS and Nix: there is no ldconfig cache, so Python cannot find libusb on its own. Re-exec
# this script inside this folder's nix shell (shell.nix: python3, libusb, the library path);
# IN_NIX_SHELL stops a second entry. Other distributions are unchanged below.
if [ -z "$IN_NIX_SHELL" ] && [ -e /nix/store ] && command -v nix-shell >/dev/null 2>&1; then
  exec nix-shell "$PWD/shell.nix" --run "$(printf '%q ' "$PWD/$(basename "$0")" "$@")"
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "Python 3.9 or later not found. Install it with your distribution's package manager, then run this again."
  close 1
fi
python3 -m venv .venv || close 1
.venv/bin/python -m pip install --upgrade pip || close 1
.venv/bin/python -m pip install -r requirements.txt || close 1
if ! .venv/bin/python -c 'import ctypes.util, sys; sys.exit(not ctypes.util.find_library("usb-1.0"))'; then
  echo "libusb was not found. Install it with your distribution's package manager (Debian/Ubuntu:"
  echo "libusb-1.0-0, Fedora: libusb1, Arch: libusb), then run this again. On NixOS this needs the"
  echo "nix shell of shell.nix (nix-shell linux/shell.nix) and something has kept it out: re-run"
  echo "linux/setup.sh on its own and read what nix-shell said."
  close 1
fi
echo
echo "Done. No driver is needed on Linux: the launchers ask for your password instead, see README.md."
close 0