"""The `make callbacks` ranking must be deterministic — and stay wired to `_rank`.

`scripts/count_callbacks.py` reports a "most handed around" list. Sorting by count
alone left ties in the set's iteration order, which is per-process hash-randomised
(``PYTHONHASHSEED``): three runs, three lists, while the header counts stay stable.
``AGENTS.md`` prescribes this exact output for before/after comparisons of a split,
so a floating name list makes that comparison unreliable.

Two guards, deliberately **not** a "run twice in-process and compare" test — one
process shares one hash seed, so that test would be green even with the count-only
sort still in place. Instead:

* ``_rank`` breaks ties by name (the sort itself), and
* the printed summary line is built from ``_rank``'s return value (the call site),
  so a later re-inline of a raw sort regresses the test — test the caller, not only
  the receiver.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import count_callbacks as cc


class TestRankIsDeterministic(unittest.TestCase):
    def test_rank_sorts_by_count_then_name(self):
        # z_top has the highest count, so it leads despite its name; the six *_mid
        # methods tie on count and must come out in NAME order, not set order. Six
        # names, not two: with a two-name tie the set may already sit in name order
        # for ~half of all PYTHONHASHSEED values, so the count-only bug would pass on
        # those seeds — the test would inherit the very non-determinism it guards.
        # Six names cut a chance pass to 1/6! ~ 0.14%.
        res = {
            "direct": {"z_top": [1, 1, 1],
                       "f_mid": [1], "b_mid": [1], "e_mid": [1],
                       "a_mid": [1], "d_mid": [1], "c_mid": [1]},
            "wrapped": {},
            "internal": {},
        }
        self.assertEqual(
            cc._rank(res),
            ["z_top", "a_mid", "b_mid", "c_mid", "d_mid", "e_mid", "f_mid"])

    def test_wrapped_only_method_is_counted(self):
        # A method handed only via a wrapper (lambda) still ranks; _rank must not
        # KeyError on its absence from the direct map.
        res = {"direct": {}, "wrapped": {"only_wrapped": [1]}, "internal": {}}
        self.assertEqual(cc._rank(res), ["only_wrapped"])

    def test_summary_line_is_built_from_rank(self):
        # Call-site guard: the printed "most handed around" line IS _rank's return
        # value. If someone re-inlines a raw sorted() and drops _rank, the sentinel
        # never appears -> red. Seed-independent, unlike a double-run comparison.
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.py"
            f.write_text(
                "class C:\n"
                "    def a(self): pass\n"
                "    def wire(self, o):\n"
                "        o.reg(self.a)\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with mock.patch.object(cc, "_rank",
                                   return_value=["ZZZ_sentinel", "YYY_sentinel"]), \
                    contextlib.redirect_stdout(buf):
                cc.main(["count_callbacks.py", str(f)])
        line = next(ln for ln in buf.getvalue().splitlines()
                    if "most handed around" in ln)
        self.assertIn("ZZZ_sentinel, YYY_sentinel", line)


if __name__ == "__main__":
    unittest.main()
