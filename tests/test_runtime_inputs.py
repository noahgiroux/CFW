from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from compat.runtime_inputs import INSTALLER_COMPONENTS, installer_header, locked_value

ROOT = Path(__file__).resolve().parents[1]
INPUTS = json.loads((ROOT / "compat/runtime-inputs.json").read_text())


class RuntimeInputTests(unittest.TestCase):
    def test_installer_header_matches_lock_and_both_package_filenames(self):
        self.assertEqual(
            (ROOT / "compat/installer-inputs.h").read_text(), installer_header(INPUTS)
        )
        for component in ("chocolatey", "powershell"):
            with self.subTest(component=component):
                inputs = deepcopy(INPUTS)
                inputs["versions"][component] = "99.1.0"
                with self.assertRaisesRegex(ValueError, "filename does not match"):
                    installer_header(inputs)

    def test_all_native_bootstrap_downloads_come_from_the_lock(self):
        header = installer_header(INPUTS)
        for component in INSTALLER_COMPONENTS:
            for field in ("url", "filename"):
                value = INPUTS["downloads"][component][field]
                self.assertIn(f"#define CFW_{component.upper()}_{field.upper()} L{json.dumps(value)}", header)
        self.assertNotIn('https://', (ROOT / "installer.c").read_text())
        for field in ("msiFilename", "installDirectory"):
            inputs = deepcopy(INPUTS)
            inputs["downloads"]["dotnet"][field] = "../escape"
            with self.assertRaises(ValueError):
                installer_header(inputs)

    def test_lookup_reads_all_runtime_input_sections(self):
        for keys in (
            ("runtimeId",), ("versions", "powershell"),
            ("downloads", "chocolatey", "url"),
            ("downloads", "chocolatey", "filename"),
            ("downloads", "cfwRelease", "installerFilename"),
            ("checkoutSources", "choc_install.ps1", "sha256"),
        ):
            with self.subTest(keys=keys):
                expected = INPUTS
                for key in keys:
                    expected = expected[key]
                self.assertEqual(locked_value(INPUTS, *keys), expected)

    def test_lookup_rejects_missing_nonstring_and_malformed_values(self):
        for keys, value in (
            (("runtimeId",), "unsafe id"),
            (("runtimeId",), "line\nbreak"),
            (("versions", "powershell"), "7.6" + "\n5"),
            (("versions", "powershell"), 765),
            (("downloads", "chocolatey", "sha256"), "abc"),
            (("checkoutSources", "choc_install.ps1", "sha256"), "A" * 64),
            (("downloads", "chocolatey", "filename"), "../payload.nupkg"),
            (("downloads", "cfwRelease", "installerFilename"), ".."),
            (("downloads", "chocolatey", "url"), "http://example.org/payload"),
            (("downloads", "chocolatey", "url"), "https://user:secret@example.org/payload"),
        ):
            with self.subTest(keys=keys, value=value):
                inputs = deepcopy(INPUTS)
                parent = inputs
                for key in keys[:-1]:
                    parent = parent[key]
                parent[keys[-1]] = value
                with self.assertRaises(ValueError):
                    locked_value(inputs, *keys)
        for keys in ((), ("missing",), ("versions", "missing"), ("runtimeId", "nested")):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                locked_value(INPUTS, *keys)

    def test_cli_returns_only_the_requested_value_and_fails_closed(self):
        command = [sys.executable, str(ROOT / "compat/runtime_inputs.py")]
        result = subprocess.run([*command, "get", "versions", "powershell"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, INPUTS["versions"]["powershell"] + "\n")
        result = subprocess.run([*command, "get", "downloads", "missing", "sha256"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertIn("missing runtime input", result.stderr)
        result = subprocess.run([*command, "check-header"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
