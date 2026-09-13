[CmdletBinding()]
param([string]$FakeCloudPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'fakecloud'))
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& docker compose --project-directory $FakeCloudPath -f (Join-Path $FakeCloudPath 'docker-compose.yml') up -d
if ($LASTEXITCODE -ne 0) { throw 'FakeCloud failed to start.' }
$emulatorReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { $null = Invoke-RestMethod 'http://localhost:4566/_fakecloud/health' -TimeoutSec 2; $emulatorReady = $true; break }
    catch { Start-Sleep -Seconds 1 }
}
if (-not $emulatorReady) { throw 'FakeCloud did not become healthy.' }
& $python fakecloud_local.py setup
if ($LASTEXITCODE -ne 0) { throw 'FakeCloud setup failed.' }
$listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
if (-not $listener) {
    $process = Start-Process -FilePath $python -ArgumentList 'fakecloud_local.py', 'serve' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput '.fakecloud\api.stdout.log' -RedirectStandardError '.fakecloud\api.stderr.log'
    $process.Id | Set-Content '.fakecloud\api.pid'
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try { $null = Invoke-RestMethod 'http://localhost:8000/healthz' -TimeoutSec 2; $ready = $true; break }
        catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'API did not start. Check .fakecloud\api.stderr.log.' }
}
& $python fakecloud_local.py verify
if ($LASTEXITCODE -ne 0) { throw 'FakeCloud integration verification failed; port 8000 may belong to another API.' }
Write-Host 'Ready: open PattyOps Kitchen. Cloud API: http://localhost:8000'
