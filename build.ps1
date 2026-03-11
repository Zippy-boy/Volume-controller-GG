# Build standalone EXEs with PyInstaller
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
  $py = $venvPy
} else {
  $py = (Get-Command python).Source
}

& $py -m pip install -r requirements.txt

# Ensure icon.ico exists for packaging
@'
from PIL import Image
from pathlib import Path
src = Path("web/static/icon.png")
dst = Path("web/static/icon.ico")
if src.exists():
    img = Image.open(src)
    img.save(dst, sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
'@ | & $py -

$distDir = Join-Path $root 'dist'
$buildDir = Join-Path $root 'build'

# Kill running packaged apps that may lock dist files
Get-Process -Name "GGHardwareWeb","GGHardwareServer" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

if (Test-Path $distDir) { Remove-Item $distDir -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force -ErrorAction SilentlyContinue }

& $py -m PyInstaller --noconfirm --clean --noconsole --onefile --name GGHardwareServer --distpath $distDir --workpath $buildDir `
  --icon "web\\static\\icon.ico" `
  --add-data "web;web" `
  --hidden-import pystray `
  --hidden-import serial `
  --hidden-import serial.tools.list_ports `
  --hidden-import steelseries_sonar_py `
  --hidden-import PIL `
  --hidden-import PIL.Image `
  --hidden-import PIL.ImageDraw `
  --collect-all pystray `
  pc\server.py

& $py -m PyInstaller --noconfirm --clean --noconsole --onefile --name GGHardwareWeb --distpath $distDir --workpath $buildDir `
  --icon "web\\static\\icon.ico" `
  --add-data "web;web" `
  --hidden-import flaskwebgui `
  --hidden-import steelseries_sonar_py `
  --collect-all flaskwebgui `
  web\app.py

Write-Host "Build complete: $distDir"
