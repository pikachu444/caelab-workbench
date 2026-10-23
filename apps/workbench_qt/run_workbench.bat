@echo off
rem CAELAB Workbench - Windows launcher.
rem Creates .venv if needed and syncs the declared GUI/RAG packages on every run.
rem Usage: double-click this file, or run it from the repo root folder.
setlocal
cd /d "%~dp0\..\.."

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

echo Synchronizing Workbench packages...
.venv\Scripts\python.exe -m pip install -e ".[gui,rag]"
if errorlevel 1 goto :fail

.venv\Scripts\python.exe -m apps.workbench_qt
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo Failed to prepare or start Workbench. See the error output above.
pause
exit /b 1
