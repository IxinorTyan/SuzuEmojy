@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "SUZU_BUILD_SHELL=powershell.exe"
where /q pwsh.exe && set "SUZU_BUILD_SHELL=pwsh.exe"
%SUZU_BUILD_SHELL% -NoProfile -Sta -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" -Interactive %*
set "SUZU_BUILD_EXIT=%ERRORLEVEL%"
popd
echo.
pause
exit /b %SUZU_BUILD_EXIT%
