#!/bin/bash
# Sony A7C II (firmware 2.01): 5.7K open gate: 5760x3840 at 300 Mbps on the 100M settings (use a fast card; --rate100 for the menu's rate), rear screen 3:2, C2 de-squeeze preview.
# UNTESTED ON AN A7C II. WRITES into the camera's memory (not firmware, not settings). A cold boot (battery out for 10 seconds) returns it to stock.
# Extra options are passed on, e.g.  --ntsc  --pal  --dry-run  --selftest  --no-cycle  --no-lcd32  --rate100
cd "$(dirname "$0")" || exit 1
exec "$PWD/run.sh" apply --real57 "$@"
