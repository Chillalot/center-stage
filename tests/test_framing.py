import unittest

import numpy as np

from center_stage.framing import (
    BASE_ZOOM,
    LOST_HOLD_SECONDS,
    AutoFramer,
    FaceBox,
    CropWindow,
    crop_and_resize,
    select_primary_face,
)


class FramingTests(unittest.TestCase):
    def test_selects_largest_face_without_history(self):
        small = FaceBox(0, 0, 20, 20)
        large = FaceBox(100, 100, 80, 80)
        self.assertIs(select_primary_face([small, large], None), large)

    def test_selects_nearest_face_with_history(self):
        previous = FaceBox(100, 100, 40, 40)
        near = FaceBox(105, 100, 40, 40)
        far = FaceBox(800, 300, 100, 100)
        self.assertIs(select_primary_face([far, near], previous), near)

    def test_crop_always_stays_inside_frame(self):
        framer = AutoFramer(1920, 1080, auto_zoom=True)
        face = FaceBox(1850, 1000, 60, 60)
        now = 1.0
        for _ in range(120):
            snapshot = framer.update([face], True, now)
            now += 1 / 30
        crop = snapshot.crop
        self.assertGreaterEqual(crop.x, 0)
        self.assertGreaterEqual(crop.y, 0)
        self.assertLessEqual(crop.x + crop.width, 1920)
        self.assertLessEqual(crop.y + crop.height, 1080)

    def test_pan_only_keeps_base_zoom(self):
        framer = AutoFramer(1920, 1080, auto_zoom=False)
        snapshot = framer.update([FaceBox(800, 300, 100, 100)], True, 1.0)
        self.assertEqual(snapshot.zoom, BASE_ZOOM)

    def test_short_face_loss_holds_then_recenters(self):
        framer = AutoFramer(1920, 1080, auto_zoom=True)
        framer.update([FaceBox(800, 300, 100, 100)], True, 1.0)
        held = framer.update([], True, 1.0 + LOST_HOLD_SECONDS / 2)
        recentered = framer.update([], True, 1.0 + LOST_HOLD_SECONDS + 0.1)
        self.assertEqual(held.status, "HOLD")
        self.assertEqual(recentered.status, "RECENTERING")

    def test_720p_input_is_resized_to_fixed_1080p_output(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        output = crop_and_resize(frame, CropWindow(0, 0, 1280, 720), 1920, 1080)
        self.assertEqual(output.shape, (1080, 1920, 3))


if __name__ == "__main__":
    unittest.main()
