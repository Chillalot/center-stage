import unittest

from center_stage.engine import (
    InputCaps,
    OUTPUT_FPS,
    output_timestamps,
    select_input_caps,
)


class EngineCapsTests(unittest.TestCase):
    def test_prefers_full_hd_mjpeg_at_30_fps(self):
        selected = select_input_caps(
            [
                InputCaps("video/x-raw", 640, 480, 30, 1),
                InputCaps("image/jpeg", 1280, 720, 30, 1),
                InputCaps("image/jpeg", 1920, 1080, 30, 1),
            ]
        )
        self.assertEqual((selected.media_type, selected.width, selected.height), ("image/jpeg", 1920, 1080))

    def test_falls_back_to_best_30_fps_mode(self):
        selected = select_input_caps(
            [
                InputCaps("video/x-raw", 1920, 1080, 10, 1),
                InputCaps("image/jpeg", 1280, 720, 25, 1),
                InputCaps("video/x-raw", 640, 480, 30, 1),
            ]
        )
        self.assertEqual((selected.width, selected.height, selected.fps), (640, 480, 30.0))

    def test_output_timestamps_are_continuous(self):
        first_pts, duration = output_timestamps(0)
        second_pts, second_duration = output_timestamps(1)
        self.assertEqual(first_pts, 0)
        self.assertEqual(second_pts, duration)
        self.assertEqual(second_duration, duration)
        self.assertAlmostEqual(duration / 1_000_000_000, 1 / OUTPUT_FPS, places=8)


if __name__ == "__main__":
    unittest.main()
