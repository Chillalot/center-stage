import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from center_stage import platform_paths
from center_stage.devices import CameraDevice, discover_cameras_windows, discover_microphones_windows


class PlatformPathTests(unittest.TestCase):
    def test_linux_config_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = platform_paths.config_dir({"CENTER_STAGE_CONFIG_DIR": directory})
        self.assertEqual(path, Path(directory))

    def test_linux_runtime_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = platform_paths.runtime_dir({"CENTER_STAGE_RUNTIME_DIR": directory})
        self.assertEqual(path, Path(directory))

    def test_linux_state_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = platform_paths.state_dir({"CENTER_STAGE_STATE_DIR": directory})
        self.assertEqual(path, Path(directory))

    def test_bundled_roots_include_project_root(self):
        roots = platform_paths.bundled_roots()
        self.assertIn(Path(__file__).resolve().parent.parent, roots)

    def test_default_output_device_is_platform_specific(self):
        expected = "obs" if os.name == "nt" else "/dev/video10"
        self.assertEqual(platform_paths.default_output_device(), expected)


class WindowsImportSafetyTests(unittest.TestCase):
    def test_engine_and_media_import_without_pygobject(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory)
            (blocked / "gi.py").write_text("raise ImportError('blocked gi')\n", encoding="utf-8")
            (blocked / "fcntl.py").write_text("raise ImportError('blocked fcntl')\n", encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join((str(blocked), str(root)))
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import center_stage.engine as e; import center_stage.media as m; "
                    "print(e.GST_AVAILABLE, m.GST_AVAILABLE)",
                ],
                text=True,
                capture_output=True,
                env=env,
                cwd=root,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "False False")


class WindowsDeviceTests(unittest.TestCase):
    def test_windows_camera_infos_are_normalized(self):
        fake = types.ModuleType("cv2_enumerate_cameras")
        fake.enumerate_cameras = lambda _backend: [
            types.SimpleNamespace(index=0, name="Integrated Camera", path="\\\\?\\usb#0", vid="1234", pid="5678"),
            types.SimpleNamespace(index=1, name="USB Camera", path="\\\\?\\usb#1", vid="abcd", pid="ef01"),
        ]
        with mock.patch.dict(sys.modules, {"cv2_enumerate_cameras": fake}):
            cameras = discover_cameras_windows()
        self.assertEqual([camera.camera_id for camera in cameras], ["windows-0", "windows-1"])
        self.assertEqual(cameras[1].name, "USB Camera")
        self.assertIsInstance(cameras[0], CameraDevice)

    def test_windows_microphones_use_sounddevice(self):
        fake = types.ModuleType("sounddevice")
        fake.default = types.SimpleNamespace(device=(1, 0))
        fake.query_devices = lambda: [
            {"name": "Speakers", "max_input_channels": 0},
            {"name": "USB Microphone", "max_input_channels": 2},
            {"name": "Laptop Microphone", "max_input_channels": 1},
        ]
        with mock.patch.dict(sys.modules, {"sounddevice": fake}):
            microphones = discover_microphones_windows()
        self.assertEqual([item.name for item in microphones], ["USB Microphone", "Laptop Microphone"])
        self.assertEqual(microphones[0].microphone_id, "1")


if __name__ == "__main__":
    unittest.main()
