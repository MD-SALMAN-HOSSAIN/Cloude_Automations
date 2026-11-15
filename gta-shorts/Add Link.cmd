@echo off
setlocal
cd /d "%~dp0"
title GTA Shorts - make Shorts from a link
echo.
echo   GTA SHORTS - paste a link, get vertical Shorts
echo   ============================================
echo.

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

rem --- refuse to start without the AI key, with a clear explanation -------------
"%PY%" -c "from gtashorts.config import load_config; raise SystemExit(0 if load_config().gemini_key() else 1)" >nul 2>&1
if errorlevel 1 (
  echo   GEMINI_API_KEY is missing.
  echo.
  echo   1. Open this file in Notepad:  "%~dp0.env"
  echo   2. Put your key on the line:   GEMINI_API_KEY=AIza...
  echo   3. Save, then double-click this file again.
  echo.
  echo   Free key: https://aistudio.google.com/apikey
  echo.
  pause
  exit /b 1
)

if not "%~1"=="" (
  set "LINK=%~1"
) else (
  set /p "LINK=Paste the YouTube link (or drag a video file here), then press Enter: "
)
if "%LINK%"=="" (
  echo Nothing entered.
  pause
  exit /b 1
)

echo.
echo Working... this can take a few minutes. Leave this window open.
echo.
if exist "%LINK%" (
  call "%~dp0shorts.cmd" file "%LINK%"
) else (
  call "%~dp0shorts.cmd" add "%LINK%"
)
if errorlevel 1 (
  echo.
  echo Something failed - the error is above.
  pause
  exit /b 1
)

echo.
echo Opening the folder with your finished Shorts...
start "" "%~dp0Shorts"
echo.
echo Each clip has .mp4 (video), .jpg (thumbnail) and .txt (title + description + hashtags).
echo Upload the .mp4 to YouTube yourself - posting is manual by design.
echo.
pause
