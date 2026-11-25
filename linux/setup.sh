#!/bin/bash
# Creates linux/.venv with the two packages the launchers need (pyusb, pycryptodomex). Needs
# Python 3.9 or later, and the distribution's own libusb (pyusb loads it; no wheel carries it on
# Linux, as libusb-package does on macOS and Windows).
cd "$(dirname "$0")" || exit 1
close() { echo; read -r -p "Press Return to close. "; exit "$1"; }
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "Python 3.9 or later not found. Install it with your distribution's package manager, then run this again."
  close 1
fi
python3 -m venv .venv || close 1
.venv/bin/python -m pip install --upgrade pip || close 1
.venv/bin/python -m pip install -r requirements.txt || close 1
if ! .venv/bin/python -c 'import ctypes.util, sys; sys.exit(not ctypes.util.find_library("usb-1.0"))'; then
  echo "libusb was not found. Install it with your distribution's package manager (Debian/Ubuntu:"
  echo "libusb-1.0-0, Fedora: libusb1, Arch: libusb), then run this again."
  close 1
fi
echo
echo "Done. No driver is needed on Linux: the launchers ask for your password instead, see README.md."
close 0