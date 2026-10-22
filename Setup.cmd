@echo off
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 (
    echo Install Python 3.12 with Tcl/Tk and the Python launcher first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    pause
    exit /b 1
)
echo Python setup complete. Follow README.md to install the engine and a model.
pause
