"""Markdown Vault — git integration layer.

Thin wrapper around ``git`` CLI commands.  All functions are designed
to fail silently — when a directory is not a git repository or when
git is not installed, callers receive empty results rather than exceptions.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _read_harden_flags(cwd: str | Path) -> list[str]:
    """``-c`` flags that disable every command-valued git config a READ command would
    otherwise execute in a repo whose contents the user has not vetted.

    A vault is a directory the user opened; the sidebar's git panel refreshes on every
    note-open, so a crafted ``.git/config`` / ``.gitattributes`` runs code on the
    drive-by. Static keys: ``core.fsmonitor`` (fires on status), the ``post-index-change``
    hook via ``core.hooksPath`` (also on status), and ``gpg.program`` via
    ``log.showSignature`` (on log). Freely-named ``filter.<name>.{clean,smudge,process}``
    fire on ``git diff`` (selected by a shipped ``.gitattributes``) and are enumerated by
    SCOPE: ``git config --list --show-scope`` attributes every key in the merged listing —
    includes already resolved, ``includeIf`` conditions already evaluated — to the scope
    that pulled it in (``local``/``worktree`` for the repo, ``global``/``system`` for the
    user). Neutralise every filter key whose scope is NOT ``global``/``system``; the user's
    own filters (git-lfs, even via an ``include.path`` in their global config) keep
    working, and any file the repo pulls in — directly, via ``--worktree`` or via
    ``include.path`` — is ``local`` and gets blanked, with no path resolution or trust list
    to get wrong. Keys reported as ``command`` scope (from ``-c`` or ``GIT_CONFIG_KEY_*``)
    are neutralised too, deliberately: an env var is not the user's config file and can come
    from anywhere. The enumeration runs ``harden=False``, so it carries no *hardening*
    ``-c`` — only the standing ``core.quotepath=false`` from :func:`_run_git`, which appears
    as ``command`` scope in its own listing but is a ``core.*`` key, not a filter, so the
    loop below skips it. Each neutralised filter also gets ``required=false``: to git a
    blanked *required* filter is a FAILED filter and it aborts the operation (git-lfs sets
    ``required``). Needs git >= 2.26 for ``--show-scope``; on an older git the enumeration
    fails and the filter family is left unprotected — logged, not silent (see below).

    **Boundary (F18):** this covers only the READ commands the wrapper runs today —
    ``status``, ``diff``, ``log``, ``rev-parse`` — none of which touch the network or a
    TTY. The moment a ``fetch``/``pull``/``push`` is added, keys like ``credential.helper``,
    ``core.sshCommand``, ``url.*.insteadOf`` and ``protocol.*`` become live command
    vectors and must be neutralised here too. ``core.pager`` and aliases are absent on
    purpose (no TTY; aliases cannot shadow built-ins).

    **No memoisation (F16):** ``_read_harden_flags`` spawns one ``git config`` per hardened
    read op, so the note-open path pays a second git process per op. Deliberately not
    cached: a correct cache must invalidate on every *included* config file too — and their
    paths are only known after parsing — so a naive ``.git/config`` mtime cache would serve
    stale hardening after an ``include.path`` change, a security regression rather than a
    perf bug. Revisit as its own perf ticket with include-aware invalidation if the cost
    ever bites.

    Read-only by design (see :func:`_run_git`). ``diff.external`` and the
    ``diff.<driver>`` command/textconv keys are handled by ``--no-ext-diff``/
    ``--no-textconv`` at the diff call site, not here: an empty ``-c`` for a program-path
    key makes git exec ``""`` and silently empties the diff. Listing config does not
    execute command values, so the enumeration runs unhardened and cannot recurse.
    """
    flags = [
        "-c", "core.fsmonitor=",
        "-c", "core.hooksPath=/dev/null",
        "-c", "log.showSignature=false",
    ]
    blank_keys: list[str] = []
    names: set[str] = set()
    code, out, _ = _run_git(
        ["config", "--list", "--show-scope", "--name-only"], cwd=cwd, harden=False)
    if code != 0:
        # git < 2.26 has no --show-scope, so the filter family stays unprotected. Not
        # attacker-triggerable (an environment property; a broken repo config makes git
        # refuse the diff too), but security-relevant — surface it instead of swallowing.
        # The read op still runs, unhardened for filters only.
        logger.warning(
            "git config --show-scope failed (rc=%s); filter hardening inactive "
            "(needs git >= 2.26)", code)
    else:
        for line in out.splitlines():
            scope, _sep, key = line.partition("\t")
            if not key or scope in ("global", "system"):
                continue          # the user's own config — trusted
            parts = key.strip().split(".")
            if (parts[0] == "filter" and len(parts) >= 3
                    and parts[-1] in ("clean", "smudge", "process")):
                blank_keys.append(key.strip())
                names.add(".".join(parts[1:-1]))
    for key in blank_keys:
        flags += ["-c", f"{key}="]
    for name in sorted(names):
        flags += ["-c", f"filter.{name}.required=false"]
    return flags


def _run_git(args: list[str], cwd: str | Path, harden: bool = True) -> tuple[int, str, str]:
    """Run a git command and return ``(returncode, stdout, stderr)``.

    Returns ``(-1, "", "<error>")`` when git is not installed or the command times out.
    With *harden* (the default, for READ commands), command-valued config the repository
    could weaponise is neutralised first (see :func:`_read_harden_flags`). Pass
    ``harden=False`` on the WRITE path (commit/add — the user asked for the commit, and
    blanking a filter there would write unfiltered content into history) and for the
    enumeration call, which must not recurse.

    Accepted residual (F17): the write path is therefore fully unhardened, so a crafted
    repo's hooks (``pre-commit``/``commit-msg``/``post-index-change``) and ``core.fsmonitor``
    run the first time the user commits in that vault. That is a different, milder risk
    class than the read path — it needs a deliberate commit, not merely opening a note — and
    disabling *hooks* here (unlike blanking *filters*) would silently drop the user's own
    legitimate hooks. Left live by decision; split ``harden`` into filter/hook concerns only
    if that trade-off is ever revisited.
    """
    # core.quotepath=false: one config home so no call site forgets it (all path output
    # stays UTF-8, not octal-escaped). Cosmetic, not security, so it applies on every call
    # including the write path; the security hardening below is read-only.
    prefix = ["-c", "core.quotepath=false", *(_read_harden_flags(cwd) if harden else [])]
    try:
        result = subprocess.run(
            ["git", *prefix, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        logger.warning("git not found in PATH")
        return -1, "", "git not found"
    except subprocess.TimeoutExpired:
        logger.warning("git command timed out: %s", args)
        return -1, "", "git timed out"


def is_git_repo(path: str | Path) -> bool:
    """Return ``True`` if *path* is inside a git working tree."""
    code, _, _ = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return code == 0


def get_status(path: str | Path) -> list[dict[str, str]]:
    """Return porcelain status entries for the working tree.

    Each entry is ``{"status": str, "path": str}`` where *status* is the
    two-character code from ``git status --porcelain`` (e.g. ``"M "``,
    ``"??"``, ``"R "``).  For renames, only the new path is returned.
    """
    code, stdout, _ = _run_git(
        ["status", "--porcelain", "-z"],
        cwd=path,
    )
    if code != 0:
        return []
    entries: list[dict[str, str]] = []
    # NUL-separated output. Each entry: "XY path\0"
    # For renames (status starts with R), there are two entries:
    # "R  new_path\0" followed by "   old_path\0"
    parts = stdout.split('\0')
    i = 0
    while i < len(parts) - 1:  # -1 because split leaves trailing empty string
        entry = parts[i]
        if not entry:
            i += 1
            continue
        if len(entry) >= 3:
            status = entry[:2]  # Keep both chars (e.g., "M ", "??", "R ")
            filepath = entry[3:]  # Skip "XY "
            # If this is a rename, the NEXT part is the old path - skip it
            if status.startswith('R'):
                i += 1  # skip the old path entry
            entries.append({"status": status.strip(), "path": filepath})
        i += 1
    return entries


def get_diff(path: str | Path, filepath: str | None = None) -> str:
    """Return a diffstat summary of the working tree — one line per changed file with
    its insertion/deletion counts, plus a totals line — NOT the full unified diff.

    The sidebar panel shows this summary; a truncated raw diff (``diff[:2000]``) was
    neither bounded nor readable. Bounding it at the source (F19) keeps a working tree
    with large uncommitted changes from building a multi-megabyte string to display a few
    lines — ``--stat`` output is one line per file, not per diff line. When *filepath* is
    given, only that file's stat is returned. A real line-level diff viewer is future work.
    """
    # --no-ext-diff/--no-textconv disable diff.external and the diff.<driver>
    # command/textconv keys (a crafted repo would otherwise run those as commands, see
    # _read_harden_flags). The flags are the correct mechanism: an empty
    # `-c diff.external=` makes git exec an empty program and silently empties the diff.
    args = ["diff", "--stat", "--no-ext-diff", "--no-textconv"]
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
    # harden=False: the write path is user-triggered, and read-hardening here would blank
    # the user's own filters and write unfiltered content into history (git-lfs corruption).
    code, stdout, stderr = _run_git(["commit", "-m", message], cwd=path, harden=False)
    if code != 0:
        logger.warning("git commit failed in %s: %s", path, stderr or stdout)
    return code == 0, stderr or stdout


def stage_and_commit(
    path: str | Path,
    files: list[str],
    message: str,
) -> tuple[bool, str]:
    """Stage the given *files* and commit.  Returns ``(success, output)``."""
    for fpath in files:
        # harden=False: see commit() — the write path keeps the user's filters intact.
        code, _, err = _run_git(["add", "--", fpath], cwd=path, harden=False)
        if code != 0:
            return False, err
    return commit(path, message)
