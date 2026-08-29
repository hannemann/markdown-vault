"""Test-support helpers for the StateFS-migrated tiers.

A module whose write path now goes through StateFS refuses a target outside the allowed
state roots, so a test that writes into a temp dir must allow that dir as a state root.
This confines the reach into StateFS's private ``_state_roots`` / ``_vault_roots`` to ONE
place instead of an inline ``mock.patch`` pair in every migrated caller's test.

Not for testing StateFS itself — those tests (``test_state_fs``) control the vault clause
and the root topology directly; this always clears the vault roots.

It patches ``_state_roots`` and ``_vault_roots`` but NOT ``_model_roots`` (used only by
``write_stream``). When the download tier migrates, its tests need the model roots pinned
too, or the real configured model folder quietly applies — harmless under the test-home
pinning, but unintended.
"""

from contextlib import contextmanager
from unittest import mock

from markdown_vault.core import state_fs
from markdown_vault.core import vault_fs


@contextmanager
def state_roots(*roots):
    """Make *roots* the only allowed StateFS state roots and clear the vault roots, so a
    guarded state write into one of them succeeds. Usable as a ``with`` block or driven
    manually via ``__enter__`` / ``__exit__`` from ``setUp`` / ``tearDown``."""
    allowed = [str(r) for r in roots]
    with mock.patch.object(state_fs, "_state_roots", return_value=allowed), \
         mock.patch.object(state_fs, "_vault_roots", return_value=[]):
        yield


@contextmanager
def vault_roots(*roots):
    """Make *roots* the configured vault roots for VaultFS, so a guarded vault write into
    one of them is allowed. The VaultFS mirror of :func:`state_roots`, for the caller tests
    of modules whose vault writes now go through VaultFS (which reads config.load_vaults)."""
    allowed = [str(r) for r in roots]
    with mock.patch.object(vault_fs, "_vault_roots", return_value=allowed):
        yield
