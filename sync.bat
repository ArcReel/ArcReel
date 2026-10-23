@echo off
rem ============================================================
rem  Sync ArcReel code: main worktree -> current worktree
rem  This is a launcher only. All logic lives in sync.ps1
rem  (PowerShell handles UTF-8 Chinese output correctly,
rem   which a .bat file cannot do reliably.)
rem ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync.ps1"
if errorlevel 1 (
    echo.
    echo Sync script exited with an error. See messages above.
    pause
)
