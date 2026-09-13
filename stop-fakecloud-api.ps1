$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.fakecloud\api.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No tracked FakeCloud API process.'; return }
$apiProcessId = [int](Get-Content -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $apiProcessId"
if ($process) {
    $expectedPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if ($process.ExecutablePath -ne $expectedPython -or $process.CommandLine -notmatch 'fakecloud_local.py\s+serve') {
        throw 'PID belongs to a different process; leaving it running.'
    }
    Stop-Process -Id $apiProcessId
}
Remove-Item -LiteralPath $pidFile
Write-Host 'PattyOps test API stopped. FakeCloud and its database remain running.'
