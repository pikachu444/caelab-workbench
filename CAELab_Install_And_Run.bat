@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_and_run_windows.ps1" %*
set "CAELAB_EXIT=%ERRORLEVEL%"
if "%CAELAB_EXIT%"=="0" exit /b 0
echo CAELab Workbench setup or launch failed. See the message above.
pause
exit /b %CAELAB_EXIT%
