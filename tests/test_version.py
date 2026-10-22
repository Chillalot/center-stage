import unittest
from pathlib import Path

from center_stage import __version__


class VersionTests(unittest.TestCase):
    def test_version_file_matches_runtime(self):
        root = Path(__file__).resolve().parent.parent
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), __version__)

    def test_build_script_reads_the_version_file(self):
        root = Path(__file__).resolve().parent.parent
        script = (root / "packaging" / "build_deb.sh").read_text(encoding="utf-8")
        self.assertIn('PACKAGE_VERSION=$(tr -d \'[:space:]\' <"$PROJECT_DIR/VERSION")', script)
        self.assertNotRegex(script, r"PACKAGE_VERSION=\d+\.\d+\.\d+")

    def test_version_is_semver(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


if __name__ == "__main__":
    unittest.main()
