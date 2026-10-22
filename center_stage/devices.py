"""V4L2 camera discovery and normalized hardware image controls."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .config import CONTROL_NAMES

CONTROL_LINE = re.compile(
    r"^\s*(brightness|contrast|saturation|hue)\s+\S+\s+\(int\)\s+:\s+"
    r"min=(-?\d+)\s+max=(-?\d+)\s+step=(\d+)\s+default=(-?\d+)\s+value=(-?\d+)"
)


@dataclass(frozen=True)
class CameraControl:
    name: str
    minimum: int
    maximum: int
    step: int
    default: int
    value: int

    def from_normalized(self, normalized: int) -> int:
        normalized = max(-100, min(100, int(normalized)))
        if normalized < 0:
            raw = self.default + (self.default - self.minimum) * normalized / 100.0
        else:
            raw = self.default + (self.maximum - self.default) * normalized / 100.0
        stepped = self.minimum + round((raw - self.minimum) / self.step) * self.step
        return max(self.minimum, min(self.maximum, int(stepped)))

    def to_normalized(self, raw: int) -> int:
        raw = max(self.minimum, min(self.maximum, int(raw)))
        if raw < self.default:
            span = self.default - self.minimum
            return -round((self.default - raw) * 100 / span) if span else 0
        span = self.maximum - self.default
        return round((raw - self.default) * 100 / span) if span else 0


@dataclass(frozen=True)
class CameraDevice:
    camera_id: str
    path: str
    resolved_path: str
    name: str
    serial: str
    kind: str


@dataclass(frozen=True)
class MicrophoneDevice:
    microphone_id: str
    name: str


def discover_microphones(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> list[MicrophoneDevice]:
    """Discover real Pulse/PipeWire microphone sources, excluding output monitors."""
    if os.name == "nt":
        return discover_microphones_windows()
    result = (runner or _run)(["pactl", "list", "short", "sources"])
    if result.returncode != 0:
        return []
    devices: list[MicrophoneDevice] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            fields = line.split()
        if len(fields) < 2:
            continue
        source = fields[1]
        if source.endswith(".monitor"):
            continue
        label = source.replace("alsa_input.", "").replace(".mono-fallback", "")
        label = label.replace("_", " ").replace("-", " ").strip()
        devices.append(MicrophoneDevice(source, label or source))
    return devices


def preferred_microphone(
    microphones: Iterable[MicrophoneDevice], selected_id: str | None = None
) -> MicrophoneDevice | None:
    available = list(microphones)
    if selected_id:
        selected = next((item for item in available if item.microphone_id == selected_id), None)
        if selected is not None:
            return selected
    return available[0] if available else None


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, text=True, capture_output=True, check=False)


def discover_cameras(
    by_id_dir: Path = Path("/dev/v4l/by-id"),
    sys_class_dir: Path = Path("/sys/class/video4linux"),
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
) -> list[CameraDevice]:
    if os.name == "nt":
        return discover_cameras_windows()
    candidates: Iterable[Path]
    if by_id_dir.is_dir():
        candidates = sorted(by_id_dir.glob("*-video-index0"))
    else:
        candidates = sorted(Path("/dev").glob("video*"))

    devices: list[CameraDevice] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if str(resolved) in seen:
            continue
        details = runner(["v4l2-ctl", "-d", str(candidate), "--all"])
        output = f"{details.stdout}\n{details.stderr}"
        if details.returncode != 0 or "Video Capture" not in output:
            continue
        if "v4l2 loopback" in output.lower() or "Metadata Capture" in _device_caps(output):
            continue

        video_name = resolved.name
        sys_device = sys_class_dir / video_name / "device"
        product, serial, removable = _usb_identity(sys_device)
        fallback_name = _read_text(sys_class_dir / video_name / "name") or video_name
        name = product or fallback_name.split(":", 1)[0]
        kind = "integrated" if removable == "fixed" else "usb"
        camera_id = candidate.name if by_id_dir.is_dir() else f"path-{resolved.name}"
        devices.append(
            CameraDevice(
                camera_id=camera_id,
                path=str(candidate),
                resolved_path=str(resolved),
                name=name,
                serial=serial or camera_id,
                kind=kind,
            )
        )
        seen.add(str(resolved))
    return devices


def discover_cameras_windows() -> list[CameraDevice]:
    """Enumerate DirectShow/Media Foundation cameras without opening a stream."""
    try:
        import cv2

        from cv2_enumerate_cameras import enumerate_cameras
    except ImportError:
        return _probe_windows_cameras()

    devices: list[CameraDevice] = []
    try:
        infos = list(enumerate_cameras(cv2.CAP_MSMF))
    except Exception:
        infos = []
    for info in infos:
        index = int(getattr(info, "index", 0))
        name = str(getattr(info, "name", "") or f"Camera {index}")
        path = str(getattr(info, "path", "") or index)
        serial = str(getattr(info, "pid", "") or getattr(info, "vid", "") or index)
        devices.append(
            CameraDevice(
                camera_id=f"windows-{index}",
                path=str(index),
                resolved_path=str(index),
                name=name,
                serial=serial,
                kind="usb",
            )
        )
    return devices or _probe_windows_cameras()


def _probe_windows_cameras() -> list[CameraDevice]:
    try:
        import cv2
    except ImportError:
        return []
    devices: list[CameraDevice] = []
    for index in range(10):
        capture = cv2.VideoCapture(index, cv2.CAP_MSMF)
        try:
            if not capture.isOpened():
                continue
            devices.append(
                CameraDevice(
                    camera_id=f"windows-{index}",
                    path=str(index),
                    resolved_path=str(index),
                    name=f"Camera {index}",
                    serial=str(index),
                    kind="usb",
                )
            )
        finally:
            capture.release()
    return devices


def discover_microphones_windows() -> list[MicrophoneDevice]:
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        return []
    try:
        default_input = sd.default.device[0] if sd.default.device else -1
        raw_devices = sd.query_devices()
    except Exception:
        return []
    devices: list[MicrophoneDevice] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_devices):
        if int(item.get("max_input_channels", 0)) <= 0:
            continue
        name = str(item.get("name", "")).strip() or f"Microphone {index}"
        if name in seen:
            continue
        seen.add(name)
        devices.append(MicrophoneDevice(str(index), name))
    if default_input is not None and int(default_input) >= 0:
        default_id = str(default_input)
        devices.sort(key=lambda item: item.microphone_id != default_id)
    return devices


def find_camera(camera_id: str | None) -> CameraDevice | None:
    if not camera_id:
        return None
    return next((camera for camera in discover_cameras() if camera.camera_id == camera_id), None)


def preferred_camera(cameras: Iterable[CameraDevice]) -> CameraDevice | None:
    """Prefer an external USB input, then fall back to the first integrated camera."""
    available = list(cameras)
    return next((camera for camera in available if camera.kind == "usb"), None) or (
        available[0] if available else None
    )


def query_controls(
    device: str,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
) -> dict[str, CameraControl]:
    if os.name == "nt":
        return {}
    result = runner(["v4l2-ctl", "-d", device, "--list-ctrls"])
    if result.returncode != 0:
        return {}
    controls: dict[str, CameraControl] = {}
    for line in result.stdout.splitlines():
        match = CONTROL_LINE.match(line)
        if not match:
            continue
        name, minimum, maximum, step, default, value = match.groups()
        controls[name] = CameraControl(
            name=name,
            minimum=int(minimum),
            maximum=int(maximum),
            step=max(1, int(step)),
            default=int(default),
            value=int(value),
        )
    return controls


def set_normalized_control(
    device: str,
    control: CameraControl,
    normalized: int,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
) -> bool:
    if os.name == "nt":
        return False
    raw = control.from_normalized(normalized)
    result = runner(
        ["v4l2-ctl", "-d", device, f"--set-ctrl={control.name}={raw}"]
    )
    return result.returncode == 0


def apply_saved_controls(device: CameraDevice, values: dict[str, int]) -> dict[str, bool]:
    if os.name == "nt":
        return {name: False for name in CONTROL_NAMES}
    available = query_controls(device.path)
    return {
        name: (
            set_normalized_control(device.path, available[name], values.get(name, 0))
            if name in available
            else False
        )
        for name in CONTROL_NAMES
    }


def _device_caps(output: str) -> str:
    marker = "Device Caps"
    if marker not in output:
        return ""
    return output.split(marker, 1)[1].split("Media Driver Info", 1)[0]


def _usb_identity(start: Path) -> tuple[str, str, str]:
    try:
        current = start.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return "", "", "unknown"
    for parent in (current, *current.parents):
        removable_path = parent / "removable"
        if removable_path.is_file():
            return (
                _read_text(parent / "product"),
                _read_text(parent / "serial"),
                _read_text(removable_path) or "unknown",
            )
    return "", "", "unknown"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
