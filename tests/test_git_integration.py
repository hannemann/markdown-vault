"""Tests for markdown_vault.vault.git_integration — git CLI wrapper."""

import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from markdown_vault.vault import git_integration as _gi
from markdown_vault.vault.git_integration import (
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
        self.assertIn("test.md", diff)   # --stat names the changed file, not its content

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
        self.assertIn("a.md", diff)      # --stat for a.md only
        self.assertNotIn("b.md", diff)


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

    def test_commits_file_with_leading_dash_name(self):
        # A path starting with "-" must not be parsed as a git option: `git add
        # -x.md` fails with "unknown switch". The "--" separator forces it to be
        # read as a pathspec. get_diff already does this; stage_and_commit must too.
        (Path(self._tmpdir) / "-x.md").write_text("content")
        ok, output = stage_and_commit(self._tmpdir, ["-x.md"], "Add dash file")
        self.assertTrue(ok, output)
        self.assertEqual(get_status(self._tmpdir), [])


class TestUntrustedConfigHardening(unittest.TestCase):
    """A crafted vault must not execute command-valued git config on the READ path
    (the sidebar's git panel refreshes on every note-open). Each sentinel test asserts
    BOTH: the marker is absent (the command did not run) AND the wrapper still returns
    correct output — a neutraliser that "works" by breaking the feature (e.g. an empty
    program-path key crashing diff to empty) must fail the second assertion.

    The hardening is READ-ONLY: on the write path the user asked for the commit, and
    blanking a filter there would write unfiltered content into history (git-lfs
    corruption), so stage_and_commit/commit stay unhardened (see test_write_path_*).
    """

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())          # the (hostile) repo
        self._out = Path(tempfile.mkdtemp())          # marker/scripts OUTSIDE the repo
        self._marker = self._out / "FIRED"
        self._sentinel = self._out / "sentinel.sh"
        # git calls the config command WITH its own arguments; ignore them, `exec cat`
        # so a filter/textconv passes content through unchanged.
        self._sentinel.write_text('#!/bin/sh\ntouch "%s"\nexec cat\n' % self._marker)
        self._sentinel.chmod(0o755)
        self._hooksdir = self._out / "hooks"
        self._hooksdir.mkdir()
        hook = self._hooksdir / "post-index-change"
        hook.write_text('#!/bin/sh\ntouch "%s"\n' % self._marker)
        hook.chmod(0o755)
        self._git("init")
        self._git("config", "user.email", "test@test.com")
        self._git("config", "user.name", "Test")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        shutil.rmtree(self._out, ignore_errors=True)

    def _git(self, *args, **kw):
        return subprocess.run(["git", "-C", str(self._tmp), *args],
                              capture_output=True, text=True, check=False, **kw)

    def _tracked_modified(self, name="note.md"):
        """Commit a file (no hostile config yet), then modify it so git diff has work."""
        (self._tmp / name).write_text("one\n")
        self._git("add", "--", name)
        self._git("commit", "-m", "init")
        (self._tmp / name).write_text("one\ntwo\n")
        return name

    def _attr(self, line):
        (self._tmp / ".gitattributes").write_text(line + "\n")

    def _arm(self):
        # clear any marker left by setup, so the assertion measures only the op under test
        self._marker.unlink(missing_ok=True)

    def _fired(self):
        return self._marker.exists()

    # ---- status: core.fsmonitor + the post-index-change hook --------

    def test_fsmonitor_not_run_on_status(self):
        name = self._tracked_modified()
        self._git("config", "core.fsmonitor", str(self._sentinel))
        self._arm()
        status = get_status(self._tmp)
        self.assertFalse(self._fired(), "core.fsmonitor executed on git status")
        self.assertTrue(any(e["path"] == name for e in status))

    def test_index_hook_not_run_on_status(self):
        name = self._tracked_modified()
        self._git("config", "core.hooksPath", str(self._hooksdir))
        self._arm()
        status = get_status(self._tmp)
        self.assertFalse(self._fired(), "post-index-change hook ran on git status")
        self.assertTrue(any(e["path"] == name for e in status))

    # ---- diff: external / driver command / textconv / filter -------

    def test_diff_external_not_run(self):
        self._tracked_modified()
        self._git("config", "diff.external", str(self._sentinel))
        self._arm()
        diff = get_diff(self._tmp)
        self.assertFalse(self._fired(), "diff.external executed on git diff")
        self.assertIn("note.md", diff, "diff must still show the change")

    def test_diff_driver_command_not_run(self):
        name = self._tracked_modified()
        self._attr(f"{name} diff=evil")
        self._git("config", "diff.evil.command", str(self._sentinel))
        self._arm()
        diff = get_diff(self._tmp)
        self.assertFalse(self._fired(), "diff.<drv>.command executed")
        self.assertIn("note.md", diff)

    def test_diff_textconv_not_run(self):
        name = self._tracked_modified()
        self._attr(f"{name} diff=evil")
        self._git("config", "diff.evil.textconv", str(self._sentinel))
        self._arm()
        diff = get_diff(self._tmp)
        self.assertFalse(self._fired(), "diff.<drv>.textconv executed")
        self.assertIn("note.md", diff)

    def test_filter_clean_not_run(self):
        name = self._tracked_modified()
        self._attr(f"{name} filter=evil")
        self._git("config", "filter.evil.clean", str(self._sentinel))
        self._arm()
        diff = get_diff(self._tmp)
        self.assertFalse(self._fired(), "filter.<name>.clean executed on git diff")
        self.assertIn("note.md", diff)

    # ---- ZG2: worktree-level config bypasses a --local-only enumeration

    def test_worktree_filter_not_run(self):
        name = self._tracked_modified()
        self._attr(f"{name} filter=evil")
        self._git("config", "extensions.worktreeConfig", "true")
        self._git("config", "--worktree", "filter.evil.clean", str(self._sentinel))
        self._arm()
        diff = get_diff(self._tmp)
        self.assertFalse(self._fired(), "worktree-level filter executed")
        self.assertIn("note.md", diff)

    # ---- ZH1: a filter defined in an INCLUDED config file belongs to no level

    def test_included_filter_not_run(self):
        name = self._tracked_modified()
        self._attr(f"{name} filter=evil")
        inc = self._tmp / ".git" / "evil.cfg"
        inc.write_text('[filter "evil"]\n\tclean = %s\n' % self._sentinel)
        self._git("config", "include.path", "./evil.cfg")
        self._arm()
        diff = get_diff(self._tmp)
        self.assertFalse(self._fired(), "included-file filter executed on git diff")
        self.assertIn("note.md", diff)

    # ---- ZG3: a required filter blanked to "" is a FAILED filter -> empty diff

    def test_required_filter_still_diffs(self):
        name = self._tracked_modified()
        self._attr(f"{name} filter=corp")
        self._git("config", "filter.corp.clean", str(self._sentinel))
        self._git("config", "filter.corp.required", "true")
        self._arm()
        diff = get_diff(self._tmp)
        self.assertFalse(self._fired(), "required filter executed")
        self.assertIn("note.md", diff, "required filter must be disabled, not failed-to-empty")

    # ---- ZG5: gpg.program via log.showSignature on git log ----------

    def test_log_show_signature_not_run(self):
        self._tracked_modified()
        self._git("add", "-A")
        self._git("commit", "-m", "second")
        # give HEAD a gpgsig header so `git log --show-signature` would invoke gpg
        raw = self._git("cat-file", "commit", "HEAD").stdout
        sig = ("gpgsig -----BEGIN PGP SIGNATURE-----\n \n fake\n"
               " -----END PGP SIGNATURE-----")
        out = []
        for ln in raw.split("\n"):
            out.append(ln)
            if ln.startswith("committer "):
                out.append(sig)
        new_hash = self._git("hash-object", "-w", "-t", "commit", "--stdin",
                             input="\n".join(out)).stdout.strip()
        self._git("update-ref", "HEAD", new_hash)
        self._git("config", "log.showSignature", "true")
        self._git("config", "gpg.program", str(self._sentinel))
        self._arm()
        log = get_log(self._tmp)
        self.assertFalse(self._fired(), "gpg.program ran via log.showSignature")
        self.assertTrue(log, "log must still return commits")

    # ---- ZG1: the WRITE path must NOT be hardened (else it corrupts history)

    def test_write_path_applies_the_users_filter(self):
        # A clean filter that uppercases: on `git add` it must run, so the committed
        # blob is filtered. If the read-hardening leaked onto the write path, the blob
        # would be the raw content — the git-lfs corruption case.
        self._git("config", "filter.up.clean", "tr a-z A-Z")
        self._attr("*.md filter=up")
        (self._tmp / "note.md").write_text("hallo\n")
        ok, output = stage_and_commit(self._tmp, ["note.md"], "add")
        self.assertTrue(ok, output)
        blob = self._git("show", "HEAD:note.md").stdout
        self.assertEqual(blob, "HALLO\n", "clean filter must run on the write path")

    # ---- ZI1: a split ~/.gitconfig (git-lfs in an included file) stays trusted

    def test_split_global_config_keeps_the_users_lfs(self):
        home = Path(tempfile.mkdtemp())
        try:
            (home / ".gitconfig").write_text("[include]\n\tpath = lfs.cfg\n")
            (home / "lfs.cfg").write_text(
                '[filter "lfs"]\n\tclean = git-lfs clean\n\trequired = true\n')
            with unittest.mock.patch.dict(os.environ, {
                    "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
                    "GIT_CONFIG_SYSTEM": os.devnull}):
                flags = _gi._read_harden_flags(self._tmp)
            self.assertNotIn("filter.lfs.clean=", " ".join(flags),
                             "the user's own included git-lfs must stay trusted")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    # ---- ZJ1: a non-matching includeIf must not over-trust a repo-included file

    def test_conditional_include_not_over_trusted(self):
        shared = self._out / "shared.cfg"
        shared.write_text('[filter "evil"]\n\tclean = %s\n' % self._sentinel)
        gcfg = self._out / "gitconfig"
        gcfg.write_text(
            '[includeIf "gitdir:/definitely/nonexistent/"]\n\tpath = %s\n' % shared)
        name = self._tracked_modified()
        self._attr(f"{name} filter=evil")
        self._git("config", "include.path", str(shared))   # the repo includes it itself
        self._arm()
        with unittest.mock.patch.dict(os.environ, {
                "GIT_CONFIG_GLOBAL": str(gcfg), "GIT_CONFIG_SYSTEM": os.devnull}):
            diff = get_diff(self._tmp)
        self.assertFalse(self._fired(),
                         "a conditionally-referenced file the repo includes was over-trusted")
        self.assertIn("note.md", diff)

    # ---- ZJ2: an old git (no --show-scope) fails the enumeration -> log, not silent

    def test_enumeration_failure_is_logged(self):
        with unittest.mock.patch.object(_gi, "_run_git",
                                        return_value=(129, "", "unknown option")):
            with self.assertLogs(
                    "markdown_vault.vault.git_integration", level="WARNING") as cm:
                flags = _gi._read_harden_flags(self._tmp)
        joined = " ".join(flags)
        self.assertIn("core.fsmonitor=", joined)   # static flags still applied
        self.assertNotIn("filter.", joined)         # filter family unprotected, but…
        self.assertTrue(any("show-scope failed" in m for m in cm.output),
                        "the failure must be surfaced, not swallowed")

    # ---- regression: hardening must not change normal results -------

    def test_normal_repo_unaffected(self):
        name = self._tracked_modified()
        self.assertIn("note.md", get_diff(self._tmp))
        self.assertTrue(any(e["path"] == name for e in get_status(self._tmp)))
        self._git("add", "--", name)
        self._git("commit", "-m", "second")
        self.assertEqual(len(get_log(self._tmp)), 2)


if __name__ == "__main__":
    unittest.main()
