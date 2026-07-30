import os
import subprocess
import threading
import time
import unittest

import numpy as np

from center_stage.devices import discover_cameras
from center_stage.engine import (
    OUTPUT_FORMAT,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    PipeWireCameraCapture,
    V4L2VirtualOutput,
    v4l2_output_caps_are_valid,
)
from center_stage.config import preview_socket_path


INTEGRATION_ENABLED = os.environ.get("CENTER_STAGE_INTEGRATION_TESTS") == "1"
HARDWARE_ENABLED = os.environ.get("CENTER_STAGE_HARDWARE_TESTS") == "1"
def invocation_id(service: str) -> str:
    return subprocess.run(
        ["systemctl", "--user", "show", "--property=InvocationID", "--value", service],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()


@unittest.skipUnless(INTEGRATION_ENABLED, "set CENTER_STAGE_INTEGRATION_TESTS=1")
class GStreamerIntegrationTests(unittest.TestCase):
    def test_private_preview_and_v4l2_consumer_run_together(self):
        frame = np.full((OUTPUT_HEIGHT, OUTPUT_WIDTH, 3), 72, dtype=np.uint8)
        output = V4L2VirtualOutput("/dev/video10")
        stop = threading.Event()
        worker = None

        def producer():
            while not stop.wait(1 / 30):
                output.send(frame)

        try:
            output.start(frame)
            worker = threading.Thread(target=producer, daemon=True)
            worker.start()
            preview = subprocess.Popen(
                [
                    "gst-launch-1.0", "-q", "shmsrc",
                    f"socket-path={preview_socket_path()}", "is-live=true",
                    "do-timestamp=true", "num-buffers=90", "!",
                    f"video/x-raw,format={OUTPUT_FORMAT},width=1920,height=1080,framerate=30/1",
                    "!", "fakesink",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            consumer = subprocess.Popen(
                [
                    "gst-launch-1.0", "-q", "v4l2src", "device=/dev/video10",
                    "num-buffers=90", "!",
                    f"video/x-raw,format={OUTPUT_FORMAT},width=1920,height=1080,framerate=30/1",
                    "!", "fakesink",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for process in (preview, consumer):
                _stdout, stderr = process.communicate(timeout=12)
                self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
        finally:
            stop.set()
            if worker is not None:
                worker.join(timeout=1)
            output.close()

    def test_v4l2_output_caps_consumer_and_no_pipewire_restart(self):
        with open(
            "/sys/module/v4l2loopback/parameters/max_buffers", encoding="utf-8"
        ) as parameters:
            max_buffers = int(parameters.read().strip())
        if max_buffers < 8:
            self.skipTest("reboot after installing V4.0.5 to apply v4l2loopback max_buffers=8")
        before = {name: invocation_id(name) for name in ("pipewire.service", "wireplumber.service")}
        frame = np.full((OUTPUT_HEIGHT, OUTPUT_WIDTH, 3), 48, dtype=np.uint8)
        output = V4L2VirtualOutput("/dev/video10")
        stop = threading.Event()
        worker = None

        def producer():
            position = 0
            while not stop.wait(1 / 30):
                frame[:, :, 1] = 48
                frame[100:220, position : position + 120, 1] = 220
                position = (position + 20) % (OUTPUT_WIDTH - 120)
                output.send(frame)

        try:
            output.start(frame)
            worker = threading.Thread(target=producer, daemon=True)
            worker.start()
            details = subprocess.run(
                ["v4l2-ctl", "-d", "/dev/video10", "-D", "--get-fmt-video", "--get-parm"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertTrue(v4l2_output_caps_are_valid(details.stdout))

            consumer = subprocess.run(
                [
                    "gst-launch-1.0", "-q", "v4l2src", "device=/dev/video10",
                    "num-buffers=90", "!",
                    f"video/x-raw,format={OUTPUT_FORMAT},width=1920,height=1080,framerate=30/1",
                    "!", "fakesink",
                ],
                capture_output=True,
                timeout=12,
                check=False,
            )
            self.assertEqual(
                consumer.returncode,
                0,
                consumer.stderr.decode(errors="replace"),
            )

            payload = subprocess.run(
                ["pw-dump"], text=True, capture_output=True, check=False
            ).stdout
            self.assertNotIn("center-stage.virtual", payload)
            self.assertNotIn("center-stage.v4l2", payload)
        finally:
            stop.set()
            if worker is not None:
                worker.join(timeout=1)
            output.close()
        after = {name: invocation_id(name) for name in before}
        self.assertEqual(after, before)

    def test_ten_start_stop_cycles(self):
        frame = np.full((OUTPUT_HEIGHT, OUTPUT_WIDTH, 3), 64, dtype=np.uint8)
        for _cycle in range(10):
            output = V4L2VirtualOutput("/dev/video10")
            stop = threading.Event()
            worker = None

            def producer():
                while not stop.wait(1 / 30):
                    output.send(frame)

            try:
                output.start(frame)
                worker = threading.Thread(target=producer, daemon=True)
                worker.start()
                consumer = subprocess.run(
                    [
                        "gst-launch-1.0", "-q", "v4l2src", "device=/dev/video10",
                        "num-buffers=3", "!", "fakesink",
                    ],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(
                    consumer.returncode,
                    0,
                    consumer.stderr.decode(errors="replace"),
                )
            finally:
                stop.set()
                if worker is not None:
                    worker.join(timeout=1)
                output.close()
            details = subprocess.run(
                [
                    "v4l2-ctl", "-d", "/dev/video10", "-D",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertIn("Video Output", details.stdout)


@unittest.skipUnless(HARDWARE_ENABLED, "set CENTER_STAGE_HARDWARE_TESTS=1")
class CameraSharingHardwareTests(unittest.TestCase):
    def test_all_cameras_capture_through_pipewire(self):
        cameras = discover_cameras()
        self.assertGreaterEqual(len(cameras), 2)
        for camera in cameras:
            capture = PipeWireCameraCapture(camera)
            try:
                frame, _mode = capture.start()
                self.assertGreater(frame.size, 0)
            finally:
                capture.close()

    def test_two_pipewire_consumers_share_emeet(self):
        camera = next(item for item in discover_cameras() if "EMEET" in item.name)
        first = PipeWireCameraCapture(camera)
        second = PipeWireCameraCapture(camera)
        try:
            first.start()
            second.start()
            first_frames = sum(first.read(200_000_000) is not None for _ in range(30))
            second_frames = sum(second.read(200_000_000) is not None for _ in range(30))
            self.assertGreaterEqual(first_frames, 25)
            self.assertGreaterEqual(second_frames, 25)
        finally:
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
