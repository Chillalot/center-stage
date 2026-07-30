import unittest

import numpy as np

from center_stage.engine import OUTPUT_FORMAT, raw_bgr_payload, v4l2_output_caps_are_valid


class VirtualOutputTests(unittest.TestCase):
    def test_appsrc_payload_is_tightly_packed_bgr(self):
        frame = np.zeros((6, 10, 3), dtype=np.uint8)[:, 1:9]

        payload = raw_bgr_payload(frame)

        self.assertEqual(len(payload), 6 * 8 * 3)

    def test_virtual_camera_uses_browser_compatible_yuy2(self):
        self.assertEqual(OUTPUT_FORMAT, "YUY2")

    def test_runtime_caps_must_be_capture_1080p30_yuy2(self):
        valid = """
            Device Caps      : 0x04200001
                    Video Capture
            Format Video Capture:
                    Width/Height      : 1920/1080
                    Pixel Format      : 'YUYV' (YUYV 4:2:2)
            Streaming Parameters Video Capture:
                    Frames per second: 30.000 (30/1)
        """
        self.assertTrue(v4l2_output_caps_are_valid(valid))
        self.assertFalse(v4l2_output_caps_are_valid(valid.replace("Video Capture", "Video Output")))
        self.assertFalse(v4l2_output_caps_are_valid(valid.replace("1920/1080", "1280/720")))
        self.assertFalse(v4l2_output_caps_are_valid(valid.replace("'YUYV'", "'NV12'")))
        self.assertFalse(v4l2_output_caps_are_valid(valid.replace("30.000 (30/1)", "25.000 (25/1)")))


if __name__ == "__main__":
    unittest.main()
