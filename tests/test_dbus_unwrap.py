"""Tests for scripts/dbus-unwrap.py — turning a gdbus reply into raw output."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dbus-unwrap.py"


def _proc(stdin: str):
    return subprocess.run([sys.executable, str(SCRIPT)], input=stdin,
                          capture_output=True, text=True)


def run(stdin: str) -> str:
    return _proc(stdin).stdout


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

    def test_boolean_maps_to_python(self):
        self.assertEqual(run("(true,)").strip(), "True")
        self.assertEqual(run("(false,)").strip(), "False")

    def test_empty_typed_array_unwraps_to_nothing(self):
        # gdbus annotates a value whose type can't be inferred: an empty `as` is
        # printed as (@as [],) — the "no tabs" / "no search hits" case (R120.2).
        self.assertEqual(run("(@as [],)"), "\n")

    def test_at_sign_inside_string_is_preserved(self):
        # The @<sig> stripping must not touch an @ inside a quoted string.
        self.assertEqual(run("('reach me at a@b.com',)"), "reach me at a@b.com\n")

    def test_false_inside_json_string_is_untouched(self):
        # DumpState returns a JSON string; a bare `false` inside it must NOT be
        # mapped to Python `False` (that would break the JSON).
        out = run('(\'{"hide": false}\',)')
        self.assertEqual(out, '{"hide": false}\n')
        json.loads(out)

    def test_empty_stdin_is_an_error(self):
        # A dead service yields empty stdin; that must not read as a valid answer.
        r = _proc("")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_code_fence_answer_survives(self):
        out = run("('text\\n\\n```py\\nx = 1\\n```\\n',)")
        self.assertEqual(out, "text\n\n```py\nx = 1\n```\n\n")


if __name__ == "__main__":
    unittest.main()
