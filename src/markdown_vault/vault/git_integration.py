"""Markdown Vault — git integration layer.

Thin wrapper around ``git`` CLI commands.  All functions are designed
to fail silently — when a directory is not a git repository or when
git is not installed, callers receive empty results rather than exceptions.

Only the READ surface is wired up today: the sidebar's git panel uses
``is_git_repo``, ``get_status`` and ``get_diff``. The write surface
(``commit``, ``stage_and_commit``) and ``get_log`` have no caller yet — they
are kept for a planned commit/history UI, already hardening-aware (their
``harden=False`` and the write-path notes in :func:`_run_git` take effect once
that UI is wired up).
"""

import contextlib
import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_batch = threading.local()


@contextlib.contextmanager
def batch_reads():
    """Memoise :func:`_read_harden_flags` per repo for the duration of the block.

    The sidebar's git panel makes three hardened reads (``is_git_repo``, ``get_status``,
    ``get_diff``) on the same repo per refresh; without this each re-enumerates the repo
    config, so a refresh spawns six git processes where four would do. Scoped to the block
    with a fresh cache, so there is no stale-config risk — the config cannot change within
    one synchronous refresh (a *persistent* cache would have to invalidate on every
    included file; see :func:`_read_harden_flags`). Thread-local, so concurrent refreshes
    on different worker threads do not share a cache. Nestable: an inner block reuses the
    outer cache.
    """
    prev = getattr(_batch, "cache", None)
    if prev is None:
        _batch.cache = {}
    try:
        yield
    finally:
        _batch.cache = prev


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

    Boundary: this covers the read commands the wrapper runs today; if a network command
    (``fetch``/``pull``/``push``) is ever added, ``credential.helper``, ``core.sshCommand``
    and friends become live and must be neutralised here too.

    **Memoisation (F16):** within a :func:`batch_reads` block (the sidebar wraps its
    refresh in one) the result is cached per repo, so the three hardened reads of one
    refresh enumerate once, not three times. NOT cached across refreshes: a persistent
    cache would have to invalidate on every *included* config file — paths known only after
    parsing — so a naive ``.git/config`` mtime cache would serve stale hardening after an
    ``include.path`` change, a security regression. The within-refresh cache sidesteps
    that: the config cannot change within one synchronous refresh.

    Read-only by design (see :func:`_run_git`). ``diff.external`` and the
    ``diff.<driver>`` command/textconv keys are handled by ``--no-ext-diff``/
    ``--no-textconv`` at the diff call site, not here: an empty ``-c`` for a program-path
    key makes git exec ``""`` and silently empties the diff. Listing config does not
    execute command values, so the enumeration runs unhardened and cannot recurse.
    """
    cache = getattr(_batch, "cache", None)
    cache_key = str(cwd)   # NOT `key`: the loops below rebind `key` to config keys
    if cache is not None and cache_key in cache:
        return cache[cache_key]
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
    if cache is not None:
        cache[cache_key] = flags
    return flags


def _run_git(args: list[str], cwd: str | Path, harden: bool = True) -> tuple[int, str, str]:
    """Run a git command and return ``(returncode, stdout, stderr)``.

    Returns ``(-1, "", "<error>")`` when git is not installed or the command times out.
    With *harden* (the default, for READ commands), command-valued config the repository
    could weaponise is neutralised first (see :func:`_read_harden_flags`). Pass
    ``harden=False`` on the WRITE path (commit/add — the user asked for the commit, and
    blanking a filter there would write unfiltered content into history) and for the
    enumeration call, which must not recurse.

    The write path is not reached today (``commit``/``stage_and_commit`` have no caller —
    see the module docstring), so this is foresight, not a live residual. When a commit UI
    is wired up: the write path stays unhardened so a filter is not blanked into history
    (git-lfs corruption), but that also leaves a crafted repo's hooks
    (``pre-commit``/``commit-msg``/``post-index-change``) and ``core.fsmonitor`` running on
    the user's own commit — a milder risk class (a deliberate action, not a drive-by read).
    Disabling *hooks* there (unlike blanking *filters*) would drop the user's own legitimate
    hooks, so split ``harden`` into filter/hook concerns and decide that trade-off then.
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
