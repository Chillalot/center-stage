import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

HAS_QT = importlib.util.find_spec("PySide6") is not None


class QtGuiTests(unittest.TestCase):
    @unittest.skipUnless(HAS_QT, "PySide6 is not installed")
    def test_offscreen_selftest(self):
        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [sys.executable, "-m", "center_stage.gui_qt", "--selftest"],
            text=True,
            capture_output=True,
            env=env,
            cwd=root,
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Qt selftest OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
