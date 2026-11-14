@echo off
rem CS2-AimAudit launcher (Windows): creates the venv, installs packages,
rem downloads the radar images once, starts the app.
setlocal
cd /d "%~dp0"

echo [1/4] Checking Python 3.13 (the version this project is tested with)...
rem The Python pin (3, 13) / py -3.13 must match ci.yml, README and requirements.txt
rem (tests/check_ui.py compares it with ci.yml).
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
    py -3.13 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=py -3.13"
)
if not defined PY goto :no_python

rem A .venv left by an older AimAudit may run another Python (not 3.13)
rem or be broken: recreate it with the current Python instead of dying in pip.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo   .venv is broken or runs an older Python; recreating it ...
        rmdir /s /q .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo   Creating a virtual environment .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo   Could not create the virtual environment.
        echo   Is Python installed correctly?
        echo.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

echo [2/4] Installing packages, first run takes 1-3 minutes ...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo.
    echo   Package installation failed. Check your internet
    echo   connection and run start.bat again.
    echo.
    pause
    exit /b 1
)

rem The Map page draws on the real radars. They are downloaded once into
rem data\radars and never again; with no internet the app still works on a
rem plain grid and this step retries tomorrow. To skip the download for good:
rem set AIMAUDIT_NO_RADARS=1 (the Windows setx command makes it permanent).
echo [3/4] Checking the radar images for the Map page ...
if not defined AIMAUDIT_NO_RADARS python tools\fetch_radars.py --if-missing

rem Streamlit asks for an email on its very first run and waits for Enter,
rem the window then looks frozen. An empty credentials file skips that prompt.
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
    > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
    >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

echo [4/4] Starting CS2-AimAudit, a browser tab will open ...
python -m streamlit run app.py

echo.
echo CS2-AimAudit stopped.
pause
exit /b 0

:no_python
echo.
echo   Python 3.13 was not found. The pinned packages in requirements.txt
echo   are tested on 3.13 only, other versions are not supported.
echo   Install it from https://www.python.org/downloads/ and check
echo   "Add python.exe to PATH" during installation, or run this in a
echo   terminal:  winget install Python.Python.3.13
echo   then start start.bat again.
echo   If Windows opens the Microsoft Store instead, turn off the python.exe
echo   alias: Settings, Apps, Advanced app settings, App execution aliases.
echo.
pause
exit /b 1
