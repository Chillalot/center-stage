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

    def test_workspace_and_tray_controls_are_present(self):
        for token in (
            'add_titled(self._build_camera_page(), "camera"',
            'add_titled(self._build_studio_page(), "studio"',
            'add_titled(self._build_calls_page(), "calls"',
            "AyatanaAppIndicator3.Indicator.new",
            'self._("Center Stage")',
            'N_("Portrait")',
            'N_("Studio Light")',
            'N_("Reactions")',
            'N_("Background")',
        ):
            self.assertIn(token, self.source)

    def test_main_window_has_no_start_stop_button(self):
        self.assertNotIn('Gtk.Button.new_with_label(self._("Start"))', self.source)
        self.assertNotIn('Gtk.Button.new_with_label(self._("Stop"))', self.source)

    def test_close_hides_and_tray_quit_stops_service(self):
        self.assertIn('self.connect("delete-event", self._on_delete_event)', self.source)
        self.assertIn('self._service_action("stop", self._finish_quit)', self.source)


if __name__ == "__main__":
    unittest.main()
