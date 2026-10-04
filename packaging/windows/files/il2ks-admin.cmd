@echo off
rem Runs il2ks as Administrator (the data folder is private to administrators): used by the Start menu shortcuts.
rem   il2ks-admin.cmd doctor      runs "il2ks doctor" and waits for a key
rem   il2ks-admin.cmd shell       opens a command prompt where "il2ks" works
rem
rem Quoting: no parenthesised blocks (a ")" in a folder name such as "Program Files (x86)" would end them early), and
rem nothing is pasted into a PowerShell command line. The folder of this file and the arguments travel in environment
rem variables, which PowerShell reads as data, so spaces, quotes and apostrophes in them cannot break the command.
net session >nul 2>&1
if not errorlevel 1 goto :elevated
set "IL2KS_ADMIN_SELF=%~f0"
set "IL2KS_ADMIN_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = $env:IL2KS_ADMIN_ARGS; if ($a) { Start-Process -FilePath $env:IL2KS_ADMIN_SELF -ArgumentList $a -Verb RunAs } else { Start-Process -FilePath $env:IL2KS_ADMIN_SELF -Verb RunAs }"
exit /b

:elevated
set "PATH=%~dp0;%PATH%"
cd /d "%ProgramData%\il2ks"
if /i "%~1"=="shell" goto :shell
call "%~dp0il2ks.cmd" %*
echo.
pause
exit /b

:shell
echo il2ks command prompt. Try: il2ks doctor   or   il2ks --help
cmd /k
exit /b
