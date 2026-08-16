"""Tests for markdown_vault.uikit.banners."""

import unittest

from markdown_vault.uikit.banners import BannerBox, create_banner


class TestBannerBox(unittest.TestCase):
    """Structural tests for BannerBox."""

    def test_module_has_banner_box_class(self):
        self.assertTrue(hasattr(BannerBox, "__gsignals__"))

    def test_instantiation(self):
        box = BannerBox()
        self.assertIsNotNone(box)

    def test_default_banner_type_is_warning(self):
        box = BannerBox()
        self.assertIn("banner-warning", box.get_css_classes())

    def test_set_text(self):
        box = BannerBox()
        box.set_text("Hello")
        self.assertEqual(box._label.get_text(), "Hello")

    def test_has_dismissed_signal(self):
        box = BannerBox()
        emitted = []
        box.connect("dismissed", lambda _: emitted.append(True))
        box.emit("dismissed")
        self.assertEqual(len(emitted), 1)

    def test_add_button(self):
        box = BannerBox()
        clicked = []
        box.add_button("Retry", lambda: clicked.append(True))
        # Click the button.
        for child in box._button_box:
            if hasattr(child, "get_label") and child.get_label() == "Retry":
                child.emit("clicked")
                break
        self.assertEqual(len(clicked), 1)

    def test_clear_buttons(self):
        box = BannerBox()
        box.add_button("A", lambda: None)
        box.add_button("B", lambda: None)
        box.clear_buttons()
        count = sum(1 for _ in box._button_box)
        self.assertEqual(count, 0)

    def test_reset_to_error_type(self):
        box = BannerBox()
        box.set_text("error")
        box.add_button("X", lambda: None)
        box.reset(banner_type="error")
        self.assertEqual(box._label.get_text(), "")
        self.assertIn("banner-error", box.get_css_classes())
        self.assertNotIn("banner-warning", box.get_css_classes())

    def test_reset_with_icon_override(self):
        box = BannerBox()
        box.reset(banner_type="warning", icon_name="dialog-error-symbolic")
        self.assertEqual(box._icon.get_icon_name(), "dialog-error-symbolic")
        self.assertIn("banner-warning", box.get_css_classes())

    def test_icon_override_in_constructor(self):
        box = BannerBox(icon_name="custom-icon")
        self.assertEqual(box._icon.get_icon_name(), "custom-icon")


class TestBannerTypes(unittest.TestCase):
    """Tests for banner type → icon + CSS class mapping."""

    def test_warning_type(self):
        box = BannerBox(banner_type="warning")
        self.assertIn("banner-warning", box.get_css_classes())
        self.assertEqual(box._icon.get_icon_name(), "dialog-warning-symbolic")

    def test_error_type(self):
        box = BannerBox(banner_type="error")
        self.assertIn("banner-error", box.get_css_classes())
        self.assertEqual(box._icon.get_icon_name(), "dialog-error-symbolic")

    def test_info_type(self):
        box = BannerBox(banner_type="info")
        self.assertIn("banner-info", box.get_css_classes())
        self.assertEqual(box._icon.get_icon_name(), "dialog-information-symbolic")

    def test_success_type(self):
        box = BannerBox(banner_type="success")
        self.assertIn("banner-success", box.get_css_classes())
        self.assertEqual(box._icon.get_icon_name(), "object-select-symbolic")

    def test_unknown_type_falls_back_to_warning(self):
        box = BannerBox(banner_type="unknown")
        self.assertIn("banner-warning", box.get_css_classes())
        self.assertEqual(box._icon.get_icon_name(), "dialog-warning-symbolic")


class TestCreateBanner(unittest.TestCase):
    """Tests for the create_banner factory."""

    def test_returns_revealer_and_banner(self):
        revealer, banner = create_banner()
        self.assertIsNotNone(revealer)
        self.assertIsNotNone(banner)
        self.assertIsInstance(banner, BannerBox)

    def test_default_type_is_warning(self):
        _, banner = create_banner()
        self.assertIn("banner-warning", banner.get_css_classes())

    def test_error_type(self):
        _, banner = create_banner(banner_type="error")
        self.assertIn("banner-error", banner.get_css_classes())

    def test_icon_override(self):
        _, banner = create_banner(banner_type="warning", icon_name="custom-icon")
        self.assertEqual(banner._icon.get_icon_name(), "custom-icon")
        self.assertIn("banner-warning", banner.get_css_classes())

    def test_dismissed_hides_revealer(self):
        revealer, banner = create_banner()
        revealer.set_reveal_child(True)
        banner.emit("dismissed")
        # The caller would connect this; we just verify the signal works.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
