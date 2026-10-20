"""The same checked build recipe serves release and runtime qualification."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CompileInstallerTests(unittest.TestCase):
    def test_shared_recipe_quotes_paths_and_propagates_compiler_failure(self):
        with tempfile.TemporaryDirectory(prefix="cfw compile ") as directory:
            root = Path(directory)
            compiler = root / "x86_64-w64-mingw32-gcc"
            compiler.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
pathlib.Path(os.environ["CFW_TEST_ARGUMENTS"]).write_text(json.dumps(sys.argv[1:]))
if os.environ.get("CFW_TEST_FAIL"): sys.exit(23)
pathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_bytes(b"compiled")
''')
            compiler.chmod(0o755)
            arguments = root / "arguments.json"
            output = root / "output with spaces" / "installer.exe"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"], "CFW_TEST_ARGUMENTS": str(arguments)}
            command = ["bash", str(ROOT / "compat/compile-installer.sh"), str(output)]
            result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(arguments.read_text())
            self.assertIn(str(ROOT / "installer.c"), args)
            self.assertEqual(args[args.index("-o") + 1], str(output))
            self.assertIn("-nostdlib", args)
            self.assertIn("-lshell32", args)
            output.unlink()
            result = subprocess.run(command, cwd=root, env={**env, "CFW_TEST_FAIL": "1"}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 23)
            self.assertFalse(output.exists())

    def test_all_compile_workflows_use_shared_recipe(self):
        for name in ("release", "build-container-runtime", "layer-contract"):
            workflow = (ROOT / f".github/workflows/{name}.yml").read_text()
            self.assertIn("bash compat/compile-installer.sh", workflow)
            self.assertNotIn("-finline-limit=64", workflow)
        workflow = (ROOT / ".github/workflows/layer-contract.yml").read_text()
        self.assertIn("branches: [main]", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("'tests/**'", workflow)


if __name__ == "__main__":
    unittest.main()
