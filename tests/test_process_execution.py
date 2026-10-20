"""Execute the production process helper against controlled Windows API outcomes."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProcessExecutionTests(unittest.TestCase):
    def test_process_errors_timeouts_and_handles_are_preserved(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("a host C compiler is required for the Windows API harness")
        source = (ROOT / "installer.c").read_text()
        helpers = source[source.index("static DWORD run_process_bounded("):source.index("static DWORD download_file(")]
        harness = r'''
#include <assert.h>
#include <stddef.h>
#include <wchar.h>
typedef unsigned long DWORD;
typedef int BOOL;
typedef const wchar_t *LPCWSTR;
typedef wchar_t *LPWSTR;
typedef struct { size_t cb; } STARTUPINFOW;
typedef struct { int hProcess; int hThread; } PROCESS_INFORMATION;
#define INFINITE 0xffffffffUL
#define WAIT_OBJECT_0 0
#define WAIT_TIMEOUT 258
#define ERROR_TIMEOUT 1460
#define ERROR_GEN_FAILURE 31
static int mode, closed, waits, terminated;
static DWORD observed_timeout;
static DWORD GetLastError(void) { return 123; }
static BOOL CreateProcessW(LPCWSTR app, LPWSTR command, void *pa, void *ta,
    BOOL inherit, DWORD flags, void *env, void *cwd, STARTUPINFOW *startup,
    PROCESS_INFORMATION *process) {
    (void)app; (void)command; (void)pa; (void)ta; (void)inherit;
    (void)flags; (void)env; (void)cwd;
    assert(startup->cb == sizeof(*startup));
    process->hProcess = 1; process->hThread = 2;
    return mode != 1;
}
static DWORD WaitForSingleObject(int handle, DWORD timeout) {
    assert(handle == 1);
    if (waits++ == 0) {
        observed_timeout = timeout;
        if (mode == 2 || mode == 5) return WAIT_TIMEOUT;
        if (mode == 3) return 0xffffffffUL;
    }
    return WAIT_OBJECT_0;
}
static BOOL GetExitCodeProcess(int handle, DWORD *code) {
    assert(handle == 1); *code = 42;
    return mode != 4;
}
static BOOL TerminateProcess(int handle, DWORD code) {
    assert(handle == 1); assert(code == ERROR_TIMEOUT); terminated++;
    return mode != 5;
}
static void CloseHandle(int handle) { assert(handle == 1 || handle == 2); closed++; }
'''
        assertions = r'''
int main(void) {
    for (mode = 0; mode <= 5; mode++) {
        for (int bounded = 0; bounded <= 1; bounded++) {
            closed = waits = terminated = 0;
            DWORD actual = bounded ? run_process_bounded(NULL, NULL, 0, 250)
                                   : run_process(NULL, NULL, 0);
            DWORD expected = mode == 0 ? 42
                           : !bounded && (mode == 2 || mode == 3 || mode == 5) ? ERROR_GEN_FAILURE
                           : mode == 2 ? ERROR_TIMEOUT : 123;
            assert(actual == expected);
            assert(closed == (mode == 1 ? 0 : 2));
            if (mode != 1) assert(observed_timeout == (bounded ? 250 : INFINITE));
            assert(terminated == (bounded && (mode == 2 || mode == 5) ? 1 : 0));
        }
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "process.c").write_text(harness + helpers + assertions)
            result = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(path / "process.c"), "-o", str(path / "process")], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(path / "process")], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
