@echo off
rem Sony A7C II (firmware 2.01): put the 4K state back to stock step by step (press C2 in movie mode until the screen is at 3:2 first; a cold boot does the same).
rem UNTESTED ON AN A7C II. Writes TEMPORARY settings into the camera's memory. A cold boot (battery out for 10 seconds) returns it to stock.
rem Asks whether the camera is set to NTSC (24p) or PAL (25p). Extra options are passed on, e.g.  --ntsc  --pal  --dry-run  --selftest  --no-cycle  --no-lcd32  --rate100  --no-meta
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing: run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -I -B "tools\camera_tests\CT6_memory_read_check\open_gate_apply_c2.py" --putback %*
echo.
echo The sessions are in windows\camera_private (newest folders). Zip them and send them back with what the camera did.
pause
