@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run Setup.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scripts\setup_models.py --model z-image-base
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" setup_z_image_base.py --test
pause
