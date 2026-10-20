"""Execute production native helpers against controlled Windows API outcomes."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

# Extract the actual implementations, rather than reimplementing their behavior.
COMMON = (ROOT / "compat/native-common.h").read_text()
HELPERS = COMMON[COMMON.index("static inline BOOL append_wide"):COMMON.index("\n#endif")]

HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include <wctype.h>
typedef uint32_t DWORD;
typedef unsigned char BYTE;
typedef int BOOL;
typedef int HKEY;
#define TRUE 1
#define FALSE 0
#define MAX_PATH 260
#define PATH_BUFFER 8192
#define ERROR_SUCCESS 0
#define ERROR_FILE_NOT_FOUND 2
#define ERROR_PATH_NOT_FOUND 3
#define ERROR_ACCESS_DENIED 5
#define ERROR_INVALID_DATA 13
#define ERROR_INSUFFICIENT_BUFFER 122
#define ERROR_ALREADY_EXISTS 183
#define INVALID_FILE_ATTRIBUTES 0xffffffffU
#define FILE_ATTRIBUTE_DIRECTORY 16
#define FILE_ATTRIBUTE_REPARSE_POINT 1024
#define MOVEFILE_REPLACE_EXISTING 1
#define MOVEFILE_WRITE_THROUGH 8
#define HKEY_LOCAL_MACHINE 1
#define REG_OPTION_NON_VOLATILE 0
#define KEY_QUERY_VALUE 1
#define KEY_SET_VALUE 2
#define REG_SZ 1
#define REG_EXPAND_SZ 2
static DWORD last_error;
static DWORD GetLastError(void) { return last_error; }
static int _wcsnicmp(const wchar_t *a, const wchar_t *b, size_t n) {
    for(size_t i = 0; i < n; i++) {
        if(towlower(a[i]) != towlower(b[i])) return 1;
    }
    return 0;
}
struct entry { wchar_t path[1024]; DWORD attributes; };
static struct entry files[30];
static int file_count, copies, moves, deletes, fail_copy, fail_move;
static int find(const wchar_t *path) {
    for(int i=0; i<file_count; i++) if(!wcscmp(files[i].path, path)) return i;
    return -1;
}
static void put(const wchar_t *path, DWORD attributes) {
    int i = find(path);
    if(i<0) i = file_count++;
    assert(file_count < 30);
    wcscpy(files[i].path, path); files[i].attributes = attributes;
}
static DWORD GetFileAttributesW(const wchar_t *path) {
    int i = find(path);
    if(i<0) { last_error = ERROR_FILE_NOT_FOUND; return INVALID_FILE_ATTRIBUTES; }
    return files[i].attributes;
}
static BOOL CreateDirectoryW(const wchar_t *path, void *security) {
    (void)security;
    if(find(path)>=0) { last_error = ERROR_ALREADY_EXISTS; return FALSE; }
    put(path, FILE_ATTRIBUTE_DIRECTORY); return TRUE;
}
static BOOL DeleteFileW(const wchar_t *path) {
    deletes++;
    int i = find(path);
    if(i<0) { last_error = ERROR_FILE_NOT_FOUND; return FALSE; }
    files[i] = files[--file_count]; return TRUE;
}
static BOOL CopyFileW(const wchar_t *source, const wchar_t *target, BOOL exclusive) {
    copies++;
    if(fail_copy) { last_error = ERROR_ACCESS_DENIED; return FALSE; }
    if(exclusive && find(target)>=0) { last_error = ERROR_ALREADY_EXISTS; return FALSE; }
    assert(find(source)>=0); put(target, 0); return TRUE;
}
static BOOL MoveFileExW(const wchar_t *source, const wchar_t *target, DWORD flags) {
    moves++;
    if(fail_move) { last_error = ERROR_ACCESS_DENIED; return FALSE; }
    int i = find(source), j = find(target);
    assert(i>=0);
    if(j>=0 && !(flags & MOVEFILE_REPLACE_EXISTING)) {
        last_error = ERROR_ALREADY_EXISTS; return FALSE;
    }
    DWORD attributes = files[i].attributes;
    DeleteFileW(source); put(target, attributes); return TRUE;
}
static wchar_t registry_path[PATH_BUFFER], installed_root[MAX_PATH], process_path[PATH_BUFFER];
static DWORD registry_type, written_type;
static int missing_path, closed_keys, fail_query, fail_write, expanded, fail_env;
static DWORD RegCreateKeyExW(HKEY base, const wchar_t *name, DWORD reserved,
    void *class_name, DWORD options, DWORD access, void *security, HKEY *out, void *disposition) {
    (void)base; (void)name; (void)reserved; (void)class_name; (void)options;
    (void)access; (void)security; (void)disposition; *out = 7; return ERROR_SUCCESS;
}
static DWORD RegQueryValueExW(HKEY key, const wchar_t *name, void *reserved,
    DWORD *type, BYTE *buffer, DWORD *bytes) {
    assert(key==7); (void)name; (void)reserved;
    if(fail_query) return ERROR_ACCESS_DENIED;
    if(missing_path) return ERROR_FILE_NOT_FOUND;
    assert(*bytes >= (wcslen(registry_path)+1)*sizeof(wchar_t));
    wcscpy((wchar_t*)buffer, registry_path); *type = registry_type; return ERROR_SUCCESS;
}
static DWORD RegSetValueExW(HKEY key, const wchar_t *name, DWORD reserved,
    DWORD type, const BYTE *value, DWORD bytes) {
    assert(key==7); (void)reserved; (void)bytes;
    if(fail_write) return ERROR_ACCESS_DENIED;
    if(!wcscmp(name, L"Path")) { wcscpy(registry_path, (const wchar_t*)value); written_type = type; }
    else wcscpy(installed_root, (const wchar_t*)value);
    return ERROR_SUCCESS;
}
static void RegCloseKey(HKEY key) { assert(key==7); closed_keys++; }
static DWORD ExpandEnvironmentStringsW(const wchar_t *source, wchar_t *target, DWORD capacity) {
    const wchar_t *value = source;
    expanded++;
    if(!wcscmp(source, L"%SystemRoot%\\system32;C:\\Chocolatey\\bin"))
        value = L"C:\\Windows\\system32;C:\\Chocolatey\\bin";
    DWORD needed = (DWORD)wcslen(value)+1;
    if(needed<=capacity) wcscpy(target, value);
    return needed;
}
static BOOL SetEnvironmentVariableW(const wchar_t *name, const wchar_t *value) {
    if(fail_env) { last_error=ERROR_ACCESS_DENIED; return FALSE; }
    if(!wcscmp(name,L"Path")) wcscpy(process_path,value);
    return TRUE;
}
static void reset(void) {
    file_count=copies=moves=deletes=fail_copy=fail_move=0;
    missing_path=closed_keys=fail_query=fail_write=expanded=fail_env=0;
    registry_type=REG_EXPAND_SZ; written_type=0;
    registry_path[0]=installed_root[0]=process_path[0]=0;
}
'''
ASSERTIONS = r'''
int main(void) {
    wchar_t command[1024];
    const wchar_t *args[] = {L"C:\\Program Files\\CFW\\7z.exe", L"", L"-oC:\\cache dir\\", L"say \"hello\"", NULL};
    assert(build_command(command, 1024, args));
    assert(!wcscmp(command, L"\"C:\\Program Files\\CFW\\7z.exe\" \"\" \"-oC:\\cache dir\\\\\" \"say \\\"hello\\\"\""));
    const wchar_t *escaped[] = {L"a\\\"b", NULL};
    assert(build_command(command, 1024, escaped));
    assert(!wcscmp(command, L"\"a\\\\\\\"b\""));
    struct { wchar_t left, buffer[6], right; } guarded = {L'L', L"", L'R'};
    const wchar_t *exact[] = {L"abc", NULL};
    assert(build_command(guarded.buffer, 6, exact));
    assert(!wcscmp(guarded.buffer, L"\"abc\""));
    const wchar_t *too_long[] = {L"abcd", NULL};
    assert(!build_command(guarded.buffer, 6, too_long));
    assert(guarded.buffer[0]==0 && guarded.left==L'L' && guarded.right==L'R');
    assert(!build_command(NULL, 0, args));
    assert(!append_argument(NULL, 0, L"x"));
    assert(join_path(command, 1024, L"C:\\cache dir", L"payload.exe"));
    assert(!wcscmp(command, L"C:\\cache dir\\payload.exe"));
    assert(join_path(command, 1024, L"C:\\", L"file"));
    assert(!wcscmp(command, L"C:\\file"));
    assert(!join_path(guarded.buffer, 6, L"C:\\directory", L"x"));
    assert(guarded.left==L'L' && guarded.right==L'R');
    assert(expand_path(L"abcdef", guarded.buffer, 6)==ERROR_INSUFFICIENT_BUFFER);
    assert(path_has_component(L"C:\\x; \"c:\\CHOCOLATEY\\bin\\\" ;C:\\y", L"C:\\Chocolatey\\bin"));
    assert(!path_has_component(L"C:\\Chocolatey\\binary", L"C:\\Chocolatey\\bin"));

    reset(); put(L"raw",FILE_ATTRIBUTE_DIRECTORY); put(L"raw\\choco.exe",0);
    assert(move_chocolatey_tree(L"raw",L"canonical",0)==ERROR_SUCCESS);
    assert(find(L"raw")<0 && find(L"canonical")>=0 && moves==1);
    reset(); put(L"raw",FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT);
    assert(move_chocolatey_tree(L"raw",L"canonical",0)==ERROR_INVALID_DATA && moves==0);
    reset(); put(L"raw",FILE_ATTRIBUTE_DIRECTORY); put(L"raw\\choco.exe",FILE_ATTRIBUTE_DIRECTORY);
    assert(move_chocolatey_tree(L"raw",L"canonical",0)==ERROR_INVALID_DATA && moves==0);
    put(L"raw\\choco.exe",0); put(L"canonical",FILE_ATTRIBUTE_DIRECTORY);
    assert(move_chocolatey_tree(L"raw",L"canonical",0)==ERROR_ALREADY_EXISTS && moves==0);

    reset(); put(L"root",FILE_ATTRIBUTE_DIRECTORY); put(L"root\\choco.exe",0);
    assert(install_chocolatey_bin(L"root",FALSE)==ERROR_SUCCESS);
    assert(find(L"root\\bin\\choco.exe")>=0 && find(L"root\\bin\\choco.exe.cfw-part")<0);
    put(L"root\\bin\\choco.exe.cfw-part",0);
    assert(install_chocolatey_bin(L"root",FALSE)==ERROR_ALREADY_EXISTS);
    assert(install_chocolatey_bin(L"root",TRUE)==ERROR_SUCCESS);
    assert(find(L"root\\bin\\choco.exe.cfw-part")<0);
    fail_move=1;
    assert(install_chocolatey_bin(L"root",TRUE)==ERROR_ACCESS_DENIED);
    assert(find(L"root\\bin\\choco.exe.cfw-part")<0 && find(L"root\\bin\\choco.exe")>=0);
    fail_move=0; fail_copy=1;
    assert(install_chocolatey_bin(L"root",TRUE)==ERROR_ACCESS_DENIED);
    fail_copy=0; put(L"root\\bin",FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT);
    int prior_copies=copies;
    assert(install_chocolatey_bin(L"root",TRUE)==ERROR_INVALID_DATA && copies==prior_copies);

    reset(); wcscpy(registry_path,L"%SystemRoot%\\system32");
    assert(set_chocolatey_environment(L"C:\\Chocolatey", L"C:\\Chocolatey\\bin")==ERROR_SUCCESS);
    assert(!wcscmp(registry_path,L"%SystemRoot%\\system32;C:\\Chocolatey\\bin"));
    assert(!wcscmp(process_path,L"C:\\Windows\\system32;C:\\Chocolatey\\bin"));
    assert(written_type==REG_EXPAND_SZ && closed_keys==1);
    assert(set_chocolatey_environment(L"C:\\Chocolatey", L"C:\\Chocolatey\\bin")==ERROR_SUCCESS);
    assert(!wcscmp(registry_path,L"%SystemRoot%\\system32;C:\\Chocolatey\\bin"));
    reset(); registry_type=REG_SZ; wcscpy(registry_path,L"C:\\Windows;\"c:\\CHOCOLATEY\\bin\\\"");
    assert(set_chocolatey_environment(L"C:\\Chocolatey", L"C:\\Chocolatey\\bin")==ERROR_SUCCESS);
    assert(written_type==REG_SZ && expanded==0 && !wcscmp(process_path, registry_path));
    reset(); registry_type=99;
    assert(set_chocolatey_environment(L"root", L"bin")==ERROR_INVALID_DATA && closed_keys==1);
    reset(); fail_query=1;
    assert(set_chocolatey_environment(L"root", L"bin")==ERROR_ACCESS_DENIED && closed_keys==1);
    reset(); fail_write=1;
    assert(set_chocolatey_environment(L"root", L"bin")==ERROR_ACCESS_DENIED && closed_keys==1);
    reset(); missing_path=1;
    assert(set_chocolatey_environment(L"root", L"bin")==ERROR_SUCCESS && written_type==REG_EXPAND_SZ);
    reset(); wmemset(registry_path,L'x',PATH_BUFFER-1); registry_path[PATH_BUFFER-1]=0;
    assert(set_chocolatey_environment(L"root",L"bin")==ERROR_INSUFFICIENT_BUFFER && closed_keys==1);
    return 0;
}
'''


class NativeCommonTests(unittest.TestCase):
    def test_quoting_bounds_promotion_recovery_and_environment(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler required")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "native.c"
            binary = Path(directory) / "native"
            source.write_text(HARNESS + HELPERS + ASSERTIONS)
            result = subprocess.run([compiler, "-std=c11", "-Wall", "-Werror", str(source), "-o", str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
