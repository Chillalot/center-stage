import json
import tempfile
import unittest
from pathlib import Path

from center_stage.config import (
    camera_control_values,
    load_config,
    save_config,
    set_camera_control_value,
)


class ConfigTests(unittest.TestCase):
    def test_english_is_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(Path(directory) / "missing.json")
        self.assertEqual(config["language"], "en")

    def test_invalid_language_falls_back_to_english(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"language": "fr"}), encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config["language"], "en")

    def test_controls_are_saved_per_camera_and_clamped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = load_config(path)
            set_camera_control_value(config, "camera-a", "brightness", 150)
            set_camera_control_value(config, "camera-b", "brightness", -25)
            save_config(config, path)
            loaded = load_config(path)
        self.assertEqual(camera_control_values(loaded, "camera-a")["brightness"], 100)
        self.assertEqual(camera_control_values(loaded, "camera-b")["brightness"], -25)


if __name__ == "__main__":
    unittest.main()
