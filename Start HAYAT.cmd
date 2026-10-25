@echo off
rem Double-click to start HAYAT from this folder, wherever it was copied: it uses the Python inside runtime\
rem (no install needed). The launcher re-points .venv at that Python if the folder has moved.
cd /d "%~dp0"
if exist "runtime\python\pythonw.exe" (
    start "" "runtime\python\pythonw.exe" "launch.pyw"
) else (
    rem A folder without runtime\ (a git clone): use the Python installed on this machine.
    start "" pythonw "launch.pyw"
)
