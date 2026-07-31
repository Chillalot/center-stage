import subprocess
import tempfile
import unittest
from pathlib import Path

from center_stage.devices import (
    CameraControl,
    CameraDevice,
    discover_cameras,
    preferred_camera,
    query_controls,
)


class DeviceTests(unittest.TestCase):
    def test_prefers_external_camera_then_integrated_fallback(self):
        integrated = CameraDevice("built-in", "/dev/video0", "/dev/video0", "Built-in", "a", "integrated")
        usb = CameraDevice("usb", "/dev/video2", "/dev/video2", "USB", "b", "usb")
        self.assertIs(preferred_camera([integrated, usb]), usb)
        self.assertIs(preferred_camera([integrated]), integrated)
        self.assertIsNone(preferred_camera([]))

    def test_normalized_mapping_uses_hardware_default_as_zero(self):
        control = CameraControl("saturation", 0, 255, 1, 64, 64)
        self.assertEqual(control.from_normalized(-100), 0)
        self.assertEqual(control.from_normalized(0), 64)
        self.assertEqual(control.from_normalized(100), 255)
        self.assertEqual(control.to_normalized(64), 0)

    def test_parses_supported_controls(self):
        output = """
 brightness 0x00980900 (int) : min=-64 max=64 step=1 default=0 value=5
 contrast 0x00980901 (int) : min=0 max=64 step=1 default=32 value=32
"""

        def runner(_arguments):
            return subprocess.CompletedProcess([], 0, stdout=output, stderr="")

        controls = query_controls("/dev/video2", runner=runner)
        self.assertEqual(controls["brightness"].minimum, -64)
        self.assertEqual(controls["brightness"].value, 5)
        self.assertEqual(controls["contrast"].default, 32)

    def test_discovers_integrated_and_external_cameras(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            by_id = root / "by-id"
            sys_class = root / "sys-class"
            dev = root / "dev"
            by_id.mkdir()
            sys_class.mkdir()
            dev.mkdir()

            fixtures = (
                ("integrated-video-index0", "video0", "Integrated Camera", "fixed"),
                ("external-video-index0", "video2", "EMEET SmartCam C100", "removable"),
                ("metadata-video-index0", "video3", "Metadata", "removable"),
            )
            for camera_id, video_name, product, removable in fixtures:
                device_file = dev / video_name
                device_file.touch()
                (by_id / camera_id).symlink_to(device_file)
                usb = root / f"usb-{video_name}"
                usb.mkdir()
                (usb / "product").write_text(product, encoding="utf-8")
                (usb / "serial").write_text(video_name, encoding="utf-8")
                (usb / "removable").write_text(removable, encoding="utf-8")
                video_sys = sys_class / video_name
                video_sys.mkdir()
                (video_sys / "device").symlink_to(usb)
                (video_sys / "name").write_text(product, encoding="utf-8")

            def runner(arguments):
                device = Path(arguments[2]).resolve().name
                caps = "Metadata Capture" if device == "video3" else "Video Capture"
                output = f"Device Caps      : 0x0\n\t\t{caps}\nMedia Driver Info:\n"
                return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

            devices = discover_cameras(by_id, sys_class, runner)

        self.assertEqual([device.name for device in devices], ["EMEET SmartCam C100", "Integrated Camera"])
        kinds = {device.name: device.kind for device in devices}
        self.assertEqual(kinds["Integrated Camera"], "integrated")
        self.assertEqual(kinds["EMEET SmartCam C100"], "usb")


if __name__ == "__main__":
    unittest.main()
