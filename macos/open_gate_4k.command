#!/bin/bash
# Sony A7C II (firmware 2.01): 4K open gate: 3840x2560 (XAVC HS 4K or XAVC S 4K, 24p; PAL: XAVC S 4K 25p), rear screen 3:2 with focus frames, C2 de-squeeze preview.
# UNTESTED ON AN A7C II. WRITES into the camera's memory (not firmware, not settings). A cold boot (battery out for 10 seconds) returns it to stock.
# Extra options are passed on, e.g.  --ntsc  --pal  --dry-run  --selftest  --no-cycle  --no-lcd32  --rate100  --no-meta
cd "$(dirname "$0")" || exit 1
exec "$PWD/run.sh" apply  "$@"
