/* Installs PowerShell Core, net48, chocolatey and ConEmu
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301, USA
 *
 * Compile with: bash compat/compile-installer.sh OUTPUT.exe
 */
 
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include <winternl.h>
#include <shlobj.h>
#include <knownfolders.h>
#include "compat/installer-inputs.h"
#include "compat/native-common.h"

struct paths {
    wchar_t pathW[MAX_PATH];
    wchar_t setupcache[MAX_PATH];
    wchar_t sevenzippath[MAX_PATH];
    wchar_t cache_dir[MAX_PATH];
    BOOL save_cache;
    BOOL quiet;
};

struct download_input { const wchar_t *url; const wchar_t *filename; };
static const struct download_input auxiliary_downloads[] = { CFW_AUXILIARY_DOWNLOADS };

static BOOL install_succeeded(DWORD exit_code) {
    return exit_code == ERROR_SUCCESS || exit_code == ERROR_SUCCESS_REBOOT_REQUIRED;
}

static DWORD run_process_bounded(
    LPCWSTR application,
    LPWSTR command_line,
    DWORD creation_flags,
    DWORD timeout_ms
) {
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    DWORD exit_code = ERROR_GEN_FAILURE;
    DWORD wait_result;

    startup.cb = sizeof(startup);
    if(!CreateProcessW(application, command_line, 0, 0, 0, creation_flags, 0, 0, &startup, &process))
        return GetLastError();
    wait_result = WaitForSingleObject(process.hProcess, timeout_ms);
    if(wait_result == WAIT_OBJECT_0) {
        if(!GetExitCodeProcess(process.hProcess, &exit_code))
            exit_code = GetLastError();
    }
    else if(wait_result == WAIT_TIMEOUT && timeout_ms != INFINITE) {
        if(!TerminateProcess(process.hProcess, ERROR_TIMEOUT))
            exit_code = GetLastError();
        else {
            WaitForSingleObject(process.hProcess, 10000);
            exit_code = ERROR_TIMEOUT;
        }
    }
    else
        exit_code = timeout_ms == INFINITE ? ERROR_GEN_FAILURE : GetLastError();
    CloseHandle(process.hProcess);
    CloseHandle(process.hThread);
    return exit_code;
}

static DWORD run_process(LPCWSTR application, LPWSTR command_line, DWORD creation_flags) {
    return run_process_bounded(application, command_line, creation_flags, INFINITE);
}

static DWORD run_argv(LPCWSTR application, const wchar_t *const *arguments, DWORD flags, DWORD timeout_ms) {
    wchar_t command[8192];
    if(!build_command(command, sizeof(command) / sizeof(command[0]), arguments))
        return ERROR_INSUFFICIENT_BUFFER;
    return timeout_ms == INFINITE ? run_process(application, command, flags)
                                 : run_process_bounded(application, command, flags, timeout_ms);
}

static DWORD download_file(LPCWSTR source, LPCWSTR destination) {
    HRESULT result;
    if(_wgetenv(L"CFW_OFFLINE")) return ERROR_FILE_NOT_FOUND;
    result = URLDownloadToFileW(NULL, source, destination, 0, NULL);
    return SUCCEEDED(result) ? ERROR_SUCCESS : (DWORD)result;
}

static DWORD cached_payload(struct paths *p, const wchar_t *url, const wchar_t *filename, wchar_t *path) {
    DWORD attributes, result;
    if(!join_path(path, MAX_PATH, p->cache_dir, filename)) return ERROR_INSUFFICIENT_BUFFER;
    attributes = GetFileAttributesW(path);
    if(attributes != INVALID_FILE_ATTRIBUTES) return require_file(path);
    result = GetLastError();
    if(result != ERROR_FILE_NOT_FOUND && result != ERROR_PATH_NOT_FOUND) return result;
    if(!join_path(path, MAX_PATH, p->setupcache, filename)) return ERROR_INSUFFICIENT_BUFFER;
    return download_file(url, path);
}

static DWORD validate_canonical_choco(void) {
    wchar_t path[MAX_PATH] = L"";
    DWORD result = expand_path(L"%ProgramData%\\chocolatey\\bin\\choco.exe", path, MAX_PATH);
    return result == ERROR_SUCCESS ? require_file(path) : result;
}

static DWORD native_finalize_chocolatey(struct paths *p) {
    wchar_t raw_root[MAX_PATH] = L"";
    wchar_t canonical_root[MAX_PATH] = L"";
    wchar_t canonical_bin[MAX_PATH] = L"";
    wchar_t mscoree_msu[MAX_PATH] = L"";
    wchar_t system_mscoree[MAX_PATH] = L"";
    wchar_t syswow64_mscoree[MAX_PATH] = L"";
    DWORD expanded, result, attributes;
    HKEY dll_overrides;

    log_stage("[cfw] stage=native-finalizer-start\n");

    /*
     * The PowerShell finalizer installs the .NET shared shim update through
     * wusa before selecting native mscoree. Container builds bypass that
     * script, so perform the same prerequisite here and prove both loader
     * architectures exist before publishing the override.
     */
    if(!join_path(mscoree_msu, MAX_PATH, p->cache_dir, CFW_MSCOREE_FILENAME))
        return ERROR_INSUFFICIENT_BUFFER;
    attributes = GetFileAttributesW(mscoree_msu);
    if(attributes == INVALID_FILE_ATTRIBUTES) return GetLastError();
    if((attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
        return ERROR_INVALID_DATA;
    const wchar_t *mscoree_arguments[] = {L"wusa.exe", mscoree_msu, L"/quiet", L"/norestart", NULL};
    log_stage("[cfw] stage=mscoree-install-start\n");
    result = run_argv(NULL, mscoree_arguments, 0, 180000);
    if(!install_succeeded(result) && result != ERROR_TIMEOUT) return result;
    expanded = ExpandEnvironmentStringsW(
        L"%SystemRoot%\\System32\\mscoree.dll",
        system_mscoree,
        MAX_PATH
    );
    if(expanded == 0) return GetLastError();
    if(expanded > MAX_PATH) return ERROR_INSUFFICIENT_BUFFER;
    expanded = ExpandEnvironmentStringsW(
        L"%SystemRoot%\\SysWOW64\\mscoree.dll",
        syswow64_mscoree,
        MAX_PATH
    );
    if(expanded == 0) return GetLastError();
    if(expanded > MAX_PATH) return ERROR_INSUFFICIENT_BUFFER;
    attributes = GetFileAttributesW(system_mscoree);
    if(attributes == INVALID_FILE_ATTRIBUTES) return GetLastError();
    if((attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
        return ERROR_INVALID_DATA;
    attributes = GetFileAttributesW(syswow64_mscoree);
    if(attributes == INVALID_FILE_ATTRIBUTES) return GetLastError();
    if((attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
        return ERROR_INVALID_DATA;
    log_stage("[cfw] stage=mscoree-install-complete\n");

    result = expand_path(L"%ProgramData%\\tools\\chocolateyInstall", raw_root, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    result = expand_path(L"%ProgramData%\\chocolatey", canonical_root, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    result = move_chocolatey_tree(raw_root, canonical_root, MOVEFILE_COPY_ALLOWED | MOVEFILE_WRITE_THROUGH);
    if(result != ERROR_SUCCESS) return result;
    result = install_chocolatey_bin(canonical_root, FALSE);
    if(result != ERROR_SUCCESS) return result;
    if(!append_wide(canonical_bin, MAX_PATH, canonical_root) || !append_wide(canonical_bin, MAX_PATH, L"\\bin"))
        return ERROR_INSUFFICIENT_BUFFER;
    result = set_chocolatey_environment(canonical_root, canonical_bin);
    if(result != ERROR_SUCCESS) return result;

    /*
     * The interactive PowerShell finalizer normally installs this override.
     * Container builds intentionally bypass that script, but Microsoft .NET
     * still requires Wine to load the native mscoree.dll. Without it, managed
     * Chocolatey entry points fall back to Wine Mono and exit before Main.
     */
    result = RegCreateKeyExW(
        HKEY_CURRENT_USER,
        L"Software\\Wine\\DllOverrides",
        0,
        NULL,
        REG_OPTION_NON_VOLATILE,
        KEY_SET_VALUE,
        NULL,
        &dll_overrides,
        NULL
    );
    if(result != ERROR_SUCCESS) return result;
    {
        const WCHAR native_override[] = L"native";
        result = RegSetValueExW(
            dll_overrides,
            L"mscoree",
            0,
            REG_SZ,
            (BYTE*)native_override,
            sizeof(native_override)
        );
    }
    RegCloseKey(dll_overrides);
    if(result != ERROR_SUCCESS) return result;

    log_stage("[cfw] stage=native-finalizer-complete\n");
    return ERROR_SUCCESS;
}
 
DWORD WINAPI net48_install(void *ptr) {
    struct paths *p = (struct paths*)ptr;
    wchar_t directory[MAX_PATH], msi[MAX_PATH], archive[MAX_PATH], output[MAX_PATH + 3] = L"-o";
    DWORD result;
    log_stage("[cfw] stage=net48-start\n");
    if(!join_path(directory, MAX_PATH, p->cache_dir, CFW_DOTNET_INSTALL_DIRECTORY) ||
       !join_path(msi, MAX_PATH, directory, CFW_DOTNET_MSI_FILENAME)) return ERROR_INSUFFICIENT_BUFFER;
    if(GetFileAttributesW(msi) == INVALID_FILE_ATTRIBUTES) {
        result = cached_payload(p, CFW_DOTNET_URL, CFW_DOTNET_FILENAME, archive);
        if(result != ERROR_SUCCESS) return result;
        if(!join_path(directory, MAX_PATH, p->setupcache, CFW_DOTNET_INSTALL_DIRECTORY) ||
           !join_path(msi, MAX_PATH, directory, CFW_DOTNET_MSI_FILENAME) ||
           !append_wide(output, MAX_PATH + 3, directory)) return ERROR_INSUFFICIENT_BUFFER;
        const wchar_t *extract[] = {p->sevenzippath, L"x", L"-x!*.cab", L"-x!netfx_c*", L"-x!netfx_e*", L"-x!NetFx4*", L"-ms190M", archive, output, NULL};
        result = run_argv(p->sevenzippath, extract, 0, INFINITE);
        if(!install_succeeded(result)) return result;
    }
    result = require_file(msi);
    if(result != ERROR_SUCCESS) return result;
    const wchar_t *install[] = {L"msiexec.exe", L"/i", msi, L"MSIFASTINSTALL=2", L"DISABLEROLLBACK=1", L"/QN", NULL};
    result = run_argv(NULL, install, REALTIME_PRIORITY_CLASS, INFINITE);
    if(install_succeeded(result)) log_stage("[cfw] stage=net48-complete\n");
    return install_succeeded(result) ? ERROR_SUCCESS : result;
}

DWORD WINAPI chocolatey_install(void *ptr) {
    struct paths *p = (struct paths*)ptr;
    wchar_t payload[MAX_PATH], destination[MAX_PATH + 3] = L"-o", program_data[MAX_PATH];
    DWORD result;
    log_stage("[cfw] stage=chocolatey-payload-start\n");
    result = expand_path(L"%ProgramData%", program_data, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    if(!append_wide(destination, MAX_PATH + 3, program_data)) return ERROR_INSUFFICIENT_BUFFER;
    result = cached_payload(p, CFW_CHOCOLATEY_URL, CFW_CHOCOLATEY_FILENAME, payload);
    if(result != ERROR_SUCCESS) return result;
    const wchar_t *extract[] = {p->sevenzippath, L"x", payload, L"tools/chocolateyInstall/*", destination, NULL};
    result = run_argv(p->sevenzippath, extract, 0, INFINITE);
    if(install_succeeded(result)) log_stage("[cfw] stage=chocolatey-payload-complete\n");
    return install_succeeded(result) ? ERROR_SUCCESS : result;
}

DWORD WINAPI pscore_install(void *ptr) {
    struct paths *p = (struct paths*)ptr;
    wchar_t payload[MAX_PATH], pwsh[MAX_PATH], script[MAX_PATH];
    DWORD result;
    HKEY key;
    log_stage("[cfw] stage=powershell-start\n");
    result = expand_path(L"%ProgramFiles%\\PowerShell\\7\\pwsh.exe", pwsh, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    result = cached_payload(p, CFW_POWERSHELL_URL, CFW_POWERSHELL_FILENAME, payload);
    if(result != ERROR_SUCCESS) return result;
    const wchar_t *install[] = {L"msiexec.exe", L"/i", payload, L"DISABLE_TELEMETRY=1", L"ENABLE_PSREMOTING=1", L"REGISTER_MANIFEST=1", L"MSIFASTINSTALL=2", L"DISABLEROLLBACK=1", L"MSIDISABLEEEUI=1", L"/QN", NULL};
    result = run_argv(NULL, install, REALTIME_PRIORITY_CLASS, INFINITE);
    if(!install_succeeded(result)) return result;
    for(size_t i = 0; i < sizeof(auxiliary_downloads) / sizeof(auxiliary_downloads[0]); i++) {
        result = cached_payload(p, auxiliary_downloads[i].url, auxiliary_downloads[i].filename, payload);
        if(result != ERROR_SUCCESS) return result;
    }
    const WCHAR webview[] = L"--disable-dwm-composition --disable_direct_composition=1 --disable-gpu-sandbox --disable-d3d11 --disable-sandbox --use-angle=d3d9 --disable-gpu";
    result = RegCreateKeyExW(HKEY_CURRENT_USER, L"Environment", 0, NULL, REG_OPTION_NON_VOLATILE, KEY_SET_VALUE, NULL, &key, NULL);
    if(result != ERROR_SUCCESS) return result;
    result = RegSetValueExW(key, L"PS7", 0, REG_SZ, (const BYTE*)pwsh, (DWORD)((wcslen(pwsh) + 1) * sizeof(WCHAR)));
    if(result == ERROR_SUCCESS)
        result = RegSetValueExW(key, L"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", 0, REG_SZ, (const BYTE*)webview, sizeof(webview));
    RegCloseKey(key);
    if(result != ERROR_SUCCESS) return result;
    if(_wgetenv(L"CFW_CONTAINER_BUILDER")) {
        if(!_wgetenv(L"CFW_OFFLINE")) return ERROR_ACCESS_DENIED;
        return native_finalize_chocolatey(p);
    }
    if(!join_path(script, MAX_PATH, p->pathW, L"choc_install.ps1")) return ERROR_INSUFFICIENT_BUFFER;
    const wchar_t *finalize[] = {pwsh, L"-NoLogo", L"-NonInteractive", L"-File", script, p->pathW, NULL, NULL, NULL};
    size_t index = 6;
    if(p->save_cache) finalize[index++] = L"/s";
    if(p->quiet) finalize[index++] = L"/q";
    finalize[index] = NULL;
    log_stage("[cfw] stage=finalizer-start\n");
    result = run_argv(pwsh, finalize, 0, INFINITE);
    if(install_succeeded(result)) log_stage("[cfw] stage=finalizer-complete\n");
    return install_succeeded(result) ? ERROR_SUCCESS : result;
}

DWORD WINAPI cdrive_install(void *ptr) {
    struct paths *p = (struct paths*)ptr;
    wchar_t archive[MAX_PATH];
    DWORD result;
    log_stage("[cfw] stage=cdrive-start\n");
    if(!join_path(archive, MAX_PATH, p->pathW, L"c_drive.7z")) return ERROR_INSUFFICIENT_BUFFER;
    const wchar_t *extract[] = {p->sevenzippath, L"x", L"-spf", L"-aot", archive, NULL};
    result = run_argv(p->sevenzippath, extract, 0, INFINITE);
    if(install_succeeded(result)) log_stage("[cfw] stage=cdrive-complete\n");
    return install_succeeded(result) ? ERROR_SUCCESS : result;
}

int mainCRTStartup(void) {
    wchar_t **argv, *module_name, *documents = NULL;
    int argc, i;
    DWORD exit_code = ERROR_SUCCESS, thread_exit = ERROR_SUCCESS, module_path_length;
    HKEY key;
    HANDLE hThread[3] = {0};
    struct paths p = {0};
    log_stage("[cfw] stage=bootstrap-start\n");
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if(!argv) ExitProcess(GetLastError());
    exit_code = RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Wine\\DllOverrides", 0, NULL, REG_OPTION_NON_VOLATILE, KEY_SET_VALUE, NULL, &key, NULL);
    if(exit_code != ERROR_SUCCESS) ExitProcess(exit_code);
    const WCHAR disabled[] = L"";
    exit_code = RegSetValueExW(key, L"mscorsvc", 0, REG_SZ, (const BYTE*)disabled, sizeof(disabled));
    RegCloseKey(key);
    if(exit_code != ERROR_SUCCESS) ExitProcess(exit_code);
    const wchar_t *cache = _wgetenv(L"CFW_CACHE");
    if(cache) {
        if(!join_path(p.cache_dir, MAX_PATH, cache, L"choc_install_files")) ExitProcess(ERROR_INSUFFICIENT_BUFFER);
    } else {
        HRESULT hr = SHGetKnownFolderPath(&FOLDERID_Documents, 0, 0, &documents);
        if(FAILED(hr)) ExitProcess((DWORD)hr);
        BOOL joined = join_path(p.cache_dir, MAX_PATH, documents, L"Chocolatey-for-wine\\choc_install_files");
        CoTaskMemFree(documents);
        if(!joined) ExitProcess(ERROR_INSUFFICIENT_BUFFER);
    }
    exit_code = expand_path(L"%SystemRoot%\\Microsoft.NET\\Framework64\\v4.0.30319\\SetupCache", p.setupcache, MAX_PATH);
    if(exit_code != ERROR_SUCCESS) ExitProcess(exit_code);
    exit_code = (DWORD)SHCreateDirectoryExW(NULL, p.setupcache, NULL);
    if(exit_code != ERROR_SUCCESS && exit_code != ERROR_ALREADY_EXISTS && exit_code != ERROR_FILE_EXISTS) ExitProcess(exit_code);
    exit_code = require_directory(p.setupcache);
    if(exit_code != ERROR_SUCCESS) ExitProcess(exit_code);
    module_path_length = GetModuleFileNameW(NULL, p.pathW, MAX_PATH);
    if(module_path_length == 0) ExitProcess(GetLastError());
    if(module_path_length >= MAX_PATH) ExitProcess(ERROR_INSUFFICIENT_BUFFER);
    module_name = wcsrchr(p.pathW, L'\\');
    if(!module_name) ExitProcess(ERROR_BAD_PATHNAME);
    *module_name = 0;
    if(!join_path(p.sevenzippath, MAX_PATH, p.pathW, L"7z.exe")) ExitProcess(ERROR_INSUFFICIENT_BUFFER);
    for(i = 1; i < argc; i++) {
        if(_wcsicmp(argv[i], L"/s") == 0) p.save_cache = TRUE;
        else if(_wcsicmp(argv[i], L"/q") == 0) p.quiet = TRUE;
    }
    LocalFree(argv);
    /* Prerequisites may run concurrently, but finalization must wait for all. */
    hThread[0] = CreateThread(NULL, 0, net48_install, &p, 0, 0);
    hThread[1] = CreateThread(NULL, 0, chocolatey_install, &p, 0, 0);
    hThread[2] = CreateThread(NULL, 0, cdrive_install, &p, 0, 0);
    for(i=0; i<3; i++) {
        if(!hThread[i]) {
            exit_code = GetLastError();
            for(int j=0; j<3; j++) {
                if(hThread[j]) { WaitForSingleObject(hThread[j], INFINITE); CloseHandle(hThread[j]); }
            }
            ExitProcess(exit_code);
        }
    }
    SetThreadPriority(hThread[0], THREAD_PRIORITY_TIME_CRITICAL);
    if(WaitForMultipleObjects(3, hThread, TRUE, INFINITE) != WAIT_OBJECT_0) exit_code = ERROR_GEN_FAILURE;
    for(i=0; i<3; i++) {
        if(!GetExitCodeThread(hThread[i], &thread_exit) && exit_code == ERROR_SUCCESS) exit_code = GetLastError();
        else if(thread_exit != ERROR_SUCCESS && exit_code == ERROR_SUCCESS) exit_code = thread_exit;
        CloseHandle(hThread[i]);
    }
    if(exit_code == ERROR_SUCCESS) { log_stage("[cfw] stage=prerequisites-complete\n"); exit_code = pscore_install(&p); }
    if(exit_code == ERROR_SUCCESS) { log_stage("[cfw] stage=canonical-check\n"); exit_code = validate_canonical_choco(); }
    if(exit_code == ERROR_SUCCESS) log_stage("[cfw] stage=bootstrap-complete\n");
    ExitProcess(exit_code);
}
