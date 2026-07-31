import subprocess
import unittest
from pathlib import Path


class DebianPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = Path(__file__).resolve().parent.parent / "dist" / "center-stage_4.1.0_amd64.deb"
        if not cls.package.is_file():
            raise unittest.SkipTest("build dist/center-stage_4.1.0_amd64.deb first")

    def test_package_is_within_release_budget(self):
        self.assertLessEqual(self.package.stat().st_size, 75 * 1024 * 1024)

    def test_package_metadata_and_runtime_are_clean(self):
        metadata = subprocess.run(
            ["dpkg-deb", "--field", str(self.package)],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        contents = subprocess.run(
            ["dpkg-deb", "--contents", str(self.package)],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn("Version: 4.1.0", metadata)
        self.assertIn("gir1.2-ayatanaappindicator3-0.1", metadata)
        self.assertIn("center-stage-active.svg", contents)
        self.assertIn("center-stage-idle.svg", contents)
        self.assertIn("center-stage-warning.svg", contents)
        self.assertNotIn("pipewire-bin", metadata)
        self.assertIn("gstreamer1.0-pipewire", metadata)
        self.assertIn("gstreamer1.0-plugins-bad", metadata)
        self.assertIn("opencv_python_headless-5.0.0.93.dist-info", contents)
        self.assertNotIn("pyvirtualcam", contents)
        self.assertNotIn("opencv_contrib_python", contents)
        self.assertNotIn("/PIL/", contents)


if __name__ == "__main__":
    unittest.main()
