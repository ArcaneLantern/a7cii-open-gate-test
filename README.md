# A7C II open gate: field test kit (UNTESTED)

For the Sony A7C II (ILCE-7CM2) on firmware **2.01**. A port of the A7 IV open-gate kit (a7iv-open-gate-test).
Writes **temporary settings** into the camera's memory **only**; a cold boot (battery out for 10 seconds) returns it to stock.

> **Nothing in this kit has run on an A7C II yet.** Every step is the A7 IV's working step, rebuilt for the A7C II's firmware:
> the same tables found at the A7C II's addresses and checked byte for byte, and each library run against a model of the A7C II.
> Each step first checks that the camera's memory holds exactly the A7C II 2.01 stock bytes and writes nothing otherwise.
> First testers: please send the `camera_private` folders and say what the camera did.

## What it does

| | 4K | 5.7K |
|---|---|---|
| File | 3840x2560, XAVC HS 4K or XAVC S 4K | 5760x3840, XAVC HS 4K |
| Frame rate | 24p (NTSC), 25p XAVC S 4K (PAL) | 24p (NTSC), 25p XAVC S 4K (PAL) |
| Bit rate | the menu's | the 100M settings record at about 300 Mbps (use a fast card) |
| MP4 headers (tkhd, stsd, Sony's PROF box) | say 3840x2560, 3:2 | say 5760x3840, 3:2 |
| Pictures | as the camera's | I and P only (no duplicate frames), first picture shown |
| Rear screen | 3:2, whole picture, focus frames drawn | the same |
| De-squeeze preview | **C2** in movie mode: 3:2 -> 2:1 (1.33x) -> 2.4:1 (1.6x) -> 3:2 | the same |

The A7C II has no C3 key, so the preview key is **C2** (in movie mode C2 is taken over; in stills it works as before).
The file is not de-squeezed; only the preview is.

The MP4 headers state the real size, so editors (e.g. DaVinci Resolve) and players open the files at 3:2 with no ffmpeg remux, as on
the A7 IV. Sony's own XML block inside the file (NonRealTimeMeta) still says 3840x2160, 16:9: the camera's writer has no 3:2 entry for
it. Check a file with MediaInfo (Width/Height), not ffprobe, which reads the stream and always showed the real size. `--no-meta` gives
the camera's own headers (3840x2160). 5.7K files do not play back in the camera.

- `windows/open_gate_4k.cmd`, `windows/open_gate_5.7k.cmd`: apply. `windows/open_gate_putback_4k.cmd`, `..._5.7k.cmd`: put back step by step (a cold boot does the same).
- `macos/*.command`: the same four on a Mac.
- `linux/*.sh`: the same four on Linux.
- `android/A7CII-OpenGate.apk`: the phone app (USB-C to the camera), when published.

Options (add them after the launcher name in a command window): `--ntsc`, `--pal`, `--no-cycle` (C2 keeps its own function),
`--no-lcd32` (no screen steps), `--rate100` (5.7K at the menu's bit rate), `--no-meta` (camera's own MP4 headers), `--dry-run`, `--selftest` (both send nothing).

## Before you start

1. Firmware 2.01 (Menu > Setup > Version).
2. Set File Format (XAVC HS 4K or XAVC S 4K), 24p (NTSC) or 25p with XAVC S 4K (PAL), full frame (APS-C/Super 35 off), not S&Q.
3. Cold boot: power off, battery out for 10 seconds, battery in. Turn on with the Still/Movie/S&Q switch on **stills**.
4. Connect USB, USB Connection Mode: Mass Storage. Re-select it whenever the camera comes back between steps; do not power the camera off.

While it is applied, record only in the format and frame rate above, full frame. Other formats, HD, S&Q and APS-C are not set up.

## Windows

Once: install Python 3, run `windows/setup.cmd`, then install the libusb-win32 driver for the camera with Zadig (camera on, Mass
Storage; again once it has switched to service mode). Then run a launcher.

## macOS

Once: double-click `macos/setup.command` (Python 3.9 or later; no Homebrew, no driver). Then double-click a launcher; it asks for
your password to take the camera over from macOS's own USB driver. If macOS refuses to open a .command file, use System Settings >
Privacy & Security > Open Anyway.

## Linux

Once: install Python 3 (3.9 or later) and libusb with the distribution's package manager (Debian/Ubuntu: `python3`, `libusb-1.0-0`;
Fedora: `python3`, `libusb1`), then run `linux/setup.sh`. On NixOS nothing is installed by hand: `setup.sh`, `run.sh` and the
launchers put themselves inside the Nix shell of `linux/shell.nix` (Python 3 and libusb from nixpkgs, the library path set —
NixOS has no ldconfig cache, so Python cannot find libusb without it); `nix develop linux/` (`linux/flake.nix`, nixpkgs pinned
by `linux/flake.lock`) brings the same shell. Then run a launcher, e.g. `linux/open_gate_4k.sh` (make it executable first:
`chmod +x linux/*.sh`); it asks for your password: unmounting the camera's card and taking it over from Linux's own usb-storage
driver need admin rights. No driver is installed. The sessions are in `windows/camera_private`, as on Windows and macOS.

## APS-C 3:2 4K 59.94p (macOS, experimental)

3840x2560 at 59.94p (NTSC) from the APS-C / Super 35 area of the sensor: 4608x3072 read out, scaled to 3840x2560,
true 3:2 with no squeeze. Tested on one A7C II only (firmware 2.01, NTSC, macOS). Same rules as the rest of the kit:
memory only, each step checks the stock bytes first, a cold boot puts the camera back.

- `macos/OpenGate_APSC60.command`: all 5 steps in one service-mode session (about 5 seconds, no replugs). Bit rates
  are raised 1.20x to match stock 16:9 density (200M -> 240M, 150M -> 180M, ...): use a V60 or V90 card.
- `macos/OpenGate_APSC60_StockBitrate.command`: the same, at the menu's bit rates.
- `macos/Test_APSC60_Phase2A.command`: the sensor and scaler step on its own.

Steps: `apsc60_p2a_v3` (sensor and scaler tables for all four col-14 modes, 45 sites), `outsz2560_c2_v1`,
`ogfile2560_60p_r240_c2_v1` or `ogfile2560_60p_c2_v1` (2-core 59.94p encoder, 16 descriptors, plus 4 libmpr.so sites),
`lcdfill_c2_v2`, `lcdcycle_c2_v1`. Before running: cold boot, stills, Mass Storage. Afterwards: Movie, APS-C/Super 35 on,
XAVC HS 4K or XAVC S 4K, **60p only**. Options: `--menu-rate`, `--putback`, `--no-cycle`, `--no-lcd32`, `--remove-only`,
`--dry-run`, `--selftest`.

The `.so` files in `macos/apsc60_phase2a/` and `macos/apsc60_phase2b/` are SHA-256 pinned in their
runners. The Phase 2B libraries were built from `helper/readcheck.c` as of aa38526; rebuilding with the current
`readcheck.c` gives different hashes, so update the pins if you rebuild. The generators and `test_*_sim.py` read the
unpacked 2.01 firmware from `windows/camera_private/extracted_a7c2_201/` (not included).

## Android

USB-C to C cable and a phone with USB OTG. Camera on stills, Mass Storage, then pick 4K or 5.7K and tap Apply.

## What to report (first runs)

Does each step say DONE; does it record; the clip's size and frame rate (MediaInfo); does the picture show the whole sensor height
(compare with a photo of the same scene); the rear screen in movie mode; what C2 does; whether switching the camera off and on
removes the settings; anything odd. Zip and send the newest folders in `windows/camera_private`.
