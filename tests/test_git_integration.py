"""Tests for markdown_vault.git_integration — git CLI wrapper."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from markdown_vault.git_integration import (
    is_git_repo,
    get_status,
    get_diff,
    get_log,
    commit,
    stage_and_commit,
)


class _GitRepoMixin:
    """Create and tear down a temporary git repository."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        os.system(f"git init {self._tmpdir} >/dev/null 2>&1")
        os.system(f"git -C {self._tmpdir} config user.email 'test@test.com'")
        os.system(f"git -C {self._tmpdir} config user.name 'Test'")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestIsGitRepo(_GitRepoMixin, unittest.TestCase):
    def test_identifies_repo(self):
        self.assertTrue(is_git_repo(self._tmpdir))

    def test_rejects_non_repo(self):
        self.assertFalse(is_git_repo("/tmp"))


class TestGetStatus(_GitRepoMixin, unittest.TestCase):
    def test_clean_tree(self):
        self.assertEqual(get_status(self._tmpdir), [])

    def test_untracked_file(self):
        (Path(self._tmpdir) / "new.md").write_text("hello")
        status = get_status(self._tmpdir)
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["path"], "new.md")

    def test_renamed_file(self):
        """Renamed file shows as R with new path."""
        fp = Path(self._tmpdir) / "old.md"
        fp.write_text("content")
        os.system(f"git -C {self._tmpdir} add old.md >/dev/null 2>&1")
        os.system(f"git -C {self._tmpdir} commit -m 'init' >/dev/null 2>&1")
        os.system(f"git -C {self._tmpdir} mv old.md new.md >/dev/null 2>&1")
        status = get_status(self._tmpdir)
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["status"], "R")
        self.assertEqual(status[0]["path"], "new.md")

    def test_non_ascii_path(self):
        """Non-ASCII filename is returned unquoted."""
        fp = Path(self._tmpdir) / "Müller.md"
        fp.write_text("content")
        os.system(f"git -C {self._tmpdir} add 'Müller.md' >/dev/null 2>&1")
        os.system(f"git -C {self._tmpdir} commit -m 'init' >/dev/null 2>&1")
        fp.write_text("modified")
        status = get_status(self._tmpdir)
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["path"], "Müller.md")
        self.assertNotIn("\\", status[0]["path"])

    def test_non_repo_returns_empty(self):
        self.assertEqual(get_status("/tmp"), [])


class TestGetDiff(_GitRepoMixin, unittest.TestCase):
    def test_no_diff_on_clean_tree(self):
        self.assertEqual(get_diff(self._tmpdir), "")

    def test_shows_modification(self):
        fp = Path(self._tmpdir) / "test.md"
        fp.write_text("original")
        os.system(f"git -C {self._tmpdir} add test.md >/dev/null 2>&1")
        os.system(f"git -C {self._tmpdir} commit -m 'init' >/dev/null 2>&1")
        fp.write_text("modified")
        diff = get_diff(self._tmpdir)
        self.assertIn("modified", diff)

    def test_diff_specific_file(self):
        fp1 = Path(self._tmpdir) / "a.md"
        fp2 = Path(self._tmpdir) / "b.md"
        fp1.write_text("a")
        fp2.write_text("b")
        os.system(f"git -C {self._tmpdir} add . >/dev/null 2>&1")
        os.system(f"git -C {self._tmpdir} commit -m 'init' >/dev/null 2>&1")
        fp1.write_text("a2")
        fp2.write_text("b2")
        diff = get_diff(self._tmpdir, filepath="a.md")
        self.assertIn("a2", diff)
        self.assertNotIn("b2", diff)


class TestGetLog(_GitRepoMixin, unittest.TestCase):
    def test_empty_log(self):
        self.assertEqual(get_log(self._tmpdir), [])

    def test_returns_commits(self):
        (Path(self._tmpdir) / "test.md").write_text("content")
        os.system(f"git -C {self._tmpdir} add . >/dev/null 2>&1")
        os.system(f"git -C {self._tmpdir} commit -m 'First' >/dev/null 2>&1")
        log = get_log(self._tmpdir)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["message"], "First")
        self.assertIn("hash", log[0])
        self.assertIn("author", log[0])
        self.assertIn("date", log[0])

    def test_respects_max_count(self):
        for i in range(5):
            (Path(self._tmpdir) / f"f{i}.md").write_text(str(i))
            os.system(f"git -C {self._tmpdir} add . >/dev/null 2>&1")
            os.system(f"git -C {self._tmpdir} commit -m 'Commit {i}' >/dev/null 2>&1")
        log = get_log(self._tmpdir, max_count=2)
        self.assertEqual(len(log), 2)


class TestStageAndCommit(_GitRepoMixin, unittest.TestCase):
    def test_commits_new_file(self):
        (Path(self._tmpdir) / "new.md").write_text("content")
        ok, output = stage_and_commit(self._tmpdir, ["new.md"], "Add new.md")
        self.assertTrue(ok)
        status = get_status(self._tmpdir)
        self.assertEqual(len(status), 0)

    def test_commit_without_staging_fails(self):
        ok, _ = commit(self._tmpdir, "Nothing staged")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
