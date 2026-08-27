"""Shared failure-behaviour guard for the two facades' atomic byte writers.

``state_fs._atomic_bytes`` and ``vault_fs._atomic_bytes`` are deliberately duplicated: a
shared writer would need a third module carrying raw filesystem mutation, reintroducing
the allowlist entry the two-door design removed, and it cannot live in ``path_guard``
(that module is read-only by contract). After the AQ1/AP1 work the two are the SAME shape
again (both resolve the leaf, both tmp-then-replace, both clean up on failure).

This write core has the longest bug history in the whole effort (AI1/AI2/AJ1 in the
downloader, then again in each facade), so the duplication is pinned here: both writers
run through the same failure scenarios. Fix one and not the other, or "clean up" one to
differ, and this goes red.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from markdown_vault.core import state_fs, vault_fs

_WRITERS = [state_fs._atomic_bytes, vault_fs._atomic_bytes]


class TestAtomicWriterParity(unittest.TestCase):
    def test_a_failed_replace_leaves_the_previous_file_and_no_part(self):
        for writer in _WRITERS:
            with self.subTest(writer=writer.__module__), TemporaryDirectory() as d:
                target = Path(d) / "f"
                target.write_text("OLD")
                with mock.patch(f"{writer.__module__}.os.replace",
                                side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        writer(target, b"NEW")
                self.assertEqual(target.read_text(), "OLD")        # previous content survives
                self.assertFalse((Path(d) / "f.part").exists())    # partial cleaned up

    def test_a_write_error_creates_no_target_and_leaves_no_part(self):
        for writer in _WRITERS:
            with self.subTest(writer=writer.__module__), TemporaryDirectory() as d:
                target = Path(d) / "f"
                with mock.patch(f"{writer.__module__}.open",
                                side_effect=OSError("io"), create=True):
                    with self.assertRaises(OSError):
                        writer(target, b"data")
                self.assertFalse(target.exists())
                self.assertFalse((Path(d) / "f.part").exists())

    def test_a_failing_cleanup_does_not_mask_the_real_error(self):
        # The real failure (replace) must reach the caller even if removing the .part
        # itself fails — the cleanup is best-effort in both writers.
        for writer in _WRITERS:
            with self.subTest(writer=writer.__module__), TemporaryDirectory() as d:
                target = Path(d) / "f"
                target.write_text("OLD")
                with mock.patch(f"{writer.__module__}.os.replace",
                                side_effect=OSError("real failure")), \
                     mock.patch.object(Path, "unlink", side_effect=OSError("cleanup")):
                    with self.assertRaises(OSError) as ctx:
                        writer(target, b"NEW")
                self.assertEqual(str(ctx.exception), "real failure")   # not the cleanup error


if __name__ == "__main__":
    unittest.main()
