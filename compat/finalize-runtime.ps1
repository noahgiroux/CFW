param(
    [Parameter(Mandatory = $true)][string] $FragmentSource,
    [Parameter(Mandatory = $true)][string] $ContractPath,
    [Parameter(Mandatory = $true)][string] $MarkerPath
)

$ErrorActionPreference = 'Stop'
$script:diagnosticPath = $null
function Write-Diagnostic {
    param(
        [Parameter(Mandatory = $true)][string] $Message,
        [switch] $Reset
    )
    if (-not $script:diagnosticPath) { return }
    try {
        if ($Reset) {
            [IO.File]::WriteAllText($script:diagnosticPath, $Message)
        } else {
            [IO.File]::AppendAllText($script:diagnosticPath, $Message)
        }
    } catch { }
}
try {
    $script:diagnosticPath = Join-Path (Split-Path -Parent $MarkerPath) 'prepared-finalizer-diagnostic.txt'
    [IO.Directory]::CreateDirectory((Split-Path -Parent $script:diagnosticPath)) | Out-Null
    Write-Diagnostic -Reset -Message "[cfw] stage=prepared-finalizer-script-entry`n"
} catch {
    $script:diagnosticPath = $null
}
trap {
    try {
        $exceptionType = $_.Exception.GetType().FullName
        $exceptionMessage = $_.Exception.Message -replace "[`r`n]+", ' '
        Write-Diagnostic -Message "[cfw] stage=prepared-finalizer-failed type=$exceptionType message=$exceptionMessage`n"
    } finally {
        exit 70
    }
}
[Console]::Out.WriteLine('[cfw] stage=prepared-finalizer-script-entry')

$chocolateyRoot = Join-Path $env:ProgramData 'chocolatey'
$chocolateyBin = Join-Path $chocolateyRoot 'bin'
$choco = Join-Path $chocolateyRoot 'choco.exe'
$chocoShim = Join-Path $chocolateyBin 'choco.exe'
if (-not (Test-Path -LiteralPath $choco -PathType Leaf)) {
    throw "canonical Chocolatey executable is missing: $choco"
}
if (-not (Test-Path -LiteralPath $chocoShim -PathType Leaf)) {
    throw "Chocolatey compatibility shim is missing: $chocoShim"
}
Write-Diagnostic -Message "[cfw] stage=canonical-chocolatey-present`n"

[Environment]::SetEnvironmentVariable('ChocolateyInstall', $chocolateyRoot, 'Machine')
[Environment]::SetEnvironmentVariable('ChocolateyToolsLocation', 'C:\tools', 'Machine')
$env:ChocolateyInstall = $chocolateyRoot
$env:ChocolateyToolsLocation = 'C:\tools'
if (($env:Path -split ';') -notcontains $chocolateyBin) {
    $env:Path = $env:Path.TrimEnd(';') + ';' + $chocolateyBin
}

$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
if (-not $machinePath) { $machinePath = '' }
if (($machinePath -split ';') -notcontains $chocolateyBin) {
    $machinePath = $machinePath.TrimEnd(';') + ';' + $chocolateyBin
    [Environment]::SetEnvironmentVariable('Path', $machinePath, 'Machine')
}

$profileRoot = Join-Path $env:ProgramData 'Chocolatey-for-wine\profile.d'
$applicationProfileRoot = Join-Path $env:ProgramData 'Chocolatey-for-wine\application-profile.d'
New-Item -ItemType Directory -Force -Path $profileRoot, $applicationProfileRoot | Out-Null

. (Join-Path $PSScriptRoot 'profile-contract.ps1')
$requiredFragments = @(Get-CfwProfileFragments -ContractPath $ContractPath -FragmentSource $FragmentSource)
foreach ($name in $requiredFragments) {
    $source = Join-Path $FragmentSource $name
    Copy-Item -LiteralPath $source -Destination (Join-Path $profileRoot $name) -Force
}
Get-CfwProfileFragments -ContractPath $ContractPath -FragmentSource $profileRoot | Out-Null
Write-Diagnostic -Message "[cfw] stage=profile-fragments-installed`n"

$pwshRoot = $PSHOME
$profile = Join-Path $pwshRoot 'profile.ps1'
$legacyProfile = Join-Path $pwshRoot 'cfw-legacy-profile.ps1'
if ((Test-Path -LiteralPath $profile -PathType Leaf) -and
    -not (Test-Path -LiteralPath $legacyProfile -PathType Leaf)) {
    Move-Item -LiteralPath $profile -Destination $legacyProfile
}

@'
$legacyProfile = Join-Path $PSScriptRoot 'cfw-legacy-profile.ps1'
if (Test-Path -LiteralPath $legacyProfile -PathType Leaf) { . $legacyProfile }
$cfwProfileRoot = Join-Path $env:ProgramData 'Chocolatey-for-wine\profile.d'
if (Test-Path -LiteralPath $cfwProfileRoot -PathType Container) {
    Get-ChildItem -LiteralPath $cfwProfileRoot -Filter '*.ps1' -File | Sort-Object -Property Name | ForEach-Object { . $_.FullName }
}
$cfwApplicationProfileRoot = Join-Path $env:ProgramData 'Chocolatey-for-wine\application-profile.d'
if (Test-Path -LiteralPath $cfwApplicationProfileRoot -PathType Container) {
    Get-ChildItem -LiteralPath $cfwApplicationProfileRoot -Filter '*.ps1' -File | Sort-Object -Property Name | ForEach-Object { . $_.FullName }
}
Remove-Variable legacyProfile, cfwProfileRoot, cfwApplicationProfileRoot -ErrorAction SilentlyContinue
'@ | Set-Content -LiteralPath $profile -Encoding utf8
Write-Diagnostic -Message "[cfw] stage=profile-loader-installed`n"

$markerParent = Split-Path -Parent $MarkerPath
New-Item -ItemType Directory -Force -Path $markerParent | Out-Null
[IO.File]::WriteAllText(
    $MarkerPath,
    "[cfw] stage=prepared-finalizer-script-entry`n[cfw] stage=prepared-finalizer-complete`n"
)
Write-Diagnostic -Message "[cfw] stage=prepared-finalizer-complete`n"
[Console]::Out.WriteLine('[cfw] stage=prepared-finalizer-complete')
