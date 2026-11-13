@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv\Scripts\python.exe.
  echo Follow the installation steps in README.md, then run this script again.
  if "%~1"=="" pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0start.py" %*
set "START_EXIT=%ERRORLEVEL%"
if not "%START_EXIT%"=="0" if "%~1"=="" pause
exit /b %START_EXIT%
