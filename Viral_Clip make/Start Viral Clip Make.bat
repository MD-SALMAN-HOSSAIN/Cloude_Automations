@echo off
cd /d "%~dp0"
if exist "dist\ViralClipMake\ViralClipMake.exe" (
    start "" "dist\ViralClipMake\ViralClipMake.exe"
) else (
    python app.py
)
