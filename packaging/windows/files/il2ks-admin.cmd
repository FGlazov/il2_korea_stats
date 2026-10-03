@echo off
rem Runs il2ks as Administrator (the data folder is private to administrators): used by the Start menu shortcuts.
rem   il2ks-admin.cmd doctor      runs "il2ks doctor" and waits for a key
rem   il2ks-admin.cmd shell       opens a command prompt where "il2ks" works
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)
set "PATH=%~dp0;%PATH%"
cd /d "%ProgramData%\il2ks"
if /i "%~1"=="shell" (
    echo il2ks command prompt. Try: il2ks doctor   or   il2ks --help
    cmd /k
    exit /b
)
call "%~dp0il2ks.cmd" %*
echo.
pause
