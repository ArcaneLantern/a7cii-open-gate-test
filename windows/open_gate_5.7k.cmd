@echo off
rem Sony A7C II (firmware 2.01): 5.7K open gate: 5760x3840 at 300 Mbps on the 100M settings (use a fast card; --rate100 for the menu's rate), rear screen 3:2, C2 de-squeeze preview.
rem UNTESTED ON AN A7C II. Writes TEMPORARY settings into the camera's memory. A cold boot (battery out for 10 seconds) returns it to stock.
rem Asks whether the camera is set to NTSC (24p) or PAL (25p). Extra options are passed on, e.g.  --ntsc  --pal  --dry-run  --selftest  --no-cycle  --no-lcd32  --rate100
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing: run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -I -B "tools\camera_tests\CT6_memory_read_check\open_gate_apply_c2.py" --real57 %*
echo.
echo The sessions are in windows\camera_private (newest folders). Zip them and send them back with what the camera did.
pause
