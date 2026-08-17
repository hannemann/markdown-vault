"""Tests for markdown_vault.core.secret_store — keyring access with graceful
degradation (no real Secret Service is touched; the libsecret module is mocked)."""
import unittest
from unittest.mock import patch, MagicMock

from markdown_vault.core import secret_store


class TestSecretStore(unittest.TestCase):
    def setUp(self):
        secret_store._schema = None      # drop the cached schema between tests

    @patch("markdown_vault.core.secret_store._secret")
    def test_get_returns_stored_value(self, mock_secret):
        mock_secret.return_value.password_lookup_sync.return_value = "sk-abc"
        self.assertEqual(secret_store.get_secret("ask_api_key"), "sk-abc")

    @patch("markdown_vault.core.secret_store._secret")
    def test_get_missing_returns_empty(self, mock_secret):
        mock_secret.return_value.password_lookup_sync.return_value = None
        self.assertEqual(secret_store.get_secret("ask_api_key"), "")

    @patch("markdown_vault.core.secret_store._secret",
           side_effect=RuntimeError("no libsecret"))
    def test_get_degrades_to_empty_on_error(self, _mock):
        self.assertEqual(secret_store.get_secret("ask_api_key"), "")

    @patch("markdown_vault.core.secret_store._secret")
    def test_set_stores_value(self, mock_secret):
        S = mock_secret.return_value
        S.password_store_sync.return_value = True
        self.assertTrue(secret_store.set_secret("ask_api_key", "sk-xyz"))
        # value passed through to the keyring, not returned/logged
        self.assertIn("sk-xyz", S.password_store_sync.call_args.args)

    @patch("markdown_vault.core.secret_store._secret")
    def test_set_empty_clears(self, mock_secret):
        S = mock_secret.return_value
        secret_store.set_secret("ask_api_key", "")
        S.password_clear_sync.assert_called_once()
        S.password_store_sync.assert_not_called()

    @patch("markdown_vault.core.secret_store._secret",
           side_effect=RuntimeError("no service"))
    def test_set_degrades_to_false_on_error(self, _mock):
        self.assertFalse(secret_store.set_secret("ask_api_key", "sk"))

    @patch("markdown_vault.core.secret_store._secret")
    def test_available_true_when_probe_succeeds(self, _mock):
        self.assertTrue(secret_store.available())

    @patch("markdown_vault.core.secret_store._secret",
           side_effect=RuntimeError("no service"))
    def test_available_false_when_unavailable(self, _mock):
        self.assertFalse(secret_store.available())


if __name__ == "__main__":
    unittest.main()
