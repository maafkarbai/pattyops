[CmdletBinding()]
param(
    [Parameter()]
    [string]$VideoFile,

    [Parameter()]
    [string]$DatabaseFile,

    [Parameter()]
    [string]$OutputFile,

    [Parameter()]
    [switch]$NoBuild,

    [Parameter()]
    [switch]$ValidateOnly,

    [Parameter()]
    [ValidateRange(30, 600)]
    [int]$DockerStartTimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-LeafName {
    param(
        [Parameter(Mandatory)]
        [string]$Value,

        [Parameter(Mandatory)]
        [string]$ParameterName
    )

    if ([System.IO.Path]::GetFileName($Value) -ne $Value) {
        throw "$ParameterName must be a filename without a directory: $Value"
    }
}

function Invoke-Docker {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Test-DockerEngine {
    $previousErrorActionPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "SilentlyContinue"
        & docker info --format "{{.ServerVersion}}" 2> $null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$DefaultValue
    )

    $match = Select-String `
        -LiteralPath ".env" `
        -Pattern "^$([regex]::Escape($Name))=(.*)$" |
        Select-Object -Last 1

    if (-not $match) {
        return $DefaultValue
    }

    $value = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }

    return $value
}

$projectDirectory = $PSScriptRoot
if (-not $projectDirectory) {
    $projectDirectory = (Get-Location).Path
}

Push-Location $projectDirectory

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker was not found. Install or start Docker Desktop first."
    }

    if (-not (Test-DockerEngine)) {
        $dockerDesktopPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

        if (-not (Test-Path -LiteralPath $dockerDesktopPath -PathType Leaf)) {
            throw "Docker Desktop is installed but its engine is not running."
        }

        Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
        Start-Process -FilePath $dockerDesktopPath -WindowStyle Hidden

        $deadline = (Get-Date).AddSeconds($DockerStartTimeoutSeconds)
        while ((Get-Date) -lt $deadline -and -not (Test-DockerEngine)) {
            Start-Sleep -Seconds 3
        }

        if (-not (Test-DockerEngine)) {
            throw "Docker Desktop did not become ready within $DockerStartTimeoutSeconds seconds."
        }

        Write-Host "Docker Desktop is ready." -ForegroundColor Green
    }

    foreach ($requiredFile in @("compose.yaml", "Dockerfile", ".env")) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Missing required file: $requiredFile"
        }
    }

    $apiKeyConfigured = Select-String `
        -LiteralPath ".env" `
        -Pattern '^ROBOFLOW_API_KEY=(?!replace_with_your_key\s*$).+' `
        -Quiet

    if (-not $apiKeyConfigured) {
        throw "ROBOFLOW_API_KEY is missing or still contains the placeholder in .env."
    }

    if ([string]::IsNullOrWhiteSpace($VideoFile)) {
        $VideoFile = Get-DotEnvValue `
            -Name "PATTYOPS_VIDEO_FILE" `
            -DefaultValue "Patty3.mp4"
    }

    if ([string]::IsNullOrWhiteSpace($DatabaseFile)) {
        $DatabaseFile = Get-DotEnvValue `
            -Name "PATTYOPS_DATABASE_FILE" `
            -DefaultValue "pattyops.db"
    }

    if ([string]::IsNullOrWhiteSpace($OutputFile)) {
        $OutputFile = Get-DotEnvValue `
            -Name "PATTYOPS_OUTPUT_FILE" `
            -DefaultValue "pattyops_output.mp4"
    }

    Assert-LeafName -Value $VideoFile -ParameterName "VideoFile"
    Assert-LeafName -Value $DatabaseFile -ParameterName "DatabaseFile"
    Assert-LeafName -Value $OutputFile -ParameterName "OutputFile"

    $videoPath = Join-Path "input" $VideoFile
    if (-not (Test-Path -LiteralPath $videoPath -PathType Leaf)) {
        throw "Input video not found: $videoPath"
    }

    New-Item -ItemType Directory -Force -Path "Database", "output" | Out-Null

    $env:PATTYOPS_VIDEO_FILE = $VideoFile
    $env:PATTYOPS_DATABASE_FILE = $DatabaseFile
    $env:PATTYOPS_OUTPUT_FILE = $OutputFile

    Write-Host "Video:    input\$VideoFile"
    Write-Host "Database: Database\$DatabaseFile"
    Write-Host "Output:   output\$OutputFile"
    Write-Host "Validating PattyOps Docker configuration..." -ForegroundColor Cyan
    Invoke-Docker -Arguments @("compose", "config", "--quiet")

    if ($ValidateOnly) {
        Write-Host "Validation passed. No containers were started." -ForegroundColor Green
        return
    }

    $upArguments = @(
        "compose",
        "up",
        "--abort-on-container-exit",
        "--exit-code-from",
        "pattyops"
    )

    if (-not $NoBuild) {
        $upArguments = @("compose", "up", "--build") + $upArguments[2..($upArguments.Count - 1)]
    }

    Write-Host "Starting PattyOps for input\$VideoFile..." -ForegroundColor Cyan

    try {
        Invoke-Docker -Arguments $upArguments
    }
    finally {
        Write-Host "Cleaning up PattyOps containers..." -ForegroundColor DarkGray
        & docker compose down --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Docker cleanup returned exit code $LASTEXITCODE."
        }
    }

    $databasePath = Join-Path "Database" $DatabaseFile
    $outputPath = Join-Path "output" $OutputFile

    if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
        throw "Processing finished but the database was not created: $databasePath"
    }

    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
        throw "Processing finished but the annotated video was not created: $outputPath"
    }

    Write-Host "PattyOps completed successfully." -ForegroundColor Green
    Write-Host "Database: $((Resolve-Path -LiteralPath $databasePath).Path)"
    Write-Host "Video:    $((Resolve-Path -LiteralPath $outputPath).Path)"
}
finally {
    Pop-Location
}
