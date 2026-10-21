@echo off
rem CAELAB Workbench - Windows launcher.
rem First run creates .venv in the repo root and installs the GUI packages.
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
    echo Installing packages (PySide6, numpy, vtk) - first run only...
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install PySide6 numpy vtk
    if errorlevel 1 goto :fail
)

.venv\Scripts\python -m apps.workbench_qt
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo Failed to start. Check that Python 3.11+ is installed and on PATH.
pause
exit /b 1
