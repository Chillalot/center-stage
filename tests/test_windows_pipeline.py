import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from center_stage.devices import CameraDevice
from center_stage.engine import InputCaps
from center_stage.engine_win import WindowsPipeline


class FakeCapture:
    def __init__(self, _camera):
        self.closed = False
        self.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.frame[:, :, 1] = 120

    def start(self):
        return self.frame.copy(), InputCaps("video/x-raw", 1280, 720, 30, 1)

    def read(self, timeout_ns=0):
        del timeout_ns
        time.sleep(0.01)
        return self.frame.copy()

    def close(self):
        self.closed = True


class FakeOutput:
    instances = []

    def __init__(self):
        self.frames = 0
        self.closed = False
        FakeOutput.instances.append(self)

    def start(self, first_frame):
        del first_frame

    def send(self, frame):
        self.frames += 1
        if frame.shape != (1080, 1920, 3):
            raise AssertionError(frame.shape)

    def close(self):
        self.closed = True


class FakeDetector:
    def close(self):
        pass


class WindowsPipelineTests(unittest.TestCase):
    def test_pipeline_emits_preview_status_and_cleans_up(self):
        camera = CameraDevice("windows-0", "0", "0", "Fake Camera", "0", "usb")
        previews = []
        statuses = []
        errors = []
        config = {
            "center_stage_enabled": False,
            "mirror_output": False,
            "portrait_enabled": False,
            "studio_light_enabled": False,
        }
        pipeline = WindowsPipeline(
            config,
            camera,
            on_preview=previews.append,
            on_status=statuses.append,
            on_error=lambda code, message: errors.append((code, message)),
            publish_status=False,
        )
        with mock.patch("center_stage.engine_win.WindowsCameraCapture", FakeCapture), \
             mock.patch("center_stage.engine_win.ObsVirtualOutput", FakeOutput), \
             mock.patch("center_stage.engine_win.create_face_detector", return_value=FakeDetector()), \
             mock.patch("center_stage.engine_win.detect_faces", return_value=[]), \
             mock.patch("center_stage.engine_win.model_path", return_value=Path("fake.tflite")), \
             mock.patch("center_stage.engine_win.apply_saved_controls", return_value={}), \
             mock.patch("center_stage.engine_win.load_config", return_value=config):
            thread = threading.Thread(target=pipeline.run)
            thread.start()
            deadline = time.monotonic() + 3.0
            while not previews and time.monotonic() < deadline:
                time.sleep(0.02)
            snapshot = pipeline.latest_frame()
            pipeline.stop()
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertTrue(previews)
        self.assertEqual(previews[0].shape[2], 3)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.shape, (1080, 1920, 3))
        self.assertTrue(any(item.get("capture_backend") == "msmf" for item in statuses))
        self.assertEqual(errors, [])
        self.assertTrue(FakeOutput.instances[-1].closed)

    def test_pipeline_reports_missing_obs_but_keeps_previewing(self):
        camera = CameraDevice("windows-0", "0", "0", "Fake Camera", "0", "usb")
        errors = []
        config = {"center_stage_enabled": False, "mirror_output": False}

        class MissingOutput:
            def __init__(self):
                pass

            def start(self, first_frame):
                del first_frame
                from center_stage.engine import EngineError

                raise EngineError("virtual_camera_missing", "OBS is not installed")

            def close(self):
                pass

        pipeline = WindowsPipeline(
            config,
            camera,
            on_error=lambda code, message: errors.append((code, message)),
            publish_status=False,
        )
        with mock.patch("center_stage.engine_win.WindowsCameraCapture", FakeCapture), \
             mock.patch("center_stage.engine_win.ObsVirtualOutput", MissingOutput), \
             mock.patch("center_stage.engine_win.create_face_detector", return_value=FakeDetector()), \
             mock.patch("center_stage.engine_win.detect_faces", return_value=[]), \
             mock.patch("center_stage.engine_win.model_path", return_value=Path("fake.tflite")), \
             mock.patch("center_stage.engine_win.apply_saved_controls", return_value={}), \
             mock.patch("center_stage.engine_win.load_config", return_value=config):
            thread = threading.Thread(target=pipeline.run)
            thread.start()
            time.sleep(0.4)
            pipeline.stop()
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertTrue(any(code == "virtual_camera_missing" for code, _ in errors))


if __name__ == "__main__":
    unittest.main()
