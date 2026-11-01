@echo off
rem First run: builds a private Python environment, installs the requirements and writes config.toml.
rem After that it just starts the farmer. Extra arguments are passed on (for example: start.bat --plain).
setlocal
cd /d "%~dp0"

rem The environment lives outside this folder on purpose: Desktop and Documents are often synced by
rem OneDrive, which makes creating thousands of small files very slow. Override with OWO_FARMER_VENV.
if not defined OWO_FARMER_VENV set "OWO_FARMER_VENV=%LOCALAPPDATA%\owo-farmer\venv"
set "VENV=%OWO_FARMER_VENV%"
set "PY=%VENV%\Scripts\python.exe"

if exist "%PY%" (
    "%PY%" -c "import discord, rich, aiohttp" >nul 2>&1
    if not errorlevel 1 goto :ready
    "%PY%" -m pip --version >nul 2>&1
    if not errorlevel 1 goto :install
    echo The Python environment is incomplete; rebuilding it...
    rmdir /s /q "%VENV%"
)

echo Setting up, first run only. This can take a few minutes: please keep this window open.
py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%" || goto :fail

:install
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt || goto :fail
echo Setup finished.

:ready
if not exist "config.toml" (
    "%PY%" owo_farmer.py --init
    echo.
    echo Open config.toml, set farm.channel_id, then run start.bat again.
    pause
    exit /b 0
)

"%PY%" owo_farmer.py %*
set code=%errorlevel%
if not "%code%"=="0" pause
exit /b %code%

:fail
echo.
echo Setup failed. Install Python 3.10 or newer from python.org (tick "Add Python to PATH") and try again.
pause
exit /b 1
