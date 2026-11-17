#Requires -RunAsAdministrator
param([ValidateSet('Apply', 'Remove')][string]$Mode = 'Apply')
$ErrorActionPreference = 'Stop'
$RuleName = 'AI-Cat-Mini-LAN-20261030-Phone109'
$PythonExe = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'

# This is a temporary trusted-household-LAN exception, not an Internet service.
# Do not widen the addresses, change network profiles, or disable the firewall.
if ($Mode -eq 'Remove') {
    $Existing = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
    if ($Existing) {
        $App = $Existing | Get-NetFirewallApplicationFilter
        $Address = $Existing | Get-NetFirewallAddressFilter
        $Port = $Existing | Get-NetFirewallPortFilter
        if ($Existing.DisplayName -ne 'AI Cat mini LAN phone 192.168.1.109 only' -or
            $Existing.Direction -ne 'Inbound' -or $Existing.Action -ne 'Allow' -or
            $App.Program -ne $PythonExe -or @($Address.LocalAddress).Count -ne 1 -or
            @($Address.RemoteAddress).Count -ne 1 -or $Address.LocalAddress -ne '192.168.1.110' -or
            $Address.RemoteAddress -ne '192.168.1.109' -or $Port.Protocol -ne 'TCP' -or $Port.LocalPort -ne '8788') {
            throw 'Rule identity differs; no firewall change performed.'
        }
        $Existing | Remove-NetFirewallRule
    }
    Write-Output 'Dedicated phone LAN rule removed; other rules unchanged.'
    exit 0
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Expected Python is unavailable.' }
$Addresses = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress '192.168.1.110' -ErrorAction Stop)
if ($Addresses.Count -ne 1) { throw 'Expected computer LAN address is unavailable.' }
$InterfaceIndex = $Addresses[0].InterfaceIndex
$InterfaceAlias = $Addresses[0].InterfaceAlias
$Profiles = @(Get-NetConnectionProfile -InterfaceIndex $InterfaceIndex -ErrorAction Stop)
if ($Profiles.Count -ne 1 -or $Profiles[0].NetworkCategory -eq 'DomainAuthenticated') {
    throw 'Unexpected network profile; no firewall change performed.'
}
if (Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue) {
    throw 'Rule already exists. Review/remove only this dedicated rule before recreating it.'
}
New-NetFirewallRule -Name $RuleName -DisplayName 'AI Cat mini LAN phone 192.168.1.109 only' `
    -Description 'Temporary explicit trusted LAN HTTP development; not production or public access.' `
    -Direction Inbound -Action Allow -Enabled True -Protocol TCP -LocalPort 8788 `
    -LocalAddress '192.168.1.110' -RemoteAddress '192.168.1.109' -Program $PythonExe `
    -InterfaceAlias $InterfaceAlias -Profile $Profiles[0].NetworkCategory.ToString() `
    -EdgeTraversalPolicy Block | Out-Null
Write-Output 'Dedicated phone LAN rule added; other rules and profiles unchanged.'
