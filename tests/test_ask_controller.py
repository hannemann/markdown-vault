"""Tests for markdown_vault.app.ask_controller.AskController.endpoint_status —
the local-model verdict that makes the palette banner and block a broken choice.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from markdown_vault.app.ask_controller import AskController


def _controller(settings):
    return AskController(settings, get_semantic_index=lambda: None,
                         get_scope_paths=lambda: [])


class TestEndpointStatusLocal(unittest.TestCase):
    def test_local_ok_returns_none(self):
        ctrl = _controller({"ask": {"backend": "local"}})
        with patch("markdown_vault.search.llama_runtime.availability",
                   return_value=None):
            self.assertIsNone(ctrl.endpoint_status())

    def test_local_blocks_and_names_the_wanted_model(self):
        # The verdict must be fed the *wanted* path (folder + stored filename), not
        # resolve_model_path's result — that is "" for a gone choice and the banner
        # would read "(unset)" instead of the chosen model.
        ctrl = _controller({"ask": {"backend": "local",
                                    "gguf": {"dir": "/m", "path": "gone.gguf"}}})
        seen = {}

        def fake_availability(path):
            seen["path"] = path
            return f"No local model file at {path or '(unset)'}. Download one …"

        with patch("markdown_vault.search.llama_runtime.availability",
                   side_effect=fake_availability):
            st = ctrl.endpoint_status()
        self.assertEqual(seen["path"], str(Path("/m") / "gone.gguf"))  # not ""
        self.assertFalse(st.can_ask)
        self.assertIn("gone.gguf", st.message)


if __name__ == "__main__":
    unittest.main()
