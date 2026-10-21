/*
 * Deterministic Chocolatey finalizer for container/orchestrated Wine prefixes.
 *
 * This program does not install PowerShell and does not install a Windows
 * PowerShell wrapper. A verified pwsh engine and Synchro wrapper layer must
 * already exist. The caller must also stage the Chocolatey nupkg payload at:
 *
 *   %ProgramData%\tools\chocolateyInstall
 *
 * Required environment variables:
 *
 *   CFW_CONTAINER_BUILDER=1
 *   CFW_OFFLINE=1
 *   CFW_EXTERNAL_POWERSHELL=1
 *
 * Compile example:
 *
 *   x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -municode \
 *     compat/container-finalizer.c -ladvapi32 -o cfw-container-finalizer.exe
 */

#include <windows.h>
#include <wchar.h>
#include <string.h>

#include "native-common.h"

static DWORD validate_external_powershell(void) {
    wchar_t pwsh[MAX_PATH] = L"";
    wchar_t wrapper64[MAX_PATH] = L"";
    wchar_t wrapper32[MAX_PATH] = L"";
    DWORD result;

    if(!_wgetenv(L"CFW_CONTAINER_BUILDER") ||
       !_wgetenv(L"CFW_OFFLINE") ||
       !_wgetenv(L"CFW_EXTERNAL_POWERSHELL"))
        return ERROR_ACCESS_DENIED;

    log_stage("[cfw] stage=external-powershell-check-start\n");
    result = expand_path(L"%ProgramFiles%\\PowerShell\\7\\pwsh.exe", pwsh, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    result = expand_path(L"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", wrapper64, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    result = expand_path(L"%SystemRoot%\\SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe", wrapper32, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;

    result = require_file(pwsh);
    if(result != ERROR_SUCCESS) return result;
    result = require_file(wrapper64);
    if(result != ERROR_SUCCESS) return result;
    result = require_file(wrapper32);
    if(result != ERROR_SUCCESS) return result;

    log_stage("[cfw] stage=external-powershell-check-complete\n");
    return ERROR_SUCCESS;
}

static DWORD finalize_chocolatey(void) {
    wchar_t raw_root[MAX_PATH] = L"";
    wchar_t stage_root[MAX_PATH] = L"";
    wchar_t canonical_root[MAX_PATH] = L"";
    wchar_t canonical_bin[MAX_PATH] = L"";
    wchar_t canonical_choco[MAX_PATH] = L"";
    DWORD result;
    DWORD canonical_attributes;
    DWORD stage_attributes;

    result = expand_path(L"%ProgramData%\\tools\\chocolateyInstall", raw_root, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    result = expand_path(L"%ProgramData%\\chocolatey.cfw-stage", stage_root, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;
    result = expand_path(L"%ProgramData%\\chocolatey", canonical_root, MAX_PATH);
    if(result != ERROR_SUCCESS) return result;

    if(!append_wide(canonical_bin, MAX_PATH, canonical_root) ||
       !append_wide(canonical_bin, MAX_PATH, L"\\bin") ||
       !append_wide(canonical_choco, MAX_PATH, canonical_bin) ||
       !append_wide(canonical_choco, MAX_PATH, L"\\choco.exe"))
        return ERROR_INSUFFICIENT_BUFFER;

    canonical_attributes = GetFileAttributesW(canonical_root);
    if(canonical_attributes != INVALID_FILE_ATTRIBUTES) {
        if(!(canonical_attributes & FILE_ATTRIBUTE_DIRECTORY) ||
           (canonical_attributes & FILE_ATTRIBUTE_REPARSE_POINT))
            return ERROR_INVALID_DATA;
        result = require_file(canonical_choco);
        if(result != ERROR_SUCCESS)
            return ERROR_INVALID_DATA;
        log_stage("[cfw] stage=canonical-reconcile\n");
        return set_chocolatey_environment(canonical_root, canonical_bin);
    }
    result = GetLastError();
    if(result != ERROR_FILE_NOT_FOUND && result != ERROR_PATH_NOT_FOUND)
        return result;

    stage_attributes = GetFileAttributesW(stage_root);
    if(stage_attributes == INVALID_FILE_ATTRIBUTES) {
        result = GetLastError();
        if(result != ERROR_FILE_NOT_FOUND && result != ERROR_PATH_NOT_FOUND)
            return result;
        log_stage("[cfw] stage=stage-create\n");
        result = move_chocolatey_tree(raw_root, stage_root, MOVEFILE_COPY_ALLOWED | MOVEFILE_WRITE_THROUGH);
        if(result != ERROR_SUCCESS) return result;
    }
    else if(!(stage_attributes & FILE_ATTRIBUTE_DIRECTORY) ||
            (stage_attributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
        return ERROR_INVALID_DATA;
    }
    else {
        log_stage("[cfw] stage=stage-resume\n");
    }

    result = install_chocolatey_bin(stage_root, TRUE);
    if(result != ERROR_SUCCESS) return result;

    log_stage("[cfw] stage=canonical-promote\n");
    result = move_chocolatey_tree(stage_root, canonical_root, MOVEFILE_WRITE_THROUGH);
    if(result != ERROR_SUCCESS) return result;
    result = require_file(canonical_choco);
    if(result != ERROR_SUCCESS) return result;

    result = set_chocolatey_environment(canonical_root, canonical_bin);
    if(result != ERROR_SUCCESS) return result;
    log_stage("[cfw] stage=container-finalizer-complete\n");
    return ERROR_SUCCESS;
}

int wmain(void) {
    DWORD result;

    log_stage("[cfw] stage=container-finalizer-start\n");
    result = validate_external_powershell();
    if(result == ERROR_SUCCESS)
        result = finalize_chocolatey();
    return (int)result;
}
