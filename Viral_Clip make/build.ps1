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

Write-Host "[4/4] Done." -ForegroundColor Green
Write-Host "Run it from:  dist\ViralClipMake\ViralClipMake.exe" -ForegroundColor Green
