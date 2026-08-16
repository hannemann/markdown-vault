"""Tests for scripts/dbus-unwrap.py — turning a gdbus reply into raw output."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dbus-unwrap.py"


def run(stdin: str) -> str:
    return subprocess.run([sys.executable, str(SCRIPT)], input=stdin,
                          capture_output=True, text=True).stdout


class TestDbusUnwrap(unittest.TestCase):
    def test_string_reply_gets_real_newlines(self):
        # gdbus prints a multi-line string as one line with literal \n escapes.
        self.assertEqual(run("('line1\\nline2',)"), "line1\nline2\n")

    def test_json_string_reply_is_valid_json(self):
        out = run('(\'{"a": 1, "b": [2]}\',)')
        self.assertEqual(out, '{"a": 1, "b": [2]}\n')
        json.loads(out)                       # pipeable to jq

    def test_array_reply_is_one_item_per_line(self):
        self.assertEqual(run("(['/v/a.md', '/v/b.md'],)"), "/v/a.md\n/v/b.md\n")

    def test_boolean_reply_passes_through(self):
        # (true,)/(false,) is not Python-parsable — leave it as-is, still readable.
        self.assertEqual(run("(true,)").strip(), "(true,)")

    def test_code_fence_answer_survives(self):
        out = run("('text\\n\\n```py\\nx = 1\\n```\\n',)")
        self.assertEqual(out, "text\n\n```py\nx = 1\n```\n\n")


if __name__ == "__main__":
    unittest.main()
