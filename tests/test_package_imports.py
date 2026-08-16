"""Import-integrity net for the whole package.

Every ``markdown_vault`` submodule must import without error. This is the safety
net for move/rename refactorings (splitting the flat package into subpackages): a
broken relative import after files move is caught here even for modules that no
behavioural test happens to load. ``pkgutil.walk_packages`` discovers modules
recursively, so it keeps covering subpackages once they exist — no maintenance.

Run in a subprocess: importing ``markdown_vault.main`` initialises logging, which
redirects the process's stdout/stderr, so it must not run inside the test process.
Failures are written to a file (a fresh fd, unaffected by that redirect).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"

_IMPORT_ALL = r"""
import importlib, pkgutil, sys
import markdown_vault
fails = []
for info in pkgutil.walk_packages(markdown_vault.__path__, markdown_vault.__name__ + "."):
    if info.name.rsplit(".", 1)[-1] == "__main__":
        continue  # a runner, not a library module — importing it runs the app
    try:
        importlib.import_module(info.name)
    except BaseException as e:  # incl. SystemExit: a module must not exit on import
        fails.append("%s: %s: %s" % (info.name, type(e).__name__, e))
open(sys.argv[1], "w").write("\n".join(fails))
sys.exit(1 if fails else 0)
"""


class TestPackageImports(unittest.TestCase):
    def test_every_submodule_imports_cleanly(self):
        env = dict(os.environ, PYTHONPATH=str(_SRC))
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "failures.txt")
            result = subprocess.run(
                [sys.executable, "-c", _IMPORT_ALL, out],
                env=env, capture_output=True, text=True,
            )
            failures = Path(out).read_text().strip() if os.path.exists(out) else ""
        self.assertEqual(
            result.returncode, 0,
            "markdown_vault submodules failed to import:\n" + (failures or result.stderr),
        )


if __name__ == "__main__":
    unittest.main()
