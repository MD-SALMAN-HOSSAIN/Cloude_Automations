@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw app.py
) else (
    python app.py
    if errorlevel 1 pause
)
