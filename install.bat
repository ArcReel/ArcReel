@echo off
chcp 65001 >nul 2>&1
title ArcReel - Universal Installer for Windows

echo.
echo ╔════════════════════════════════════════════════╗
echo ║     🎬 ArcReel — AI Video Creation Platform    ║
echo ║     Universal Installer v1.0 (Windows)         ║
echo ╚════════════════════════════════════════════════╝
echo.

REM ─── Check if PowerShell is available ──────────────────────────────────────
where powershell >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Dang chay PowerShell installer / Running PowerShell installer...
    powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
    goto :end
)

where pwsh >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Dang chay PowerShell Core installer / Running PowerShell Core installer...
    pwsh -ExecutionPolicy Bypass -File "%~dp0install.ps1"
    goto :end
)

REM ─── Fallback: Check for WSL ───────────────────────────────────────────────
where wsl >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] PowerShell khong kha dung, su dung WSL...
    echo [INFO] PowerShell not available, using WSL...
    wsl bash -c "cd '%~dp0' && bash install.sh"
    goto :end
)

echo.
echo [ERROR] Khong tim thay PowerShell hoac WSL!
echo [ERROR] Cannot find PowerShell or WSL!
echo.
echo Vui long cai dat mot trong cac phan mem sau:
echo Please install one of the following:
echo   1. Windows PowerShell (co san tren Windows 10+)
echo   2. PowerShell Core: https://github.com/PowerShell/PowerShell
echo   3. WSL: wsl --install
echo.

:end
pause
