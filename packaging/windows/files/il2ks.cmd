@echo off
rem il2ks command line, installed by the il2ks Windows installer. Usage: il2ks doctor, il2ks backup, il2ks --help, ...
rem It runs the Python that ships with il2ks, finds the installed configuration and the bundled Caddy.
setlocal
set "IL2KS_HOME=%~dp0"
if not defined IL2KS_CONFIG set "IL2KS_CONFIG=%ProgramData%\il2ks\il2ks.toml"
set "PATH=%IL2KS_HOME%bin;%PATH%"
"%IL2KS_HOME%python\python.exe" -m il2ks %*
exit /b %ERRORLEVEL%
