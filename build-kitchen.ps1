[CmdletBinding()]
param([switch]$Portable, [string]$InnoCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run bun run setup on the build computer first.' }
if (-not $Portable -and -not (Test-Path -LiteralPath $InnoCompiler)) { throw 'Install Inno Setup 6 on the build computer, or pass -InnoCompiler with its ISCC.exe path.' }
& $python -m pip install -r requirements-desktop-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
& $python build_brand_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Brand asset export failed.' }
& $python -m PyInstaller --noconfirm kitchen-app.spec
if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed.' }
if ($Portable) { Write-Host 'Portable kitchen app: dist\PattyOps Kitchen'; return }
& $InnoCompiler kitchen-installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer packaging failed.' }
Write-Host 'Kitchen installer: dist\PattyOps-Kitchen-Setup.exe'
