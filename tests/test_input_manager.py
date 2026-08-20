"""Tests for markdown_vault.app.input_manager — InputManager."""

import unittest
import unittest.mock

from markdown_vault.app.input_manager import InputManager


class TestInputManagerInit(unittest.TestCase):
    """InputManager.__init__."""

    def test_stores_dependencies(self):
        """__init__ stores all dependencies."""
        application = unittest.mock.MagicMock()
        on_nav_file_opened = unittest.mock.MagicMock()
        nav_history = unittest.mock.MagicMock()
        back_btn = unittest.mock.MagicMock()
        forward_btn = unittest.mock.MagicMock()
        settings = {"tab_switch_mode": "mru"}

        mgr = InputManager(
            application=application,
            on_nav_file_opened=on_nav_file_opened,
            nav_history=nav_history,
            back_btn=back_btn,
            forward_btn=forward_btn,
            settings=settings,
        )

        self.assertEqual(mgr._application, application)
        self.assertEqual(mgr._on_nav_file_opened, on_nav_file_opened)
        self.assertEqual(mgr._nav_history, nav_history)
        self.assertEqual(mgr._back_btn, back_btn)
        self.assertEqual(mgr._forward_btn, forward_btn)
        self.assertEqual(mgr._settings, settings)
        self.assertIsNone(mgr._tab_shortcut_ctrl)
        self.assertEqual(mgr._tab_shortcuts, [])


class TestPushHistory(unittest.TestCase):
    """InputManager.push_history."""

    def setUp(self):
        self.application = unittest.mock.MagicMock()
        self.on_nav_file_opened = unittest.mock.MagicMock()
        self.nav_history = unittest.mock.MagicMock()
        self.back_btn = unittest.mock.MagicMock()
        self.forward_btn = unittest.mock.MagicMock()
        self.settings = {"tab_switch_mode": "mru"}
        self._mgr = InputManager(
            application=self.application,
            on_nav_file_opened=self.on_nav_file_opened,
            nav_history=self.nav_history,
            back_btn=self.back_btn,
            forward_btn=self.forward_btn,
            settings=self.settings,
        )

    def test_push_calls_history_push(self):
        """push_history delegates to nav_history.push()."""
        self._mgr.push_history("/path/to/file.md")
        self.nav_history.push.assert_called_once_with("/path/to/file.md")

    def test_push_updates_buttons(self):
        """push_history calls update_nav_buttons()."""
        with unittest.mock.patch.object(InputManager, '_update_nav_buttons') as mock_btn:
            self._mgr.push_history("/path/to/file.md")
            mock_btn.assert_called_once()


class TestNavBack(unittest.TestCase):
    """InputManager.nav_back."""

    def setUp(self):
        self.application = unittest.mock.MagicMock()
        self.on_nav_file_opened = unittest.mock.MagicMock()
        self.nav_history = unittest.mock.MagicMock()
        self.back_btn = unittest.mock.MagicMock()
        self.forward_btn = unittest.mock.MagicMock()
        self.settings = {"tab_switch_mode": "mru"}
        self._mgr = InputManager(
            application=self.application,
            on_nav_file_opened=self.on_nav_file_opened,
            nav_history=self.nav_history,
            back_btn=self.back_btn,
            forward_btn=self.forward_btn,
            settings=self.settings,
        )

    def test_nav_back_calls_callback(self):
        """nav_back calls on_nav_file_opened with the back path."""
        self.nav_history.back.return_value = "/path/to/previous.md"
        self._mgr.nav_back()
        self.on_nav_file_opened.assert_called_once_with("/path/to/previous.md", _from_nav=True)

    def test_nav_back_noop_when_none(self):
        """nav_back does nothing when back() returns None."""
        self.nav_history.back.return_value = None
        self._mgr.nav_back()
        self.on_nav_file_opened.assert_not_called()

    def test_nav_back_updates_buttons(self):
        """nav_back calls update_nav_buttons()."""
        self.nav_history.back.return_value = "/path/to/previous.md"
        with unittest.mock.patch.object(InputManager, '_update_nav_buttons'):
            self._mgr.nav_back()


class TestNavForward(unittest.TestCase):
    """InputManager.nav_forward."""

    def setUp(self):
        self.application = unittest.mock.MagicMock()
        self.on_nav_file_opened = unittest.mock.MagicMock()
        self.nav_history = unittest.mock.MagicMock()
        self.back_btn = unittest.mock.MagicMock()
        self.forward_btn = unittest.mock.MagicMock()
        self.settings = {"tab_switch_mode": "mru"}
        self._mgr = InputManager(
            application=self.application,
            on_nav_file_opened=self.on_nav_file_opened,
            nav_history=self.nav_history,
            back_btn=self.back_btn,
            forward_btn=self.forward_btn,
            settings=self.settings,
        )

    def test_nav_forward_calls_callback(self):
        """nav_forward calls on_nav_file_opened with the forward path."""
        self.nav_history.forward.return_value = "/path/to/next.md"
        self._mgr.nav_forward()
        self.on_nav_file_opened.assert_called_once_with("/path/to/next.md", _from_nav=True)

    def test_nav_forward_noop_when_none(self):
        """nav_forward does nothing when forward() returns None."""
        self.nav_history.forward.return_value = None
        self._mgr.nav_forward()
        self.on_nav_file_opened.assert_not_called()

    def test_nav_forward_updates_buttons(self):
        """nav_forward calls update_nav_buttons()."""
        self.nav_history.forward.return_value = "/path/to/next.md"
        with unittest.mock.patch.object(InputManager, '_update_nav_buttons'):
            self._mgr.nav_forward()


class TestUpdateNavButtons(unittest.TestCase):
    """InputManager.update_nav_buttons."""

    def setUp(self):
        self.application = unittest.mock.MagicMock()
        self.on_nav_file_opened = unittest.mock.MagicMock()
        self.nav_history = unittest.mock.MagicMock()
        self.back_btn = unittest.mock.MagicMock()
        self.forward_btn = unittest.mock.MagicMock()
        self.settings = {"tab_switch_mode": "mru"}
        self._mgr = InputManager(
            application=self.application,
            on_nav_file_opened=self.on_nav_file_opened,
            nav_history=self.nav_history,
            back_btn=self.back_btn,
            forward_btn=self.forward_btn,
            settings=self.settings,
        )

    def test_buttons_dim_instead_of_disable(self):
        """update_nav_buttons dims (opacity) rather than disabling, so a click is
        always consumed and never falls through to the header's maximize area."""
        self.nav_history.can_go_back.return_value = True
        self.nav_history.can_go_forward.return_value = False
        self._mgr.update_nav_buttons()
        self.back_btn.set_opacity.assert_called_once_with(1.0)
        self.forward_btn.set_opacity.assert_called_once_with(0.35)
        self.back_btn.set_sensitive.assert_not_called()


class TestApplyKeybindings(unittest.TestCase):
    """InputManager.apply_keybindings."""

    def setUp(self):
        self.application = unittest.mock.MagicMock()
        self.on_nav_file_opened = unittest.mock.MagicMock()
        self.nav_history = unittest.mock.MagicMock()
        self.back_btn = unittest.mock.MagicMock()
        self.forward_btn = unittest.mock.MagicMock()
        self.settings = {"tab_switch_mode": "mru"}
        self._mgr = InputManager(
            application=self.application,
            on_nav_file_opened=self.on_nav_file_opened,
            nav_history=self.nav_history,
            back_btn=self.back_btn,
            forward_btn=self.forward_btn,
            settings=self.settings,
        )
        self.tab_shortcut_ctrl = unittest.mock.MagicMock()
        self.tab_shortcuts = []

    def test_no_application_does_nothing(self):
        """apply_keybindings does nothing when application is None."""
        self._mgr._application = None
        self._mgr.apply_keybindings(self.tab_shortcut_ctrl, self.tab_shortcuts)
        self.tab_shortcut_ctrl.remove_shortcut.assert_not_called()

    def test_sets_application_accels(self):
        """apply_keybindings sets application accelerators."""
        self._mgr.apply_keybindings(self.tab_shortcut_ctrl, self.tab_shortcuts)
        self.application.get_application().set_accels_for_action.assert_any_call(
            "win.nav-back", ["<Alt>Left"]
        )
        self.application.get_application().set_accels_for_action.assert_any_call(
            "win.nav-forward", ["<Alt>Right"]
        )

    def test_stores_shortcut_references(self):
        """apply_keybindings stores tab_shortcut_ctrl and tab_shortcuts."""
        self._mgr.apply_keybindings(self.tab_shortcut_ctrl, self.tab_shortcuts)
        self.assertEqual(self._mgr._tab_shortcut_ctrl, self.tab_shortcut_ctrl)
        self.assertEqual(self._mgr._tab_shortcuts, self.tab_shortcuts)


class TestUpdateTabShortcuts(unittest.TestCase):
    """InputManager.update_tab_shortcuts."""

    def setUp(self):
        self.application = unittest.mock.MagicMock()
        self.on_nav_file_opened = unittest.mock.MagicMock()
        self.nav_history = unittest.mock.MagicMock()
        self.back_btn = unittest.mock.MagicMock()
        self.forward_btn = unittest.mock.MagicMock()
        self.settings = {"tab_switch_mode": "linear"}
        self._mgr = InputManager(
            application=self.application,
            on_nav_file_opened=self.on_nav_file_opened,
            nav_history=self.nav_history,
            back_btn=self.back_btn,
            forward_btn=self.forward_btn,
            settings=self.settings,
        )
        self.tab_shortcut_ctrl = unittest.mock.MagicMock()
        self.tab_shortcuts = []
        self._mgr.apply_keybindings(self.tab_shortcut_ctrl, self.tab_shortcuts)

    def test_mru_mode_returns_early(self):
        """update_tab_shortcuts returns early in MRU mode."""
        self._mgr._settings["tab_switch_mode"] = "mru"
        self._mgr._tab_shortcuts.clear()
        self._mgr.update_tab_shortcuts()
        self.assertEqual(len(self._mgr._tab_shortcuts), 0)

    def test_linear_mode_adds_shortcuts(self):
        """update_tab_shortcuts adds shortcuts in linear mode."""
        self._mgr._settings["tab_switch_mode"] = "linear"
        self._mgr.update_tab_shortcuts()
        self.tab_shortcut_ctrl.add_shortcut.assert_called()


class TestUpdateNavButtonsPublic(unittest.TestCase):
    """InputManager.update_nav_buttons (public API)."""

    def setUp(self):
        self.application = unittest.mock.MagicMock()
        self.on_nav_file_opened = unittest.mock.MagicMock()
        self.nav_history = unittest.mock.MagicMock()
        self.back_btn = unittest.mock.MagicMock()
        self.forward_btn = unittest.mock.MagicMock()
        self.settings = {"tab_switch_mode": "mru"}
        self._mgr = InputManager(
            application=self.application,
            on_nav_file_opened=self.on_nav_file_opened,
            nav_history=self.nav_history,
            back_btn=self.back_btn,
            forward_btn=self.forward_btn,
            settings=self.settings,
        )

    def test_public_api_delegates_to_private(self):
        """update_nav_buttons public method delegates to _update_nav_buttons."""
        self._mgr._update_nav_buttons = unittest.mock.MagicMock()
        self._mgr.update_nav_buttons()
        self._mgr._update_nav_buttons.assert_called_once()


class TestPositionCallbacks(unittest.TestCase):
    """save_position_fn / restore_position_fn wrap push and back/forward so the
    reader's position is recorded on leave and restored on return — feature: the
    history restores the scroll position."""

    def _mgr(self, *, with_fns=True):
        self.nav_history = unittest.mock.MagicMock()
        self.on_nav = unittest.mock.MagicMock()
        self.save = unittest.mock.MagicMock()
        self.restore = unittest.mock.MagicMock()
        kw = dict(save_position_fn=self.save,
                  restore_position_fn=self.restore) if with_fns else {}
        return InputManager(
            application=unittest.mock.MagicMock(),
            on_nav_file_opened=self.on_nav,
            nav_history=self.nav_history,
            back_btn=unittest.mock.MagicMock(),
            forward_btn=unittest.mock.MagicMock(),
            settings={"tab_switch_mode": "mru"},
            **kw)

    def test_push_saves_leaving_position_before_pushing(self):
        mgr = self._mgr()
        order = []
        self.save.side_effect = lambda: order.append("save")
        self.nav_history.push.side_effect = lambda *a, **k: order.append("push")
        mgr.push_history("/f.md")
        self.assertEqual(order, ["save", "push"])   # record before the new entry

    def test_back_saves_before_and_restores_after_opening(self):
        mgr = self._mgr()
        order = []
        self.save.side_effect = lambda: order.append("save")
        self.nav_history.back.side_effect = lambda: (order.append("back") or "/prev.md")
        self.on_nav.side_effect = lambda *a, **k: order.append("open")
        self.restore.side_effect = lambda: order.append("restore")
        mgr.nav_back()
        self.assertEqual(order, ["save", "back", "open", "restore"])

    def test_forward_saves_before_and_restores_after_opening(self):
        mgr = self._mgr()
        order = []
        self.save.side_effect = lambda: order.append("save")
        self.nav_history.forward.side_effect = lambda: (order.append("fwd") or "/next.md")
        self.on_nav.side_effect = lambda *a, **k: order.append("open")
        self.restore.side_effect = lambda: order.append("restore")
        mgr.nav_forward()
        self.assertEqual(order, ["save", "fwd", "open", "restore"])

    def test_back_does_not_restore_when_nowhere_to_go(self):
        mgr = self._mgr()
        self.nav_history.back.return_value = None
        mgr.nav_back()
        self.restore.assert_not_called()

    def test_push_with_a_position_forwards_it_and_skips_save(self):
        # An anchor jump pushes an entry that already carries its position; it
        # must not run save-on-leave (that would overwrite the from-offset the
        # caller set), and the position is forwarded to nav_history.push.
        mgr = self._mgr()
        mgr.push_history("/a.md", preview_scroll=900.0)
        self.save.assert_not_called()
        self.nav_history.push.assert_called_once_with("/a.md", preview_scroll=900.0)

    def test_plain_push_still_saves_on_leave(self):
        mgr = self._mgr()
        mgr.push_history("/a.md")
        self.save.assert_called_once()
        self.nav_history.push.assert_called_once_with("/a.md")

    def test_reentrant_push_during_nav_open_is_suppressed(self):
        # Opening the target on back/forward fires a tab-change, which calls
        # push_history again. That re-entrant push must be a no-op: otherwise it
        # re-saves the freshly-loaded (scroll 0) position over the target entry
        # we are about to restore, and the note lands at the top.
        mgr = self._mgr()
        calls = []
        self.save.side_effect = lambda: calls.append("save")
        self.nav_history.back.return_value = "/prev.md"

        def on_open(fp, **k):
            calls.append("open")
            mgr.push_history(fp)          # what on_tab_changed does

        self.on_nav.side_effect = on_open
        mgr.nav_back()
        self.assertEqual(calls, ["save", "open"])   # no second save
        self.nav_history.push.assert_not_called()   # no re-entrant push
        self.restore.assert_called_once()           # restore still runs after

    def test_callbacks_are_optional(self):
        mgr = self._mgr(with_fns=False)
        self.nav_history.back.return_value = "/prev.md"
        mgr.push_history("/f.md")   # no save fn → must not crash
        mgr.nav_back()              # no restore fn → must not crash
        self.on_nav.assert_called_once_with("/prev.md", _from_nav=True)


if __name__ == "__main__":
    unittest.main()
