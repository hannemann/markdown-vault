"""Store secrets (e.g. an Ask API key) outside the config, via libsecret.

Keeps secrets out of ``settings.yaml`` and the logs. **Where** they land is libsecret's
call, and it differs by install type — the API here is the same either way:

* normal install → the **OS keyring** (Secret Service), visible and revocable in a
  keyring manager such as Seahorse;
* inside a **Flatpak/Snap sandbox** → libsecret's **file backend**, i.e. app-private
  storage encrypted with the sandbox's per-application master key from the Secret
  portal. Not a host keyring entry, and not shared with a non-sandboxed install.

All access is lazy (libsecret is imported inside the functions so importing this
module stays cheap) and **degrades gracefully**: when no backend is reachable —
headless run, locked keyring, libsecret missing — reads return ``""`` and writes
return ``False`` instead of falling back to plaintext on disk. The caller treats
"no key" as "re-enter it".

Secrets are keyed by a short logical name under a single schema. The name is the
caller's to choose and may carry scope — the Ask API key uses
``"ask_api_key:<backend>|<url>"`` so a key stays with the server it belongs to
(see :func:`markdown_vault.search.ask_models.secret_name`), and shows up under
that name in a keyring manager.
"""
import logging

logger = logging.getLogger(__name__)

_SCHEMA_NAME = "de.hannemann.markdown-vault"
_schema = None


def _secret():
    """Return the ``Secret`` gi module, or raise if libsecret is unavailable."""
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret  # type: ignore[attr-defined]
    return Secret


def _get_schema():
    global _schema
    if _schema is None:
        Secret = _secret()
        _schema = Secret.Schema.new(
            _SCHEMA_NAME, Secret.SchemaFlags.NONE,
            {"key": Secret.SchemaAttributeType.STRING},
        )
    return _schema


def available() -> bool:
    """True if a Secret Service responds (a probe lookup succeeds, even if empty)."""
    try:
        Secret = _secret()
        Secret.password_lookup_sync(_get_schema(), {"key": "__probe__"}, None)
        return True
    except Exception as exc:  # noqa: BLE001 — any libsecret failure means unavailable
        logger.debug("secret service unavailable: %s", exc)
        return False


def get_secret(key: str) -> str:
    """Return the stored secret for *key*, or ``""`` if none / unavailable."""
    try:
        Secret = _secret()
        return Secret.password_lookup_sync(_get_schema(), {"key": key}, None) or ""
    except Exception as exc:
        logger.warning("keyring lookup failed for %r: %s", key, exc, exc_info=True)
        return ""


def set_secret(key: str, value: str) -> bool:
    """Store *value* for *key* (empty *value* clears it). Return whether it stuck."""
    try:
        Secret = _secret()
        schema = _get_schema()
        if value:
            return bool(Secret.password_store_sync(
                schema, {"key": key}, Secret.COLLECTION_DEFAULT,
                f"Markdown Vault: {key}", value, None))
        return bool(Secret.password_clear_sync(schema, {"key": key}, None))
    except Exception as exc:
        logger.warning("keyring store failed for %r: %s", key, exc, exc_info=True)
        return False
