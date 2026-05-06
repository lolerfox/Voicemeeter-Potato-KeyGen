@echo off
setlocal EnableExtensions

REM Removes VoicemeeterAutogen from Task Scheduler. Run elevated.

net session >nul 2>&1
if errorlevel 1 (
    echo [!] Run this batch as administrator: right-click, then "Run as administrator".
    pause
    exit /b 1
)

set "TASK=VoicemeeterAutogen"
schtasks /query /tn "%TASK%" >nul 2>&1
if errorlevel 1 (
    echo [-] Task "%TASK%" is not registered — nothing to delete.
    pause
    exit /b 0
)

schtasks /delete /tn "%TASK%" /f
if errorlevel 1 (
    echo [!] schtasks /delete failed.
    pause
    exit /b 1
)

echo [+] Task "%TASK%" removed.
pause
endlocal
