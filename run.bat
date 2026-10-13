@echo off
REM Tata Steel Road Infrastructure Dashboard - dev server launcher (Windows).
REM Usage:  run.bat        (rebuild data + start on :8000)
REM         set PORT=9000 && run.bat
REM         run.bat --no-build

cd /d "%~dp0"
if "%PORT%"=="" set PORT=8000

python3 -c "import fastapi, uvicorn, openpyxl" 2>NUL
if errorlevel 1 python3 -m pip install fastapi uvicorn openpyxl

if not "%1"=="--no-build" (
  echo Rebuilding dataset from source Excel...
  python3 scripts\build_data.py
)

echo Starting dashboard on http://localhost:%PORT%
python3 -m uvicorn dashboard.main:app --reload --host 0.0.0.0 --port %PORT%
