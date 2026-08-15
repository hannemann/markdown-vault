"""Tests for scripts/gen-wheel-lock.sh — the pip --require-hashes lock generator.

Verifies name/version parsing for both wheels and sdists. Wheel filenames escape
the distribution name (python_pptx-...); sdist filenames keep the name's hyphens
(python-pptx-1.0.2.tar.gz), so the two branches must parse differently (R101.1).
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen-wheel-lock.sh"


class TestGenWheelLock(unittest.TestCase):
    def _run(self, filenames):
        with tempfile.TemporaryDirectory() as d:
            for name in filenames:
                # Distinct bytes -> distinct sha256; pip3 hash accepts any file.
                (Path(d) / name).write_bytes(name.encode())
            return subprocess.run(
                ["sh", str(_SCRIPT), d],
                capture_output=True, text=True, check=True,
            ).stdout

    def test_wheel_name_and_version(self):
        out = self._run(["python_pptx-1.0.2-py3-none-any.whl"])
        self.assertRegex(out, r"python_pptx==1\.0\.2 --hash=sha256:[0-9a-f]+")

    def test_sdist_with_hyphenated_name_splits_on_last_hyphen(self):
        # R101.1: {name}-{version}.tar.gz keeps the name's hyphens; a PEP 440
        # version has none, so split on the LAST hyphen, not the first.
        out = self._run(["python-pptx-1.0.2.tar.gz"])
        self.assertIn("python-pptx==1.0.2 ", out)
        self.assertNotIn("python==pptx", out)

    def test_sdist_simple_name(self):
        out = self._run(["odfpy-1.4.1.tar.gz"])
        self.assertIn("odfpy==1.4.1 ", out)

    def test_multiplatform_wheels_merge_onto_one_line(self):
        out = self._run([
            "foo-2.0-cp313-cp313-manylinux2014_x86_64.whl",
            "foo-2.0-cp313-cp313-manylinux_2_28_x86_64.whl",
        ])
        lines = [ln for ln in out.splitlines() if ln.startswith("foo==2.0")]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].count("--hash="), 2)


if __name__ == "__main__":
    unittest.main()
