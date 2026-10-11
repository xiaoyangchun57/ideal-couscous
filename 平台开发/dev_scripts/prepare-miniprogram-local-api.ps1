[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\miniprogram\utils\config.js'),
    [string]$Address = '',
    [ValidateRange(1, 65535)]
    [int]$Port = 5000,
    [switch]$Clear
)

$ErrorActionPreference = 'Stop'
$markerPattern = "(?m)^const PREPARED_REAL_DEVICE_API_BASE_URL = '[^']*'; // local-api-prepared-value\r?$"

function Test-PrivateIpv4 {
    param([string]$Value)

    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Value, [ref]$parsed)) {
        return $false
    }
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        return $false
    }
    $octets = $parsed.GetAddressBytes()
    return ($octets[0] -eq 10) -or
        ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31) -or
        ($octets[0] -eq 192 -and $octets[1] -eq 168)
}

function Set-PreparedUrl {
    param([string]$Url)

    $resolvedPath = [System.IO.Path]::GetFullPath($ConfigPath)
    if (-not [System.IO.File]::Exists($resolvedPath)) {
        throw "Mini-program config file not found: $resolvedPath"
    }
    $content = [System.IO.File]::ReadAllText($resolvedPath)
    $matches = [System.Text.RegularExpressions.Regex]::Matches($content, $markerPattern)
    if ($matches.Count -ne 1) {
        throw 'Expected exactly one local API preparation marker in config.js'
    }
    $replacement = "const PREPARED_REAL_DEVICE_API_BASE_URL = '$Url'; // local-api-prepared-value"
    $updated = [System.Text.RegularExpressions.Regex]::Replace(
        $content, $markerPattern, $replacement)
    if ($updated -ne $content) {
        [System.IO.File]::WriteAllText(
            $resolvedPath, $updated, [System.Text.UTF8Encoding]::new($false))
    }
}

if ($Clear) {
    Set-PreparedUrl -Url ''
    Write-Output 'Cleared the generated real-device API address.'
    exit 0
}

try {
    $selectedAddress = $Address.Trim()
    if (-not $selectedAddress) {
        $candidates = Get-NetIPConfiguration | Where-Object {
            $_.NetAdapter.Status -eq 'Up' -and
            -not $_.NetAdapter.Virtual -and
            $_.IPv4DefaultGateway -and
            $_.IPv4Address
        } | ForEach-Object {
            $metric = if ($_.NetIPv4Interface) { $_.NetIPv4Interface.InterfaceMetric } else { 9999 }
            foreach ($entry in $_.IPv4Address) {
                if (Test-PrivateIpv4 -Value $entry.IPAddress) {
                    [pscustomobject]@{
                        Address = $entry.IPAddress
                        Metric = $metric
                        InterfaceAlias = $_.InterfaceAlias
                    }
                }
            }
        } | Sort-Object Metric, InterfaceAlias, Address
        $selectedAddress = ($candidates | Select-Object -First 1).Address
    }

    if (-not $selectedAddress -or -not (Test-PrivateIpv4 -Value $selectedAddress)) {
        throw 'No reachable private LAN IPv4 address is available for real-device debugging.'
    }

    $preparedUrl = "http://${selectedAddress}:$Port"
    Set-PreparedUrl -Url $preparedUrl
    Write-Output "Prepared mini-program real-device API: $preparedUrl"
} catch {
    try {
        Set-PreparedUrl -Url ''
    } catch {
        Write-Error "Local API preparation failed and stale output could not be cleared: $($_.Exception.Message)"
        exit 1
    }
    Write-Error $_.Exception.Message
    exit 1
}
