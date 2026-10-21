/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Shared routines extracted from CFW's LGPL-licensed native installer.
 */
#ifndef CFW_NATIVE_COMMON_H
#define CFW_NATIVE_COMMON_H
#include <windows.h>
#include <wchar.h>
#include <string.h>
#define PATH_BUFFER 8192

static inline void log_stage(const char *message) {
    DWORD written = 0;
    HANDLE output = GetStdHandle(STD_OUTPUT_HANDLE);
    if(output != NULL && output != INVALID_HANDLE_VALUE)
        WriteFile(output, message, (DWORD)strlen(message), &written, NULL);
}

static inline BOOL append_wide(wchar_t *destination, size_t capacity, const wchar_t *source) {
    size_t destination_length = wcslen(destination);
    size_t source_length = wcslen(source);
    size_t i;

    if(destination_length >= capacity || source_length >= capacity - destination_length)
        return FALSE;
    for(i = 0; i <= source_length; i++)
        destination[destination_length + i] = source[i];
    return TRUE;
}

/* Quote an argument according to the Windows C runtime's backslash rules. */
static inline BOOL append_argument(wchar_t *command, size_t capacity, const wchar_t *argument) {
    size_t used, slashes;
    const wchar_t *cursor = argument;
#define PUT(ch) do { if(used + 1 >= capacity) { command[0] = 0; return FALSE; } command[used++] = (ch); } while(0)
    if(!capacity) return FALSE;
    used = wcslen(command);
    if(used) PUT(L' ');
    /* Wine's msiexec parser preserves quotes around switches and properties. */
    if(*argument && !wcspbrk(argument, L" \t\"")) {
        while(*cursor) PUT(*cursor++);
    }
    else {
        PUT(L'"');
        for(;;) {
            slashes = 0;
            while(*cursor == L'\\') { slashes++; cursor++; }
            if(*cursor == L'"' || !*cursor) slashes *= 2;
            while(slashes--) PUT(L'\\');
            if(!*cursor) break;
            if(*cursor == L'"') PUT(L'\\');
            PUT(*cursor++);
        }
        PUT(L'"');
    }
    command[used] = 0;
#undef PUT
    return TRUE;
}

static inline BOOL build_command(wchar_t *command, size_t capacity, const wchar_t *const *arguments) {
    if(!capacity || !arguments || !arguments[0]) return FALSE;
    command[0] = 0;
    for(; *arguments; arguments++)
        if(!append_argument(command, capacity, *arguments)) return FALSE;
    return TRUE;
}

static inline BOOL join_path(wchar_t *destination, size_t capacity, const wchar_t *directory, const wchar_t *name) {
    size_t length = wcslen(directory);
    if(!capacity || !length) return FALSE;
    destination[0] = 0;
    return append_wide(destination, capacity, directory) &&
        ((directory[length - 1] == L'\\' || directory[length - 1] == L'/') || append_wide(destination, capacity, L"\\")) &&
        append_wide(destination, capacity, name);
}

static inline DWORD expand_path(const wchar_t *source, wchar_t *destination, DWORD capacity) {
    DWORD expanded = ExpandEnvironmentStringsW(source, destination, capacity);
    if(expanded == 0)
        return GetLastError();
    if(expanded > capacity)
        return ERROR_INSUFFICIENT_BUFFER;
    return ERROR_SUCCESS;
}

static inline DWORD require_directory(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    if(attributes == INVALID_FILE_ATTRIBUTES)
        return GetLastError();
    if(!(attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
        return ERROR_INVALID_DATA;
    return ERROR_SUCCESS;
}

static inline DWORD require_file(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    if(attributes == INVALID_FILE_ATTRIBUTES)
        return GetLastError();
    if((attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
        return ERROR_INVALID_DATA;
    return ERROR_SUCCESS;
}

static inline BOOL path_has_component(const wchar_t *path, const wchar_t *component) {
    const wchar_t *cursor = path;
    size_t component_length = wcslen(component);

    while(cursor && *cursor) {
        const wchar_t *end = wcschr(cursor, L';');
        size_t length = end ? (size_t)(end - cursor) : wcslen(cursor);

        while(length && (*cursor == L' ' || *cursor == L'\"')) {
            cursor++;
            length--;
        }
        while(length && (cursor[length - 1] == L' ' || cursor[length - 1] == L'\"' || cursor[length - 1] == L'\\'))
            length--;

        if(length == component_length && _wcsnicmp(cursor, component, length) == 0)
            return TRUE;
        cursor = end ? end + 1 : NULL;
    }
    return FALSE;
}

static inline DWORD set_chocolatey_environment(const wchar_t *canonical_root, const wchar_t *canonical_bin) {
    wchar_t machine_path[PATH_BUFFER] = L"";
    wchar_t process_path[PATH_BUFFER] = L"";
    DWORD path_bytes = sizeof(machine_path);
    DWORD path_type = 0;
    DWORD result;
    DWORD expanded;
    HKEY environment;

    result = RegCreateKeyExW(
        HKEY_LOCAL_MACHINE,
        L"SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment",
        0,
        NULL,
        REG_OPTION_NON_VOLATILE,
        KEY_QUERY_VALUE | KEY_SET_VALUE,
        NULL,
        &environment,
        NULL
    );
    if(result != ERROR_SUCCESS)
        return result;

    result = RegQueryValueExW(environment, L"Path", NULL, &path_type, (BYTE*)machine_path, &path_bytes);
    if(result == ERROR_FILE_NOT_FOUND) {
        machine_path[0] = 0;
        path_type = REG_EXPAND_SZ;
    }
    else if(result != ERROR_SUCCESS || (path_type != REG_SZ && path_type != REG_EXPAND_SZ)) {
        RegCloseKey(environment);
        return result == ERROR_SUCCESS ? ERROR_INVALID_DATA : result;
    }
    machine_path[(sizeof(machine_path) / sizeof(machine_path[0])) - 1] = 0;

    if(!path_has_component(machine_path, canonical_bin)) {
        if(machine_path[0] && machine_path[wcslen(machine_path) - 1] != L';' &&
           !append_wide(machine_path, sizeof(machine_path) / sizeof(machine_path[0]), L";")) {
            RegCloseKey(environment);
            return ERROR_INSUFFICIENT_BUFFER;
        }
        if(!append_wide(machine_path, sizeof(machine_path) / sizeof(machine_path[0]), canonical_bin)) {
            RegCloseKey(environment);
            return ERROR_INSUFFICIENT_BUFFER;
        }
    }

    result = RegSetValueExW(
        environment,
        L"ChocolateyInstall",
        0,
        REG_SZ,
        (const BYTE*)canonical_root,
        (DWORD)((wcslen(canonical_root) + 1) * sizeof(wchar_t))
    );
    if(result == ERROR_SUCCESS)
        result = RegSetValueExW(
            environment,
            L"Path",
            0,
            path_type,
            (const BYTE*)machine_path,
            (DWORD)((wcslen(machine_path) + 1) * sizeof(wchar_t))
        );
    RegCloseKey(environment);
    if(result != ERROR_SUCCESS)
        return result;

    if(!SetEnvironmentVariableW(L"ChocolateyInstall", canonical_root))
        return GetLastError();
    if(path_type == REG_EXPAND_SZ) {
        expanded = ExpandEnvironmentStringsW(
            machine_path,
            process_path,
            sizeof(process_path) / sizeof(process_path[0])
        );
        if(expanded == 0)
            return GetLastError();
        if(expanded > sizeof(process_path) / sizeof(process_path[0]))
            return ERROR_INSUFFICIENT_BUFFER;
    }
    else if(!append_wide(process_path, sizeof(process_path) / sizeof(process_path[0]), machine_path)) {
        return ERROR_INSUFFICIENT_BUFFER;
    }
    if(!SetEnvironmentVariableW(L"Path", process_path))
        return GetLastError();
    return ERROR_SUCCESS;
}

static inline DWORD move_chocolatey_tree(const wchar_t *source, const wchar_t *destination, DWORD flags) {
    wchar_t executable[MAX_PATH] = L"";
    DWORD result = require_directory(source);
    DWORD attributes;
    if(result != ERROR_SUCCESS) return result;
    if(!append_wide(executable, MAX_PATH, source) || !append_wide(executable, MAX_PATH, L"\\choco.exe"))
        return ERROR_INSUFFICIENT_BUFFER;
    result = require_file(executable);
    if(result != ERROR_SUCCESS) return result;
    attributes = GetFileAttributesW(destination);
    if(attributes != INVALID_FILE_ATTRIBUTES) return ERROR_ALREADY_EXISTS;
    result = GetLastError();
    if(result != ERROR_FILE_NOT_FOUND && result != ERROR_PATH_NOT_FOUND) return result;
    if(!MoveFileExW(source, destination, flags)) return GetLastError();
    return require_directory(destination);
}

static inline DWORD install_chocolatey_bin(const wchar_t *root, BOOL resume) {
    wchar_t source_choco[MAX_PATH] = L"", bin[MAX_PATH] = L"";
    wchar_t target[MAX_PATH] = L"", part[MAX_PATH] = L"";
    DWORD result = require_directory(root);
    if(result != ERROR_SUCCESS) return result;
    if(!append_wide(source_choco, MAX_PATH, root) || !append_wide(source_choco, MAX_PATH, L"\\choco.exe") ||
       !append_wide(bin, MAX_PATH, root) || !append_wide(bin, MAX_PATH, L"\\bin") ||
       !append_wide(target, MAX_PATH, bin) || !append_wide(target, MAX_PATH, L"\\choco.exe") ||
       !append_wide(part, MAX_PATH, target) || !append_wide(part, MAX_PATH, L".cfw-part"))
        return ERROR_INSUFFICIENT_BUFFER;
    result = require_file(source_choco);
    if(result != ERROR_SUCCESS) return result;
    if(!CreateDirectoryW(bin, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) return GetLastError();
    result = require_directory(bin);
    if(result != ERROR_SUCCESS) return result;
    if(resume) DeleteFileW(part);
    if(!CopyFileW(source_choco, part, !resume)) return GetLastError();
    result = require_file(part);
    if(result != ERROR_SUCCESS) { DeleteFileW(part); return result; }
    if(!MoveFileExW(part, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        result = GetLastError(); DeleteFileW(part); return result;
    }
    return require_file(target);
}

#endif
