#!/usr/bin/env python3
"""Read validated runtime lock values and keep the native installer in sync."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def locked_value(inputs: dict, *keys: str) -> str:
    if not keys:
        raise ValueError("runtime input path is required")
    value = inputs
    try:
        for key in keys:
            value = value[key]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"missing runtime input {'.'.join(keys)}") from exc
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError(f"invalid runtime input {'.'.join(keys)}")
    if keys[-1] == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"invalid runtime input digest {'.'.join(keys)}")
    if keys[0] == "versions" and not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
        raise ValueError(f"invalid runtime version {'.'.join(keys)}")
    if keys == ("runtimeId",) and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError("invalid runtimeId")
    if keys[-1] in {"filename", "installerFilename"} and (
        value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", value)
    ):
        raise ValueError(f"invalid runtime filename {'.'.join(keys)}")
    if keys[-1] == "url":
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or re.search(r"\s", value):
            raise ValueError(f"invalid runtime URL {'.'.join(keys)}")
    return value


def installer_header(inputs: dict) -> str:
    lines = [
        "/* Generated from runtime-inputs.json; regenerate with runtime_inputs.py header. */",
        "#ifndef CFW_INSTALLER_INPUTS_H",
        "#define CFW_INSTALLER_INPUTS_H",
    ]
    for component in ("chocolatey", "powershell"):
        version = locked_value(inputs, "versions", component)
        filename = locked_value(inputs, "downloads", component, "filename")
        expected = f"chocolatey.{version}.nupkg" if component == "chocolatey" else f"PowerShell-{version}-win-x64.msi"
        if filename != expected:
            raise ValueError(f"{component} package filename does not match locked version")
        for field in ("url", "filename"):
            value = locked_value(inputs, "downloads", component, field)
            lines.append(f"#define CFW_{component.upper()}_{field.upper()} L{json.dumps(value)}")
    return "\n".join([*lines, "#endif", ""])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("get", "header", "check-header"))
    parser.add_argument("keys", nargs="*")
    args = parser.parse_args()
    try:
        inputs = json.loads((ROOT / "runtime-inputs.json").read_text(encoding="utf-8"))
        if args.command == "get":
            print(locked_value(inputs, *args.keys))
        else:
            if args.keys:
                raise ValueError("header commands do not accept input keys")
            header = installer_header(inputs)
            if args.command == "header":
                print(header, end="")
            elif (ROOT / "installer-inputs.h").read_text(encoding="utf-8") != header:
                raise ValueError("installer-inputs.h is stale; run python3 compat/runtime_inputs.py header > compat/installer-inputs.h")
    except (OSError, ValueError) as exc:
        parser.exit(64, f"[cfw] {exc}\n")


if __name__ == "__main__":
    main()
