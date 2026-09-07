@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtual environment found.
    echo Create/install it using the short setup section in README.md.
    pause
    exit /b 1
)

if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo Created .env. Add your Mapbox token, then run start.bat again.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py

if errorlevel 1 (
    echo.
    echo GPS Tractor stopped with an error.
    pause
)
