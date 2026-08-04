import ast
import unittest
from pathlib import Path


class GuiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent.parent / "center_stage" / "gui.py").read_text(
            encoding="utf-8"
        )
        cls.tree = ast.parse(cls.source)

    def test_gui_does_not_use_blocking_subprocess_run(self):
        blocking_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "run"
        ]
        self.assertEqual(blocking_calls, [])

    def test_preview_does_not_consume_the_standard_virtual_camera(self):
        self.assertIn("shmsrc socket-path=", self.source)
        self.assertNotIn("v4l2src device=/dev/video10", self.source)
        self.assertNotIn("center-stage.virtual", self.source)
        self.assertNotIn("videoflip method=horizontal-flip", self.source)

    def test_photo_booth_and_advanced_tray_controls_are_present(self):
        for token in (
            "class PhotoBoothWindow",
            "class AppShell",
            "class TrayController",
            '(("photo", N_("Photo")), ("four_up", N_("4-up")), ("video", N_("Video")))',
            "AyatanaAppIndicator3.Indicator.new",
            'self._("Center Stage")',
            'self._("Portrait")',
            'self._("Studio Light")',
            'N_("Reactions")',
            'N_("Background")',
            'self._("Microphone")',
            'self._("Mute Microphone")',
            'self._("Configure…")',
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("_build_calls_page", self.source)
        self.assertNotIn("_build_studio_page", self.source)

    def test_language_change_does_not_replace_window_or_indicator(self):
        self.assertIn("def _retranslate_in_place", self.source)
        self.assertNotIn("reload_window", self.source)
        self.assertEqual(self.source.count("AyatanaAppIndicator3.Indicator.new"), 1)

    def test_effects_and_image_adjustments_use_tray_popups(self):
        for token in (
            "class TraySliderPopup",
            "_append_effect_controls",
            'self._("Background Blur")',
            'self._("Virtual Light")',
            "_show_image_adjustments_popup",
            "present_at_pointer",
            "Gtk.Scale.new_with_range",
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("_scale_menu_item", self.source)
        self.assertNotIn("_build_effect_submenu", self.source)
        self.assertNotIn("Gtk.Dialog(title=self._(\"Image Adjustments\")", self.source)

    def test_main_window_has_no_start_stop_button(self):
        self.assertNotIn('Gtk.Button.new_with_label(self._("Start"))', self.source)
        self.assertNotIn('Gtk.Button.new_with_label(self._("Stop"))', self.source)

    def test_close_hides_and_tray_quit_stops_service(self):
        self.assertIn('self.connect("delete-event", self._on_delete_event)', self.source)
        self.assertIn('self._service_action("stop", self._finish_quit)', self.source)

    def test_slim_capture_bar_and_collapsible_album_are_present(self):
        for token in (
            'MODE_ICONS = {',
            '"four_up": "view-grid-symbolic"',
            'self.capture_overlay.set_size_request(-1, 58)',
            'self.gallery_revealer = Gtk.Revealer()',
            'Gtk.Orientation.HORIZONTAL, self.filmstrip_frame.get_hadjustment()',
            'self.capture_icon_stack.set_transition_duration(160)',
            'style.add_class("active")',
        ):
            self.assertIn(token, self.source)

    def test_theme_is_in_window_and_existing_tray_menu(self):
        for token in (
            'self.theme_button = Gtk.Button()',
            'theme = Gtk.MenuItem.new_with_label(self._("Theme"))',
            'N_("Light Theme")',
            'N_("Dark Theme")',
            'gtk-application-prefer-dark-theme',
        ):
            self.assertIn(token, self.source)
        self.assertEqual(self.source.count("AyatanaAppIndicator3.Indicator.new"), 1)

    def test_video_countdown_timer_and_duration_thumbnail_are_present(self):
        for token in (
            'self._countdown(3, self._start_recording)',
            'self.recording_time_label = Gtk.Label(label="● 00:00")',
            'GLib.timeout_add(250, self._recording_timer_tick)',
            'preview = media_preview(path)',
            'duration.get_style_context().add_class("video-duration")',
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("media-playback-start-symbolic", self.source)

    def test_tray_configure_popup_has_separate_picture_and_video_paths(self):
        for token in (
            "class MediaFoldersPopup",
            '"pictures_folder"',
            '"videos_folder"',
            'self._("Pictures")',
            'self._("Videos")',
            'self._("Choose the folder where Center Stage will save videos")',
            "list_media_folders(self.media_library.folders)",
        ):
            self.assertIn(token, self.source)


if __name__ == "__main__":
    unittest.main()
