import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from center_stage.i18n import translator


class TranslationTests(unittest.TestCase):
    def test_vietnamese_catalog_is_complete_and_valid(self):
        required = ("xgettext", "msgcmp", "msgfmt")
        if any(shutil.which(command) is None for command in required):
            self.skipTest("GNU gettext tools are not installed")
        project = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory) / "messages.pot"
            subprocess.run(
                [
                    "xgettext",
                    "--language=Python",
                    "--keyword=_",
                    "--keyword=N_",
                    "--from-code=UTF-8",
                    f"--output={extracted}",
                    str(project / "center_stage" / "gui.py"),
                ],
                check=True,
            )
            subprocess.run(
                ["msgcmp", "--use-fuzzy", str(project / "po" / "vi.po"), str(extracted)],
                check=True,
            )
            subprocess.run(
                ["msgfmt", "--check", "--check-format", "-o", str(Path(directory) / "vi.mo"), str(project / "po" / "vi.po")],
                check=True,
            )

    def test_vietnamese_translation_loads(self):
        if shutil.which("msgfmt") is None:
            self.skipTest("GNU gettext tools are not installed")
        project = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            locale_root = Path(directory)
            messages = locale_root / "vi" / "LC_MESSAGES"
            messages.mkdir(parents=True)
            subprocess.run(
                ["msgfmt", "-o", str(messages / "center-stage.mo"), str(project / "po" / "vi.po")],
                check=True,
            )
            translate = translator("vi", [locale_root])
            self.assertEqual(translate("Start"), "Bắt đầu")
            self.assertEqual(translate("External USB camera"), "Webcam USB rời")


if __name__ == "__main__":
    unittest.main()
