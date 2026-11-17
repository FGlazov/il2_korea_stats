@echo off
rem Runs il2ks as Administrator (the data folder is private to administrators): used by the Start menu shortcuts.
rem   il2ks-admin.cmd doctor      runs "il2ks doctor" and waits for a key
rem   il2ks-admin.cmd shell       opens a command prompt where "il2ks" works
rem   il2ks-admin.cmd restart     stops and starts the il2ks service (after a change in il2ks.toml) and waits for a key
rem   il2ks-admin.cmd stop        stops the service and keeps it stopped after a reboot (start type "manual")
rem   il2ks-admin.cmd start       starts the service and lets it start with Windows again (delayed automatic start)
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
if /i "%~1"=="restart" goto :restart
if /i "%~1"=="start" goto :start
if /i "%~1"=="stop" goto :stop
call "%~dp0il2ks.cmd" %*
echo.
pause
exit /b

:shell
echo il2ks command prompt. Try: il2ks doctor   or   il2ks --help
cmd /k
exit /b

:restart
echo Restarting the il2ks service (changes in il2ks.toml take effect now)...
net stop il2ks
goto :start_service

:start
echo Starting the il2ks service...
goto :start_service

:start_service
rem Back to "start with Windows", which "stop" below switches off.
sc.exe config il2ks start= delayed-auto >nul
net start il2ks
if errorlevel 1 goto :start_failed
echo The service is running. The site needs 10 to 30 seconds before it answers.
echo.
pause
exit /b

:start_failed
echo.
echo The service did not start. Run "Run doctor (check the setup)" from the Start menu: it says what is wrong.
echo.
pause
exit /b 1

:stop
echo Stopping the il2ks service...
rem "Manual" start: Windows does not start it at the next boot, and an il2ks upgrade leaves it stopped too.
sc.exe config il2ks start= demand >nul
net stop il2ks
echo.
echo The site is off and stays off, also after a reboot, until you use "Start il2ks service" (or "Restart").
echo.
pause
exit /b
