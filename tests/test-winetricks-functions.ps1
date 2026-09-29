$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot '../winetricks.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "winetricks.ps1 has PowerShell parse errors: $parseErrors" }
function Assert-True($condition, $message) { if (-not $condition) { throw $message } }
$functionAst = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'w_download_to' }, $true)
if ($functionAst.Count -ne 1) { throw 'Expected exactly one w_download_to function' }
. ([scriptblock]::Create($functionAst[0].Extent.Text))
foreach ($name in @('func_vcrun2019', 'func_vcrun2022')) {
    $verb = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if ($verb.Count -ne 1 -or $verb[0].Extent.Text -notmatch 'dlloverride ''native,builtin'' \$i') {
        throw "$name must preserve Wine builtin fallback"
    }
}

if ($IsWindows) {
    $registryFunction = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'dlloverride' }, $true)
    if ($registryFunction.Count -ne 1) { throw 'Expected exactly one dlloverride function' }
    . ([scriptblock]::Create($registryFunction[0].Extent.Text))
    $dll = 'cfw_test_' + [guid]::NewGuid().ToString('N')
    dlloverride 'native,builtin' $dll
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Wine\DllOverrides', $true)
    try {
        Assert-True ($key.GetValueKind($dll) -eq [Microsoft.Win32.RegistryValueKind]::String) 'DLL override is not REG_SZ'
        Assert-True ($key.GetValue($dll) -ceq 'native,builtin') 'DLL override readback failed'
        $key.DeleteValue($dll)
    }
    finally { $key.Dispose() }
}

function global:wget2 {
    $script:requests++
    $outputIndex = [array]::IndexOf($args, '-O')
    if ($outputIndex -lt 0 -or $outputIndex + 1 -ge $args.Count) { throw 'wget2 missing -O destination' }
    if ($script:downloadCode -ne 0) {
        $global:LASTEXITCODE = $script:downloadCode
        return
    }
    [IO.File]::WriteAllText($args[$outputIndex + 1], $script:downloadBody)
    $global:LASTEXITCODE = 0
}

$cachedir = Join-Path ([IO.Path]::GetTempPath()) ('cfw-winetricks-' + [guid]::NewGuid().ToString('N'))
$script:requests = 0
$script:downloadCode = 0
$script:downloadBody = 'verified payload'
$digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($script:downloadBody)))
try {
    # The URL name intentionally differs from the requested VC++ x86 cache name.
    w_download_to 'vcrun2022/32' 'https://example.invalid/vc_redist.x86.exe' 'VC__redist.x86.exe' $digest
    $destination = Join-Path $cachedir 'vcrun2022/32/VC__redist.x86.exe'
    Assert-True ([IO.File]::ReadAllText($destination) -eq $script:downloadBody) 'requested filename not populated'
    Assert-True (-not (Test-Path (Join-Path $cachedir 'vcrun2022/32/vc_redist.x86.exe'))) 'server filename leaked into cache'
    Assert-True ((@(Get-ChildItem (Split-Path $destination) -Filter '*.part')).Count -eq 0) 'temporary download remained'

    $env:CFW_OFFLINE = '1'
    w_download_to 'vcrun2022/32' 'https://example.invalid/vc_redist.x86.exe' 'VC__redist.x86.exe' $digest
    Assert-True ($script:requests -eq 1) 'offline cache hit used network'
    [IO.File]::WriteAllText($destination, 'corrupt')
    try { w_download_to 'vcrun2022/32' 'https://example.invalid/vc_redist.x86.exe' 'VC__redist.x86.exe' $digest; throw 'corrupt cache accepted' }
    catch { Assert-True ($_.Exception.Message -like '*failed SHA-256*') 'corrupt cache failure missing' }
    Assert-True ($script:requests -eq 1) 'corrupt offline cache used network'
    try { w_download_to 'vcrun2022/32' 'https://example.invalid/nohash' 'nohash.exe'; throw 'offline unverified cache accepted' }
    catch { Assert-True ($_.Exception.Message -like '*expected SHA-256 required*') 'offline hash guard missing' }

    Remove-Item Env:CFW_OFFLINE
    $script:downloadCode = 23
    try { w_download_to 'vcrun2022/32' 'https://example.invalid/failure' 'failure.exe' $digest; throw 'download failure accepted' }
    catch { Assert-True ($_.Exception.Message -like '*code 23*') 'downloader exit code lost' }
    Assert-True (-not (Test-Path (Join-Path $cachedir 'vcrun2022/32/failure.exe'))) 'failed download promoted'
    $script:downloadCode = 0
    $script:downloadBody = 'wrong payload'
    try { w_download_to 'vcrun2022/32' 'https://example.invalid/wrong' 'wrong.exe' $digest; throw 'wrong digest accepted' }
    catch { Assert-True ($_.Exception.Message -like '*failed SHA-256*') 'download hash failure missing' }
    Assert-True (-not (Test-Path (Join-Path $cachedir 'vcrun2022/32/wrong.exe'))) 'wrong digest promoted'
}
finally {
    Remove-Item Env:CFW_OFFLINE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $cachedir -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host 'winetricks downloader behavioral checks passed'
