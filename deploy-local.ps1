[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$SyncOnce
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Test-DockerEngine {
    $dockerCommand = (Get-Command docker -ErrorAction Stop).Source
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $dockerCommand
    $startInfo.Arguments = 'info --format "{{.ServerVersion}}"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        [void]$process.Start()
        if (-not $process.WaitForExit(5000)) {
            $process.Kill()
            $process.WaitForExit()
            return $false
        }
        return $process.ExitCode -eq 0
    }
    finally {
        $process.Dispose()
    }
}

function Start-DockerEngine {
    if (Test-DockerEngine) {
        return
    }

    $dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $dockerDesktop -PathType Leaf)) {
        throw 'Docker Desktop is not installed or could not be found.'
    }

    Write-Host 'Starting Docker Desktop...'
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 3
        if (Test-DockerEngine) {
            return
        }
    } while ((Get-Date) -lt $deadline)

    throw 'Docker Desktop did not become ready within two minutes.'
}

function Read-EnvironmentFile {
    param([string]$Path)

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
            continue
        }
        $name, $value = $trimmed -split '=', 2
        $values[$name] = $value
    }
    return $values
}

$requiredFiles = @('.env.cloud', '.env.edge', '.env.edge.host', 'models\best.pt')
$missingFiles = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($missingFiles.Count -gt 0) {
    throw "Deployment is not configured. Run .\configure-deployment.ps1 first."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI is not installed or is not on PATH.'
}

Start-DockerEngine

& docker compose --env-file .env.cloud -f compose.cloud.yaml config --quiet
if ($LASTEXITCODE -ne 0) {
    throw 'Cloud Compose configuration validation failed.'
}

$upArguments = @('compose', '--env-file', '.env.cloud', '-f', 'compose.cloud.yaml', 'up', '-d')
if (-not $SkipBuild) {
    $upArguments += '--build'
}
& docker @upArguments
if ($LASTEXITCODE -ne 0) {
    throw 'Cloud deployment failed to start.'
}

$healthUrl = 'http://localhost:8000/healthz'
$deadline = (Get-Date).AddMinutes(2)
$health = $null
do {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
    }
    catch {
        Start-Sleep -Seconds 2
    }
} while ($null -eq $health -and (Get-Date) -lt $deadline)

if ($null -eq $health) {
    & docker compose --env-file .env.cloud -f compose.cloud.yaml logs --tail=80 cloud-api
    throw "Cloud API did not become healthy at $healthUrl"
}

$hostConfig = Read-EnvironmentFile -Path '.env.edge.host'
$headers = @{
    Authorization = "Bearer $($hostConfig['PATTYOPS_DEVICE_TOKEN'])"
    'X-Device-ID' = $hostConfig['PATTYOPS_DEVICE_ID']
}
$null = Invoke-RestMethod -Uri 'http://localhost:8000/v1/events?limit=1' -Headers $headers -TimeoutSec 5

if ($SyncOnce) {
    foreach ($entry in $hostConfig.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'The project virtual environment is missing. Run .\run-pattyops.ps1 -SetupOnly.'
    }
    & $python edge_sync.py --once
    if ($LASTEXITCODE -ne 0) {
        throw 'The one-time edge synchronization failed.'
    }
}

Write-Host ''
Write-Host 'PattyOps cloud deployment is healthy and authentication succeeded.'
Write-Host '  API: http://localhost:8000'
Write-Host '  Health: http://localhost:8000/healthz'
Write-Host '  Stop: .\stop-local-deployment.ps1'
& docker compose --env-file .env.cloud -f compose.cloud.yaml ps
