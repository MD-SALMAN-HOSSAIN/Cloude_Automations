@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw app.py
) else (
    if exist "dist\ViralClipMake\ViralClipMake.exe" (
        start "" "dist\ViralClipMake\ViralClipMake.exe"
    ) else (
        where python >nul 2>nul
        if not errorlevel 1 (
            start "" python app.py
        ) else (
            echo Python not found. Install Python or build the .exe first.
            pause
        )
    )
)
