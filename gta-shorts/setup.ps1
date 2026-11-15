# GTA Shorts Automator - one-shot setup
# Run:  powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "`n=== GTA Shorts Automator setup ===`n" -ForegroundColor Cyan

function Test-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# --- Python ---------------------------------------------------------------
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) {
    if (-not (Test-Cmd winget)) {
        throw "winget is missing. Install Python 3.12 from python.org, then re-run setup.ps1."
    }
    Write-Host "Installing Python 3.12 (user scope)..." -ForegroundColor Yellow
    winget install --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --silent
    if (-not (Test-Path $py)) { throw "Python install did not complete. Install Python 3.12 manually and re-run." }
}
& $py --version

# --- FFmpeg ---------------------------------------------------------------
if (-not (Test-Cmd ffmpeg) -and -not (Test-Path "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe")) {
    Write-Host "Installing FFmpeg (this downloads ~100 MB)..." -ForegroundColor Yellow
    winget install --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent
}
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff -and (Test-Path "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe")) {
    $env:PATH = "$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:PATH"
}
ffmpeg -version 2>$null | Select-Object -First 1

# --- Python packages ------------------------------------------------------
Write-Host "`nInstalling Python packages..." -ForegroundColor Yellow
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed. Check your internet connection and re-run." }

# --- .env -----------------------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "`nCreated .env - add your free Gemini key there:" -ForegroundColor Yellow
    Write-Host "  https://aistudio.google.com/apikey"
}

# --- Self test ------------------------------------------------------------
Write-Host "`nRendering a synthetic 26s clip to verify the pipeline..." -ForegroundColor Yellow
& $py -m gtashorts selftest
if ($LASTEXITCODE -ne 0) { throw "Self-test failed. Read the FAIL lines above." }

Write-Host "`n=== Setup finished ===" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Put your Gemini key in .env"
Write-Host "  2. Double-click Add Link.cmd and paste a YouTube link"
Write-Host "  3. Upload the finished clips from the Shorts folder yourself"
Write-Host "     (posting is manual by design - no YouTube login needed)"
Write-Host ""
