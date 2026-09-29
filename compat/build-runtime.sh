#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_root="${1:-$repo_root/dist/cfw-runtime}"
wine_prefix="${WINEPREFIX:-$output_root/prefix}"
work="${CFW_BUILD_CACHE:-$output_root/cache}"
payload_cache="$work/choc_install_files"
release_root="$work/release"
release_dir="$release_root/Chocolatey-for-wine"
logs="$output_root/logs"
evidence_name="${CFW_RUNTIME_EVIDENCE_NAME:-runtime.json}"
manifest_name="${CFW_RUNTIME_MANIFEST_NAME:-cfw-runtime-manifest.json}"
metadata="$output_root/$evidence_name"
manifest="$output_root/$manifest_name"
runtime_inputs="$repo_root/compat/runtime-inputs.json"
compat_contract="$repo_root/compat/contract.json"
compiled_installer="${CFW_COMPILED_INSTALLER:-$repo_root/compat/ChoCinstaller-under-test.exe}"
artifact_name="${CFW_RUNTIME_ARTIFACT_NAME:-cfw-runtime-prefix}"
stage="setup"

for output_name in "$artifact_name" "$evidence_name" "$manifest_name"; do
  case "$output_name" in
    *[!A-Za-z0-9._-]* | '')
      echo "[cfw] invalid runtime output name: $output_name" >&2
      exit 64
      ;;
  esac
done

# The workflow may reuse an output directory across attempts. Remove the prior
# public diagnostic before any per-run validation can fail, so a new failure is
# never attributed evidence from an older run.
if ! [[ ! -L "$output_root" && ! -L "$logs" ]]; then
  echo "[cfw] output root and logs directory must not be symbolic links" >&2
  exit 65
fi
mkdir -p "$output_root" "$logs"
rm -f "$logs/prepared-finalizer-diagnostic.log"

[[ -f "$runtime_inputs" ]] || {
  echo "[cfw] runtime inputs file is missing: $runtime_inputs" >&2
  exit 65
}
[[ -f "$compat_contract" ]] || {
  echo "[cfw] compatibility contract is missing: $compat_contract" >&2
  exit 65
}
[[ -s "$compiled_installer" ]] || {
  echo "[cfw] compiled installer under test is missing: $compiled_installer" >&2
  exit 65
}

mkdir -p "$output_root" "$payload_cache" "$release_root" "$logs"
export WINEPREFIX="$wine_prefix"
export WINEARCH=win64
unset CFW_CONTAINER_BUILDER
unset WINEDLLOVERRIDES

CFW_RUNTIME_INPUTS_SHA256="$(sha256sum "$runtime_inputs" | awk '{print $1}')"
CFW_CONTRACT_SHA256="$(sha256sum "$compat_contract" | awk '{print $1}')"
CFW_INSTALLER_SHA256="$(sha256sum "$compiled_installer" | awk '{print $1}')"
CFW_SOURCE_REVISION="${CFW_SOURCE_REVISION:?CFW_SOURCE_REVISION must be the exact source commit}"
CFW_WINE_IMAGE="${CFW_WINE_IMAGE:?CFW_WINE_IMAGE must be the digest-pinned Wine producer image}"
CFW_WINE_SESSION_CONTRACT="${CFW_WINE_SESSION_CONTRACT:?CFW_WINE_SESSION_CONTRACT must bind the producer session contract}"
CFW_EXPECTED_WINE_VERSION="${CFW_EXPECTED_WINE_VERSION:?CFW_EXPECTED_WINE_VERSION must bind the Wine artifact identity}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:?SOURCE_DATE_EPOCH must identify the exact source revision time}"
if [[ ! "$CFW_SOURCE_REVISION" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; then
  echo "[cfw] CFW_SOURCE_REVISION must be a full lowercase Git commit SHA" >&2
  exit 64
fi
if [[ ! "$CFW_WINE_IMAGE" =~ ^ghcr\.io/pelagians/cage-wine@sha256:[0-9a-f]{64}$ ]]; then
  echo "[cfw] CFW_WINE_IMAGE must be a ghcr.io/pelagians/cage-wine digest" >&2
  exit 64
fi
if [[ ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
  echo "[cfw] SOURCE_DATE_EPOCH must be a non-negative integer" >&2
  exit 64
fi
export CFW_RUNTIME_INPUTS_SHA256 CFW_CONTRACT_SHA256 CFW_INSTALLER_SHA256 CFW_SOURCE_REVISION CFW_WINE_IMAGE SOURCE_DATE_EPOCH

on_error() {
  rc="$?"
  printf '[cfw] ERROR stage=%s rc=%s\n' "$stage" "$rc" | tee -a "$logs/build-stages.log" >&2
  exit "$rc"
}

trap on_error ERR

mark_stage() {
  stage="$1"
  printf '[cfw] stage=%s\n' "$stage" | tee -a "$logs/build-stages.log"
}

normalize_log() {
  local path="$1" normalized="${1}.normalized"
  tr -d '\r' < "$path" > "$normalized"
  mv -f "$normalized" "$path"
}

read_single_observed_line() {
  local label="$1" path="$2"
  local -a lines
  mapfile -t lines <"$path"
  if [[ "${#lines[@]}" -ne 1 || -z "${lines[0]}" ]]; then
    printf '[cfw] %s must emit exactly one non-empty normalized line\n' "$label" >&2
    return 65
  fi
  printf '%s\n' "${lines[0]}"
}

input_value() {
  local section="$1" field="$2"
  python3 - "$runtime_inputs" "$section" "$field" <<'PY2'
import json
import re
import sys

path, section, field = sys.argv[1:]
try:
    value = json.load(open(path, encoding="utf-8"))["downloads"][section][field]
except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid runtime input {section}.{field}: {exc}")
if not isinstance(value, str) or not value:
    raise SystemExit(f"invalid runtime input {section}.{field}")
if field == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", value):
    raise SystemExit(f"invalid runtime input digest {section}.{field}")
print(value)
PY2
}

runtime_value() {
  local field="$1"
  python3 - "$runtime_inputs" "$field" <<'PY2'
import json
import re
import sys

path, field = sys.argv[1:]
try:
    value = json.load(open(path, encoding="utf-8"))[field]
except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid runtime input {field}: {exc}")
if not isinstance(value, str) or not value:
    raise SystemExit(f"invalid runtime input {field}")
if field == "runtimeId" and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
    raise SystemExit("invalid runtimeId")
print(value)
PY2
}

runtime_version() {
  local component="$1"
  python3 - "$runtime_inputs" "$component" <<'PY2'
import json
import re
import sys

path, component = sys.argv[1:]
try:
    value = json.load(open(path, encoding="utf-8"))["versions"][component]
except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid runtime version {component}: {exc}")
if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
    raise SystemExit(f"invalid runtime version {component}")
print(value)
PY2
}

CFW_RUNTIME_ID="$(runtime_value runtimeId)"
mapfile -t contract_identity < <(python3 - "$compat_contract" <<'PY2'
import json
import re
import sys
contract = json.load(open(sys.argv[1], encoding="utf-8"))
if contract.get("schemaVersion") != "cfw.compatibility-contract/v3":
    raise SystemExit("invalid compatibility contract schema")
candidates = contract["build"]["wineCandidates"]
if not isinstance(candidates, list) or len(candidates) != 1:
    raise SystemExit("Phase 1 compatibility contract must declare exactly one Wine candidate")
value = candidates[0]
if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
    raise SystemExit("invalid compatibility contract Wine candidate")
session_contract = contract["build"].get("producerSessionContract")
if session_contract != "cage.selkies-wayland/v1":
    raise SystemExit("invalid producer session contract")
print(contract["schemaVersion"])
print(value)
print(session_contract)
PY2
)
CFW_CONTRACT_SCHEMA="${contract_identity[0]:?missing compatibility contract schema}"
CFW_CONTRACT_WINE_VERSION="${contract_identity[1]:?missing compatibility contract Wine candidate}"
CFW_CONTRACT_SESSION_CONTRACT="${contract_identity[2]:?missing producer session contract}"
if [[ "$CFW_WINE_SESSION_CONTRACT" != "$CFW_CONTRACT_SESSION_CONTRACT" ]]; then
  printf '[cfw] producer session contract mismatch: expected=%s selected=%s\n' \
    "$CFW_CONTRACT_SESSION_CONTRACT" "$CFW_WINE_SESSION_CONTRACT" >&2
  exit 64
fi
if [[ "$CFW_EXPECTED_WINE_VERSION" != "wine-$CFW_CONTRACT_WINE_VERSION" ]]; then
  printf '[cfw] Wine selection does not match compatibility contract: expected=wine-%s selected=%s\n' \
    "$CFW_CONTRACT_WINE_VERSION" "$CFW_EXPECTED_WINE_VERSION" >&2
  exit 64
fi
CFW_EXPECTED_POWERSHELL_VERSION="$(runtime_version powershell)"
CFW_EXPECTED_CHOCOLATEY_VERSION="$(runtime_version chocolatey)"
CFW_EXPECTED_SYNCHRO_VERSION="$(runtime_version synchro)"
export CFW_RUNTIME_ID CFW_CONTRACT_SCHEMA CFW_WINE_SESSION_CONTRACT CFW_EXPECTED_WINE_VERSION CFW_EXPECTED_POWERSHELL_VERSION CFW_EXPECTED_CHOCOLATEY_VERSION CFW_EXPECTED_SYNCHRO_VERSION

checkout_source_sha256() {
  local source_name="$1"
  python3 - "$runtime_inputs" "$source_name" <<'PY2'
import json
import re
import sys

path, source_name = sys.argv[1:]
try:
    value = json.load(open(path, encoding="utf-8"))["checkoutSources"][source_name]["sha256"]
except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid checkout source {source_name}: {exc}")
if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
    raise SystemExit(f"invalid checkout source digest {source_name}")
print(value)
PY2
}

verify_checkout_source() {
  local source_name="$1" source_path="$2" expected actual
  expected="$(checkout_source_sha256 "$source_name")"
  actual="$(sha256sum "$source_path" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf '[cfw] checkout source checksum mismatch: %s\nexpected=%s\nactual=%s\n' \
      "$source_name" "$expected" "$actual" >&2
    return 69
  fi
}

fetch_verified() {
  local url="$1" expected="$2" destination="$3"
  mkdir -p "$(dirname "$destination")"
  if [[ -f "$destination" ]] && [[ "$(sha256sum "$destination" | awk '{print $1}')" == "$expected" ]]; then
    return 0
  fi
  rm -f "$destination" "$destination.part"
  curl -fL --retry 4 --connect-timeout 30 --max-time 1800 -o "$destination.part" "$url"
  local actual
  actual="$(sha256sum "$destination.part" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf 'checksum mismatch for %s\nexpected=%s\nactual=%s\n' "$url" "$expected" "$actual" >&2
    return 69
  fi
  mv -f "$destination.part" "$destination"
}

fetch_input() {
  local input_name="$1" destination="$2"
  fetch_verified "$(input_value "$input_name" url)" "$(input_value "$input_name" sha256)" "$destination"
}

winepath_to_windows() {
  local label="$1" host_path="$2" log settle_log status converted rc settle_rc
  local -a converted_lines
  log="$logs/winepath-${label}.log"
  settle_log="$logs/winepath-${label}-settle.log"
  status="$logs/winepath-${label}.status"
  rm -f "$log" "$settle_log" "$status"
  trap - ERR
  set +e
  timeout --kill-after=10s 60s winepath -w "$host_path" >"$log" 2>&1
  rc="$?"
  timeout --kill-after=10s 60s wineserver -w >"$settle_log" 2>&1
  settle_rc="$?"
  set -e
  trap on_error ERR
  printf '%s %s\n' "$rc" "$settle_rc" >"$status"
  if [[ "$rc" -ne 0 || "$settle_rc" -ne 0 ]]; then
    printf '[cfw] Wine path conversion failed: label=%s process=%s settle=%s host_path=%s prefix=%s dll_overrides=%s\n' \
      "$label" "$rc" "$settle_rc" "$host_path" "$WINEPREFIX" "${WINEDLLOVERRIDES:-<unset>}" >&2
    cat "$log" >&2 || true
    cat "$settle_log" >&2 || true
    return 70
  fi
  normalize_log "$log"
  mapfile -t converted_lines <"$log"
  if [[ "${#converted_lines[@]}" -ne 1 || -z "${converted_lines[0]}" ]]; then
    printf '[cfw] Wine path conversion failed: label=%s process=0 settle=0 non-canonical-output host_path=%s prefix=%s\n' \
      "$label" "$host_path" "$WINEPREFIX" >&2
    return 65
  fi
  converted="${converted_lines[0]}"
  printf '%s\n' "$converted"
}

build_smoke_package() {
  local smoke_feed="$1" smoke_package="$smoke_feed/cfw-runtime-smoke.0.1.0.nupkg"
  mkdir -p "$smoke_feed"
  python3 - "$smoke_package" <<'PY2'
from pathlib import Path
import sys
import zipfile

archive = Path(sys.argv[1])
install = r'''$ErrorActionPreference = 'Stop'
$marker = 'C:\ProgramData\CFW\RuntimeProbe\chocolatey-install.txt'
[IO.Directory]::CreateDirectory((Split-Path -Parent $marker)) | Out-Null
[IO.File]::WriteAllText($marker, 'installed')
[Console]::Out.WriteLine('[cfw-smoke] install-script-executed')
'''
uninstall = r'''$ErrorActionPreference = 'Stop'
$marker = 'C:\ProgramData\CFW\RuntimeProbe\chocolatey-uninstall.txt'
[IO.Directory]::CreateDirectory((Split-Path -Parent $marker)) | Out-Null
[IO.File]::WriteAllText($marker, 'uninstalled')
[Console]::Out.WriteLine('[cfw-smoke] uninstall-script-executed')
'''
nuspec = '''<?xml version="1.0"?>
<package><metadata><id>cfw-runtime-smoke</id><version>0.1.0</version><title>CFW runtime smoke</title><authors>CFW</authors><description>Deterministic CFW runtime lifecycle proof.</description></metadata></package>
'''
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
    package.writestr("cfw-runtime-smoke.nuspec", nuspec)
    package.writestr("tools/chocolateyinstall.ps1", install)
    package.writestr("tools/chocolateyuninstall.ps1", uninstall)
PY2
  test -s "$smoke_package"
}

mark_stage fetch-inputs
release_archive="$work/$(input_value cfwRelease filename)"
fetch_input cfwRelease "$release_archive"
rm -rf "$release_root"
mkdir -p "$release_root"
7z x -y "$release_archive" "-o$release_root" >"$logs/release-extract.log"
release_installer="$(input_value cfwRelease installerFilename)"
if [[ ! "$release_installer" =~ ^ChoCinstaller_[0-9]\.[0-9][A-Za-z]\.([0-9])([0-9])([0-9])\.exe$ ]]; then
  printf '[cfw] release installer filename is incompatible with the installer version parser: %s\n' \
    "$release_installer" >&2
  exit 64
fi
release_powershell_version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
if [[ "$release_powershell_version" != "$CFW_EXPECTED_POWERSHELL_VERSION" ]]; then
  printf '[cfw] release installer PowerShell version mismatch: filename=%s locked=%s\n' \
    "$release_powershell_version" "$CFW_EXPECTED_POWERSHELL_VERSION" >&2
  exit 64
fi
[[ -f "$release_dir/$release_installer" ]]
cp -f "$compiled_installer" "$release_dir/$release_installer"
printf '%s  %s\n' "$CFW_INSTALLER_SHA256" "$compiled_installer" >"$logs/installer-under-test.sha256"
verify_checkout_source choc_install.ps1 "$repo_root/choc_install.ps1"
cp -f "$repo_root/choc_install.ps1" "$release_dir/choc_install.ps1"
verify_checkout_source winetricks.ps1 "$repo_root/winetricks.ps1"

for input_name in chocolatey powershell dotnet mscoree d3d64 d3d32 conemu sevenZipExtractor windowsPowerShell; do
  fetch_input "$input_name" "$payload_cache/$(input_value "$input_name" filename)"
done

mark_stage prove-wine-identity
trap - ERR
set +e
timeout --kill-after=10s 60s wine --version >"$logs/wine-version.log" 2>&1
wine_version_rc="$?"
timeout --kill-after=10s 60s wineserver -w >"$logs/wine-version-settle.log" 2>&1
wine_version_settle_rc="$?"
set -e
trap on_error ERR
normalize_log "$logs/wine-version.log"
CFW_OBSERVED_WINE_VERSION="$(read_single_observed_line wine-version "$logs/wine-version.log")"
if [[ "$wine_version_rc" -ne 0 || "$wine_version_settle_rc" -ne 0 || "$CFW_OBSERVED_WINE_VERSION" != "$CFW_EXPECTED_WINE_VERSION" ]]; then
  printf '[cfw] Wine identity proof failed: expected=%s observed=%s process=%s settle=%s image=%s\n' \
    "$CFW_EXPECTED_WINE_VERSION" "$CFW_OBSERVED_WINE_VERSION" "$wine_version_rc" "$wine_version_settle_rc" "$CFW_WINE_IMAGE" >&2
  exit 70
fi
export CFW_OBSERVED_WINE_VERSION

mark_stage initialize-prefix
# Cage Wine initializes fresh prefixes with these overrides to prevent Mono/HTML
# first-run setup from blocking wineboot. Clear them immediately afterwards so
# CFW's .NET, CLR, and PowerShell work uses its own compatibility policy.
export WINEDLLOVERRIDES="mscoree,mshtml="
rm -rf "$wine_prefix"
mkdir -p "$wine_prefix"
trap - ERR
set +e
timeout --kill-after=15s 300s wine wineboot --init >"$logs/wineboot.log" 2>&1
wineboot_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/wineboot.log" 2>&1
wineboot_settle_rc="$?"
set -e
trap on_error ERR
if [[ "$wineboot_rc" -ne 0 || "$wineboot_settle_rc" -ne 0 || ! -d "$wine_prefix/drive_c" ]]; then
  printf 'Wine prefix initialization failed: process=%s settle=%s\n' "$wineboot_rc" "$wineboot_settle_rc" >&2
  cat "$logs/wineboot.log" >&2 || true
  exit 70
fi

# CFW owns the post-bootstrap compatibility policy. Do not carry the Wineboot
# suppression into installer, CLR, or external PowerShell execution.
export WINEDLLOVERRIDES=""

mark_stage install-cfw
cfw_cache_win="$(winepath_to_windows cfw-cache "$work")"
export CFW_CACHE="$cfw_cache_win"
export CFW_OFFLINE=1
export CFW_CONTAINER_BUILDER=1
export CFW_EXTERNAL_POWERSHELL=1
installer_win="$(winepath_to_windows cfw-installer "$release_dir/$release_installer")"
trap - ERR
set +e
timeout --kill-after=30s "${CFW_INSTALL_TIMEOUT:-7200s}" wine "$installer_win" /s /q >"$logs/installer.log" 2>&1
installer_rc="$?"
timeout --kill-after=15s 300s wineserver -w >>"$logs/installer.log" 2>&1
installer_settle_rc="$?"
set -e
trap on_error ERR
pwsh="$wine_prefix/drive_c/Program Files/PowerShell/7/pwsh.exe"
choco="$wine_prefix/drive_c/ProgramData/chocolatey/choco.exe"
choco_shim="$wine_prefix/drive_c/ProgramData/chocolatey/bin/choco.exe"

if [[ "$installer_rc" -ne 0 || "$installer_settle_rc" -ne 0 ]]; then
  printf 'CFW installer failed: installer=%s settle=%s\n' "$installer_rc" "$installer_settle_rc" >&2
  tail -160 "$logs/installer.log" >&2 || true
  exit 70
fi
[[ -s "$pwsh" && -s "$choco" && -s "$choco_shim" ]] || {
  printf 'CFW output incomplete: pwsh=%s choco=%s choco_shim=%s\n' \
    "$pwsh" "$choco" "$choco_shim" >&2
  exit 70
}
winetricks_script="$wine_prefix/drive_c/ProgramData/Chocolatey-for-wine/winetricks.ps1"
[[ -f "$winetricks_script" && ! -L "$winetricks_script" ]] || {
  echo '[cfw] installer did not provide a regular winetricks script' >&2
  exit 70
}
cp -f "$repo_root/winetricks.ps1" "$winetricks_script"
verify_checkout_source winetricks.ps1 "$winetricks_script"

mark_stage prove-clr-policy
clr_policy_key='HKCU\Software\Wine\DllOverrides'
trap - ERR
set +e
timeout --kill-after=10s 60s wine reg query "$clr_policy_key" /v mscoree \
  >"$logs/clr-policy.log" 2>&1
clr_policy_query_rc="$?"
timeout --kill-after=10s 60s wineserver -w >>"$logs/clr-policy.log" 2>&1
clr_policy_settle_rc="$?"
normalize_log "$logs/clr-policy.log"
grep -Eq 'mscoree[[:space:]]+REG_SZ[[:space:]]+native[[:space:]]*$' \
  "$logs/clr-policy.log"
clr_policy_value_rc="$?"
set -e
trap on_error ERR
if [[ "$clr_policy_query_rc" -ne 0 || "$clr_policy_settle_rc" -ne 0 || \
      "$clr_policy_value_rc" -ne 0 ]]; then
  printf '[cfw] native .NET CLR policy failed: query=%s settle=%s value=%s\n' \
    "$clr_policy_query_rc" "$clr_policy_settle_rc" "$clr_policy_value_rc" >&2
  cat "$logs/clr-policy.log" >&2 || true
  exit 70
fi

mark_stage apply-pre-pwsh-policy
pwsh_policy="$repo_root/compat/pwsh-policy.reg"
pwsh_policy_win="$(winepath_to_windows pwsh-policy "$pwsh_policy")"
pwsh_policy_key='HKCU\Software\Wine\AppDefaults\pwsh.exe\DllOverrides'
: >"$logs/pwsh-policy.log"
trap - ERR
set +e
timeout --kill-after=10s 60s winecfg /v win10 >>"$logs/pwsh-policy.log" 2>&1
pwsh_winecfg_rc="$?"
timeout --kill-after=10s 60s wineserver -w >>"$logs/pwsh-policy.log" 2>&1
pwsh_winecfg_settle_rc="$?"
timeout --kill-after=10s 60s wine regedit /S "$pwsh_policy_win" >>"$logs/pwsh-policy.log" 2>&1
pwsh_regedit_rc="$?"
timeout --kill-after=10s 60s wineserver -w >>"$logs/pwsh-policy.log" 2>&1
pwsh_regedit_settle_rc="$?"
timeout --kill-after=10s 60s wine reg query "$pwsh_policy_key" >>"$logs/pwsh-policy.log" 2>&1
pwsh_query_rc="$?"
timeout --kill-after=10s 60s wineserver -w >>"$logs/pwsh-policy.log" 2>&1
pwsh_query_settle_rc="$?"
normalize_log "$logs/pwsh-policy.log"
grep -Eq 'amsi[[:space:]]+REG_SZ[[:space:]]*$' "$logs/pwsh-policy.log"
pwsh_amsi_rc="$?"
grep -Eq 'dwmapi[[:space:]]+REG_SZ[[:space:]]*$' "$logs/pwsh-policy.log"
pwsh_dwmapi_rc="$?"
grep -Eq 'mscoree[[:space:]]+REG_SZ[[:space:]]+builtin[[:space:]]*$' "$logs/pwsh-policy.log"
pwsh_mscoree_rc="$?"
grep -Eq 'rpcrt4[[:space:]]+REG_SZ[[:space:]]+native,builtin[[:space:]]*$' "$logs/pwsh-policy.log"
pwsh_rpcrt4_rc="$?"
set -e
trap on_error ERR
if [[ "$pwsh_winecfg_rc" -ne 0 || "$pwsh_winecfg_settle_rc" -ne 0 || \
      "$pwsh_regedit_rc" -ne 0 || "$pwsh_regedit_settle_rc" -ne 0 || \
      "$pwsh_query_rc" -ne 0 || "$pwsh_query_settle_rc" -ne 0 || \
      "$pwsh_amsi_rc" -ne 0 || "$pwsh_dwmapi_rc" -ne 0 || \
      "$pwsh_mscoree_rc" -ne 0 || "$pwsh_rpcrt4_rc" -ne 0 ]]; then
  printf '[cfw] pre-PowerShell policy failed: winecfg=%s/%s regedit=%s/%s query=%s/%s amsi=%s dwmapi=%s mscoree=%s rpcrt4=%s\n' \
    "$pwsh_winecfg_rc" "$pwsh_winecfg_settle_rc" "$pwsh_regedit_rc" "$pwsh_regedit_settle_rc" \
    "$pwsh_query_rc" "$pwsh_query_settle_rc" "$pwsh_amsi_rc" "$pwsh_dwmapi_rc" \
    "$pwsh_mscoree_rc" "$pwsh_rpcrt4_rc" >&2
  exit 70
fi

mark_stage prove-pwsh
command -v wineconsole >/dev/null
: "${DISPLAY:?CFW PowerShell proof requires the producer image X display}"
probe_dir="$wine_prefix/drive_c/ProgramData/CFW/RuntimeProbe"
pwsh_marker="$probe_dir/pwsh.txt"
pwsh_evidence="$probe_dir/pwsh-evidence.txt"
pwsh_evidence_expected="$logs/pwsh-evidence.expected"
pwsh_marker_expected="$logs/pwsh-marker.expected"
pwsh_probe_script="$probe_dir/pwsh-probe.ps1"
mkdir -p "$probe_dir"
rm -f "$pwsh_marker" "$pwsh_evidence"
printf '[cfw] pwsh-script-entry\n[cfw] pwsh=%s\n' \
  "$CFW_EXPECTED_POWERSHELL_VERSION" >"$pwsh_evidence_expected"
printf '%s' "$CFW_EXPECTED_POWERSHELL_VERSION" >"$pwsh_marker_expected"
cat > "$pwsh_probe_script" <<'PS1'
param(
    [Parameter(Mandatory = $true)][string]$MarkerPath,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion
)
$ErrorActionPreference = 'Stop'
[Console]::Out.WriteLine('[cfw] pwsh-script-entry')
$version = $PSVersionTable.PSVersion.ToString()
[Console]::Out.WriteLine('[cfw] pwsh=' + $version)
$evidencePath = Join-Path (Split-Path -Parent $MarkerPath) 'pwsh-evidence.txt'
[IO.File]::WriteAllText(
    $evidencePath,
    "[cfw] pwsh-script-entry`n[cfw] pwsh=$version`n"
)
if ($version -cne $ExpectedVersion) {
    throw "PowerShell version mismatch: expected=$ExpectedVersion observed=$version"
}
[IO.File]::WriteAllText($MarkerPath, $version)
PS1
pwsh_win="$(winepath_to_windows pwsh-executable "$pwsh")"
pwsh_launcher=(wineconsole "$pwsh_win")
pwsh_probe_script_win="$(winepath_to_windows pwsh-probe-script "$pwsh_probe_script")"
pwsh_marker_win="$(winepath_to_windows pwsh-marker "$pwsh_marker")"
trap - ERR
set +e
timeout --kill-after=15s 300s "${pwsh_launcher[@]}" -NoLogo -NoProfile -NonInteractive \
  -File "$pwsh_probe_script_win" "$pwsh_marker_win" "$CFW_EXPECTED_POWERSHELL_VERSION" \
  >"$logs/pwsh-probe.log" 2>&1
pwsh_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/pwsh-probe.log" 2>&1
pwsh_settle_rc="$?"
if [[ -s "$pwsh_evidence" ]]; then
  cat "$pwsh_evidence" >>"$logs/pwsh-probe.log"
fi
normalize_log "$logs/pwsh-probe.log"
cmp -s "$pwsh_evidence_expected" "$pwsh_evidence"
pwsh_entry_rc="$?"
cmp -s "$pwsh_marker_expected" "$pwsh_marker"
pwsh_version_rc="$?"
set -e
trap on_error ERR
if [[ "$pwsh_rc" -ne 0 || "$pwsh_settle_rc" -ne 0 || "$pwsh_entry_rc" -ne 0 || "$pwsh_version_rc" -ne 0 || ! -s "$pwsh_marker" ]]; then
  if [[ -s "$pwsh_marker" ]]; then marker_status=present; else marker_status=missing; fi
  printf 'PowerShell runtime proof failed: process=%s settle=%s entry=%s version=%s marker=%s path=%s\n' \
    "$pwsh_rc" "$pwsh_settle_rc" "$pwsh_entry_rc" "$pwsh_version_rc" "$marker_status" "$pwsh_win" \
    | tee "$logs/pwsh-proof-summary.log" >&2
  cat "$logs/pwsh-probe.log" >&2 || true
  trap - ERR
  set +e
  pwsh_host_trace="$probe_dir/pwsh-host-trace.log"
  dotnet_host_trace="$probe_dir/dotnet-host-trace.log"
  rm -f "$pwsh_host_trace" "$dotnet_host_trace"
  COREHOST_TRACE=1 COREHOST_TRACE_VERBOSITY=4 \
  COREHOST_TRACEFILE='C:\ProgramData\CFW\RuntimeProbe\pwsh-host-trace.log' \
  DOTNET_HOST_TRACE=1 DOTNET_HOST_TRACE_VERBOSITY=4 \
  DOTNET_HOST_TRACEFILE='C:\ProgramData\CFW\RuntimeProbe\dotnet-host-trace.log' \
  timeout --kill-after=15s 90s "${pwsh_launcher[@]}" -NoLogo -NoProfile -NonInteractive -Version \
    >"$logs/pwsh-host-probe.log" 2>&1
  host_probe_rc="$?"
  timeout --kill-after=10s 60s wineserver -w >>"$logs/pwsh-host-probe.log" 2>&1
  host_probe_settle_rc="$?"
  [[ -f "$pwsh_host_trace" ]] && cp -f "$pwsh_host_trace" "$logs/pwsh-host-trace.log"
  [[ -f "$dotnet_host_trace" ]] && cp -f "$dotnet_host_trace" "$logs/dotnet-host-trace.log"
  printf 'host-probe process=%s settle=%s corehost-trace=%s dotnet-trace=%s\n' \
    "$host_probe_rc" "$host_probe_settle_rc" \
    "$([[ -s "$pwsh_host_trace" ]] && printf present || printf missing)" \
    "$([[ -s "$dotnet_host_trace" ]] && printf present || printf missing)" \
    | tee -a "$logs/pwsh-proof-summary.log" >&2
  WINEDEBUG=+process,+loaddll,+seh timeout --kill-after=15s 90s \
    "${pwsh_launcher[@]}" -NoLogo -NoProfile -NonInteractive \
    -File "$pwsh_probe_script_win" "$pwsh_marker_win" "$CFW_EXPECTED_POWERSHELL_VERSION" \
    >"$logs/pwsh-failure-trace.log" 2>&1
  trace_rc="$?"
  timeout --kill-after=10s 60s wineserver -w >>"$logs/pwsh-failure-trace.log" 2>&1
  trace_settle_rc="$?"
  set -e
  trap on_error ERR
  printf 'failure-trace process=%s settle=%s\n' "$trace_rc" "$trace_settle_rc" \
    | tee -a "$logs/pwsh-proof-summary.log" >&2
  exit 70
fi

mark_stage finalize-prepared-runtime
prepared_finalizer="$repo_root/compat/finalize-runtime.ps1"
prepared_finalizer_win="$(winepath_to_windows prepared-finalizer "$prepared_finalizer")"
profile_fragments_win="$(winepath_to_windows profile-fragments "$repo_root/compat/profile.d")"
prepared_finalizer_marker="$probe_dir/prepared-finalizer.txt"
prepared_finalizer_marker_win="$(winepath_to_windows prepared-finalizer-marker "$prepared_finalizer_marker")"
prepared_finalizer_diagnostic="$probe_dir/prepared-finalizer-diagnostic.txt"
prepared_finalizer_expected="$logs/prepared-finalizer.expected"
rm -f "$prepared_finalizer_marker" "$prepared_finalizer_diagnostic"
printf '[cfw] stage=prepared-finalizer-script-entry\n[cfw] stage=prepared-finalizer-complete\n' \
  >"$prepared_finalizer_expected"
trap - ERR
set +e
timeout --kill-after=15s 300s "${pwsh_launcher[@]}" -NoLogo -NoProfile -NonInteractive \
  -File "$prepared_finalizer_win" -FragmentSource "$profile_fragments_win" -MarkerPath "$prepared_finalizer_marker_win" \
  >"$logs/prepared-finalizer.log" 2>&1
prepared_finalizer_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/prepared-finalizer.log" 2>&1
prepared_finalizer_settle_rc="$?"
if [[ -s "$prepared_finalizer_diagnostic" ]]; then
  cp -f "$prepared_finalizer_diagnostic" "$logs/prepared-finalizer-diagnostic.log" || true
fi
normalize_log "$logs/prepared-finalizer.log"
cmp -s "$prepared_finalizer_expected" "$prepared_finalizer_marker"
prepared_finalizer_evidence_rc="$?"
set -e
trap on_error ERR
if [[ "$prepared_finalizer_rc" -ne 0 || "$prepared_finalizer_settle_rc" -ne 0 || \
      "$prepared_finalizer_evidence_rc" -ne 0 ]]; then
  printf 'Prepared runtime finalizer failed: process=%s settle=%s evidence=%s marker=%s\n' \
    "$prepared_finalizer_rc" "$prepared_finalizer_settle_rc" \
    "$prepared_finalizer_evidence_rc" "$prepared_finalizer_marker" >&2
  cat "$logs/prepared-finalizer.log" >&2 || true
  cat "$logs/prepared-finalizer-diagnostic.log" >&2 || true
  exit 70
fi

# Chocolatey's bin\choco.exe is a shimgen launcher for ..\choco.exe. That
# compatibility shim cannot reliably create its managed child under Wine, so
# the CFW execution contract uses the real Chocolatey console executable.
# Query Chocolatey directly so redirected output remains observable. Package
# operations need wineconsole because the Synchro child process must inherit a
# real Windows console before it can host pwsh.exe.
choco_win='C:\ProgramData\chocolatey\choco.exe'
choco_query_launcher=(wine "$choco_win")
choco_package_launcher=(wineconsole "$choco_win")
# Chocolatey 2.6.0 asks .NET for Environment.CurrentDirectory during startup.
# A native s6 task begins under a Linux-only path, which Wine exposes in a form
# Chocolatey's FileIOPermission compatibility layer rejects. Run every query
# and package operation from the prefix's C: root, matching upstream CFW.
choco_working_directory="$wine_prefix/drive_c"

wrapper64="$wine_prefix/drive_c/windows/system32/WindowsPowerShell/v1.0/powershell.exe"
wrapper32="$wine_prefix/drive_c/windows/syswow64/WindowsPowerShell/v1.0/powershell.exe"
mark_stage install-synchro
synchro_cache="$work/synchro-v$CFW_EXPECTED_SYNCHRO_VERSION"
fetch_input synchro64 "$synchro_cache/powershell64.exe"
fetch_input synchro32 "$synchro_cache/powershell32.exe"
mkdir -p "$(dirname "$wrapper64")" "$(dirname "$wrapper32")"
cp -f "$synchro_cache/powershell64.exe" "$wrapper64"
cp -f "$synchro_cache/powershell32.exe" "$wrapper32"
test -s "$wrapper64" && test -s "$wrapper32"

# Chocolatey keeps its in-process PowerShell host disabled, but several types
# in choco.exe still reference the legacy System.Management.Automation
# assembly. CLR type discovery resolves those references before Chocolatey can
# parse any command. Supply the verified legacy metadata dependencies beside
# choco.exe while continuing to execute package scripts through Synchro.
mark_stage install-chocolatey-type-dependencies
chocolatey_root="$wine_prefix/drive_c/ProgramData/chocolatey"
windows_powershell_package="$payload_cache/$(input_value windowsPowerShell filename)"
windows_powershell_stage="$work/windows-powershell-type-dependencies"
rm -rf -- "$windows_powershell_stage"
mkdir -p "$windows_powershell_stage"
7z e -y -aoa -ssc- -r \
  -x'!*resources.dll' \
  "$windows_powershell_package" \
  'Microsoft.PowerShell*.dll' \
  'Microsoft.WSMan*.dll' \
  'System.Management.Automation.dll' \
  "-o$windows_powershell_stage" \
  >"$logs/chocolatey-type-dependencies.log" 2>&1
find "$windows_powershell_stage" -maxdepth 1 -type f -iname '*.dll' -printf '%f\n' \
  | sort -f >"$logs/chocolatey-type-dependency-inventory.log"
windows_powershell_assembly_count="$(
  wc -l <"$logs/chocolatey-type-dependency-inventory.log" | tr -d '[:space:]'
)"
if [[ "$windows_powershell_assembly_count" -lt 8 ]]; then
  printf '[cfw] incomplete Chocolatey type dependency set: count=%s\n' \
    "$windows_powershell_assembly_count" >&2
  cat "$logs/chocolatey-type-dependency-inventory.log" >&2 || true
  exit 69
fi
find "$windows_powershell_stage" -maxdepth 1 -type f -iname '*.dll' \
  -exec cp -f -t "$chocolatey_root" {} +
rm -rf -- "$windows_powershell_stage"
system_management_automation="$(
  find "$chocolatey_root" -maxdepth 1 -type f \
    -iname 'System.Management.Automation.dll' -print -quit
)"
test -n "$system_management_automation" && test -s "$system_management_automation"

export ChocolateyInstall='C:\ProgramData\chocolatey'
export ChocolateyToolsLocation='C:\tools'

# CFW's native bootstrap promotes the canonical Chocolatey tree before
# Chocolatey's first command, but the upstream package does not contain the
# config directory or embedded default config as a loose file. Seed the locked
# 2.6.0 template with CFW's external-host policy before starting choco.exe.
mark_stage apply-chocolatey-policy
chocolatey_config_template="$repo_root/compat/chocolatey.config"
chocolatey_config="$wine_prefix/drive_c/ProgramData/chocolatey/config/chocolatey.config"
trap - ERR
set +e
timeout --kill-after=5s 60s python3 "$repo_root/compat/set-chocolatey-policy.py" seed \
  "$chocolatey_config_template" "$chocolatey_config" >"$logs/chocolatey-policy-seed.log" 2>&1
feature_policy_seed_rc="$?"
set -e
trap on_error ERR
if [[ "$feature_policy_seed_rc" -ne 0 ]]; then
  printf 'Chocolatey powershellHost policy seed failed: process=%s\n' \
    "$feature_policy_seed_rc" >&2
  cat "$logs/chocolatey-policy-seed.log" >&2 || true
  exit 70
fi

cd "$choco_working_directory"
mark_stage prove-runtime
synchro64_marker="$probe_dir/synchro-x64.txt"
synchro32_marker="$probe_dir/synchro-x86.txt"
smoke_install_marker="$probe_dir/chocolatey-install.txt"
smoke_uninstall_marker="$probe_dir/chocolatey-uninstall.txt"
rm -f "$synchro64_marker" "$synchro32_marker" "$smoke_install_marker" "$smoke_uninstall_marker"
synchro64_marker_win="$(winepath_to_windows synchro-x64-marker "$synchro64_marker")"
synchro32_marker_win="$(winepath_to_windows synchro-x86-marker "$synchro32_marker")"
smoke_feed="$work/smoke-feed"
build_smoke_package "$smoke_feed"
smoke_feed_win="$(winepath_to_windows smoke-feed "$smoke_feed")"

trap - ERR
set +e
timeout --kill-after=15s 300s "${choco_query_launcher[@]}" feature list --limit-output >"$logs/choco-feature-status.log" 2>&1
feature_status_command_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-feature-status.log" 2>&1
feature_status_settle_rc="$?"
set -e
trap on_error ERR
normalize_log "$logs/choco-feature-status.log"
trap - ERR
set +e
python3 "$repo_root/compat/set-chocolatey-policy.py" verify-status \
  "$logs/choco-feature-status.log"
feature_status_rc="$?"
timeout --kill-after=15s 300s "${choco_query_launcher[@]}" --version >"$logs/choco-version.log" 2>&1
choco_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-version.log" 2>&1
choco_settle_rc="$?"
set -e
trap on_error ERR
normalize_log "$logs/choco-version.log"
trap - ERR
set +e
CFW_OBSERVED_CHOCOLATEY_VERSION="$(read_single_observed_line chocolatey-version "$logs/choco-version.log")"
choco_version_output_rc="$?"
if [[ "$choco_version_output_rc" -ne 0 ]]; then
  choco_version_rc="$choco_version_output_rc"
elif [[ "$CFW_OBSERVED_CHOCOLATEY_VERSION" == "$CFW_EXPECTED_CHOCOLATEY_VERSION" ]]; then
  choco_version_rc=0
else
  choco_version_rc=1
fi
if [[ "$feature_status_command_rc" -ne 0 || "$feature_status_settle_rc" -ne 0 || \
      "$feature_status_rc" -ne 0 || "$choco_rc" -ne 0 || "$choco_settle_rc" -ne 0 || \
      "$choco_version_rc" -ne 0 ]]; then
  printf '[cfw] Chocolatey probe return codes: feature=%s featureSettle=%s featureStatus=%s version=%s versionSettle=%s versionProof=%s\n' \
    "$feature_status_command_rc" "$feature_status_settle_rc" "$feature_status_rc" \
    "$choco_rc" "$choco_settle_rc" "$choco_version_rc" >&2
  WINEDEBUG=-all timeout --kill-after=15s 90s \
    wine "$choco_win" --version >"$logs/choco-version-direct.log" 2>&1
  choco_direct_rc="$?"
  timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-version-direct.log" 2>&1
  choco_direct_settle_rc="$?"
  normalize_log "$logs/choco-version-direct.log"
  printf '[cfw] Chocolatey direct diagnostic return codes: process=%s settle=%s\n' \
    "$choco_direct_rc" "$choco_direct_settle_rc" >&2
  choco_loader_probe_source="$probe_dir/choco-loader-probe.cs"
  choco_loader_probe_exe="$probe_dir/choco-loader-probe.exe"
  cp -f "$repo_root/compat/choco-loader-probe.cs" "$choco_loader_probe_source"
  choco_loader_probe_source_win="$(winepath_to_windows choco-loader-probe-source "$choco_loader_probe_source")"
  choco_loader_probe_exe_win="$(winepath_to_windows choco-loader-probe-executable "$choco_loader_probe_exe")"
  csc_win='C:\windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
  WINEDEBUG=-all timeout --kill-after=15s 120s \
    wine "$csc_win" /nologo "/out:$choco_loader_probe_exe_win" "$choco_loader_probe_source_win" \
    >"$logs/choco-loader-probe-compile.log" 2>&1
  choco_loader_probe_compile_rc="$?"
  timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-loader-probe-compile.log" 2>&1
  choco_loader_probe_compile_settle_rc="$?"
  if [[ "$choco_loader_probe_compile_rc" -eq 0 && "$choco_loader_probe_compile_settle_rc" -eq 0 ]]; then
    WINEDEBUG=-all timeout --kill-after=15s 120s \
      wine "$choco_loader_probe_exe_win" "$choco_win" >"$logs/choco-loader-probe.log" 2>&1
    choco_loader_probe_rc="$?"
    timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-loader-probe.log" 2>&1
    choco_loader_probe_settle_rc="$?"
  else
    choco_loader_probe_rc=125
    choco_loader_probe_settle_rc=125
    printf 'loader probe was not run because compilation failed\n' >"$logs/choco-loader-probe.log"
  fi
  normalize_log "$logs/choco-loader-probe-compile.log"
  normalize_log "$logs/choco-loader-probe.log"
  printf '[cfw] Chocolatey loader probe return codes: compile=%s compileSettle=%s process=%s settle=%s\n' \
    "$choco_loader_probe_compile_rc" "$choco_loader_probe_compile_settle_rc" \
    "$choco_loader_probe_rc" "$choco_loader_probe_settle_rc" >&2
  WINEDEBUG=+process,+loaddll,+seh timeout --kill-after=15s 90s \
    "${choco_query_launcher[@]}" --version >"$logs/choco-version-diagnostic.log" 2>&1
  choco_diagnostic_rc="$?"
  timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-version-diagnostic.log" 2>&1
  choco_diagnostic_settle_rc="$?"
  normalize_log "$logs/choco-version-diagnostic.log"
  printf '[cfw] Chocolatey diagnostic return codes: process=%s settle=%s\n' \
    "$choco_diagnostic_rc" "$choco_diagnostic_settle_rc" >&2
fi
timeout --kill-after=15s 300s wineconsole "$wrapper64" -NoLogo -NonInteractive -Command "if (\$env:CFW_PROFILE_COMPOSITION -ne 'cfw-runtime-v1') { throw 'CFW profile composition failed' }; [IO.File]::WriteAllText('$synchro64_marker_win', 'synchro-x64-profile-composed')" >"$logs/synchro-x64.log" 2>&1
synchro64_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/synchro-x64.log" 2>&1
synchro64_settle_rc="$?"
timeout --kill-after=15s 300s wineconsole "$wrapper32" -NoLogo -NonInteractive -Command "if (\$env:CFW_PROFILE_COMPOSITION -ne 'cfw-runtime-v1') { throw 'CFW profile composition failed' }; [IO.File]::WriteAllText('$synchro32_marker_win', 'synchro-x86-profile-composed')" >"$logs/synchro-x86.log" 2>&1
synchro32_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/synchro-x86.log" 2>&1
synchro32_settle_rc="$?"
timeout --kill-after=30s 600s "${choco_package_launcher[@]}" install cfw-runtime-smoke -y \
  --source "$smoke_feed_win" --use-system-powershell >"$logs/choco-smoke-install.log" 2>&1
smoke_install_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-smoke-install.log" 2>&1
smoke_install_settle_rc="$?"
timeout --kill-after=30s 600s "${choco_package_launcher[@]}" uninstall cfw-runtime-smoke -y \
  --use-system-powershell >"$logs/choco-smoke-uninstall.log" 2>&1
smoke_uninstall_rc="$?"
timeout --kill-after=10s 120s wineserver -w >>"$logs/choco-smoke-uninstall.log" 2>&1
smoke_uninstall_settle_rc="$?"
chocolatey_runtime_log="$chocolatey_root/logs/chocolatey.log"
if [[ -f "$chocolatey_runtime_log" ]]; then
  cp -f "$chocolatey_runtime_log" "$logs/chocolatey-runtime.log"
fi
set -e
trap on_error ERR
export CFW_OBSERVED_CHOCOLATEY_VERSION

python3 - "$metadata" \
  "$installer_rc" "$installer_settle_rc" "$pwsh_rc" "$pwsh_settle_rc" \
  "$prepared_finalizer_rc" "$prepared_finalizer_settle_rc" \
  "$feature_policy_seed_rc" \
  "$feature_status_command_rc" "$feature_status_settle_rc" "$feature_status_rc" \
  "$choco_rc" "$choco_settle_rc" "$choco_version_rc" \
  "$synchro64_rc" "$synchro64_settle_rc" "$synchro32_rc" "$synchro32_settle_rc" \
  "$smoke_install_rc" "$smoke_install_settle_rc" "$smoke_uninstall_rc" "$smoke_uninstall_settle_rc" \
  "$wine_version_rc" "$wine_version_settle_rc" \
  "$pwsh_winecfg_rc" "$pwsh_winecfg_settle_rc" "$pwsh_regedit_rc" "$pwsh_regedit_settle_rc" \
  "$pwsh_query_rc" "$pwsh_query_settle_rc" "$pwsh_amsi_rc" "$pwsh_dwmapi_rc" "$pwsh_rpcrt4_rc" "$pwsh_mscoree_rc" \
  "$logs" "$pwsh_marker" "$prepared_finalizer_marker" "$synchro64_marker" "$synchro32_marker" \
  "$smoke_install_marker" "$smoke_uninstall_marker" "$pwsh_evidence" <<'PY2'
import hashlib
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
values = [int(value) for value in sys.argv[2:35]]
logs_path = Path(sys.argv[35])
markers = [Path(value) for value in sys.argv[36:]]
keys = [
    "installer", "installerSettle", "pwsh", "pwshSettle", "preparedFinalizer",
    "preparedFinalizerSettle", "featurePolicySeed", "featurePolicyStatusCommand",
    "featurePolicyStatusSettle", "featurePolicyStatus",
    "chocolateyVersionCommand", "chocolateyVersionSettle",
    "chocolateyVersion", "synchroX64", "synchroX64Settle", "synchroX86",
    "synchroX86Settle", "smokeInstall", "smokeInstallSettle", "smokeUninstall",
    "smokeUninstallSettle", "wineVersionCommand", "wineVersionSettle",
    "prePwshWinecfg", "prePwshWinecfgSettle", "prePwshRegedit", "prePwshRegeditSettle",
    "prePwshQuery", "prePwshQuerySettle", "prePwshAmsi", "prePwshDwmapi",
    "prePwshRpcrt4", "prePwshMscoree",
]
return_codes = dict(zip(keys, values))
winepath_labels = (
    "cfw-cache", "cfw-installer", "pwsh-policy", "pwsh-executable",
    "pwsh-probe-script", "pwsh-marker", "prepared-finalizer",
    "profile-fragments", "prepared-finalizer-marker", "synchro-x64-marker",
    "synchro-x86-marker", "smoke-feed",
)
winepath_return_codes = {}
for label in winepath_labels:
    status = logs_path / f"winepath-{label}.status"
    parts = status.read_text(encoding="utf-8").split()
    if len(parts) != 2:
        raise SystemExit(f"invalid Wine path status: {label}")
    winepath_return_codes[label] = {"command": int(parts[0]), "settle": int(parts[1])}
marker_hashes = {
    marker.name: hashlib.sha256(marker.read_bytes()).hexdigest()
    for marker in markers
    if marker.is_file() and marker.stat().st_size > 0
}
observed_powershell = markers[0].read_text(encoding="utf-8")
expected_pwsh_evidence = (
    "[cfw] pwsh-script-entry\n"
    f"[cfw] pwsh={os.environ['CFW_EXPECTED_POWERSHELL_VERSION']}\n"
).encode("utf-8")
expected_finalizer_evidence = (
    b"[cfw] stage=prepared-finalizer-script-entry\n"
    b"[cfw] stage=prepared-finalizer-complete\n"
)
checks = {
    "wineIdentity": values[21] == 0 and values[22] == 0 and os.environ["CFW_OBSERVED_WINE_VERSION"] == os.environ["CFW_EXPECTED_WINE_VERSION"],
    "installer": values[0] == 0 and values[1] == 0,
    "prePwshPolicy": all(value == 0 for value in values[23:33]),
    "pathConversions": all(
        codes["command"] == 0 and codes["settle"] == 0
        for codes in winepath_return_codes.values()
    ),
    "pwsh": values[2] == 0 and values[3] == 0 and observed_powershell == os.environ["CFW_EXPECTED_POWERSHELL_VERSION"] and "pwsh.txt" in marker_hashes and "pwsh-evidence.txt" in marker_hashes and markers[6].read_bytes() == expected_pwsh_evidence,
    "preparedFinalizer": values[4] == 0 and values[5] == 0 and "prepared-finalizer.txt" in marker_hashes and markers[1].read_bytes() == expected_finalizer_evidence,
    "featurePolicy": all(value == 0 for value in values[6:10]),
    "chocolatey": values[10] == 0 and values[11] == 0 and values[12] == 0 and os.environ["CFW_OBSERVED_CHOCOLATEY_VERSION"] == os.environ["CFW_EXPECTED_CHOCOLATEY_VERSION"],
    "synchroX64": values[13] == 0 and values[14] == 0 and "synchro-x64.txt" in marker_hashes,
    "synchroX86": values[15] == 0 and values[16] == 0 and "synchro-x86.txt" in marker_hashes,
    "chocolateyLifecycle": values[17] == 0 and values[18] == 0 and values[19] == 0 and values[20] == 0 and "chocolatey-install.txt" in marker_hashes and "chocolatey-uninstall.txt" in marker_hashes,
}
record = {
    "schemaVersion": "cfw.runtime-build/v2",
    "provider": "cfw-chocolatey-runtime",
    "contract": os.environ["CFW_CONTRACT_SCHEMA"],
    "contractSha256": os.environ["CFW_CONTRACT_SHA256"],
    "runtimeId": os.environ["CFW_RUNTIME_ID"],
    "status": "passed" if all(checks.values()) else "failed",
    "wine": {"image": os.environ["CFW_WINE_IMAGE"], "version": os.environ["CFW_OBSERVED_WINE_VERSION"], "architecture": "win64"},
    "sessionContract": os.environ["CFW_WINE_SESSION_CONTRACT"],
    "sourceRevision": os.environ["CFW_SOURCE_REVISION"],
    "installerSha256": os.environ["CFW_INSTALLER_SHA256"],
    "runtimeInputsSha256": os.environ["CFW_RUNTIME_INPUTS_SHA256"],
    "profileLoader": {"path": "C:\\Program Files\\PowerShell\\7\\profile.ps1", "applicationExtensionPath": "C:\\ProgramData\\Chocolatey-for-wine\\application-profile.d"},
    "powershell": observed_powershell,
    "synchro": "v" + os.environ["CFW_EXPECTED_SYNCHRO_VERSION"],
    "chocolatey": os.environ["CFW_OBSERVED_CHOCOLATEY_VERSION"],
    "checks": checks,
    "returnCodes": return_codes,
    "winepathReturnCodes": winepath_return_codes,
    "markerSha256": marker_hashes,
}
path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if record["status"] != "passed":
    raise SystemExit(70)
PY2

mark_stage package-runtime
# The universal desktop session creates this host-bound convenience link. It is
# not part of the prepared Windows runtime and must not cross the artifact
# boundary into a consumer-controlled extraction root.
desktop_link="$wine_prefix/drive_c/users/abc/Desktop"
if [[ -L "$desktop_link" && "$(readlink "$desktop_link")" == "/config/Desktop" ]]; then
  rm -f -- "$desktop_link"
fi
mkdir -p "$wine_prefix/.cfw"
cp -f "$metadata" "$wine_prefix/.cfw/runtime.json"
archive="$output_root/$artifact_name.tar.gz"
"$repo_root/compat/package-runtime.sh" "$wine_prefix" "$archive"
archive_sha256="$(sha256sum "$archive" | awk '{print $1}')"
printf '%s  %s\n' "$archive_sha256" "$(basename "$archive")" > "$archive.sha256"

python3 - "$metadata" "$manifest" "$archive" "$archive_sha256" "$compat_contract" <<'PY2'
import hashlib
import json
import os
import sys
from pathlib import Path

evidence_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
archive_path = Path(sys.argv[3])
archive_sha256 = sys.argv[4]
contract_path = Path(sys.argv[5])
evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
contract = json.loads(contract_path.read_text(encoding="utf-8"))
required_proofs = contract["build"]["requiredProofs"]
if not isinstance(required_proofs, list) or not required_proofs or len(required_proofs) != len(set(required_proofs)):
    raise SystemExit("invalid authoritative proof inventory")
if set(evidence["checks"]) != set(required_proofs):
    raise SystemExit("runtime evidence proof inventory does not match compatibility contract")
if any(evidence["checks"][proof] is not True for proof in required_proofs):
    raise SystemExit("runtime evidence contains an unproven contract requirement")
if evidence.get("contract") != contract.get("schemaVersion"):
    raise SystemExit("runtime evidence compatibility contract mismatch")
if evidence.get("contractSha256") != hashlib.sha256(contract_path.read_bytes()).hexdigest():
    raise SystemExit("runtime evidence compatibility contract digest mismatch")
manifest = {
    "schemaVersion": "cfw.prepared-runtime-manifest/v1",
    "runtimeId": evidence["runtimeId"],
    "contract": evidence["contract"],
    "contractSha256": evidence["contractSha256"],
    "archive": {"filename": archive_path.name, "sha256": archive_sha256, "bytes": archive_path.stat().st_size},
    "runtimeEvidence": {"filename": evidence_path.name, "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest()},
    "sourceRevision": evidence["sourceRevision"],
    "installerSha256": evidence["installerSha256"],
    "runtimeInputsSha256": evidence["runtimeInputsSha256"],
    "wine": evidence["wine"],
    "sessionContract": evidence["sessionContract"],
    "requiredProofs": required_proofs,
    "interfaces": contract["artifact"]["interfaces"],
    "status": evidence["status"],
}
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY2
mark_stage complete
printf '[cfw] runtime artifact ready: %s\n' "$archive"
