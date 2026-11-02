$ErrorActionPreference = 'Stop'
$installer = Join-Path (Split-Path -Parent $PSScriptRoot) 'install.ps1'
$parseTokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
# Load only the merge function; do not install packages or change real client config.
$functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-McpConfiguration' }, $true)
. ([scriptblock]::Create($functionAst.Extent.Text))

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('fabriciq-test-' + [guid]::NewGuid())
[System.IO.Directory]::CreateDirectory($testRoot) | Out-Null
$server = [pscustomobject]@{ command = 'new-python'; args = @('proxy.py'); env = [pscustomobject]@{ FABRICIQ_TENANT = 'example.com' } }

function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }

try {
    $path = Join-Path $testRoot 'existing.json'
    $original = '{"preferences":{"theme":"dark"},"mcpServers":{"other":{"command":"keep-me","args":["one"]},"fabriciq":{"command":"old"}}}'
    [System.IO.File]::WriteAllText($path, $original)
    Write-McpConfiguration -Path $path -Server $server
    $actual = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
    Assert-True ($actual.preferences.theme -eq 'dark') 'Lost top-level settings'
    Assert-True ($actual.mcpServers.other.command -eq 'keep-me') 'Lost another MCP server'
    Assert-True ($actual.mcpServers.other.args[0] -eq 'one') 'Changed another server arguments'
    Assert-True ($actual.mcpServers.fabriciq.command -eq 'new-python') 'Did not replace fabriciq'
    $backups = @(Get-ChildItem -LiteralPath $testRoot -Filter 'existing.json.*.bak')
    Assert-True ($backups.Count -eq 1) 'Missing backup'
    Assert-True ([System.IO.File]::ReadAllText($backups[0].FullName) -ceq $original) 'Backup differs from original'
    Write-McpConfiguration -Path $path -Server $server
    Assert-True (@(Get-ChildItem -LiteralPath $testRoot -Filter 'existing.json.*.bak').Count -eq 2) 'Repeat install overwrote backup'

    foreach ($initial in @('{}', '{"mcpServers":null}', '{"preferences":{"theme":"dark"}}')) {
        $path = Join-Path $testRoot ([guid]::NewGuid().ToString() + '.json')
        [System.IO.File]::WriteAllText($path, $initial)
        Write-McpConfiguration -Path $path -Server $server
        $actual = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
        Assert-True ($actual.mcpServers.fabriciq.command -eq 'new-python') 'Failed to add missing servers'
    }

    $path = Join-Path $testRoot 'new\config.json'
    Write-McpConfiguration -Path $path -Server $server
    Assert-True (Test-Path -LiteralPath $path) 'Failed to create new config directory/file'

    foreach ($invalid in @('{broken', '[]', '[{}]', 'null', '42', '"text"', '{"mcpServers":[]}', '{"mcpServers":42}')) {
        $path = Join-Path $testRoot ([guid]::NewGuid().ToString() + '.json')
        [System.IO.File]::WriteAllText($path, $invalid)
        $failed = $false
        try { Write-McpConfiguration -Path $path -Server $server } catch { $failed = $true }
        Assert-True $failed "Accepted invalid configuration: $invalid"
        Assert-True ([System.IO.File]::ReadAllText($path) -ceq $invalid) 'Changed invalid configuration'
    }
    Write-Host 'PASS: installer parsing, merge, backup, repeated install, new config, invalid config preservation'
} finally {
    # Delete only the unique directory created by this test, within the OS temp folder.
    $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
    $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolvedTestRoot.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Leaf $resolvedTestRoot) -notlike 'fabriciq-test-*') { throw 'Unsafe test cleanup path' }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
}
