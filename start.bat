@echo off
rem CS2-AimAudit launcher: creates the venv, installs packages, starts the app.
setlocal
cd /d "%~dp0"

echo [1/3] Checking Python...
where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Python was not found. Install Python 3.10+ from
    echo   https://www.python.org/downloads/ and check
    echo   "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating a virtual environment .venv ...
    python -m venv .venv
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

echo [2/3] Installing packages, first run takes 1-3 minutes ...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo.
    echo   Package installation failed. Check your internet
    echo   connection and run start.bat again.
    echo.
    pause
    exit /b 1
)

echo [3/3] Starting CS2-AimAudit, a browser tab will open ...
python -m streamlit run app.py

echo.
echo CS2-AimAudit stopped.
pause
