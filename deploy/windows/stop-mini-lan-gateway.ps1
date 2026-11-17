param()
$ErrorActionPreference = 'Stop'
$Marker = Join-Path $PSScriptRoot 'running.json'
if (-not (Test-Path -LiteralPath $Marker -PathType Leaf)) { Write-Output 'No run marker; no process stopped.'; exit 0 }
$Info = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
$ExpectedPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$ExpectedScript = Join-Path $PSScriptRoot 'miniprogram_lan_gateway.py'
if ($Info.python -ne $ExpectedPython -or $Info.script -ne $ExpectedScript -or $Info.pid -lt 1) {
    throw 'Run marker does not match this installation; no process stopped.'
}
$Process = Get-Process -Id $Info.pid -ErrorAction SilentlyContinue
if ($Process) {
    if ($Process.Path -ne $ExpectedPython -or $Process.StartTime.ToUniversalTime().ToString('o') -ne $Info.startUtc) {
        throw 'PID was reused or identity differs; no process stopped.'
    }
    $Command = Get-CimInstance Win32_Process -Filter ('ProcessId = '+$Info.pid)
    if (-not $Command -or -not $Command.CommandLine.Contains(('"'+$ExpectedScript+'"')) -or
        -not $Command.CommandLine.Contains('--confirm-trusted-lan')) {
        throw 'Process command differs; no process stopped.'
    }
    Stop-Process -Id $Info.pid
}
Remove-Item -LiteralPath $Marker
Write-Output 'Dedicated LAN process stopped. To remove its firewall rule, run mini-lan-firewall.ps1 -Mode Remove as administrator.'
