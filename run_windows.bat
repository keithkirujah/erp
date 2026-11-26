@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title NextGen ERP Desktop

echo ================================================
echo          NEXTGEN ERP DESKTOP
echo ================================================
echo.

set "VENV=%~dp0.venv"
set "PYEXE=%VENV%\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo Creating isolated ERP Python environment...
    set "BASEPY="
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
        if not errorlevel 1 set "BASEPY=py -3"
    )
    if not defined BASEPY (
        where python >nul 2>nul
        if not errorlevel 1 (
            python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
            if not errorlevel 1 set "BASEPY=python"
        )
    )
    if not defined BASEPY (
        echo ERROR: Python 3.10 or newer was not found.
        echo Install Python and enable the Python launcher or PATH.
        pause
        exit /b 1
    )
    %BASEPY% -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: Could not create the isolated environment.
        pause
        exit /b 1
    )
)

if not exist "%PYEXE%" (
    echo ERROR: The ERP virtual environment is incomplete.
    pause
    exit /b 1
)

echo Checking ERP-only dependencies...
"%PYEXE%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies into the ERP environment only...
    "%PYEXE%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo WARNING: pip upgrade failed. Continuing with current pip.
    )
    "%PYEXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Dependency installation failed. Check internet access.
        pause
        exit /b 1
    )
)

echo Launching NextGen ERP...
"%PYEXE%" main.py
if errorlevel 1 (
    echo.
    echo The ERP closed with an error. Read the traceback above.
    pause
)
endlocal
