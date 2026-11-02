<#
.SYNOPSIS
    Install the Fabric IQ MCP proxy for any Microsoft Entra tenant.
.EXAMPLE
    .\install.ps1 -ClientId '<application-id>' -Tenant 'example.com' -ConfigureDesktop
#>
[CmdletBinding()]
param(
    [string]$ClientId = $env:FABRICIQ_CLIENT_ID,
    [string]$Tenant = $env:FABRICIQ_TENANT,
    [string]$LoginHint = $env:FABRICIQ_LOGIN_HINT,
    [switch]$ConfigureDesktop,
    [switch]$SkipLogin
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$ProxyScript = Join-Path $Root 'fabriciq_proxy.py'

function Write-Step($Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }

function Write-McpConfiguration {
    param([string]$Path, [object]$Server)

    $exists = Test-Path -LiteralPath $Path
    if ($exists) {
        $raw = [System.IO.File]::ReadAllText($Path)
        try { $cfg = $raw | ConvertFrom-Json -ErrorAction Stop }
        catch { throw "Invalid JSON in $Path. Existing configuration was not changed." }
        if (-not $raw.TrimStart().StartsWith('{') -or $cfg -isnot [System.Management.Automation.PSCustomObject]) {
            throw "Expected a JSON object in $Path. Existing configuration was not changed."
        }
    } else {
        $cfg = [pscustomobject]@{}
    }
    if ($null -eq $cfg.mcpServers) {
        $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force
    } elseif ($cfg.mcpServers -isnot [System.Management.Automation.PSCustomObject]) {
        throw "Expected mcpServers to be an object in $Path. Existing configuration was not changed."
    }
    $cfg.mcpServers | Add-Member -NotePropertyName fabriciq -NotePropertyValue $Server -Force
    $json = $cfg | ConvertTo-Json -Depth 100
    if ($exists) {
        $backup = "$Path.$(Get-Date -Format 'yyyyMMdd-HHmmss-fffffff').bak"
        Copy-Item -LiteralPath $Path -Destination $backup
        Write-Host "Backup: $backup"
    }
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    [System.IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Updated: $Path"
}

Write-Step 'Configuration'
if ($ClientId) { $ClientId = $ClientId.Trim() }
if ($Tenant) { $Tenant = $Tenant.Trim() }
if (-not $ClientId -and -not $SkipLogin) {
    throw 'Sign-in needs -ClientId for your own Entra app. Use -SkipLogin to configure first without opening a browser. See README.md.'
}
if ($ClientId -eq '04b07795-8ddb-461a-bbee-02f9e1bf7b46') {
    throw 'The Microsoft development app cannot authenticate to Fabric IQ (AADSTS65002). Use your own Entra app.'
}
if ($LoginHint) { $LoginHint = $LoginHint.Trim() }

$env:FABRICIQ_CLIENT_ID = $ClientId
$env:FABRICIQ_TENANT = $Tenant
$env:FABRICIQ_LOGIN_HINT = $LoginHint

Write-Step 'Checking Python'
$PyExe = $null; $PyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $PyExe = 'py'; $PyArgs = @('-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $PyExe = 'python' }
if (-not $PyExe) { throw 'Install Python 3.11 or newer from python.org and add it to PATH.' }
$ok = & $PyExe @PyArgs -c "import sys; print(sys.version_info >= (3, 11))"
if ($LASTEXITCODE -ne 0 -or $ok -ne 'True') { throw 'Python 3.11 or newer is required.' }

Write-Step 'Installing dependencies'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $PyExe @PyArgs -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
& $VenvPython -m pip install --quiet -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $VenvPython -m py_compile $ProxyScript
if ($LASTEXITCODE -ne 0) { throw 'Proxy syntax validation failed.' }

if (-not $SkipLogin) {
    Write-Step 'Signing in'
    & $VenvPython $ProxyScript status
    if ($LASTEXITCODE -ne 0) {
        & $VenvPython $ProxyScript login
        if ($LASTEXITCODE -ne 0) { throw 'Sign-in failed. See README.md.' }
    }
}

$serverEnv = [ordered]@{}
if ($ClientId) { $serverEnv.FABRICIQ_CLIENT_ID = $ClientId }
if ($Tenant) { $serverEnv.FABRICIQ_TENANT = $Tenant }
if ($LoginHint) { $serverEnv.FABRICIQ_LOGIN_HINT = $LoginHint }
$codeServer = [pscustomobject]@{
    type = 'stdio'
    command = $VenvPython
    args = @($ProxyScript)
    env = [pscustomobject]$serverEnv
}
Write-Step 'Configuring the project MCP server'
Write-McpConfiguration -Path (Join-Path $Root '.mcp.json') -Server $codeServer

if ($ConfigureDesktop) {
    Write-Step 'Configuring Claude Desktop'
    $candidates = @(Join-Path $env:APPDATA 'Claude\claude_desktop_config.json')
    $msix = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Packages') -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($msix) { $candidates = @(Join-Path $msix.FullName 'LocalCache\Roaming\Claude\claude_desktop_config.json') + $candidates }
    $cfgPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $cfgPath) { $cfgPath = $candidates[-1] }
    $desktopServer = [pscustomobject]@{
        command = $VenvPython
        args = @($ProxyScript)
        env = [pscustomobject]$serverEnv
    }
    Write-McpConfiguration -Path $cfgPath -Server $desktopServer
}

Write-Step 'Done'
Write-Host 'Start your MCP client and enable the fabriciq server.'
Write-Host 'For Claude Desktop, fully quit and reopen the app after configuration.'
Write-Host 'Optional Fabric IQ skill: desktop-skills\fabriciq.zip (Desktop), .claude\skills\fabriciq (Code).'
Write-Host 'Sign-in requires your own Entra client ID. Tenant is optional for multitenant apps.'
Write-Host 'MCP never opens a browser automatically. Run the proxy login command explicitly after configuration.'
