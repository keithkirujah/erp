@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build NextGen ERP Desktop

set "VENV=%~dp0.venv"
set "PYEXE=%VENV%\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo Run run_windows.bat once to initialize the isolated ERP environment.
    pause
    exit /b 1
)

echo Installing build-only tools in the isolated ERP environment...
"%PYEXE%" -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo ERROR: Could not install PyInstaller.
    pause
    exit /b 1
)

echo.
echo Building one-folder Windows distribution...
"%PYEXE%" -m PyInstaller --clean --noconfirm --windowed --onedir --name "NextGen ERP" main.py
if errorlevel 1 (
    echo.
    echo BUILD FAILED. Review the PyInstaller output above.
    pause
    exit /b 1
)

echo.
echo BUILD SUCCEEDED.
echo Output: dist\NextGen ERP\
echo Copy the complete output folder. Do not copy only the EXE.
pause
endlocal
