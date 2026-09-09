[CmdletBinding()]
param(
    [Alias('VideoFile')]
    [string]$Source,

    [string]$Model = 'models\best.pt',

    [Alias('DatabaseFile')]
    [string]$Database = 'Database\pattyops.db',

    [Alias('OutputFile')]
    [string]$SaveVideo,

    [string]$ClassMap,

    [ValidateRange(0.01, 1.0)]
    [double]$Confidence = 0.35,

    [ValidateRange(0.01, 1.0)]
    [double]$Iou = 0.5,

    [switch]$NoDisplay,

    [switch]$Setup,

    [switch]$SetupOnly,

    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Get-AbsoluteProjectPath {
    param([Parameter(Mandatory)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Path))
}

function Initialize-VirtualEnvironment {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $systemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $systemPython) {
            throw 'Python was not found. Install Python 3.12 and run this script again.'
        }
        Write-Host 'Creating PattyOps virtual environment...'
        & $systemPython.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            throw 'Virtual environment creation failed.'
        }
    }

    Write-Host 'Installing local inference dependencies...'
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw 'Dependency installation failed.'
    }
}

if ($Setup -or $SetupOnly -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Initialize-VirtualEnvironment
}

if ($SetupOnly) {
    Write-Host "PattyOps environment is ready: $venvPython"
    return
}

$modelPath = Get-AbsoluteProjectPath -Path $Model
if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) {
    throw "Local YOLO model not found: $modelPath"
}

if ([string]::IsNullOrWhiteSpace($Source)) {
    if ($ValidateOnly) {
        Write-Host "Local model found: $modelPath"
        Write-Host "Python environment found: $venvPython"
        Write-Host 'Validation passed. No camera or video was opened.'
        return
    }

    Write-Host 'Starting interactive local PattyOps setup...'
    & $venvPython pattyops.py
    if ($LASTEXITCODE -ne 0) {
        throw "PattyOps exited with code $LASTEXITCODE."
    }
    return
}

$sourceArgument = $Source
$cameraSource = $Source -match '^(camera:)?\d+$'
if (-not $cameraSource -and $Source -notmatch '^(https?|rtsp)://') {
    $sourcePath = Get-AbsoluteProjectPath -Path $Source
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Input video not found: $sourcePath"
    }
    $sourceArgument = $sourcePath
}

$databasePath = Get-AbsoluteProjectPath -Path $Database
New-Item -ItemType Directory -Path (Split-Path -Parent $databasePath) -Force | Out-Null

if ($ValidateOnly) {
    Write-Host "Local model: $modelPath"
    Write-Host "Source: $sourceArgument"
    Write-Host "Database: $databasePath"
    Write-Host 'Validation passed. Inference was not started.'
    return
}

$arguments = @(
    'pattyops.py'
    'run'
    '--model', $modelPath
    '--source', $sourceArgument
    '--db', $databasePath
    '--confidence', $Confidence.ToString([System.Globalization.CultureInfo]::InvariantCulture)
    '--iou', $Iou.ToString([System.Globalization.CultureInfo]::InvariantCulture)
)

if (-not $NoDisplay) {
    $arguments += '--show'
}

if (-not [string]::IsNullOrWhiteSpace($SaveVideo)) {
    $outputPath = Get-AbsoluteProjectPath -Path $SaveVideo
    New-Item -ItemType Directory -Path (Split-Path -Parent $outputPath) -Force | Out-Null
    $arguments += @('--save-video', $outputPath)
}

if (-not [string]::IsNullOrWhiteSpace($ClassMap)) {
    $classMapPath = Get-AbsoluteProjectPath -Path $ClassMap
    if (-not (Test-Path -LiteralPath $classMapPath -PathType Leaf)) {
        throw "Class map not found: $classMapPath"
    }
    $arguments += @('--class-map', $classMapPath)
}

Write-Host 'Starting PattyOps with local YOLO inference...'
Write-Host "  Model: $modelPath"
Write-Host "  Source: $sourceArgument"
Write-Host "  Database: $databasePath"
& $venvPython @arguments
if ($LASTEXITCODE -ne 0) {
    throw "PattyOps exited with code $LASTEXITCODE."
}
