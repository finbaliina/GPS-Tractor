@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtual environment found.
    echo Create your .venv first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" ui_app.py

if errorlevel 1 (
    echo.
    echo GPS Tractor UI stopped with an error.
    pause
)
