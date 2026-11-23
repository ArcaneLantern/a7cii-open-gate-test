#!/bin/bash
# Sony A7C II (firmware 2.01): put the 5.7K state back to stock step by step (press C2 in movie mode until the screen is at 3:2 first; a cold boot does the same).
# UNTESTED ON AN A7C II. WRITES into the camera's memory (not firmware, not settings). A cold boot (battery out for 10 seconds) returns it to stock.
# Extra options are passed on, e.g.  --ntsc  --pal  --dry-run  --selftest  --no-cycle  --no-lcd32  --rate100
cd "$(dirname "$0")" || exit 1
exec "$PWD/run.sh" apply --real57 --putback "$@"
