"""Behavioral coverage for the named evidence producer and manifest gate."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from compat.runtime_evidence import (
    MARKER_NAMES, RETURN_CODE_KEYS, WINEPATH_LABELS,
    parse_return_codes, sha256_file, write_evidence, write_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


class RuntimeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.contract = ROOT / "compat/contract.json"
        self.codes = dict.fromkeys(RETURN_CODE_KEYS, 0)
        self.markers = {name: self.root / name for name in MARKER_NAMES}
        for name, path in self.markers.items():
            content = {
                "pwsh.txt": b"7.6.5",
                "pwsh-evidence.txt": b"[cfw] pwsh-script-entry\n[cfw] pwsh=7.6.5\n",
                "prepared-finalizer.txt": b"[cfw] stage=prepared-finalizer-script-entry\n[cfw] stage=prepared-finalizer-complete\n",
            }.get(name, b"executed")
            path.write_bytes(content)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        for label in WINEPATH_LABELS:
            (self.logs / f"winepath-{label}.status").write_text("0 0\n")
        values = {
            "CFW_OBSERVED_WINE_VERSION": "wine-11.0",
            "CFW_EXPECTED_WINE_VERSION": "wine-11.0",
            "CFW_EXPECTED_POWERSHELL_VERSION": "7.6.5",
            "CFW_OBSERVED_CHOCOLATEY_VERSION": "2.7.4",
            "CFW_EXPECTED_CHOCOLATEY_VERSION": "2.7.4",
            "CFW_CONTRACT_SCHEMA": "cfw.compatibility-contract/v3",
            "CFW_CONTRACT_SHA256": hashlib.sha256(self.contract.read_bytes()).hexdigest(),
            "CFW_RUNTIME_ID": "cfw-test",
            "CFW_WINE_IMAGE": "ghcr.io/pelagians/cage-wine@sha256:" + "1" * 64,
            "CFW_WINE_SESSION_CONTRACT": "cage.selkies-wayland/v1",
            "CFW_SOURCE_REVISION": "2" * 40,
            "CFW_INSTALLER_SHA256": "3" * 64,
            "CFW_RUNTIME_INPUTS_SHA256": "4" * 64,
            "CFW_EXPECTED_SYNCHRO_VERSION": "4.2.0",
        }
        environment = patch.dict(os.environ, values)
        environment.start()
        self.addCleanup(environment.stop)
        self.evidence = self.root / "runtime.json"

    def produce(self):
        return write_evidence(self.evidence, self.logs, self.root, self.codes)

    def test_every_named_failure_prevents_passing_evidence(self):
        self.assertTrue(self.produce())
        record = json.loads(self.evidence.read_text())
        contract = json.loads(self.contract.read_text())
        self.assertEqual(set(record["checks"]), set(contract["build"]["requiredProofs"]))
        for name in RETURN_CODE_KEYS:
            with self.subTest(observation=name):
                self.codes[name] = 42
                self.assertFalse(self.produce())
                failed = json.loads(self.evidence.read_text())
                self.assertEqual(failed["returnCodes"][name], 42)
                self.assertEqual(failed["status"], "failed")
                self.codes[name] = 0
        for label in WINEPATH_LABELS:
            with self.subTest(path=label):
                status = self.logs / f"winepath-{label}.status"
                status.write_text("0 42\n")
                self.assertFalse(self.produce())
                status.write_text("0 0\n")

    def test_named_inventory_rejects_missing_unknown_and_duplicate_observations(self):
        for codes in ({}, {**self.codes, "unknown": 0}, {**self.codes, "installer": True}):
            with self.subTest(codes=codes), self.assertRaises(ValueError):
                write_evidence(self.evidence, self.logs, self.root, codes)
        for values in (["installer=0", "installer=1"], ["unnamed"], ["=0"], ["installer=not-an-int"]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_return_codes(values)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("--return-code", "installer=0")
        self.assertEqual(result.returncode, 70)
        self.assertIn("duplicate", result.stderr)

    def run_cli(self, *extra):
        command = [sys.executable, str(ROOT / "compat/runtime_evidence.py"), "evidence",
                   str(self.evidence), "--logs", str(self.logs), "--probe-dir", str(self.root)]
        for name, code in self.codes.items():
            command.extend(["--return-code", f"{name}={code}"])
        return subprocess.run([*command, *extra], capture_output=True, text=True)

    def test_cli_rejects_noncanonical_persisted_proofs(self):
        canonical = {name: marker.read_bytes() for name, marker in self.markers.items()}
        cases = {
            "canonical": None,
            "missing-pwsh-evidence": ("pwsh-evidence.txt", None),
            "duplicate-pwsh-evidence": ("pwsh-evidence.txt", canonical["pwsh-evidence.txt"] * 2),
            "fragmented-version-marker": ("pwsh.txt", b"7.\n6.5"),
            "extra-finalizer-token": ("prepared-finalizer.txt", canonical["prepared-finalizer.txt"] + b"[cfw] stage=prepared-finalizer-complete\n"),
        }
        for case, mutation in cases.items():
            with self.subTest(case=case):
                for name, marker in self.markers.items():
                    marker.write_bytes(canonical[name])
                if mutation:
                    name, content = mutation
                    if content is None:
                        self.markers[name].unlink()
                    else:
                        self.markers[name].write_bytes(content)
                result = self.run_cli()
                self.assertEqual(result.returncode, 70 if mutation else 0, result.stderr)
                self.assertEqual(json.loads(self.evidence.read_text())["status"], "failed" if mutation else "passed")

    def test_shell_and_writer_agree_on_the_exact_named_inventory(self):
        source = (ROOT / "compat/build-runtime.sh").read_text()
        codes = re.findall(r'--return-code "([^=]+)=', source)
        labels = re.findall(r'\$\(winepath_to_windows ([a-z0-9-]+) ', source)
        self.assertCountEqual(codes, RETURN_CODE_KEYS)
        self.assertIn('--probe-dir "$probe_dir"', source)
        self.assertNotIn("--marker", source)
        # Loader probes run only on a failed version query and are diagnostics.
        diagnostic = {"choco-loader-probe-source", "choco-loader-probe-executable"}
        self.assertCountEqual([label for label in labels if label not in diagnostic], WINEPATH_LABELS)

    def test_manifest_preserves_schema_hashes_and_rejects_unproven_or_rebound_evidence(self):
        self.assertTrue(self.produce())
        original = json.loads(self.evidence.read_text())
        archive = self.root / "prefix.tar.gz"
        archive.write_bytes(b"test archive")
        manifest = self.root / "manifest.json"
        checksum = archive.with_name(archive.name + ".sha256")
        with patch("compat.runtime_evidence.sha256_file", wraps=sha256_file) as digest:
            write_manifest(self.evidence, manifest, archive, self.contract)
            digest.assert_called_once_with(archive)
        result = json.loads(manifest.read_text())
        self.assertEqual(result["schemaVersion"], "cfw.prepared-runtime-manifest/v1")
        self.assertEqual(result["archive"]["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(checksum.read_text(), f'{result["archive"]["sha256"]}  {archive.name}\n')
        self.assertEqual(result["runtimeEvidence"]["sha256"], hashlib.sha256(self.evidence.read_bytes()).hexdigest())
        variants = []
        for field, value in (("contractSha256", "0" * 64), ("contract", "wrong"), ("status", "failed")):
            altered = deepcopy(original)
            altered[field] = value
            variants.append(altered)
        altered = deepcopy(original)
        altered["checks"].pop("pwsh")
        variants.append(altered)
        altered = deepcopy(original)
        altered["checks"]["pwsh"] = 1
        variants.append(altered)
        for variant in variants:
            self.evidence.write_text(json.dumps(variant))
            manifest.unlink(missing_ok=True)
            checksum.unlink(missing_ok=True)
            with self.assertRaises(ValueError):
                write_manifest(self.evidence, manifest, archive, self.contract)
            self.assertFalse(manifest.exists())
            self.assertFalse(checksum.exists())


if __name__ == "__main__":
    unittest.main()
