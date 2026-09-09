[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

& docker compose --env-file .env.cloud -f compose.cloud.yaml down
if ($LASTEXITCODE -ne 0) {
    throw 'PattyOps cloud deployment did not stop cleanly.'
}

Write-Host 'PattyOps cloud deployment stopped. PostgreSQL data was preserved.'
