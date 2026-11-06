[CmdletBinding()]
param(
    [string]$ApiUrl = 'http://127.0.0.1:5000',
    [ValidateSet('local', 'production')]
    [string]$ExpectedProfile = 'local'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Stop-WithError {
    param([string]$Message, [int]$ExitCode)
    [Console]::Error.WriteLine($Message)
    exit $ExitCode
}

try {
    $projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
} catch {
    Stop-WithError "Project root resolution failed: $($_.Exception.Message)" 2
}

$repositoryOutput = & git -c 'core.excludesFile=' -C $projectRoot rev-parse --show-toplevel 2>&1
if ($LASTEXITCODE -ne 0) {
    $detail = ($repositoryOutput | ForEach-Object { "$_" }) -join ' '
    Stop-WithError "Repository root resolution failed: $detail" 3
}
$script:RepositoryRoot = (Resolve-Path -LiteralPath ($repositoryOutput | Select-Object -First 1)).Path

function Invoke-RepoGit {
    param([string[]]$Arguments)
    $output = & git -c 'core.excludesFile=' -C $script:RepositoryRoot @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $detail = ($output | ForEach-Object { "$_" }) -join ' '
        Stop-WithError "Git context query failed: $detail" 3
    }
    return @($output | ForEach-Object { "$_" })
}

$normalizedApi = $ApiUrl.Trim().TrimEnd('/')
if (-not $normalizedApi) {
    Stop-WithError 'API URL is required' 2
}
$healthUrl = if ($normalizedApi.EndsWith('/api/health', [StringComparison]::OrdinalIgnoreCase)) {
    $normalizedApi
} else {
    "$normalizedApi/api/health"
}
$parsedHealthUrl = $null
if (-not [Uri]::TryCreate($healthUrl, [UriKind]::Absolute, [ref]$parsedHealthUrl) -or
        $parsedHealthUrl.Scheme -notin @('http', 'https')) {
    Stop-WithError "API URL is invalid: $healthUrl" 2
}

$branch = (Invoke-RepoGit -Arguments @('branch', '--show-current') |
    Select-Object -First 1).Trim()
$head = (Invoke-RepoGit -Arguments @('rev-parse', 'HEAD') |
    Select-Object -First 1).Trim()
$dirtyEntries = @(Invoke-RepoGit -Arguments @('status', '--short', '--untracked-files=all'))

try {
    $health = Invoke-RestMethod -Uri $parsedHealthUrl.AbsoluteUri -Method Get -TimeoutSec 10
} catch {
    Stop-WithError "API health request failed: $($_.Exception.Message)" 4
}
if ($null -eq $health) {
    Stop-WithError 'API health response is empty' 5
}
foreach ($field in @('status', 'runtime_profile', 'source_fingerprint')) {
    if ($health.PSObject.Properties.Name -notcontains $field -or
            [string]::IsNullOrWhiteSpace([string]$health.$field)) {
        Stop-WithError "API health response missing required field: $field" 5
    }
}
if ([string]$health.status -cne 'ok') {
    Stop-WithError "API health status is not ok: $($health.status)" 5
}
if ([string]$health.source_fingerprint -cnotmatch '^[0-9a-f]{64}$') {
    Stop-WithError 'API health source fingerprint has an invalid format' 5
}
if ([string]$health.runtime_profile -cne $ExpectedProfile) {
    Stop-WithError "API runtime profile mismatch: expected $ExpectedProfile, got $($health.runtime_profile)" 6
}

[ordered]@{
    captured_at = [DateTime]::UtcNow.ToString('o')
    repository_root = $script:RepositoryRoot
    branch = $branch
    head = $head
    dirty_entry_count = $dirtyEntries.Count
    api_url = $normalizedApi
    health_url = $parsedHealthUrl.AbsoluteUri
    health_status = [string]$health.status
    runtime_profile = [string]$health.runtime_profile
    source_fingerprint = [string]$health.source_fingerprint
} | ConvertTo-Json -Depth 3 -Compress
