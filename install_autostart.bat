@echo off
setlocal EnableExtensions

REM ----------------------------------------------------------------------------
REM install_autostart.bat — register VoicemeeterAutogen with Task Scheduler.
REM Trigger: On logon, any user. Run as SYSTEM for quiet HKLM writes.
REM Launch this batch elevated: right-click -> Run as administrator.
REM ----------------------------------------------------------------------------

cd /d "%~dp0"

REM sanity: admin session
net session >nul 2>&1
if errorlevel 1 (
    echo [!] Run this batch as administrator: right-click, then "Run as administrator".
    pause
    exit /b 1
)

REM locate 32-bit pythonw.exe next to the py -3.12-32 interpreter
set "PYW="
for /f "usebackq delims=" %%i in (`py -3.12-32 -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2^>nul`) do set "PYW=%%i"

if not defined PYW (
    echo [!] 32-bit Python 3.12 not found. Install the x86 build from python.org, then retry.
    echo     Quick check: py -3.12-32 --version
    pause
    exit /b 1
)
if not exist "%PYW%" (
    echo [!] pythonw.exe not found at: "%PYW%"
    pause
    exit /b 1
)

set "SCRIPT=%~dp0voicemeeter_autostart.py"
if not exist "%SCRIPT%" (
    echo [!] voicemeeter_autostart.py is missing beside this batch file.
    pause
    exit /b 1
)

set "TASK=VoicemeeterAutogen"

REM remove stale registration if present
schtasks /delete /tn "%TASK%" /f >nul 2>&1

REM Single-line schtasks avoids caret-continuation edge cases on some locales.
schtasks /create /tn "%TASK%" /tr "\"%PYW%\" \"%SCRIPT%\"" /sc onlogon /ru SYSTEM /rl highest /f

if errorlevel 1 (
    echo [!] schtasks /create failed — see the message above.
    pause
    exit /b 1
)

echo.
echo [+] Task "%TASK%" is registered.
echo     Trigger: at user logon, any user
echo     Account: NT AUTHORITY\SYSTEM
echo     Command: "%PYW%" "%SCRIPT%"
echo     Log file: %~dp0voicemeeter_autostart.log
echo.

set "RUN="
set /p "RUN=Run smoke test now? Type Y or N: "
if /i "%RUN%"=="Y" (
    schtasks /run /tn "%TASK%"
    echo Task started. Give it a moment, then open voicemeeter_autostart.log.
)

echo.
pause
endlocal
