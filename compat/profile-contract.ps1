function Get-CfwProfileFragments {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $ContractPath,
        [Parameter(Mandatory = $true)][string] $FragmentSource
    )
    $contract = Get-Content -LiteralPath $ContractPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($contract.schemaVersion -cne 'cfw.compatibility-contract/v3') {
        throw 'Unexpected profile compatibility contract'
    }
    $declared = @($contract.contributes.'powershell.profile-fragments')
    if ($declared.Count -eq 0) { throw 'Profile fragment inventory is empty' }
    $names = @(foreach ($path in $declared) {
        if ($path -isnot [string] -or $path -cnotmatch '^profile\.d/([0-9]{2}-[A-Za-z0-9-]+\.ps1)$') {
            throw 'Invalid profile fragment path'
        }
        $Matches[1]
    })
    $ordered = @($names | Sort-Object -CaseSensitive -Unique)
    if (($names -join "`n") -cne ($ordered -join "`n")) {
        throw 'Profile fragments must be unique and lexically ordered'
    }
    $actual = @(Get-ChildItem -LiteralPath $FragmentSource -Filter '*.ps1' -File -ErrorAction Stop | Sort-Object -Property Name -CaseSensitive | ForEach-Object { $_.Name })
    if (($actual -join "`n") -cne ($names -join "`n")) {
        throw 'Profile fragment files do not match the compatibility contract'
    }
    return $names
}
