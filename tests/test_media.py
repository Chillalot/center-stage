import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from center_stage.media import four_up, list_media, save_jpeg, unique_media_path


class MediaTests(unittest.TestCase):
    def test_unique_names_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            now = datetime(2026, 8, 3, 12, 0, 0)
            first = unique_media_path(folder, "Photo", ".jpg", now)
            first.touch()
            second = unique_media_path(folder, "Photo", ".jpg", now)
        self.assertEqual(first.name, "Photo_2026-08-03_12-00-00.jpg")
        self.assertEqual(second.name, "Photo_2026-08-03_12-00-00_2.jpg")

    def test_four_up_layout_and_atomic_jpeg(self):
        frames = [np.full((20, 30, 3), value, np.uint8) for value in (10, 20, 30, 40)]
        collage = four_up(frames)
        self.assertEqual(collage.shape, (40, 60, 3))
        with tempfile.TemporaryDirectory() as directory:
            path = save_jpeg(collage, Path(directory) / "FourUp.jpg")
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(list_media(path.parent), [path])

    def test_four_up_rejects_incomplete_capture(self):
        with self.assertRaises(ValueError):
            four_up([np.zeros((2, 2, 3), np.uint8)])


if __name__ == "__main__":
    unittest.main()
