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


if __name__ == "__main__":
    unittest.main()
