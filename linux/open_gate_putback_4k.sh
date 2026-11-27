#!/bin/bash
# Sony A7C II (firmware 2.01): put the 4K state back to stock step by step (press C2 in movie mode until the screen is at 3:2 first; a cold boot does the same).
# UNTESTED ON AN A7C II. Writes TEMPORARY settings into the camera's memory. A cold boot (battery out for 10 seconds) returns it to stock.
# Asks whether the camera is set to NTSC (24p) or PAL (25p). Extra options are passed on, e.g.  --ntsc  --pal  --dry-run  --selftest  --no-cycle  --no-lcd32  --rate100  --no-meta
cd "$(dirname "$0")" || exit 1
exec "$PWD/run.sh" apply --putback "$@"
