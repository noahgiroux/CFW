$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'compat/profile-contract.ps1')
$contractPath = Join-Path $repo 'compat/contract.json'
$source = Join-Path $repo 'compat/profile.d'
$expected = @((Get-Content $contractPath -Raw | ConvertFrom-Json).contributes.'powershell.profile-fragments' | ForEach-Object { Split-Path -Leaf $_ })
$observed = @(Get-CfwProfileFragments -ContractPath $contractPath -FragmentSource $source)
if (($expected -join "`n") -cne ($observed -join "`n")) { throw 'Canonical profile inventory did not round-trip' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('cfw-profile-' + [Guid]::NewGuid().ToString('N'))
New-Item -Path $temporary -ItemType Directory | Out-Null
try {
    $fragments = Join-Path $temporary 'fragments'
    New-Item -Path $fragments -ItemType Directory | Out-Null
    Copy-Item -Path (Join-Path $source '*.ps1') -Destination $fragments
    $candidate = Join-Path $temporary 'contract.json'
    foreach ($case in @('empty', 'duplicate', 'reversed', 'traversal', 'wrong-schema', 'missing', 'extra')) {
        $contract = Get-Content $contractPath -Raw | ConvertFrom-Json
        $paths = @($contract.contributes.'powershell.profile-fragments')
        switch ($case) {
            'empty' { $paths = @() }
            'duplicate' { $paths = @($paths[0], $paths[0]) }
            'reversed' { [array]::Reverse($paths) }
            'traversal' { $paths = @('profile.d/../outside.ps1') }
            'wrong-schema' { $contract.schemaVersion = 'unknown' }
            'missing' { Remove-Item -LiteralPath (Join-Path $fragments $expected[0]) }
            'extra' { Set-Content -LiteralPath (Join-Path $fragments '99-extra.ps1') -Value '# extra' }
        }
        $contract.contributes.'powershell.profile-fragments' = $paths
        $contract | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $candidate
        $rejected = $false
        try { Get-CfwProfileFragments -ContractPath $candidate -FragmentSource $fragments | Out-Null }
        catch { $rejected = $true }
        if (-not $rejected) { throw "Profile contract accepted invalid case: $case" }
        if ($case -eq 'missing') { Copy-Item -LiteralPath (Join-Path $source $expected[0]) -Destination $fragments }
        if ($case -eq 'extra') { Remove-Item -LiteralPath (Join-Path $fragments '99-extra.ps1') }
    }
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
Write-Output '[cfw] profile contract behavior passed'
