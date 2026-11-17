param()
$ErrorActionPreference = 'Stop'
$PythonExe = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$ProxyScript = Join-Path $PSScriptRoot 'miniprogram_lan_gateway.py'
$Marker = Join-Path $PSScriptRoot 'running.json'
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf) -or
    -not (Test-Path -LiteralPath $ProxyScript -PathType Leaf)) { throw 'Expected runtime files are unavailable.' }
if (Test-Path -LiteralPath $Marker) { throw 'A prior run marker exists. Use the dedicated stop script first.' }
if (@(Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'LAN port is already in use; existing listener left unchanged.'
}
# Never disable TLS for a production service; this fixed endpoint is trusted LAN development only.
$Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/health' -TimeoutSec 3 -MaximumRedirection 0
if (-not $Health.success -or -not $Health.data.wechatConfigured) { throw 'WSL gateway is not ready.' }
$Process = Start-Process -FilePath $PythonExe -ArgumentList @('-I', '-B', '-u', ('"'+$ProxyScript+'"'), '--confirm-trusted-lan') `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
$OwnMarker = $false
try {
    Start-Sleep -Milliseconds 400
    $Process.Refresh()
    if ($Process.HasExited) { throw 'LAN proxy failed to start.' }
    $Info = @{pid=$Process.Id; startUtc=$Process.StartTime.ToUniversalTime().ToString('o'); python=$PythonExe; script=$ProxyScript}
    # Exclusive creation: a concurrent failed starter must never replace/delete
    # another successful instance's recovery marker.
    $Stream = [IO.File]::Open($Marker, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $OwnMarker = $true
    try {
        $Bytes = [Text.Encoding]::UTF8.GetBytes(($Info | ConvertTo-Json -Compress))
        $Stream.Write($Bytes, 0, $Bytes.Length)
        $Stream.Flush($true)
    } finally { $Stream.Dispose() }
    $LanHealth = Invoke-RestMethod -Uri 'http://192.168.1.110:8788/health' -TimeoutSec 3 -MaximumRedirection 0
    if (-not $LanHealth.success) { throw 'LAN readiness check failed.' }
    Write-Output 'Phone-only LAN gateway ready at 192.168.1.110:8788; no hardware command sent.'
} catch {
    # Only stop the exact process this invocation created; never kill a port owner.
    $Process.Refresh()
    if (-not $Process.HasExited) { $Process.Kill(); $Process.WaitForExit() }
    if ($OwnMarker -and (Test-Path -LiteralPath $Marker)) {
        $Saved = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
        if ($Saved.pid -eq $Process.Id -and $Saved.startUtc -eq $Info.startUtc) {
            Remove-Item -LiteralPath $Marker
        }
    }
    throw 'LAN startup failed; newly created process stopped, other services unchanged.'
}
