$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Viral Clip Make - .exe build ===" -ForegroundColor Cyan

Write-Host "[1/4] Making sure PyInstaller and CustomTkinter are installed..."
python -m pip install --quiet pyinstaller customtkinter

Write-Host "[2/4] Locating FFmpeg..."
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg -or -not $ffprobe) {
    throw "FFmpeg not found. Install it first:  winget install Gyan.FFmpeg"
}
New-Item -ItemType Directory -Force -Path "bin" | Out-Null
Copy-Item $ffmpeg "bin\ffmpeg.exe" -Force
Copy-Item $ffprobe "bin\ffprobe.exe" -Force
Write-Host "      FFmpeg: $ffmpeg"

Write-Host "[3/4] Building the Windows executable (this can take several minutes)..."
# PyInstaller deletes dist\ViralClipMake before collecting. If the app is still
# running, Windows locks those files and the build fails partway through. Move
# the old folder aside instead of trying to delete it.
$distDir = Join-Path $PSScriptRoot "dist\ViralClipMake"
if (Test-Path $distDir) {
    $backup = Join-Path $PSScriptRoot ("dist\ViralClipMake_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    try {
        Rename-Item $distDir $backup -ErrorAction Stop
        Write-Host "      Previous build moved to: $backup"
    } catch {
        Write-Host "      WARNING: could not move the old build: $($_.Exception.Message)"
    }
}
python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name "ViralClipMake" `
    --paths "." `
    --add-data "bin;bin" `
    --collect-submodules "yt_dlp" `
    --collect-binaries "onnxruntime" `
    --collect-data "onnxruntime" `
    --collect-binaries "ctranslate2" `
    --collect-binaries "tokenizers" `
    --collect-data "faster_whisper" `
    --collect-all "customtkinter" `
    app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE. Close any running ViralClipMake window and try again."
}

Write-Host "[4/4] Done." -ForegroundColor Green
Write-Host "Run it from:  dist\ViralClipMake\ViralClipMake.exe" -ForegroundColor Green
