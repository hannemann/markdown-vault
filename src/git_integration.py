"""Markdown Vault — git integration layer.

Thin wrapper around ``git`` CLI commands.  All functions are designed
to fail silently — when a directory is not a git repository or when
git is not installed, callers receive empty results rather than exceptions.
"""

import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: str | Path) -> tuple[int, str, str]:
    """Run a git command and return ``(returncode, stdout, stderr)``.

    Returns ``(-1, "", "<error>")`` when git is not installed or the
    command times out.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "git not found"
    except subprocess.TimeoutExpired:
        return -1, "", "git timed out"


def is_git_repo(path: str | Path) -> bool:
    """Return ``True`` if *path* is inside a git working tree."""
    code, _, _ = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return code == 0


def get_status(path: str | Path) -> list[dict[str, str]]:
    """Return porcelain status entries for the working tree.

    Each entry is ``{"status": str, "path": str}`` where *status* is the
    two-character code from ``git status --porcelain``.
    """
    code, stdout, _ = _run_git(["status", "--porcelain"], cwd=path)
    if code != 0:
        return []
    entries: list[dict[str, str]] = []
    for line in stdout.strip().splitlines():
        if len(line) >= 3:
            entries.append({
                "status": line[:2].strip(),
                "path": line[3:],
            })
    return entries


def get_diff(path: str | Path, filepath: str | None = None) -> str:
    """Return the unified diff for the working tree.

    When *filepath* is given, only that file's diff is returned.
    """
    args = ["diff"]
    if filepath:
        args.extend(["--", filepath])
    code, stdout, _ = _run_git(args, cwd=path)
    return stdout if code == 0 else ""


def get_log(path: str | Path, max_count: int = 20) -> list[dict[str, str]]:
    """Return recent commits as a list of dicts.

    Each dict contains ``hash``, ``message``, ``author``, and ``date``.
    """
    code, stdout, _ = _run_git(
        ["log", f"--max-count={max_count}", "--format=%H|%s|%an|%ai"],
        cwd=path,
    )
    if code != 0:
        return []
    entries: list[dict[str, str]] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            entries.append({
                "hash": parts[0],
                "message": parts[1],
                "author": parts[2],
                "date": parts[3],
            })
    return entries


def commit(path: str | Path, message: str) -> tuple[bool, str]:
    """Commit all staged changes.  Returns ``(success, output)``."""
    code, stdout, stderr = _run_git(["commit", "-m", message], cwd=path)
    return code == 0, stderr or stdout


def stage_and_commit(
    path: str | Path,
    files: list[str],
    message: str,
) -> tuple[bool, str]:
    """Stage the given *files* and commit.  Returns ``(success, output)``."""
    for fpath in files:
        code, _, err = _run_git(["add", fpath], cwd=path)
        if code != 0:
            return False, err
    return commit(path, message)
