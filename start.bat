@echo off
setlocal
cd /d "%~dp0"

echo GPS Tractor launcher

echo.
if not exist ".venv\Scripts\python.exe" (
    echo First run: creating the Python virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :setup_error
)

echo Checking project dependencies...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto :setup_error

if not exist ".env" (
    if exist ".env.example" copy /Y ".env.example" ".env" >nul
    echo.
    echo A .env file has been created for you.
    echo Open .env and replace pk.your_token_here with your Mapbox token, then run this launcher again.
    echo.
    pause
    exit /b 1
)

findstr /B /C:"MAPBOX_TOKEN=pk.your_token_here" ".env" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo Your Mapbox token has not been configured yet.
    echo Open .env and replace pk.your_token_here with your real token.
    echo.
    pause
    exit /b 1
)

echo Starting GPS Tractor...
echo.
".venv\Scripts\python.exe" -m rtk_satellite %*
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo GPS Tractor exited with an error.
    pause
)

exit /b %APP_EXIT%

:setup_error
echo.
echo Setup failed. Check that Python 3.11 or newer is installed and available as "python".
pause
exit /b 1
