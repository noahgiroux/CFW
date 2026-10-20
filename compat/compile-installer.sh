#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo 'usage: compile-installer.sh OUTPUT.exe' >&2
  exit 64
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$repo_root/compat/runtime_inputs.py" check-header
mkdir -p "$(dirname "$1")"
x86_64-w64-mingw32-gcc -O2 -fno-ident -fno-stack-protector -fomit-frame-pointer \
  -fno-unwind-tables -fno-asynchronous-unwind-tables -mconsole -municode \
  -mno-stack-arg-probe -Xlinker --stack=0x200000,0x200000 -nostdlib -Wall -Wextra \
  -finline-limit=64 -Wl,-gc-sections "$repo_root/installer.c" \
  -lurlmon -lkernel32 -lucrtbase -luser32 -ladvapi32 -lntdll -lshell32 -lole32 -luuid -s \
  -Wl,--major-subsystem-version,6,--minor-subsystem-version,0 -o "$1"
test -s "$1"
