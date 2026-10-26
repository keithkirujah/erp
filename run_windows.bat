@echo off
setlocal
cd /d "%~dp0"
title NextGen ERP Desktop v7

echo ================================================
echo          NEXTGEN ERP DESKTOP v7
echo ================================================
echo.
set "PYEXE="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
  if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
  )
)
if not defined PYEXE (
  echo ERROR: Python was not found.
  echo Install Python 3.10 or newer and enable Add Python to PATH.
  pause
  exit /b 1
)
echo Checking desktop dependencies...
%PYEXE% -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo PySide6 is missing. Attempting installation...
  %PYEXE% -m pip install --upgrade pip
  %PYEXE% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo ERROR: Could not install dependencies. Check internet access and try again.
    pause
    exit /b 1
  )
)
echo Launching NextGen ERP...
%PYEXE% main.py
if errorlevel 1 (
  echo.
  echo The ERP closed with an error. Read the traceback above.
  pause
)
endlocal

