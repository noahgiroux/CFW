#!/usr/bin/env python3
"""Write runtime evidence and manifests from named producer observations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

RETURN_CODE_KEYS = (
    'installer',
    'installerSettle',
    'pwsh',
    'pwshSettle',
    'preparedFinalizer',
    'preparedFinalizerSettle',
    'featurePolicySeed',
    'featurePolicyStatusCommand',
    'featurePolicyStatusSettle',
    'featurePolicyStatus',
    'chocolateyVersionCommand',
    'chocolateyVersionSettle',
    'chocolateyVersion',
    'synchroX64',
    'synchroX64Settle',
    'synchroX86',
    'synchroX86Settle',
    'smokeInstall',
    'smokeInstallSettle',
    'smokeUninstall',
    'smokeUninstallSettle',
    'wineVersionCommand',
    'wineVersionSettle',
    'prePwshWinecfg',
    'prePwshWinecfgSettle',
    'prePwshRegedit',
    'prePwshRegeditSettle',
    'prePwshQuery',
    'prePwshQuerySettle',
    'prePwshAmsi',
    'prePwshDwmapi',
    'prePwshRpcrt4',
    'prePwshMscoree',
)
MARKER_NAMES = (
    'pwsh.txt',
    'prepared-finalizer.txt',
    'synchro-x64.txt',
    'synchro-x86.txt',
    'chocolatey-install.txt',
    'chocolatey-uninstall.txt',
    'pwsh-evidence.txt',
)
WINEPATH_LABELS = (
    'cfw-cache',
    'cfw-installer',
    'pwsh-policy',
    'pwsh-executable',
    'pwsh-probe-script',
    'pwsh-marker',
    'prepared-finalizer',
    'profile-fragments',
    'compatibility-contract',
    'prepared-finalizer-marker',
    'synchro-x64-marker',
    'synchro-x86-marker',
    'smoke-feed',
)


def write_evidence(path: Path, logs_path: Path, markers: dict[str, Path], return_codes: dict[str, int]) -> bool:
    if set(return_codes) != set(RETURN_CODE_KEYS) or any(type(value) is not int for value in return_codes.values()):
        raise ValueError("runtime return-code inventory does not match required observations")
    if set(markers) != set(MARKER_NAMES) or any(path.name != name for name, path in markers.items()):
        raise ValueError("runtime marker inventory does not match required observations")
    def read_marker(name: str) -> bytes:
        marker = markers[name]
        return marker.read_bytes() if marker.is_file() else b""

    winepath_return_codes = {}
    for label in WINEPATH_LABELS:
        status = logs_path / f"winepath-{label}.status"
        parts = status.read_text(encoding="utf-8").split()
        if len(parts) != 2:
            raise ValueError(f"invalid Wine path status: {label}")
        winepath_return_codes[label] = {"command": int(parts[0]), "settle": int(parts[1])}
    marker_hashes = {
        marker.name: hashlib.sha256(marker.read_bytes()).hexdigest()
        for marker in markers.values()
        if marker.is_file() and marker.stat().st_size > 0
    }
    observed_powershell = read_marker('pwsh.txt').decode("utf-8", errors="replace")
    expected_pwsh_evidence = (
        "[cfw] pwsh-script-entry\n"
        f"[cfw] pwsh={os.environ['CFW_EXPECTED_POWERSHELL_VERSION']}\n"
    ).encode("utf-8")
    expected_finalizer_evidence = (
        b"[cfw] stage=prepared-finalizer-script-entry\n"
        b"[cfw] stage=prepared-finalizer-complete\n"
    )
    def succeeded(*observations: str) -> bool:
        return all(return_codes[name] == 0 for name in observations)

    checks = {
        "wineIdentity": (
            succeeded("wineVersionCommand", "wineVersionSettle")
            and os.environ["CFW_OBSERVED_WINE_VERSION"] == os.environ["CFW_EXPECTED_WINE_VERSION"]
        ),
        "installer": succeeded("installer", "installerSettle"),
        "prePwshPolicy": succeeded(
            "prePwshWinecfg", "prePwshWinecfgSettle", "prePwshRegedit", "prePwshRegeditSettle",
            "prePwshQuery", "prePwshQuerySettle", "prePwshAmsi", "prePwshDwmapi",
            "prePwshRpcrt4", "prePwshMscoree",
        ),
        "pathConversions": all(
            codes["command"] == 0 and codes["settle"] == 0
            for codes in winepath_return_codes.values()
        ),
        "pwsh": (
            succeeded("pwsh", "pwshSettle")
            and observed_powershell == os.environ["CFW_EXPECTED_POWERSHELL_VERSION"]
            and "pwsh.txt" in marker_hashes and "pwsh-evidence.txt" in marker_hashes
            and read_marker('pwsh-evidence.txt') == expected_pwsh_evidence
        ),
        "preparedFinalizer": (
            succeeded("preparedFinalizer", "preparedFinalizerSettle")
            and "prepared-finalizer.txt" in marker_hashes
            and read_marker('prepared-finalizer.txt') == expected_finalizer_evidence
        ),
        "featurePolicy": succeeded(
            "featurePolicySeed", "featurePolicyStatusCommand", "featurePolicyStatusSettle", "featurePolicyStatus",
        ),
        "chocolatey": (
            succeeded("chocolateyVersionCommand", "chocolateyVersionSettle", "chocolateyVersion")
            and os.environ["CFW_OBSERVED_CHOCOLATEY_VERSION"] == os.environ["CFW_EXPECTED_CHOCOLATEY_VERSION"]
        ),
        "synchroX64": succeeded("synchroX64", "synchroX64Settle") and "synchro-x64.txt" in marker_hashes,
        "synchroX86": succeeded("synchroX86", "synchroX86Settle") and "synchro-x86.txt" in marker_hashes,
        "chocolateyLifecycle": (
            succeeded("smokeInstall", "smokeInstallSettle", "smokeUninstall", "smokeUninstallSettle")
            and "chocolatey-install.txt" in marker_hashes and "chocolatey-uninstall.txt" in marker_hashes
        ),
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
    return record["status"] == "passed"


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_manifest(evidence_path: Path, manifest_path: Path, archive_path: Path, contract_path: Path) -> None:
    archive_sha256 = sha256_file(archive_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if evidence.get("schemaVersion") != "cfw.runtime-build/v2" or evidence.get("status") != "passed":
        raise ValueError("runtime evidence has not passed")
    required_proofs = contract["build"]["requiredProofs"]
    if not isinstance(required_proofs, list) or not required_proofs or len(required_proofs) != len(set(required_proofs)):
        raise ValueError("invalid authoritative proof inventory")
    if set(evidence["checks"]) != set(required_proofs):
        raise ValueError("runtime evidence proof inventory does not match compatibility contract")
    if any(evidence["checks"][proof] is not True for proof in required_proofs):
        raise ValueError("runtime evidence contains an unproven contract requirement")
    if evidence.get("contract") != contract.get("schemaVersion"):
        raise ValueError("runtime evidence compatibility contract mismatch")
    if evidence.get("contractSha256") != hashlib.sha256(contract_path.read_bytes()).hexdigest():
        raise ValueError("runtime evidence compatibility contract digest mismatch")
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


def named_values(items, converter):
    result = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name or name in result:
            raise ValueError("missing or duplicate named observation")
        result[name] = converter(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evidence = commands.add_parser("evidence")
    evidence.add_argument("output", type=Path)
    evidence.add_argument("--logs", type=Path, required=True)
    evidence.add_argument("--return-code", action="append", default=[])
    evidence.add_argument("--marker", action="append", default=[])
    manifest = commands.add_parser("manifest")
    for name in ("evidence", "output", "archive", "contract"):
        manifest.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "evidence":
            passed = write_evidence(args.output, args.logs, named_values(args.marker, Path), named_values(args.return_code, int))
            return 0 if passed else 70
        write_manifest(args.evidence, args.output, args.archive, args.contract)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(70, f"[cfw] evidence failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
