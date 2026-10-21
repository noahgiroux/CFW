from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "installer.c").read_text(encoding="utf-8")
COMMON = (ROOT / "compat/native-common.h").read_text()
MAIN = INSTALLER[INSTALLER.index("int mainCRTStartup") :]


class InstallerOrchestrationContractTests(unittest.TestCase):
    def test_chocolatey_download_and_saved_cache_match_runtime_lock(self):
        inputs = json.loads((ROOT / "compat/runtime-inputs.json").read_text())
        version = inputs["versions"]["chocolatey"]
        payload = inputs["downloads"]["chocolatey"]
        filename = f"chocolatey.{version}.nupkg"
        download = INSTALLER[
            INSTALLER.index("DWORD WINAPI chocolatey_install") :
            INSTALLER.index("DWORD WINAPI pscore_install")
        ]
        header = (ROOT / "compat/installer-inputs.h").read_text()
        constants = dict(re.findall(r'#define (CFW_\w+) L("[^"\n]+")', header))
        self.assertEqual(json.loads(constants["CFW_CHOCOLATEY_URL"]), payload["url"])
        self.assertEqual(json.loads(constants["CFW_CHOCOLATEY_FILENAME"]), filename)
        self.assertIn("CFW_CHOCOLATEY_URL", download)
        self.assertIn("CFW_CHOCOLATEY_FILENAME", download)
        self.assertEqual(payload["filename"], filename)
        self.assertEqual(
            payload["url"],
            f"https://community.chocolatey.org/api/v2/package/chocolatey/{version}",
        )
        script = (ROOT / "choc_install.ps1").read_text()
        self.assertEqual(re.findall(r"chocolatey\.[0-9.]+\.nupkg", script), [filename])
        self.assertIn(f"chocolatey-{version}-", inputs["runtimeId"])
        contract = json.loads((ROOT / "compat/contract.json").read_text())
        self.assertEqual(contract["provides"]["package-manager"], f"chocolatey-{version}")

    def test_finalizer_runs_only_after_prerequisite_threads_finish(self):
        wait = MAIN.find("WaitForMultipleObjects")
        finalizer = MAIN.find("pscore_install(&p)")

        self.assertGreaterEqual(wait, 0)
        self.assertGreaterEqual(finalizer, 0)
        self.assertLess(wait, finalizer)
        self.assertNotIn("CreateThread(NULL, 0, pscore_install", MAIN)

    def test_prerequisite_thread_failures_are_propagated(self):
        self.assertIn("GetExitCodeThread", MAIN)
        self.assertIn("ExitProcess(exit_code)", MAIN)
        self.assertNotIn("ExitProcess(0);", MAIN)

    def test_child_process_exit_codes_are_checked(self):
        self.assertIn("run_process", INSTALLER)
        self.assertIn("run_process_bounded", INSTALLER)
        self.assertIn("TerminateProcess", INSTALLER)
        self.assertIn("GetExitCodeProcess", INSTALLER)
        self.assertIn("ERROR_SUCCESS_REBOOT_REQUIRED", INSTALLER)

    def test_offline_mode_blocks_download_fallbacks_and_uses_cached_dotnet(self):
        download = INSTALLER[INSTALLER.index("static DWORD download_file") : INSTALLER.index("static DWORD validate_canonical_choco")]
        self.assertIn('_wgetenv(L"CFW_OFFLINE")', download)
        self.assertLess(download.index('_wgetenv(L"CFW_OFFLINE")'), download.index("URLDownloadToFileW"))
        cache = INSTALLER[INSTALLER.index("static DWORD cached_payload") : INSTALLER.index("static DWORD validate_canonical_choco")]
        self.assertIn("return require_file(path)", cache)
        self.assertLess(cache.index("p->cache_dir"), cache.index("p->setupcache"))
        net = INSTALLER[INSTALLER.index("DWORD WINAPI net48_install") : INSTALLER.index("DWORD WINAPI chocolatey_install")]
        self.assertIn("CFW_DOTNET_MSI_FILENAME", net)
        self.assertIn("CFW_DOTNET_INSTALL_DIRECTORY", net)
        self.assertIn("cached_payload(p, CFW_DOTNET_URL", net)

    def test_container_builder_uses_native_canonical_promotion(self):
        pscore = INSTALLER[INSTALLER.index("DWORD WINAPI pscore_install") : INSTALLER.index("DWORD WINAPI cdrive_install")]
        self.assertIn('_wgetenv(L"CFW_CONTAINER_BUILDER")', pscore)
        self.assertIn('_wgetenv(L"CFW_OFFLINE")', pscore)
        self.assertIn("native_finalize_chocolatey(p)", pscore)
        self.assertIn("pscore_install(&p)", MAIN)
        self.assertLess(MAIN.index("WaitForMultipleObjects"), MAIN.index("pscore_install(&p)"))

        native = INSTALLER[INSTALLER.index("static DWORD native_finalize_chocolatey") : INSTALLER.index("DWORD WINAPI net48_install")]
        self.assertIn('L"%ProgramData%\\\\tools\\\\chocolateyInstall"', native)
        self.assertIn('L"%ProgramData%\\\\chocolatey"', native)
        self.assertIn("move_chocolatey_tree(raw_root, canonical_root, MOVEFILE_COPY_ALLOWED | MOVEFILE_WRITE_THROUGH)", native)
        self.assertIn("install_chocolatey_bin(canonical_root, FALSE)", native)
        self.assertIn("set_chocolatey_environment(canonical_root, canonical_bin)", native)
        for token in ("FILE_ATTRIBUTE_REPARSE_POINT", "ERROR_ALREADY_EXISTS", "CopyFileW(source_choco, part, !resume)", "MOVEFILE_REPLACE_EXISTING", "RegQueryValueExW", "SetEnvironmentVariableW"):
            self.assertIn(token, COMMON)
        recovery = (ROOT / "compat/container-finalizer.c").read_text()
        self.assertIn("install_chocolatey_bin(stage_root, TRUE)", recovery)
        self.assertIn("stage=stage-resume", recovery)
        self.assertNotIn("redirects", native)
        self.assertIn('L"Software\\\\Wine\\\\DllOverrides"', native)
        self.assertIn('L"mscoree"', native)
        self.assertIn('L"native"', native)
        self.assertIn('L"wusa.exe"', native)
        self.assertIn("run_argv(NULL, mscoree_arguments, 0, 180000)", native)
        self.assertIn("result != ERROR_TIMEOUT", native)
        self.assertIn('L"%SystemRoot%\\\\System32\\\\mscoree.dll"', native)
        self.assertIn('L"%SystemRoot%\\\\SysWOW64\\\\mscoree.dll"', native)
        self.assertLess(native.index("mscoree-install-start"), native.index('L"mscoree"'))
        self.assertLess(
            native.index("mscoree-install-complete"),
            native.index('L"Software\\\\Wine\\\\DllOverrides"'),
        )
        self.assertNotIn("configure_container_pwsh_policy", INSTALLER)
        self.assertNotIn('L"rpcrt4"', INSTALLER)

    def test_installer_reports_container_builder_stage_progress(self):
        stages = (
            "bootstrap-start",
            "net48-start",
            "chocolatey-payload-start",
            "cdrive-start",
            "prerequisites-complete",
            "native-finalizer-start",
            "mscoree-install-start",
            "mscoree-install-complete",
            "native-finalizer-complete",
            "powershell-start",
            "finalizer-start",
            "finalizer-complete",
            "canonical-check",
            "bootstrap-complete",
        )
        self.assertIn("static inline void log_stage", COMMON)
        for stage in stages:
            self.assertIn(f'log_stage("[cfw] stage={stage}\\n")', INSTALLER)

    def test_upstream_webview_disables_direct_composition(self):
        self.assertIn("--disable_direct_composition=1", INSTALLER)

    def test_success_requires_canonical_chocolatey(self):
        self.assertIn(
            'L"%ProgramData%\\\\chocolatey\\\\bin\\\\choco.exe"',
            INSTALLER,
        )
        self.assertIn("validate_canonical_choco", INSTALLER)

    def test_installer_directory_is_derived_without_trailing_separator(self):
        self.assertIn("module_name = wcsrchr(p.pathW, L'\\\\')", MAIN)
        self.assertIn("*module_name = 0", MAIN)
        self.assertIn("module_path_length >= MAX_PATH", MAIN)
        self.assertNotIn("filenameW", INSTALLER)
        self.assertNotIn("wcslen(p.pathW) - 26", MAIN)

    def test_powershell_finalizer_command_has_argv0_and_explicit_file_mode(self):
        pscore = INSTALLER[INSTALLER.index("DWORD WINAPI pscore_install") : INSTALLER.index("DWORD WINAPI cdrive_install")]
        self.assertIn('expand_path(L"%ProgramFiles%', pscore)
        self.assertIn('L"-NoLogo", L"-NonInteractive", L"-File", script, p->pathW', pscore)
        self.assertIn('const wchar_t *finalize[] = {pwsh,', pscore)
        self.assertIn("run_argv(pwsh, finalize, 0, INFINITE)", pscore)
        self.assertIn("build_command(command, sizeof(command)", INSTALLER)
        self.assertNotIn("wcscat(", INSTALLER)
        self.assertNotIn("swprintf(", pscore)
        self.assertIn('_wcsicmp(argv[i], L"/s")', MAIN)
        self.assertIn('_wcsicmp(argv[i], L"/q")', MAIN)
        script = (ROOT / "choc_install.ps1").read_text(encoding="utf-8")
        self.assertIn("[cfw] stage=finalizer-script-entry", script[:200])

    def test_powershell_finalizer_fails_on_nonterminating_errors(self):
        script = (ROOT / "choc_install.ps1").read_text(encoding="utf-8")
        self.assertIn("$ErrorActionPreference = 'Stop'", script[:500])

    def test_saved_cache_discovers_one_versioned_powershell_msi(self):
        script = (ROOT / "choc_install.ps1").read_text(encoding="utf-8")
        self.assertNotIn("PowerShell-7.5.5-win-x64.msi", script)
        self.assertIn("-Filter 'PowerShell-*-win-x64.msi'", script)
        self.assertIn("$powerShellCacheFiles.Count -ne 1", script)
        self.assertIn("$powerShellCacheFiles[0].Name", script)

    def test_release_workflow_validates_contracts_on_fix_branches(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("- 'fix/**'", workflow)
        self.assertIn("python3 -m unittest discover -s tests", workflow)
        self.assertIn("default: '0.5a.765'", workflow)
        self.assertIn('ver="0.5a.765"', workflow)
        self.assertNotIn("0.5c.755", workflow)
        self.assertIn('ver="${ver%%-*}"', workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("--prerelease", workflow)
        self.assertIn("compile-installer.log", workflow)
        self.assertIn("Installer compile failed", workflow)
        self.assertIn('rc="${PIPESTATUS[0]}"', workflow)

    def test_canonical_upstream_changes_are_monitored(self):
        workflow_path = ROOT / ".github/workflows/upstream-watch.yml"
        self.assertTrue(workflow_path.is_file())
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("PietJankbal/Chocolatey-for-wine.git", workflow)
        self.assertIn("HEAD..canonical-upstream/main", workflow)
        self.assertNotIn("git merge", workflow)


if __name__ == "__main__":
    unittest.main()
