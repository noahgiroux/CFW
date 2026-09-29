import hashlib
import json
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class LayerContractTests(unittest.TestCase):
    def test_chocolatey_policy_writer_is_atomic_and_rejects_unsafe_inputs(self) -> None:
        writer = ROOT / "compat" / "set-chocolatey-policy.py"
        valid = (
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<chocolatey><features><feature name="powershellHost" enabled="true" />'
            b'</features></chocolatey>\n'
        )

        def run(config: Path) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["python3", str(writer), "apply", str(config)],
                text=True,
                capture_output=True,
                check=False,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "chocolatey.config"
            config.write_bytes(valid)
            result = run(config)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(f"{config}.backup").read_bytes(), valid)
            updated = config.read_text(encoding="utf-8")
            self.assertIn('enabled="false"', updated)
            self.assertIn('setExplicitly="true"', updated)
            self.assertEqual(list(root.glob("*.update")), [])

        invalid_documents = (
            b"<chocolatey>",
            b"<chocolatey><features /></chocolatey>",
            b'<chocolatey><features><feature name="powershellHost" />'
            b'<feature name="powershellHost" /></features></chocolatey>',
        )
        for document in invalid_documents:
            with self.subTest(document=document), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "chocolatey.config"
                config.write_bytes(document)
                result = run(config)
                self.assertEqual(result.returncode, 70)
                self.assertEqual(config.read_bytes(), document)
                self.assertFalse(Path(f"{config}.backup").exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external.config"
            external.write_bytes(valid)
            config = root / "chocolatey.config"
            config.symlink_to(external)
            self.assertEqual(run(config).returncode, 70)
            self.assertEqual(external.read_bytes(), valid)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "chocolatey.config"
            os.mkfifo(config)
            result = subprocess.run(
                ["python3", str(writer), "apply", str(config)],
                text=True,
                capture_output=True,
                check=False,
                timeout=1,
            )
            self.assertEqual(result.returncode, 70)
            self.assertIn("path is not a regular file", result.stderr)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_parent = root / "real" / "nested"
            real_parent.mkdir(parents=True)
            external = real_parent / "chocolatey.config"
            external.write_bytes(valid)
            alias = root / "alias"
            alias.symlink_to(root / "real", target_is_directory=True)
            self.assertEqual(run(alias / "nested" / "chocolatey.config").returncode, 70)
            self.assertEqual(external.read_bytes(), valid)
            self.assertFalse(Path(f"{external}.backup").exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "chocolatey.config"
            config.write_bytes(valid)
            external = root / "external.backup"
            external.write_bytes(b"sentinel")
            Path(f"{config}.backup").symlink_to(external)
            self.assertEqual(run(config).returncode, 70)
            self.assertEqual(config.read_bytes(), valid)
            self.assertEqual(external.read_bytes(), b"sentinel")

        spec = importlib.util.spec_from_file_location("cfw_policy_writer", writer)
        if spec is None or spec.loader is None:
            self.fail("unable to load Chocolatey policy writer")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "chocolatey.config"
            backup = Path(f"{config}.backup")
            config.write_bytes(valid)
            real_replace = module.os.replace

            def fail_live_config_replace(
                source: str | os.PathLike[str], destination: str | os.PathLike[str]
            ) -> None:
                if Path(destination) == config:
                    raise OSError("simulated live config replace failure")
                real_replace(source, destination)

            with mock.patch.object(module.os, "replace", side_effect=fail_live_config_replace):
                with self.assertRaises(OSError):
                    module.apply_policy(config)
            self.assertEqual(config.read_bytes(), valid)
            self.assertEqual(backup.read_bytes(), valid)
            self.assertEqual(list(root.glob("*.update")), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "chocolatey.config"
            backup = Path(f"{config}.backup")
            config.write_bytes(valid)
            real_write_atomic = module._write_atomic

            def swap_after_backup(path: Path, payload: bytes) -> None:
                real_write_atomic(path, payload)
                if path == backup:
                    replacement = root / "replacement.config"
                    replacement.write_bytes(b"replacement")
                    os.replace(replacement, config)

            with mock.patch.object(module, "_write_atomic", side_effect=swap_after_backup):
                with self.assertRaises(module.PolicyError):
                    module.apply_policy(config)
            self.assertEqual(config.read_bytes(), b"replacement")
            self.assertEqual(backup.read_bytes(), valid)
            self.assertEqual(list(root.glob("*.update")), [])

        template = ROOT / "compat" / "chocolatey.config"
        with tempfile.TemporaryDirectory() as directory:
            chocolatey_root = Path(directory) / "prefix" / "drive_c" / "ProgramData" / "chocolatey"
            chocolatey_root.mkdir(parents=True)
            config = chocolatey_root / "config" / "chocolatey.config"
            result = subprocess.run(
                ["python3", str(writer), "seed", str(template), str(config)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_bytes(), template.read_bytes())
            self.assertIn('enabled="false" setExplicitly="true"', config.read_text(encoding="utf-8"))
            self.assertEqual(list(config.parent.glob("*.update")), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chocolatey_root = root / "prefix" / "drive_c" / "ProgramData" / "chocolatey"
            chocolatey_root.mkdir(parents=True)
            invalid_template = root / "invalid.config"
            invalid_template.write_bytes(valid)
            config = chocolatey_root / "config" / "chocolatey.config"
            result = subprocess.run(
                ["python3", str(writer), "seed", str(invalid_template), str(config)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 70)
            self.assertFalse(config.exists())

    def test_contract_defines_prepared_prefix_runtime(self) -> None:
        contract = json.loads((ROOT / "compat" / "contract.json").read_text(encoding="utf-8"))

        self.assertEqual(contract["mode"], "prepared-prefix-runtime")
        self.assertEqual(contract["artifact"]["kind"], "prepared-wine-prefix")
        self.assertEqual(contract["artifact"]["seedSemantics"], "replace-fresh-prefix")
        self.assertTrue(contract["artifact"]["mustPrecedeApplicationModules"])
        self.assertEqual(contract["provides"]["windows-powershell-shim"], "synchro-v4.2.0")
        self.assertEqual(contract["provides"]["package-execution-host"], "external-windows-powershell")
        self.assertEqual(contract["ownership"]["powershellRuntime"], "cfw-runtime-artifact")
        self.assertTrue(contract["constraints"]["consumerMustNotReconstructWmfOrGac"])
        self.assertTrue(contract["constraints"]["consumerMustSeedArtifactBeforeOtherModules"])
        self.assertEqual(contract["artifact"]["manifest"], "cfw-runtime-manifest.json")
        self.assertIn("chocolateyLifecycle", contract["build"]["requiredProofs"])
        self.assertIn("wineIdentity", contract["build"]["requiredProofs"])
        self.assertIn("prePwshPolicy", contract["build"]["requiredProofs"])
        self.assertIn("pathConversions", contract["build"]["requiredProofs"])
        self.assertIn("synchroX64", contract["build"]["requiredProofs"])
        self.assertEqual(
            contract["artifact"]["interfaces"]["chocolatey"]["prefixRelativePath"],
            "drive_c/ProgramData/chocolatey/choco.exe",
        )
        self.assertEqual(
            contract["artifact"]["interfaces"]["environment"],
            {"WINEDLLOVERRIDES": ""},
        )
        self.assertEqual(contract["ownership"]["profileLoader"], "cfw-runtime-artifact")
        self.assertTrue(contract["constraints"]["consumerMustVerifyRuntimeManifest"])
        self.assertFalse(contract["constraints"]["chocolateyInProcessPowerShellHost"])

    def test_runtime_builder_has_strict_proofs(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        inputs = json.loads((ROOT / "compat" / "runtime-inputs.json").read_text(encoding="utf-8"))
        contract = json.loads((ROOT / "compat" / "contract.json").read_text(encoding="utf-8"))

        self.assertIn("winepath_to_windows", source)
        self.assertIn("winepath-${label}.log", source)
        self.assertIn("Wine path conversion failed", source)
        self.assertIn('pwsh_win="$(winepath_to_windows pwsh-executable "$pwsh")"', source)
        self.assertIn('pwsh_probe_script_win="$(winepath_to_windows pwsh-probe-script "$pwsh_probe_script")"', source)
        self.assertIn('-File "$pwsh_probe_script_win" "$pwsh_marker_win"', source)
        self.assertIn("[cfw] pwsh-script-entry", source)
        self.assertIn("pwsh_entry_rc", source)
        self.assertIn("pwsh_version_rc", source)
        self.assertIn('cmp -s "$pwsh_evidence_expected" "$pwsh_evidence"', source)
        self.assertIn("CFW_EXPECTED_POWERSHELL_VERSION", source)
        self.assertIn('cmp -s "$pwsh_marker_expected" "$pwsh_marker"', source)
        self.assertNotIn('tr -d \'\\r\\n\' < "$pwsh_marker"', source)
        self.assertIn("pwsh-proof-summary.log", source)
        self.assertIn("pwsh-failure-trace.log", source)
        self.assertIn("WINEDEBUG=+process,+loaddll,+seh", source)
        self.assertIn("COREHOST_TRACE=1", source)
        self.assertIn("DOTNET_HOST_TRACE=1", source)
        self.assertIn("pwsh-host-probe.log", source)
        self.assertIn('WINEDEBUG=+process,+loaddll,+seh timeout --kill-after=15s 90s \\\n    "${pwsh_launcher[@]}"', source)
        self.assertLess(source.index("PowerShell runtime proof failed"), source.index("COREHOST_TRACE=1"))
        self.assertIn("mark_stage apply-pre-pwsh-policy", source)
        self.assertIn("pwsh-policy.log", source)
        self.assertIn("compat/pwsh-policy.reg", source)
        policy = (ROOT / "compat" / "pwsh-policy.reg").read_text(encoding="utf-8")
        self.assertIn('"amsi"=""', policy)
        self.assertIn('"dwmapi"=""', policy)
        self.assertIn('"mscoree"="builtin"', policy)
        self.assertIn('"rpcrt4"="native,builtin"', policy)
        upstream = (ROOT / "choc_install.ps1").read_text(encoding="utf-8")
        self.assertIn('"mscoree"="builtin"', upstream)
        self.assertIn('"rpcrt4"="native,builtin"', upstream)
        self.assertIn('export CFW_CONTAINER_BUILDER=1', source)
        self.assertIn('export CFW_EXTERNAL_POWERSHELL=1', source)
        self.assertIn('install-synchro', source)
        self.assertLess(
            source.index('mark_stage apply-pre-pwsh-policy'),
            source.index('mark_stage prove-pwsh'),
        )
        self.assertLess(
            source.index('mark_stage prove-pwsh'),
            source.index('mark_stage install-synchro'),
        )
        self.assertLess(
            source.index('export CFW_CONTAINER_BUILDER=1'),
            source.index('timeout --kill-after=30s "${CFW_INSTALL_TIMEOUT:-7200s}" wine "$installer_win"'),
        )
        self.assertIn('WINEDLLOVERRIDES="mscoree,mshtml="', source)
        self.assertIn('wine wineboot --init', source)
        self.assertIn('export WINEDLLOVERRIDES=""', source)
        self.assertIn("mark_stage prove-clr-policy", source)
        self.assertIn("clr-policy.log", source)
        self.assertIn("wine reg query \"$clr_policy_key\" /v mscoree", source)
        self.assertIn(
            "mscoree[[:space:]]+REG_SZ[[:space:]]+native",
            source,
        )
        self.assertLess(
            source.index('WINEDLLOVERRIDES="mscoree,mshtml="'),
            source.index('wine wineboot --init'),
        )
        self.assertLess(
            source.index('wine wineboot --init'),
            source.index('export WINEDLLOVERRIDES=""'),
        )
        self.assertEqual(inputs["downloads"]["powershell"]["filename"], "PowerShell-7.6.5-win-x64.msi")
        self.assertIn("powershell-wrapper-for-wine/releases/download/v4.2.0", inputs["downloads"]["synchro64"]["url"])
        self.assertEqual(inputs["downloads"]["synchro64"]["sha256"], "b1d594bd44abc01007b9dd2adea5248f09906fa8d4c6cea7f36a4279e2de91e0")
        self.assertEqual(inputs["downloads"]["synchro32"]["sha256"], "ca76d774273ffa37053545f8e4ad63c8914461828f1d1eef7a1915c9656fed4c")
        finalizer = (ROOT / "compat" / "finalize-runtime.ps1").read_text(encoding="utf-8")
        self.assertNotIn("feature disable --name=powershellHost", finalizer)
        self.assertNotIn("feature disable --name=powershellHost", source)
        self.assertNotIn("powershellHostFeatures", finalizer)
        self.assertIn('mark_stage apply-chocolatey-policy', source)
        self.assertIn('choco_query_launcher=(wine "$choco_win")', source)
        self.assertIn('choco_package_launcher=(wineconsole "$choco_win")', source)
        self.assertIn('choco_working_directory="$wine_prefix/drive_c"', source)
        self.assertIn('cd "$choco_working_directory"', source)
        self.assertIn('desktop_link="$wine_prefix/drive_c/users/abc/Desktop"', source)
        self.assertIn('readlink "$desktop_link"', source)
        self.assertIn('rm -f -- "$desktop_link"', source)
        self.assertLess(
            source.index('cd "$choco_working_directory"'),
            source.index('mark_stage prove-runtime'),
        )
        self.assertEqual(
            contract["artifact"]["interfaces"]["chocolatey"]["queryLauncher"],
            "wine",
        )
        self.assertEqual(
            contract["artifact"]["interfaces"]["chocolatey"]["packageLauncher"],
            "wineconsole",
        )
        self.assertIn("choco_win='C:\\ProgramData\\chocolatey\\choco.exe'", source)
        self.assertNotIn("choco_win='C:\\ProgramData\\chocolatey\\bin\\choco.exe'", source)
        self.assertIn(
            'choco_shim="$wine_prefix/drive_c/ProgramData/chocolatey/bin/choco.exe"',
            source,
        )
        self.assertIn('[[ -s "$pwsh" && -s "$choco" && -s "$choco_shim" ]]', source)
        self.assertIn("mark_stage install-chocolatey-type-dependencies", source)
        self.assertIn("$(input_value windowsPowerShell filename)", source)
        self.assertIn("'System.Management.Automation.dll'", source)
        self.assertIn("chocolatey-type-dependency-inventory.log", source)
        self.assertIn('windows_powershell_assembly_count" -lt 8', source)
        self.assertIn("-iname 'System.Management.Automation.dll' -print -quit", source)
        self.assertIn('test -n "$system_management_automation"', source)
        self.assertIn('test -s "$system_management_automation"', source)
        self.assertLess(
            source.index("mark_stage install-chocolatey-type-dependencies"),
            source.index("mark_stage apply-chocolatey-policy"),
        )
        self.assertNotIn('set-chocolatey-policy.py" apply', source)
        self.assertIn('set-chocolatey-policy.py" seed', source)
        self.assertIn('chocolatey_config_template="$repo_root/compat/chocolatey.config"', source)
        self.assertIn('python3 "$repo_root/compat/set-chocolatey-policy.py" verify-status', source)
        policy_writer = (ROOT / "compat" / "set-chocolatey-policy.py").read_text(encoding="utf-8")
        policy_template = (ROOT / "compat" / "chocolatey.config").read_text(encoding="utf-8")
        self.assertIn('feature.set("enabled", "false")', policy_writer)
        self.assertIn('feature.set("setExplicitly", "true")', policy_writer)
        self.assertIn('tempfile.mkstemp(', policy_writer)
        self.assertIn('os.replace(temporary, path)', policy_writer)
        self.assertIn('getattr(os, "O_NOFOLLOW", 0)', policy_writer)
        self.assertIn("stat.S_ISLNK", policy_writer)
        self.assertIn("Chocolatey CLI 2.6.0", policy_template)
        self.assertIn("4321c87bceeaf7f6262d2616f20bddd7e432e8d8", policy_template)
        self.assertIn(
            '<feature name="powershellHost" enabled="false" setExplicitly="true" />',
            policy_template,
        )
        self.assertIn("pwsh-probe.log", source)
        self.assertIn('normalize_log "$logs/pwsh-probe.log"', source)
        self.assertIn('normalize_log "$logs/prepared-finalizer.log"', source)
        self.assertIn('normalize_log "$logs/choco-feature-status.log"', source)
        self.assertIn('normalize_log "$logs/choco-version.log"', source)
        self.assertLess(
            source.index('normalize_log "$logs/choco-feature-status.log"'),
            source.index('python3 "$repo_root/compat/set-chocolatey-policy.py" verify-status'),
        )
        self.assertIn("feature_status_settle_rc", source)
        self.assertIn("choco_settle_rc", source)
        self.assertIn("choco_version_rc", source)
        self.assertIn("synchro-x64.txt", source)
        self.assertIn("synchro-x86.txt", source)
        self.assertIn("CFW_PROFILE_COMPOSITION", source)
        self.assertIn("--use-system-powershell", source)
        self.assertIn('"${choco_package_launcher[@]}" install', source)
        self.assertIn('"${choco_package_launcher[@]}" uninstall', source)
        self.assertIn("cfw-runtime-prefix", source)
        self.assertIn("cfw.runtime-build/v2", source)
        self.assertIn('contract = json.loads(contract_path.read_text', source)
        self.assertIn('required_proofs = contract["build"]["requiredProofs"]', source)
        self.assertIn('set(evidence["checks"]) != set(required_proofs)', source)
        self.assertNotIn('"requiredProofs": sorted(evidence["checks"])', source)
        self.assertIn('"requiredProofs": required_proofs', source)
        self.assertIn('"interfaces": contract["artifact"]["interfaces"]', source)

    def test_pwsh_boundaries_receive_a_real_wine_console(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")

        self.assertIn('command -v wineconsole >/dev/null', source)
        self.assertIn(': "${DISPLAY:?CFW PowerShell proof requires the producer image X display}"', source)
        self.assertIn('pwsh_launcher=(wineconsole "$pwsh_win")', source)
        self.assertNotIn('wineconsole --backend=', source)
        self.assertIn('pwsh_evidence="$probe_dir/pwsh-evidence.txt"', source)
        self.assertIn('pwsh_evidence_expected="$logs/pwsh-evidence.expected"', source)
        self.assertIn('pwsh_marker_expected="$logs/pwsh-marker.expected"', source)
        self.assertIn('cmp -s "$pwsh_evidence_expected" "$pwsh_evidence"', source)
        self.assertIn('cmp -s "$pwsh_marker_expected" "$pwsh_marker"', source)
        self.assertIn('cat "$pwsh_evidence" >>"$logs/pwsh-probe.log"', source)
        self.assertIn('timeout --kill-after=15s 300s "${pwsh_launcher[@]}" -NoLogo -NoProfile -NonInteractive', source)
        self.assertIn('WINEDEBUG=+process,+loaddll,+seh timeout --kill-after=15s 90s \\\n    "${pwsh_launcher[@]}"', source)
        self.assertIn('timeout --kill-after=15s 300s "${pwsh_launcher[@]}" -NoLogo -NoProfile -NonInteractive \\\n  -File "$prepared_finalizer_win"', source)
        self.assertIn('prepared_finalizer_expected="$logs/prepared-finalizer.expected"', source)
        self.assertIn('cmp -s "$prepared_finalizer_expected" "$prepared_finalizer_marker"', source)
        self.assertNotIn("grep -Fqx '[cfw] stage=prepared-finalizer-script-entry' \"$prepared_finalizer_marker\"", source)
        self.assertIn('"$smoke_install_marker" "$smoke_uninstall_marker" "$pwsh_evidence"', source)
        self.assertIn('"pwsh-evidence.txt" in marker_hashes', source)
        self.assertIn('markers[6].read_bytes() == expected_pwsh_evidence', source)
        self.assertIn('markers[1].read_bytes() == expected_finalizer_evidence', source)
        finalizer = (ROOT / "compat" / "finalize-runtime.ps1").read_text(encoding="utf-8")
        self.assertIn("[IO.File]::WriteAllText(", finalizer)
        self.assertIn("prepared-finalizer-script-entry`n[cfw] stage=prepared-finalizer-complete`n", finalizer)

    def test_captured_commands_suspend_the_global_err_trap(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        lines = [line.strip() for line in source.splitlines()]

        self.assertEqual(lines.count("trap - ERR"), 13)
        self.assertEqual(lines.count("set +e"), 13)
        self.assertEqual(lines.count("set -e"), 13)
        self.assertEqual(lines.count("trap on_error ERR"), 14)
        for index, line in enumerate(lines):
            if line == "set +e":
                self.assertEqual(lines[index - 1], "trap - ERR")
            elif line == "set -e":
                self.assertEqual(lines[index + 1], "trap on_error ERR")
        test_script = "\n".join((
            "set -euo pipefail",
            "on_error() { exit 99; }",
            "trap on_error ERR",
            "trap - ERR",
            "set +e",
            "false",
            "captured=$?",
            "set -e",
            "trap on_error ERR",
            "printf 'captured=%s\\n' \"$captured\"",
            "[[ \"$captured\" -eq 1 ]]",
        ))
        result = subprocess.run(
            ["bash"], input=test_script, text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "captured=1\n")

    def test_finalizer_persists_failure_diagnostics(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        finalizer = (ROOT / "compat" / "finalize-runtime.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertIn("prepared-finalizer-diagnostic.txt", finalizer)
        self.assertIn("function Write-Diagnostic", finalizer)
        self.assertIn("catch {", finalizer)
        self.assertIn("finally {", finalizer)
        self.assertIn("$_.Exception.GetType().FullName", finalizer)
        self.assertEqual(finalizer.count("[IO.File]::AppendAllText("), 1)
        self.assertIn('prepared_finalizer_diagnostic="$probe_dir/prepared-finalizer-diagnostic.txt"', source)
        self.assertIn('rm -f "$logs/prepared-finalizer-diagnostic.log"', source)
        self.assertLess(
            source.index('rm -f "$logs/prepared-finalizer-diagnostic.log"'),
            source.index('[[ -f "$runtime_inputs" ]]'),
        )
        self.assertIn('cp -f "$prepared_finalizer_diagnostic" "$logs/prepared-finalizer-diagnostic.log"', source)
        self.assertIn("prepared-finalizer-diagnostic", workflow)
        self.assertIn('sudo rm -rf -- "$out"', workflow)
        self.assertLess(
            workflow.index('sudo rm -rf -- "$out"'),
            workflow.index('docker run --name "$container_name"'),
        )
        self.assertIn('[[ ! -L "$output_root" && ! -L "$logs" ]]', source)
        self.assertIn("No output was attributed to this workflow attempt.", workflow)
        self.assertIn("Refusing to prepare or upload output without the current workflow-attempt marker.", workflow)
        self.assertIn("if: always() && steps.diagnostics.outcome == 'success'", workflow)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "reused-output"
            logs = output / "logs"
            logs.mkdir(parents=True)
            stale = logs / "prepared-finalizer-diagnostic.log"
            stale.write_text("STALE-DIAGNOSTIC-FROM-PRIOR-RUN\n", encoding="utf-8")
            installer = Path(temporary) / "installer.exe"
            installer.write_bytes(b"installer fixture")
            environment = os.environ.copy()
            environment["CFW_COMPILED_INSTALLER"] = str(installer)
            for name in (
                "CFW_SOURCE_REVISION", "CFW_WINE_IMAGE", "CFW_EXPECTED_WINE_VERSION",
                "SOURCE_DATE_EPOCH",
            ):
                environment.pop(name, None)
            result = subprocess.run(
                ["bash", str(ROOT / "compat" / "build-runtime.sh"), str(output)],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CFW_SOURCE_REVISION must be the exact source commit", result.stderr)
            self.assertFalse(stale.exists(), result.stderr)

            external = Path(temporary) / "external-logs"
            external.mkdir()
            external_stale = external / "prepared-finalizer-diagnostic.log"
            external_stale.write_text("EXTERNAL-SENTINEL\n", encoding="utf-8")
            symlinked_output = Path(temporary) / "symlinked-output"
            symlinked_output.mkdir()
            (symlinked_output / "logs").symlink_to(external, target_is_directory=True)
            result = subprocess.run(
                ["bash", str(ROOT / "compat" / "build-runtime.sh"), str(symlinked_output)],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be symbolic links", result.stderr)
            self.assertEqual(
                external_stale.read_text(encoding="utf-8"),
                "EXTERNAL-SENTINEL\n",
            )

    def test_runtime_evidence_rejects_noncanonical_persisted_proofs(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        anchor = 'path = Path(sys.argv[1])\nvalues = [int(value) for value in sys.argv[2:35]]'
        anchor_index = source.index(anchor)
        program_start = source.rfind("<<'PY2'\n", 0, anchor_index) + len("<<'PY2'\n")
        program_end = source.index("\nPY2\n", anchor_index)
        evidence_program = source[program_start:program_end]
        winepath_labels = (
            "cfw-cache", "cfw-installer", "pwsh-policy", "pwsh-executable",
            "pwsh-probe-script", "pwsh-marker", "prepared-finalizer",
            "profile-fragments", "prepared-finalizer-marker", "synchro-x64-marker",
            "synchro-x86-marker", "smoke-feed",
        )
        environment = {
            **os.environ,
            "CFW_OBSERVED_WINE_VERSION": "wine-11.0",
            "CFW_EXPECTED_WINE_VERSION": "wine-11.0",
            "CFW_EXPECTED_POWERSHELL_VERSION": "7.6.5",
            "CFW_OBSERVED_CHOCOLATEY_VERSION": "2.6.0",
            "CFW_EXPECTED_CHOCOLATEY_VERSION": "2.6.0",
            "CFW_CONTRACT_SCHEMA": "cfw.compatibility-contract/v3",
            "CFW_CONTRACT_SHA256": "0" * 64,
            "CFW_RUNTIME_ID": "cfw-wine-11.0",
            "CFW_WINE_IMAGE": "ghcr.io/pelagians/cage-wine@sha256:" + "1" * 64,
            "CFW_WINE_SESSION_CONTRACT": "cage.selkies-wayland/v1",
            "CFW_SOURCE_REVISION": "2" * 40,
            "CFW_INSTALLER_SHA256": "3" * 64,
            "CFW_RUNTIME_INPUTS_SHA256": "4" * 64,
            "CFW_EXPECTED_SYNCHRO_VERSION": "4.2.0",
        }
        canonical = (
            b"7.6.5",
            b"[cfw] stage=prepared-finalizer-script-entry\n[cfw] stage=prepared-finalizer-complete\n",
            b"synchro-x64", b"synchro-x86", b"installed", b"uninstalled",
            b"[cfw] pwsh-script-entry\n[cfw] pwsh=7.6.5\n",
        )
        cases = {
            "canonical": (0, "passed", None),
            "missing-pwsh-evidence": (70, "failed", (6, None)),
            "duplicate-pwsh-evidence": (70, "failed", (6, canonical[6] + canonical[6])),
            "fragmented-version-marker": (70, "failed", (0, b"7.\n6.5")),
            "extra-finalizer-token": (
                70,
                "failed",
                (1, canonical[1] + b"[cfw] stage=prepared-finalizer-complete\n"),
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logs = root / "logs"
            logs.mkdir()
            for label in winepath_labels:
                (logs / f"winepath-{label}.status").write_text("0 0\n", encoding="utf-8")
            markers = [
                root / name for name in (
                    "pwsh.txt", "prepared-finalizer.txt", "synchro-x64.txt",
                    "synchro-x86.txt", "chocolatey-install.txt",
                    "chocolatey-uninstall.txt", "pwsh-evidence.txt",
                )
            ]
            for case, (expected_rc, expected_status, mutation) in cases.items():
                for marker, content in zip(markers, canonical):
                    marker.write_bytes(content)
                if mutation is not None:
                    marker_index, content = mutation
                    if content is None:
                        markers[marker_index].unlink()
                    else:
                        markers[marker_index].write_bytes(content)
                metadata = root / f"{case}.json"
                arguments = [
                    str(metadata), *("0" for _ in range(33)), str(logs),
                    *(str(marker) for marker in markers),
                ]
                result = subprocess.run(
                    ["python3", "-", *arguments],
                    input=evidence_program,
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=False,
                )
                self.assertEqual(result.returncode, expected_rc, case)
                self.assertEqual(
                    json.loads(metadata.read_text(encoding="utf-8"))["status"],
                    expected_status,
                    case,
                )

    def test_windows_crlf_feature_status_is_normalized_before_exact_match(self) -> None:
        writer = ROOT / "compat" / "set-chocolatey-policy.py"

        def verify(payload: str) -> subprocess.CompletedProcess[str]:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as status:
                status.write(payload)
                status.flush()
                return subprocess.run(
                    ["python3", str(writer), "verify-status", status.name],
                    text=True,
                    capture_output=True,
                    check=False,
                )

        self.assertEqual(verify("powershellHost|disabled\r\n").returncode, 0)
        self.assertEqual(verify("PowershellHost|false\n").returncode, 0)
        self.assertEqual(
            verify(
                "powershellHost|Disabled|Use Chocolatey's built-in PowerShell host.\r\n"
            ).returncode,
            0,
        )
        for payload in (
            "",
            "powershellHost|enabled\n",
            "powershellHost|disabled\npowershellHost|disabled\n",
            "powershellHost|disabled\npowershellHost|enabled\n",
        ):
            with self.subTest(payload=payload):
                self.assertEqual(verify(payload).returncode, 70)

    def test_locked_versions_bind_runtime_identity_and_observed_evidence(self) -> None:
        inputs = json.loads((ROOT / "compat" / "runtime-inputs.json").read_text(encoding="utf-8"))
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")

        self.assertEqual(inputs["versions"]["powershell"], "7.6.5")
        self.assertEqual(inputs["versions"]["chocolatey"], "2.6.0")
        self.assertEqual(inputs["versions"]["synchro"], "4.2.0")
        self.assertIn('CFW_RUNTIME_ID="$(runtime_value runtimeId)"', source)
        self.assertIn('CFW_EXPECTED_POWERSHELL_VERSION="$(runtime_version powershell)"', source)
        self.assertIn('CFW_EXPECTED_CHOCOLATEY_VERSION="$(runtime_version chocolatey)"', source)
        self.assertIn('observed_powershell = markers[0].read_text(encoding="utf-8")', source)
        self.assertIn('"powershell": observed_powershell', source)
        self.assertIn('"runtimeId": os.environ["CFW_RUNTIME_ID"]', source)

    def test_prepared_runtime_finalizer_runs_after_first_pwsh_proof(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        finalizer = (ROOT / "compat" / "finalize-runtime.ps1").read_text(encoding="utf-8")

        self.assertIn("[cfw] stage=prepared-finalizer-script-entry", finalizer)
        self.assertIn("$ErrorActionPreference = 'Stop'", finalizer)
        self.assertIn("$markerParent = Split-Path -Parent $MarkerPath", finalizer)
        self.assertIn("application-profile.d", finalizer)
        self.assertNotIn("feature disable --name=powershellHost", finalizer)
        self.assertNotIn("powershellHost", finalizer)
        self.assertIn("mark_stage finalize-prepared-runtime", source)
        self.assertIn('-File "$prepared_finalizer_win"', source)
        self.assertLess(
            source.index('mark_stage prove-pwsh'),
            source.index('mark_stage finalize-prepared-runtime'),
        )
        self.assertLess(
            source.index('mark_stage finalize-prepared-runtime'),
            source.index('mark_stage install-synchro'),
        )
        self.assertLess(
            source.index('mark_stage install-synchro'),
            source.index('mark_stage apply-chocolatey-policy'),
        )
        self.assertLess(
            source.index('mark_stage apply-chocolatey-policy'),
            source.index('mark_stage prove-runtime'),
        )
        self.assertNotIn("write_cfw_profile_loader", source)

    def test_runtime_inputs_are_locked_and_checkout_overrides_are_identified(self) -> None:
        inputs_path = ROOT / "compat" / "runtime-inputs.json"
        self.assertTrue(inputs_path.is_file(), "prepared runtime inputs must be versioned")
        inputs = json.loads(inputs_path.read_text(encoding="utf-8"))

        self.assertEqual(inputs["schemaVersion"], "cfw.runtime-inputs/v1")
        upstream = inputs["upstreamRelease"]
        self.assertEqual(upstream["repository"], "PietJankbal/Chocolatey-for-wine")
        self.assertEqual(upstream["tag"], "v0.5c.765")
        self.assertEqual(
            inputs["producerWineImage"],
            "ghcr.io/pelagians/cage-wine@sha256:7ad192e00a251523f3a071d3ffa422789b010c359d18cd45227d9f89165f6b92",
        )
        self.assertEqual(upstream["revision"], "71bf92916b8d259458017a583a37dfde330b241e")
        self.assertEqual(
            inputs["downloads"]["cfwRelease"],
            {
                "url": "https://github.com/PietJankbal/Chocolatey-for-wine/releases/download/v0.5c.765/Chocolatey-for-wine.7z",
                "sha256": "225fadb61bc2608651384f91cd19aeaedb315a20b15047e6f84684ce312e0b9d",
                "filename": "Chocolatey-for-wine.7z",
                "installerFilename": "ChoCinstaller_0.5a.765.exe",
            },
        )
        self.assertEqual(
            inputs["downloads"]["powershell"],
            {
                "url": "https://github.com/PowerShell/PowerShell/releases/download/v7.6.5/PowerShell-7.6.5-win-x64.msi",
                "sha256": "3a87c24e044ec792047d734c841917ee4323a535e25f645ae6c33141a35fca8d",
                "filename": "PowerShell-7.6.5-win-x64.msi",
            },
        )
        self.assertEqual(
            upstream["selectedPorts"],
            {
                "installer.c": "WebView --disable_direct_composition=1 compatibility flag",
                "choc_install.ps1": "version-agnostic PowerShell MSI cache discovery with exact-singleton validation",
            },
        )
        self.assertEqual(
            upstream["deferredPaths"],
            [
                "EXTRAS/wine_combase.7z",
                "EXTRAS/wine_combase.patch",
                "EXTRAS/wine_wintypes.patch",
                "winetricks.ps1",
            ],
        )
        for name in (
            "cfwRelease",
            "chocolatey",
            "powershell",
            "dotnet",
            "mscoree",
            "synchro64",
            "synchro32",
        ):
            payload = inputs["downloads"][name]
            self.assertTrue(payload["url"].startswith("https://"), name)
            self.assertRegex(payload["sha256"], r"^[0-9a-f]{64}$", name)
        self.assertEqual(set(inputs["checkoutSources"]), {"choc_install.ps1", "winetricks.ps1"})
        installer_digest = inputs["checkoutSources"]["choc_install.ps1"]["sha256"]
        self.assertRegex(installer_digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            installer_digest,
            hashlib.sha256((ROOT / "choc_install.ps1").read_bytes()).hexdigest(),
            "runtime input lock must match the installer compiled by CI",
        )
        self.assertEqual(
            inputs["checkoutSources"]["winetricks.ps1"]["sha256"],
            hashlib.sha256((ROOT / "winetricks.ps1").read_bytes()).hexdigest(),
        )

        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        self.assertIn("runtime-inputs.json", source)
        self.assertIn("CFW_COMPILED_INSTALLER", source)
        self.assertIn("ChoCinstaller-under-test.exe", source)
        self.assertIn("CFW_RUNTIME_INPUTS_SHA256", source)
        self.assertIn("CFW_INSTALLER_SHA256", source)
        self.assertIn('"installerSha256"', source)
        self.assertIn("verify_checkout_source choc_install.ps1", source)
        self.assertIn("verify_checkout_source winetricks.ps1 \"$repo_root/winetricks.ps1\"", source)
        self.assertIn("verify_checkout_source winetricks.ps1 \"$winetricks_script\"", source)
        self.assertIn('release_installer="$(input_value cfwRelease installerFilename)"', source)
        self.assertIn(r'^ChoCinstaller_[0-9]\.[0-9][A-Za-z]\.([0-9])([0-9])([0-9])\.exe$', source)
        self.assertIn('release_powershell_version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"', source)
        self.assertIn('[[ "$release_powershell_version" != "$CFW_EXPECTED_POWERSHELL_VERSION" ]]', source)
        self.assertIn('[[ -f "$release_dir/$release_installer" ]]', source)
        self.assertIn('cp -f "$compiled_installer" "$release_dir/$release_installer"', source)
        self.assertIn('installer_win="$(winepath_to_windows cfw-installer "$release_dir/$release_installer")"', source)
        self.assertNotIn("ChoCinstaller_0.5c.755.exe", source)

    def test_runtime_builder_requires_behavioral_proofs_and_manifest(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")

        self.assertIn("synchro-x64.txt", source)
        self.assertIn("synchro-x86.txt", source)
        self.assertIn("chocolatey-install.txt", source)
        self.assertIn("chocolatey-uninstall.txt", source)
        self.assertIn(
            r"$marker = 'C:\ProgramData\CFW\RuntimeProbe\chocolatey-install.txt'",
            source,
        )
        self.assertIn("[cfw-smoke] install-script-executed", source)
        self.assertIn("[cfw-smoke] uninstall-script-executed", source)
        self.assertIn('cp -f "$chocolatey_runtime_log" "$logs/chocolatey-runtime.log"', source)
        self.assertIn("featurePolicy", source)
        self.assertIn("CFW_WINE_IMAGE", source)
        self.assertIn("CFW_SOURCE_REVISION must be the exact source commit", source)
        self.assertIn("must be a full lowercase Git commit SHA", source)
        self.assertIn("must be a ghcr.io/pelagians/cage-wine digest", source)
        self.assertIn("CFW_RUNTIME_EVIDENCE_NAME", source)
        self.assertIn("CFW_RUNTIME_MANIFEST_NAME", source)
        self.assertIn("values = [int(value) for value in sys.argv[2:35]]", source)
        self.assertIn("logs_path = Path(sys.argv[35])", source)
        self.assertIn("markers = [Path(value) for value in sys.argv[36:]]", source)

    def test_wine_identity_and_pre_pwsh_policy_have_isolated_settlement_evidence(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")

        for token in (
            "CFW_EXPECTED_WINE_VERSION",
            "CFW_OBSERVED_WINE_VERSION",
            "wine_version_rc",
            "wine_version_settle_rc",
            "pwsh_winecfg_rc",
            "pwsh_winecfg_settle_rc",
            "pwsh_regedit_rc",
            "pwsh_regedit_settle_rc",
            "pwsh_query_rc",
            "pwsh_query_settle_rc",
            "pwsh_mscoree_rc",
            '"wineIdentity"',
            '"prePwshPolicy"',
        ):
            self.assertIn(token, source)
        self.assertNotIn('subprocess.run(["wine", "--version"]', source)
        self.assertLess(source.index("winecfg /v win10"), source.index('pwsh_winecfg_settle_rc="$?"'))
        self.assertLess(source.index('pwsh_winecfg_settle_rc="$?"'), source.index('wine regedit /S'))
        self.assertLess(source.index('wine regedit /S'), source.index('pwsh_regedit_settle_rc="$?"'))
        pwsh_query = 'wine reg query "$pwsh_policy_key"'
        self.assertLess(source.index('pwsh_regedit_settle_rc="$?"'), source.index(pwsh_query))
        self.assertLess(source.index(pwsh_query), source.index('pwsh_query_settle_rc="$?"'))

    def test_contract_is_authoritative_for_wine_matrix_and_builder_identity(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertIn('CFW_CONTRACT_WINE_VERSION=', source)
        self.assertIn('contract["build"]["wineCandidates"]', source)
        self.assertIn('"wine-$CFW_CONTRACT_WINE_VERSION"', source)
        self.assertNotIn('CFW_EXPECTED_WINE_VERSION" != "wine-11.0"', source)
        self.assertIn("name: Resolve compatibility contract", workflow)
        self.assertIn("fromJSON(needs.contract.outputs.wines)", workflow)
        self.assertIn('contract["build"]["wineCandidates"]', workflow)
        self.assertNotIn("expected_wines=(11.0)", workflow)
        self.assertIn('assert wine in contract["build"]["wineCandidates"]', workflow)

    def test_every_winepath_boundary_is_bounded_settled_and_evidenced(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")

        helper = source[source.index("winepath_to_windows()") : source.index("build_smoke_package()")]
        self.assertIn('timeout --kill-after=10s 60s winepath -w', helper)
        self.assertIn('timeout --kill-after=10s 60s wineserver -w', helper)
        self.assertIn('status="$logs/winepath-${label}.status"', helper)
        self.assertNotRegex(source, r'(?m)^(?!.*timeout).*\$\(winepath -w')
        for label in ("synchro-x64-marker", "synchro-x86-marker", "smoke-feed"):
            self.assertIn(f'winepath_to_windows {label}', source)
        self.assertIn('"pathConversions"', source)
        self.assertIn('"winepathReturnCodes"', source)

    def test_chocolatey_version_is_one_exact_observed_value(self) -> None:
        source = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")

        self.assertIn("read_single_observed_line", source)
        self.assertIn('CFW_OBSERVED_WINE_VERSION="$(read_single_observed_line', source)
        self.assertIn('CFW_OBSERVED_CHOCOLATEY_VERSION=', source)
        self.assertIn('CFW_OBSERVED_CHOCOLATEY_VERSION="$(read_single_observed_line', source)
        self.assertIn('choco_version_output_rc="$?"', source)
        self.assertIn("choco-version-diagnostic.log", source)
        self.assertIn("choco-version-direct.log", source)
        self.assertIn('wine "$choco_win" --version', source)
        self.assertIn("choco-loader-probe.cs", source)
        self.assertIn("choco-loader-probe.log", source)
        loader_probe = (ROOT / "compat" / "choco-loader-probe.cs").read_text(encoding="utf-8")
        self.assertIn("ReflectionTypeLoadException", loader_probe)
        self.assertIn("LoaderExceptions", loader_probe)
        self.assertIn("Chocolatey probe return codes", source)
        self.assertIn('[[ "$CFW_OBSERVED_CHOCOLATEY_VERSION" == "$CFW_EXPECTED_CHOCOLATEY_VERSION" ]]', source)
        self.assertNotIn('grep -Fqx "$CFW_EXPECTED_CHOCOLATEY_VERSION"', source)
        self.assertIn('"chocolatey": os.environ["CFW_OBSERVED_CHOCOLATEY_VERSION"]', source)

        def parse_one(raw: str) -> str:
            lines = raw.replace("\r", "").splitlines()
            if len(lines) != 1 or not lines[0]:
                raise ValueError("non-canonical version output")
            return lines[0]

        self.assertEqual(parse_one("wine-11.0\n"), "wine-11.0")
        self.assertEqual(parse_one("2.6.0\n"), "2.6.0")
        for malformed in ("wine-\n11.0\n", "2.\n6.0\n", "\n2.6.0\n", ""):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    parse_one(malformed)

    def test_release_validator_reanchors_authoritative_inputs_and_archive_evidence(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")
        for token in (
            'sha256(runtime_inputs_path) == manifest["runtimeInputsSha256"]',
            'sha256(installer_path) == manifest["installerSha256"]',
            'manifest["contractSha256"] == sha256(contract_path)',
            'runtime["contractSha256"] == manifest["contractSha256"]',
            'archive_runtime = tar.extractfile("./.cfw/runtime.json")',
            'archive_runtime.read() == evidence.read_bytes()',
            'choco_interface in archive_names',
        ):
            self.assertIn(token, workflow)

    def test_release_gate_uses_authoritative_contract_and_cross_binds_provenance(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")
        for token in (
            "compat/contract.json",
            'runtime["contract"] == manifest["contract"] == contract["schemaVersion"]',
            'runtime["runtimeId"] == manifest["runtimeId"]',
            'runtime["installerSha256"] == manifest["installerSha256"]',
            'runtime["runtimeInputsSha256"] == manifest["runtimeInputsSha256"]',
            'manifest["requiredProofs"] == contract["build"]["requiredProofs"]',
            'runtime["wine"]["version"] == f"wine-{wine}"',
            'all(value is True for value in runtime["checks"].values())',
        ):
            self.assertIn(token, workflow)

    def test_runtime_archive_packaging_is_byte_reproducible(self) -> None:
        packager = ROOT / "compat" / "package-runtime.sh"
        source = packager.read_text(encoding="utf-8")
        self.assertIn('find "$prefix" -path "$dosdevices" -prune -o -type l -print0', source)
        for token in (
            "--sort=name",
            "--format=posix",
            "delete=atime",
            "delete=ctime",
            '--mtime="@${SOURCE_DATE_EPOCH}"',
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "gzip -n",
        ):
            self.assertIn(token, source)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefixes = [root / "prefix-a", root / "prefix-b"]
            files = {
                "drive_c/z-last.txt": b"last\n",
                "drive_c/a-first.txt": b"first\n",
                ".cfw/runtime.json": b"{}\n",
            }
            for index, prefix in enumerate(prefixes):
                order = list(files.items()) if index == 0 else list(reversed(files.items()))
                for relative, payload in order:
                    target = prefix / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                    os.utime(target, (1_600_000_000 + index, 1_600_000_000 + index))
                dosdevices = prefix / "dosdevices"
                dosdevices.mkdir()
                (dosdevices / "c:").symlink_to("../drive_c")
            archives = [root / "a.tar.gz", root / "b.tar.gz"]
            environment = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}
            for prefix, archive in zip(prefixes, archives):
                subprocess.run(
                    ["bash", str(packager), str(prefix), str(archive)],
                    check=True,
                    env=environment,
                )
            self.assertEqual(archives[0].read_bytes(), archives[1].read_bytes())
            unsafe_prefix = root / "prefix-unsafe"
            (unsafe_prefix / "drive_c/users/abc").mkdir(parents=True)
            (unsafe_prefix / "drive_c/users/abc/Desktop").symlink_to("/config/Desktop")
            unsafe_dosdevices = unsafe_prefix / "dosdevices"
            unsafe_dosdevices.mkdir()
            (unsafe_dosdevices / "c:").symlink_to("../drive_c")
            rejected = subprocess.run(
                ["bash", str(packager), str(unsafe_prefix), str(root / "unsafe.tar.gz")],
                check=False,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 66)
            self.assertIn("absolute symlink", rejected.stderr)
            with tarfile.open(archives[0], "r:gz") as archive:
                members = {member.name: member for member in archive.getmembers()}
                self.assertIn("./dosdevices", members)
                self.assertIn("./dosdevices/c:", members)
                self.assertTrue(members["./dosdevices/c:"].issym())
                self.assertEqual(members["./dosdevices/c:"].linkname, "../drive_c")
                self.assertFalse(
                    any(
                        member.name.startswith("./dosdevices/")
                        and member.name != "./dosdevices/c:"
                        for member in archive.getmembers()
                    )
                )

    def test_runtime_workflow_resolves_image_digest_and_publishes_tagged_assets(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertIn("docker image inspect", workflow)
        self.assertIn("CFW_WINE_IMAGE", workflow)
        self.assertIn('"${{ steps.image.outputs.image }}" \\', workflow)
        self.assertIn("cfw-runtime-v*", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("cfw-runtime-manifest-wine-", workflow)
        self.assertIn("emit failure evidence", workflow)
        self.assertIn("winepath-cfw-cache", workflow)
        self.assertIn("winepath-cfw-installer", workflow)
        self.assertIn("pwsh-probe", workflow)
        self.assertIn("pwsh-proof-summary", workflow)
        self.assertIn("pwsh-failure-trace", workflow)
        self.assertIn("pwsh-policy", workflow)
        self.assertIn("returnCodes", workflow)
        self.assertLess(workflow.index("pwsh-probe"), workflow.index("installer stages"))
        self.assertIn('head -c 2600', workflow)
        self.assertIn("CFW runtime build failed", workflow)
        self.assertIn("Compile CFW installer under test", workflow)
        self.assertIn("installer.c", workflow)
        self.assertIn("CFW_COMPILED_INSTALLER=/src/compat/ChoCinstaller-under-test.exe", workflow)
        self.assertIn("hashlib.sha256", workflow)
        self.assertIn("GITHUB_SHA", workflow)
        self.assertIn("SOURCE_DATE_EPOCH", workflow)
        self.assertIn("git show -s --format=%ct", workflow)
        self.assertIn("release already exists", workflow)
        self.assertNotIn("--clobber", workflow)

    def test_runtime_workflow_executes_through_universal_selkies_init(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertIn("Validate universal Cage runtime contract", workflow)
        self.assertIn("cage.selkies-wayland/v1", workflow)
        self.assertIn("/init", workflow)
        self.assertIn("CAGE_BUILD_SCRIPT_B64", workflow)
        self.assertIn('PUID="$(id -u)"', workflow)
        self.assertIn('PGID="$(id -g)"', workflow)
        self.assertIn('-e PUID="$PUID"', workflow)
        self.assertIn('-e PGID="$PGID"', workflow)
        self.assertIn('-e CAGE_BUILD_SCRIPT_B64="$build_script_b64"', workflow)
        self.assertIn('"${{ steps.image.outputs.image }}"', workflow)
        self.assertNotIn(
            '"${{ steps.image.outputs.image }}" \
            /bin/bash /src/compat/build-runtime.sh /out',
            workflow,
        )

    def test_runtime_workflow_keeps_public_candidate_proof_credentialless(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertNotIn("packages: read", workflow)
        self.assertNotIn("docker/login-action", workflow)
        self.assertGreaterEqual(workflow.count("persist-credentials: false"), 3)

    def test_manual_requalification_accepts_only_an_exact_candidate_digest(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertIn("wine_image:", workflow)
        self.assertIn("Immutable universal Cage Wine image", workflow)
        self.assertIn("inputs.wine_image", workflow)
        self.assertIn("producerWineImage", workflow)
        self.assertIn("needs.contract.outputs.wine_image", workflow)
        self.assertIn("ghcr.io/pelagians/cage-wine@sha256:", workflow)
        self.assertIn("Reject non-digest manual image", workflow)
        self.assertNotIn("docker run --privileged", workflow)

    def test_prepared_runtime_binds_universal_session_contract(self) -> None:
        contract = json.loads((ROOT / "compat" / "contract.json").read_text(encoding="utf-8"))
        builder = (ROOT / "compat" / "build-runtime.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertEqual(contract["build"]["producerSessionContract"], "cage.selkies-wayland/v1")
        self.assertIn("CFW_WINE_SESSION_CONTRACT", builder)
        self.assertIn('"sessionContract": os.environ["CFW_WINE_SESSION_CONTRACT"]', builder)
        self.assertIn('"sessionContract": evidence["sessionContract"]', builder)
        self.assertIn("CFW_WINE_SESSION_CONTRACT=cage.selkies-wayland/v1", workflow)
        self.assertIn('runtime["sessionContract"] == manifest["sessionContract"]', workflow)
        self.assertIn('manifest["sessionContract"] == contract["build"]["producerSessionContract"]', workflow)

    def test_runtime_task_is_finite_unprivileged_and_drops_selkies_preloads(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")

        self.assertIn("unset LD_PRELOAD", workflow)
        self.assertIn("export WINEPREFIX=/out/prefix", workflow)
        self.assertIn("export CFW_BUILD_CACHE=/out/cache", workflow)
        self.assertNotIn("-e WINEPREFIX=/out/prefix", workflow)
        self.assertNotIn("-e CFW_BUILD_CACHE=/out/cache", workflow)
        self.assertIn("exec /bin/bash /src/compat/build-runtime.sh /out", workflow)
        self.assertIn("container-exit-code", workflow)
        self.assertIn("task-exit-code", workflow)
        self.assertIn("docker wait", workflow)
        self.assertNotIn("--entrypoint", workflow)

    def test_runtime_workflow_targets_contract_wine_inventory(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-container-runtime.yml").read_text(encoding="utf-8")
        contract = json.loads((ROOT / "compat" / "contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["build"]["wineCandidates"], ["11.0"])
        self.assertIn("fromJSON(needs.contract.outputs.wines)", workflow)
        self.assertNotIn("wine: ['11.0']", workflow)

    def test_profile_fragments_are_additive(self) -> None:
        profile_dir = ROOT / "compat" / "profile.d"
        fragments = sorted(profile_dir.glob("*.ps1"))

        self.assertEqual(
            [path.name for path in fragments],
            [
                "10-runtime-contract.ps1",
                "20-chocolatey.ps1",
                "30-cfw-winetricks.ps1",
                "40-cfw-command-adapters.ps1",
            ],
        )
        for fragment in fragments:
            text = fragment.read_text(encoding="utf-8")
            self.assertNotIn("New-Item -Path $PROFILE", text)
            self.assertNotIn("Out-File $PROFILE", text)
            self.assertNotIn("WindowsPowerShell\\v1.0\\powershell.exe", text)

    def test_native_finalizer_remains_bounded(self) -> None:
        source = (ROOT / "compat" / "container-finalizer.c").read_text(encoding="utf-8")

        for variable in (
            "CFW_CONTAINER_BUILDER",
            "CFW_OFFLINE",
            "CFW_EXTERNAL_POWERSHELL",
        ):
            self.assertIn(variable, source)
        self.assertIn("stage=stage-resume", source)
        self.assertIn("stage=canonical-reconcile", source)
        self.assertIn("chocolatey.cfw-stage", source)
        self.assertNotIn("URLDownloadToFile", source)

    def test_legacy_wrapper_propagates_process_creation_failure(self) -> None:
        source = (ROOT / "mainv1.c").read_text(encoding="utf-8")

        self.assertIn("si.cb = sizeof(si);", source)
        self.assertIn("if (!CreateProcessW(", source)
        self.assertIn("return GetLastError();", source)
        self.assertIn("wait_result = WaitForSingleObject", source)
        self.assertIn("if (!GetExitCodeProcess", source)
        self.assertIn("CloseHandle(pi.hProcess);", source)
        self.assertIn("CloseHandle(pi.hThread);", source)


if __name__ == "__main__":
    unittest.main()
