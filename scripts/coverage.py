#!/usr/bin/env python3
"""Line coverage for one source file under a set of tests, via ``sys.monitoring``.

No ``coverage.py`` dependency (it is not installed). Run from the repository root:

    python3 scripts/coverage.py <src-file> [test_module ...]

``<src-file>`` is a repo-relative path, e.g.
``src/markdown_vault/importers/dialog_import.py``. Without test-module names the whole
``tests/`` suite is discovered; with them, only those modules run (faster while iterating
on one module's coverage). Prints ``<file>: covered/executable = NN.N%`` and the sorted
list of uncovered executable lines.

"Executable" lines are the line starts of the compiled module (``dis.findlinestarts``),
recursively through nested code objects (methods, comprehensions); a line counts as
covered if it fired at least once while the tests ran. This mirrors the instrument the
code reviews use, so the numbers are comparable across rounds.
"""
import dis
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("src", "tests"):
    _p = os.path.join(ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _all_lines(code):
    lines = {ln for _, ln in dis.findlinestarts(code) if ln}
    for const in code.co_consts:
        if hasattr(const, "co_code"):
            lines |= _all_lines(const)
    return lines


def _ranges(nums):
    """Collapse a set of line numbers into ``start-end`` runs — a 1200-integer
    list on one line is unreadable for the large files this is aimed at."""
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev > start else f"{start}")
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev > start else f"{start}")
    return ", ".join(out)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    target = os.path.abspath(argv[0])
    test_names = argv[1:]
    source = open(target, encoding="utf-8").read()
    executable = _all_lines(compile(source, target, "exec"))

    covered = set()
    mon = sys.monitoring
    tid = mon.COVERAGE_ID   # the tool id reserved for coverage; PROFILER_ID is a profiler's
    mon.use_tool_id(tid, "cov")

    def on_line(code, lineno):
        if code.co_filename == target and lineno is not None:
            covered.add(lineno)
        return mon.DISABLE

    mon.register_callback(tid, mon.events.LINE, on_line)
    mon.set_events(tid, mon.events.LINE)
    try:
        loader = unittest.TestLoader()
        if test_names:
            suite = unittest.TestSuite(loader.loadTestsFromName(n) for n in test_names)
        else:
            suite = loader.discover(os.path.join(ROOT, "tests"))
        result = unittest.TextTestRunner(verbosity=0).run(suite)
    finally:
        mon.set_events(tid, 0)
        mon.free_tool_id(tid)

    hit = covered & executable
    pct = 100.0 * len(hit) / len(executable) if executable else 100.0
    rel = os.path.relpath(target, ROOT)
    print(f"{rel}: {len(hit)}/{len(executable)} executable lines = {pct:.1f}%")
    print("uncovered:", _ranges(executable - covered) or "none")
    # A red suite makes the percentage wrong, not just smaller (a test that errors
    # early exercises no lines it otherwise would), so surface the run and fail.
    print(f"tests: ran={result.testsRun} failures={len(result.failures)} "
          f"errors={len(result.errors)} skipped={len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
