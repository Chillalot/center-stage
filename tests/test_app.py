import io
import os
import unittest
from contextlib import redirect_stdout


class AppEntryPointTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "the GTK entry point is Linux-only")
    def test_linux_entrypoint_forwards_argv(self):
        from center_stage.app import main

        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["center-stage", "--version"])
        self.assertEqual(result, 0)
        self.assertIn("Center Stage 5.0.0", output.getvalue())


if __name__ == "__main__":
    unittest.main()
