import subprocess
import unittest
from pathlib import Path

from center_stage import __version__


class DebianPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = (
            Path(__file__).resolve().parent.parent
            / "dist"
            / f"center-stage_{__version__}_amd64.deb"
        )
        if not cls.package.is_file():
            raise unittest.SkipTest(f"build {cls.package} first")

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
        self.assertIn(f"Version: {__version__}", metadata)
        self.assertIn("gir1.2-ayatanaappindicator3-0.1", metadata)
        self.assertIn("center-stage-active.svg", contents)
        self.assertIn("center-stage-idle.svg", contents)
        self.assertIn("center-stage-warning.svg", contents)
        self.assertIn("apps/center-stage.png", contents)
        self.assertIn("selfie_segmenter_landscape.tflite", contents)
        self.assertNotIn("pipewire-bin", metadata)
        self.assertIn("gstreamer1.0-pipewire", metadata)
        self.assertIn("gstreamer1.0-plugins-bad", metadata)
        self.assertIn("gstreamer1.0-plugins-ugly", metadata)
        self.assertIn("pulseaudio-utils", metadata)
        self.assertIn("opencv_python_headless-5.0.0.93.dist-info", contents)
        self.assertNotIn("pyvirtualcam", contents)
        self.assertNotIn("opencv_contrib_python", contents)
        self.assertNotIn("/PIL/", contents)


if __name__ == "__main__":
    unittest.main()
