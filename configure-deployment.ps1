[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$DeviceId = 'kitchen-01',

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = $PSScriptRoot
$cloudEnvPath = Join-Path $projectRoot '.env.cloud'
$edgeEnvPath = Join-Path $projectRoot '.env.edge'
$hostEdgeEnvPath = Join-Path $projectRoot '.env.edge.host'
$targetPaths = @($cloudEnvPath, $edgeEnvPath, $hostEdgeEnvPath)

if (-not $Force) {
    $existing = @($targetPaths | Where-Object { Test-Path -LiteralPath $_ })
    if ($existing.Count -gt 0) {
        throw "Deployment configuration already exists. Use -Force to rotate all credentials."
    }
}

function New-HexSecret {
    param([int]$ByteCount)

    $bytes = New-Object byte[] $ByteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

function Write-PrivateEnvironmentFile {
    param(
        [string]$Path,
        [string[]]$Lines
    )

    $contents = ($Lines -join [Environment]::NewLine) + [Environment]::NewLine
    [System.IO.File]::WriteAllText($Path, $contents, [System.Text.UTF8Encoding]::new($false))
}

$databasePassword = New-HexSecret -ByteCount 24
$deviceToken = New-HexSecret -ByteCount 32

Write-PrivateEnvironmentFile -Path $cloudEnvPath -Lines @(
    'POSTGRES_DB=pattyops'
    'POSTGRES_USER=pattyops'
    "POSTGRES_PASSWORD=$databasePassword"
    "PATTYOPS_CLOUD_DATABASE_URL=postgresql+psycopg://pattyops:${databasePassword}@postgres:5432/pattyops"
    "PATTYOPS_DEVICE_TOKENS_JSON={`"$DeviceId`":`"$deviceToken`"}"
    'PATTYOPS_CLOUD_BIND_ADDRESS=127.0.0.1'
    'PATTYOPS_CLOUD_PORT=8000'
)

Write-PrivateEnvironmentFile -Path $edgeEnvPath -Lines @(
    'PATTYOPS_DATABASE_PATH=/app/data/pattyops.db'
    'PATTYOPS_CLOUD_URL=http://cloud-api:8000'
    "PATTYOPS_DEVICE_ID=$DeviceId"
    "PATTYOPS_DEVICE_TOKEN=$deviceToken"
    'PATTYOPS_SYNC_BATCH_SIZE=100'
    'PATTYOPS_SYNC_INTERVAL_SECONDS=5'
    'PATTYOPS_SYNC_TIMEOUT_SECONDS=15'
    'PATTYOPS_ALLOW_INSECURE_HTTP=true'
)

Write-PrivateEnvironmentFile -Path $hostEdgeEnvPath -Lines @(
    'PATTYOPS_DATABASE_PATH=Database/pattyops.db'
    'PATTYOPS_CLOUD_URL=http://localhost:8000'
    "PATTYOPS_DEVICE_ID=$DeviceId"
    "PATTYOPS_DEVICE_TOKEN=$deviceToken"
    'PATTYOPS_SYNC_BATCH_SIZE=100'
    'PATTYOPS_SYNC_INTERVAL_SECONDS=5'
    'PATTYOPS_SYNC_TIMEOUT_SECONDS=15'
    'PATTYOPS_ALLOW_INSECURE_HTTP=true'
)

$trainedModel = Join-Path $projectRoot 'runs\pattyops\local-v1\weights\best.pt'
$deployedModel = Join-Path $projectRoot 'models\best.pt'
if (-not (Test-Path -LiteralPath $trainedModel -PathType Leaf)) {
    throw "Trained checkpoint not found: $trainedModel"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $deployedModel) -Force | Out-Null
Copy-Item -LiteralPath $trainedModel -Destination $deployedModel -Force

Write-Host 'PattyOps deployment configuration created.'
Write-Host "  Cloud configuration: $cloudEnvPath"
Write-Host "  Docker edge configuration: $edgeEnvPath"
Write-Host "  Windows edge configuration: $hostEdgeEnvPath"
Write-Host "  Deployed model: $deployedModel"
Write-Host 'Credentials were generated locally and were not printed.'
