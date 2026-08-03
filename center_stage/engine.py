"""Headless Center Stage camera engine."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import gi
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from .config import camera_control_values, load_config, preview_socket_path, runtime_dir, write_status
from .devices import CameraDevice, apply_saved_controls, find_camera
from .framing import AutoFramer, FaceBox, clamp, crop_and_resize

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
OUTPUT_FPS = 30
OUTPUT_FORMAT = "YUY2"
OUTPUT_DEVICE = "/dev/video10"
DETECTION_WIDTH = 640
DETECTION_INTERVAL = 4
SEGMENTATION_INTERVAL = 4
SEGMENTATION_INPUT_WIDTH = 256
EFFECTS_WIDTH = 640
CONFIG_POLL_INTERVAL = 0.15
MIN_DETECTION_CONFIDENCE = 0.5
CAMERA_START_TIMEOUT_NS = 5 * Gst.SECOND
CAMERA_STALL_SECONDS = 2.0

Gst.init(None)
cv2.setNumThreads(2)


@dataclass(frozen=True)
class AmbientLightState:
    subject_luma: float = 0.45
    background_luma: float = 0.45
    highlight_ratio: float = 0.0
    shadow_ratio: float = 0.0
    horizontal_bias: float = 0.0
    noise: float = 0.0


def _subject_bounds(alpha: np.ndarray) -> tuple[int, int, int, int]:
    points = cv2.findNonZero((alpha > 0.35).astype(np.uint8))
    if points is None:
        height, width = alpha.shape
        return width // 4, height // 8, width // 2, height * 3 // 4
    return cv2.boundingRect(points)


class AmbientLightEstimator:
    """Slow, scene-aware exposure statistics inferred from camera frames."""

    def __init__(self) -> None:
        self.state = AmbientLightState()
        self._initialized = False
        self._frame_index = 0

    def update(self, frame: np.ndarray, person_mask: np.ndarray) -> AmbientLightState:
        self._frame_index += 1
        if self._initialized and self._frame_index % 10:
            return self.state
        width = min(EFFECTS_WIDTH, frame.shape[1])
        height = max(1, round(frame.shape[0] * width / frame.shape[1]))
        work = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        alpha = cv2.resize(person_mask, (width, height), interpolation=cv2.INTER_LINEAR)
        luma = cv2.cvtColor(work, cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float32) / 255.0
        x, y, subject_width, subject_height = _subject_bounds(alpha)
        face_x1 = max(0, x + round(subject_width * 0.17))
        face_x2 = min(width, x + round(subject_width * 0.83))
        face_y1 = max(0, y)
        face_y2 = min(height, y + max(1, round(subject_height * 0.38)))
        face = luma[face_y1:face_y2, face_x1:face_x2]
        if face.size == 0:
            face = luma
        background = luma[alpha < 0.2]
        if background.size < 64:
            background = luma.reshape(-1)
        midpoint = max(1, face.shape[1] // 2)
        left = float(np.median(face[:, :midpoint]))
        right = float(np.median(face[:, midpoint:])) if face.shape[1] > midpoint else left
        denoised = cv2.GaussianBlur(face, (0, 0), 1.2)
        measured = AmbientLightState(
            subject_luma=float(np.median(face)),
            background_luma=float(np.median(background)),
            highlight_ratio=float(np.mean(face > 0.92)),
            shadow_ratio=float(np.mean(face < 0.12)),
            horizontal_bias=float(np.clip((right - left) * 2.5, -1.0, 1.0)),
            noise=float(np.median(np.abs(face - denoised))),
        )
        if not self._initialized:
            self.state = measured
            self._initialized = True
            return self.state
        scene_delta = max(
            abs(measured.subject_luma - self.state.subject_luma),
            abs(measured.background_luma - self.state.background_luma),
        )
        rate = 0.32 if scene_delta > 0.16 else 0.10
        self.state = AmbientLightState(
            **{
                field: float(getattr(self.state, field) * (1.0 - rate) + getattr(measured, field) * rate)
                for field in AmbientLightState.__dataclass_fields__
            }
        )
        return self.state


class TemporalMaskStabilizer:
    """Confidence and motion-aware mask smoothing with a fast ghost release."""

    def __init__(self) -> None:
        self.mask: np.ndarray | None = None
        self._previous_gray: np.ndarray | None = None

    def update(self, frame: np.ndarray, target: np.ndarray | None) -> np.ndarray:
        gray = cv2.resize(
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90), interpolation=cv2.INTER_AREA
        ).astype(np.float32)
        motion = 0.0
        if self._previous_gray is not None:
            motion = float(np.mean(np.abs(gray - self._previous_gray)) / 255.0)
        self._previous_gray = gray
        if target is None:
            if self.mask is None:
                width = min(EFFECTS_WIDTH, frame.shape[1])
                height = max(1, round(frame.shape[0] * width / frame.shape[1]))
                self.mask = np.ones((height, width), np.float32)
            return self.mask
        target = np.clip(target, 0.0, 1.0).astype(np.float32)
        if self.mask is None or self.mask.shape != target.shape:
            self.mask = target
            return self.mask
        motion_factor = float(np.clip(motion / 0.055, 0.0, 1.0))
        acquire = 0.48 + 0.32 * motion_factor
        release = 0.76 + 0.20 * motion_factor
        rate = np.where(target < self.mask, release, acquire).astype(np.float32)
        self.mask = self.mask + (target - self.mask) * rate
        return self.mask


@dataclass(frozen=True)
class DeviceCapabilityProfile:
    width: int
    height: int
    fps: float
    gpu_segmentation: bool


class AdaptiveQualityGovernor:
    """Selects segmentation cadence from measured device headroom."""

    LEVELS = ("eco", "balanced", "high")
    INTERVALS = {"eco": 6, "balanced": 4, "high": 3}

    def __init__(self, profile: DeviceCapabilityProfile):
        self.level = "balanced" if profile.gpu_segmentation and profile.fps >= 25 else "eco"
        self._last_wall = time.monotonic()
        self._last_cpu = time.process_time()
        self._candidate = self.level
        self._candidate_count = 0
        self.cpu_cores = 0.0

    @property
    def segmentation_interval(self) -> int:
        return self.INTERVALS[self.level]

    def update(self, measured_fps: float, segmentation_latency_ms: float, now: float) -> str:
        elapsed = now - self._last_wall
        if elapsed < 2.0:
            return self.level
        cpu_now = time.process_time()
        self.cpu_cores = max(0.0, (cpu_now - self._last_cpu) / max(elapsed, 0.001))
        self._last_cpu = cpu_now
        self._last_wall = now
        if measured_fps < 27.5 or self.cpu_cores > 2.25 or segmentation_latency_ms > 125:
            candidate = "eco"
        elif measured_fps >= 29.0 and self.cpu_cores < 1.35 and segmentation_latency_ms < 55:
            candidate = "high"
        else:
            candidate = "balanced"
        if candidate == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = candidate
            self._candidate_count = 1
        if self._candidate_count >= 2:
            self.level = candidate
        return self.level


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InputCaps:
    media_type: str
    width: int
    height: int
    fps_numerator: int
    fps_denominator: int

    @property
    def fps(self) -> float:
        return self.fps_numerator / max(1, self.fps_denominator)

    @property
    def format_name(self) -> str:
        return "MJPG" if self.media_type == "image/jpeg" else "YUY2"

    def to_gst_caps(self) -> Gst.Caps:
        value = (
            f"{self.media_type},width={self.width},height={self.height},"
            f"framerate={self.fps_numerator}/{self.fps_denominator}"
        )
        if self.media_type == "video/x-raw":
            value += ",format=YUY2"
        return Gst.Caps.from_string(value)


class FpsCounter:
    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.frames = 0
        self.value = 0.0

    def tick(self, now: float) -> float:
        self.frames += 1
        elapsed = now - self.started_at
        if elapsed >= 0.5:
            self.value = self.frames / elapsed
            self.frames = 0
            self.started_at = now
        return self.value


def model_path() -> Path:
    candidates = (
        Path(__file__).resolve().parent.parent / "blaze_face_short_range.tflite",
        Path("/opt/center-stage/share/blaze_face_short_range.tflite"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise EngineError("model_missing", "MediaPipe face detector model is missing")


def segmentation_model_path() -> Path:
    candidates = (
        Path(__file__).resolve().parent.parent / "selfie_segmenter_landscape.tflite",
        Path("/opt/center-stage/share/selfie_segmenter_landscape.tflite"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise EngineError("segmentation_model_missing", "MediaPipe selfie segmenter model is missing")


def extract_input_caps(caps: Gst.Caps) -> list[InputCaps]:
    """Extract fixed camera modes from a GStreamer caps collection."""
    result: list[InputCaps] = []
    for index in range(caps.get_size()):
        structure = caps.get_structure(index)
        media_type = structure.get_name()
        if media_type not in {"image/jpeg", "video/x-raw"}:
            continue
        width_ok, width = structure.get_int("width")
        height_ok, height = structure.get_int("height")
        try:
            fps_ok, numerator, denominator = structure.get_fraction("framerate")
        except (TypeError, ValueError):
            fps_ok, numerator, denominator = False, 0, 1
        if not width_ok or not height_ok or not fps_ok or denominator <= 0:
            continue
        if media_type == "video/x-raw":
            serialized = structure.to_string()
            if "format=(string)YUY2" not in serialized and "format=YUY2" not in serialized:
                continue
        result.append(InputCaps(media_type, width, height, numerator, denominator))
    return result


def select_input_caps(candidates: Sequence[InputCaps]) -> InputCaps:
    if not candidates:
        raise EngineError("pipewire_camera_unavailable", "Camera exposes no supported video modes")

    preferences = (
        ("image/jpeg", 1920, 1080, 30.0),
        ("video/x-raw", 1920, 1080, 30.0),
        ("image/jpeg", 1280, 720, 30.0),
        ("video/x-raw", 1280, 720, 30.0),
    )
    for media_type, width, height, minimum_fps in preferences:
        matches = [
            item
            for item in candidates
            if item.media_type == media_type
            and item.width == width
            and item.height == height
            and item.fps >= minimum_fps
        ]
        if matches:
            return max(matches, key=lambda item: item.fps)

    return max(
        candidates,
        key=lambda item: (
            min(item.fps, OUTPUT_FPS),
            item.width * item.height,
            item.media_type == "image/jpeg",
        ),
    )


def _link_many(elements: Sequence[Gst.Element]) -> None:
    for source, target in zip(elements, elements[1:]):
        if not source.link(target):
            raise EngineError(
                "output_pipeline_failed",
                f"Cannot link {source.get_name()} to {target.get_name()}",
            )


def _message_error(message: Gst.Message) -> str:
    error, debug = message.parse_error()
    return f"{error.message}: {debug or 'no debug details'}"


class PipeWireCameraCapture:
    """Shareable physical-camera input exported by WirePlumber."""

    def __init__(self, camera: CameraDevice):
        self.camera = camera
        self.monitor = Gst.DeviceMonitor.new()
        self.pipeline: Gst.Pipeline | None = None
        self.sink: Gst.Element | None = None
        self.bus: Gst.Bus | None = None
        self.mode: InputCaps | None = None

    def start(self) -> tuple[np.ndarray, InputCaps]:
        self.monitor.add_filter("Video/Source", None)
        if not self.monitor.start():
            raise EngineError("pipewire_camera_unavailable", "Cannot start PipeWire camera monitor")

        expected = os.path.realpath(self.camera.resolved_path)
        matched: Gst.Device | None = None
        for device in self.monitor.get_devices():
            properties = device.get_properties()
            path = properties.get_string("api.v4l2.path") if properties else None
            if path and os.path.realpath(path) == expected:
                matched = device
                break
        if matched is None:
            self.close()
            raise EngineError(
                "pipewire_camera_unavailable",
                f"PipeWire does not expose the selected camera: {self.camera.path}",
            )

        self.mode = select_input_caps(extract_input_caps(matched.get_caps()))
        pipeline = Gst.Pipeline.new("camera_capture")
        source = matched.create_element("pipewire_camera")
        queue = Gst.ElementFactory.make("queue", "camera_queue")
        input_filter = Gst.ElementFactory.make("capsfilter", "camera_caps")
        convert = Gst.ElementFactory.make("videoconvert", "camera_convert")
        output_filter = Gst.ElementFactory.make("capsfilter", "bgr_caps")
        sink = Gst.ElementFactory.make("appsink", "camera_sink")
        elements: list[Gst.Element | None] = [source, queue, input_filter]
        if self.mode.media_type == "image/jpeg":
            elements.append(Gst.ElementFactory.make("jpegdec", "camera_jpeg_decoder"))
        elements.extend([convert, output_filter, sink])
        if any(element is None for element in elements):
            self.close()
            raise EngineError("pipewire_camera_unavailable", "Required GStreamer camera plugin is missing")

        typed_elements = [element for element in elements if element is not None]
        queue.set_property("max-size-buffers", 1)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        queue.set_property("leaky", 2)
        input_filter.set_property("caps", self.mode.to_gst_caps())
        output_filter.set_property(
            "caps", Gst.Caps.from_string(f"video/x-raw,format=BGR,width={self.mode.width},height={self.mode.height}")
        )
        sink.set_property("emit-signals", False)
        sink.set_property("max-buffers", 1)
        sink.set_property("drop", True)
        sink.set_property("sync", False)
        for element in typed_elements:
            pipeline.add(element)
        _link_many(typed_elements)

        self.pipeline = pipeline
        self.sink = sink
        self.bus = pipeline.get_bus()
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise EngineError("pipewire_camera_unavailable", "Cannot start PipeWire camera pipeline")
        first_frame = self.read(CAMERA_START_TIMEOUT_NS)
        if first_frame is None:
            self.close()
            raise EngineError("pipewire_camera_unavailable", "Camera returned no frames through PipeWire")
        return first_frame, self.mode

    def read(self, timeout_ns: int = 0) -> np.ndarray | None:
        self._raise_bus_error()
        if self.sink is None:
            return None
        sample = self.sink.emit("try-pull-sample", timeout_ns)
        if sample is None:
            self._raise_bus_error()
            return None
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width_ok, width = structure.get_int("width")
        height_ok, height = structure.get_int("height")
        if not width_ok or not height_ok:
            raise EngineError("camera_disconnected", "PipeWire returned a frame without dimensions")
        buffer = sample.get_buffer()
        mapped, info = buffer.map(Gst.MapFlags.READ)
        if not mapped:
            raise EngineError("camera_disconnected", "Cannot map a PipeWire camera frame")
        try:
            data = np.frombuffer(info.data, dtype=np.uint8)
            required = width * height * 3
            if data.size < required or data.size % height:
                raise EngineError("camera_disconnected", "PipeWire returned an invalid BGR frame")
            stride = data.size // height
            frame = data.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()
        finally:
            buffer.unmap(info)
        return frame

    def _raise_bus_error(self) -> None:
        if self.bus is None:
            return
        message = self.bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            return
        if message.type == Gst.MessageType.ERROR:
            reason = _message_error(message)
        else:
            reason = "Camera pipeline reached end of stream"
        raise EngineError("camera_disconnected", reason)

    def close(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.sink = None
        self.bus = None
        self.monitor.stop()


def raw_bgr_payload(frame: np.ndarray) -> bytes:
    """Return a tightly packed BGR frame for the GStreamer appsrc."""
    return np.ascontiguousarray(frame).tobytes()


def output_timestamps(frame_index: int) -> tuple[int, int]:
    duration = Gst.SECOND // OUTPUT_FPS
    return frame_index * duration, duration


def _pipewire_objects(payload: str) -> list[dict[str, Any]]:
    try:
        objects = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(objects, list):
        return []
    return [item for item in objects if isinstance(item, dict)]


def pipewire_v4l2_node_ids(payload: str, device: str = OUTPUT_DEVICE) -> list[int]:
    """Return PipeWire Video/Source object IDs for one V4L2 device."""
    result: list[int] = []
    for item in _pipewire_objects(payload):
        props = item.get("info", {}).get("props", {})
        object_id = item.get("id")
        if (
            isinstance(props, dict)
            and props.get("media.class") == "Video/Source"
            and props.get("api.v4l2.path") == device
            and isinstance(object_id, int)
        ):
            result.append(object_id)
    return result


def pipewire_node_count(payload: str, device: str = OUTPUT_DEVICE) -> int:
    return len(pipewire_v4l2_node_ids(payload, device))


def pipewire_dump_has_node(payload: str, device: str = OUTPUT_DEVICE) -> bool:
    return pipewire_node_count(payload, device) > 0


def pipewire_dump_has_camera(payload: str, device: str) -> bool:
    """Compatibility helper used by older status tests."""
    return any(
        item.get("info", {}).get("props", {}).get("media.class") == "Video/Source"
        and item.get("info", {}).get("props", {}).get("api.v4l2.path") == device
        for item in _pipewire_objects(payload)
    )


V4L2_FOURCC = {"YUY2": "YUYV", "I420": "YU12", "NV12": "NV12"}


def v4l2_output_caps_are_valid(payload: str, output_format: str = OUTPUT_FORMAT) -> bool:
    """Validate the capability exposed to consumers after the writer is open."""
    expected_fourcc = V4L2_FOURCC.get(output_format)
    if expected_fourcc is None:
        return False
    capture = "Video Capture" in payload
    dimensions = re.search(r"Width/Height\s*:\s*1920/1080\b", payload) is not None
    fourcc = re.search(rf"Pixel Format\s*:\s*'{re.escape(expected_fourcc)}'", payload) is not None
    rate = (
        re.search(r"Frames per second\s*:\s*30(?:\.0+)?\b", payload) is not None
        or re.search(r"\(30/1\)", payload) is not None
    )
    return capture and dimensions and fourcc and rate


class V4L2VirtualOutput:
    """Publish one standards-compatible webcam through v4l2loopback."""

    def __init__(self, device: str, output_format: str = OUTPUT_FORMAT):
        if output_format not in V4L2_FOURCC:
            raise ValueError(f"Unsupported virtual output format: {output_format}")
        self.device = device
        self.output_format = output_format
        self.preview_socket = preview_socket_path()
        self.pipeline: Gst.Pipeline | None = None
        self.source: Gst.Element | None = None
        self.bus: Gst.Bus | None = None
        self.monitor: Gst.DeviceMonitor | None = None
        self.pipewire_visible = False
        self.last_visibility_check = 0.0
        self.frame_index = 0

    def start(self, first_frame: np.ndarray) -> None:
        self._validate_device()
        self._reset_loopback_controls()
        self.preview_socket.parent.mkdir(parents=True, exist_ok=True)
        self.preview_socket.unlink(missing_ok=True)
        pipeline = Gst.Pipeline.new("center_stage_output")
        source = Gst.ElementFactory.make("appsrc", "output_source")
        queue = Gst.ElementFactory.make("queue", "output_queue")
        convert = Gst.ElementFactory.make("videoconvert", "output_convert")
        output_filter = Gst.ElementFactory.make("capsfilter", "output_caps")
        tee = Gst.ElementFactory.make("tee", "output_tee")
        v4l2_queue = Gst.ElementFactory.make("queue", "v4l2_queue")
        v4l2_output = Gst.ElementFactory.make("v4l2sink", "v4l2_output")
        preview_queue = Gst.ElementFactory.make("queue", "preview_queue")
        preview_output = Gst.ElementFactory.make("shmsink", "preview_output")
        elements = [
            source,
            queue,
            convert,
            output_filter,
            tee,
            v4l2_queue,
            v4l2_output,
            preview_queue,
            preview_output,
        ]
        if any(element is None for element in elements):
            raise EngineError("output_pipeline_failed", "Required GStreamer output plugin is missing")

        source.set_property("is-live", True)
        source.set_property("block", False)
        source.set_property("format", Gst.Format.TIME)
        source.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,format=BGR,width={OUTPUT_WIDTH},height={OUTPUT_HEIGHT},"
                f"framerate={OUTPUT_FPS}/1,pixel-aspect-ratio=1/1"
            ),
        )
        queue.set_property("max-size-buffers", 2)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        queue.set_property("leaky", 2)
        v4l2_queue.set_property("max-size-buffers", 2)
        v4l2_queue.set_property("max-size-bytes", 0)
        v4l2_queue.set_property("max-size-time", 0)
        v4l2_queue.set_property("leaky", 2)
        preview_queue.set_property("max-size-buffers", 1)
        preview_queue.set_property("max-size-bytes", 0)
        preview_queue.set_property("max-size-time", 0)
        preview_queue.set_property("leaky", 2)
        output_filter.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,format={self.output_format},width={OUTPUT_WIDTH},height={OUTPUT_HEIGHT},"
                f"framerate={OUTPUT_FPS}/1,pixel-aspect-ratio=1/1,"
                "interlace-mode=progressive,colorimetry=bt709"
            ),
        )
        v4l2_output.set_property("device", self.device)
        v4l2_output.set_property("sync", False)
        v4l2_output.set_property("async", False)
        preview_output.set_property("socket-path", str(self.preview_socket))
        preview_output.set_property("wait-for-connection", False)
        preview_output.set_property("sync", False)
        preview_output.set_property("async", False)
        preview_output.set_property("shm-size", 32 * 1024 * 1024)

        for element in elements:
            pipeline.add(element)
        _link_many([source, queue, convert, output_filter, tee])
        _link_many([v4l2_queue, v4l2_output])
        _link_many([preview_queue, preview_output])
        if not tee.link(v4l2_queue) or not tee.link(preview_queue):
            raise EngineError("output_pipeline_failed", "Cannot link virtual output branches")

        self.pipeline = pipeline
        self.source = source
        self.bus = pipeline.get_bus()
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise EngineError("output_pipeline_failed", "Cannot start virtual output pipeline")
        self.send(first_frame)
        self._verify_capture_caps()
        self._start_optional_pipewire_monitor()

    def send(self, frame: np.ndarray) -> None:
        if frame.shape[:2] != (OUTPUT_HEIGHT, OUTPUT_WIDTH):
            raise EngineError("output_pipeline_failed", "Output frame is not 1920x1080")
        self._raise_bus_error()
        if self.source is None:
            raise EngineError("output_pipeline_failed", "Output pipeline is not running")
        payload = raw_bgr_payload(frame)
        buffer = Gst.Buffer.new_allocate(None, len(payload), None)
        buffer.fill(0, payload)
        buffer.pts, buffer.duration = output_timestamps(self.frame_index)
        buffer.dts = buffer.pts
        result = self.source.emit("push-buffer", buffer)
        if result != Gst.FlowReturn.OK:
            raise EngineError("output_pipeline_failed", f"Output rejected a frame: {result.value_nick}")
        self.frame_index += 1

    def refresh_pipewire_visibility(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self.last_visibility_check < 0.5:
            return self.pipewire_visible
        self.last_visibility_check = now
        monitor = self.monitor
        if monitor is None:
            self.pipewire_visible = False
            return False
        expected = os.path.realpath(self.device)
        self.pipewire_visible = any(
            (properties := item.get_properties()) is not None
            and (path := properties.get_string("api.v4l2.path")) is not None
            and os.path.realpath(path) == expected
            for item in monitor.get_devices()
        )
        return self.pipewire_visible

    def _start_optional_pipewire_monitor(self) -> None:
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Video/Source", None)
        if monitor.start():
            self.monitor = monitor
            self.refresh_pipewire_visibility(force=True)

    def _verify_capture_caps(self) -> None:
        deadline = time.monotonic() + 2.0
        last_details = ""
        while time.monotonic() < deadline:
            details = subprocess.run(
                ["v4l2-ctl", "-d", self.device, "-D", "--get-fmt-video", "--get-parm"],
                text=True,
                capture_output=True,
                check=False,
            )
            last_details = details.stdout
            if details.returncode == 0 and v4l2_output_caps_are_valid(
                details.stdout, self.output_format
            ):
                return
            self._raise_bus_error()
            time.sleep(0.05)
        summary = " ".join(last_details.split())[-500:]
        self.close()
        raise EngineError(
            "output_pipeline_failed",
            f"Virtual camera did not expose Video Capture 1920x1080@30 {self.output_format}: {summary}",
        )

    def _validate_device(self) -> None:
        driver = subprocess.run(
            ["modinfo", "v4l2loopback"], capture_output=True, check=False
        )
        if driver.returncode != 0:
            raise EngineError("driver_missing", "v4l2loopback is not installed")
        if not os.path.exists(self.device):
            raise EngineError("virtual_camera_missing", f"Virtual camera is missing: {self.device}")
        resolved = os.path.realpath(self.device)
        name_path = Path("/sys/class/video4linux") / Path(resolved).name / "name"
        try:
            label = name_path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise EngineError("virtual_camera_missing", str(error)) from error
        if label != "Center Stage":
            raise EngineError("output_device_conflict", f"{self.device} is named {label!r}")
        details = subprocess.run(
            ["v4l2-ctl", "-d", self.device, "-D"],
            text=True,
            capture_output=True,
            check=False,
        )
        if details.returncode != 0 or "Driver name" not in details.stdout or "v4l2 loopback" not in details.stdout:
            raise EngineError("output_device_conflict", f"{self.device} is not a v4l2loopback device")

    def _reset_loopback_controls(self) -> None:
        subprocess.run(
            ["v4l2-ctl", "-d", self.device, "-c", "keep_format=0,sustain_framerate=0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def _raise_bus_error(self) -> None:
        if self.bus is None:
            return
        message = self.bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            return
        source_name = message.src.get_name() if message.src else ""
        reason = _message_error(message) if message.type == Gst.MessageType.ERROR else "Output ended"
        if "v4l2" in source_name:
            code = "virtual_camera_missing"
        else:
            code = "output_pipeline_failed"
        raise EngineError(code, reason)

    def close(self) -> None:
        if self.source is not None:
            self.source.emit("end-of-stream")
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self._reset_loopback_controls()
        self.preview_socket.unlink(missing_ok=True)
        if self.monitor is not None:
            self.monitor.stop()
        self.monitor = None
        self.pipewire_visible = False
        self.pipeline = None
        self.source = None
        self.bus = None


# Kept as a source-compatible alias for third-party scripts using the V4.0.3 name.
DualVirtualOutput = V4L2VirtualOutput


def create_face_detector(path: Path) -> vision.FaceDetector:
    options = vision.FaceDetectorOptions(
        base_options=python.BaseOptions(model_asset_path=str(path)),
        min_detection_confidence=MIN_DETECTION_CONFIDENCE,
    )
    return vision.FaceDetector.create_from_options(options)


def detect_faces(frame: np.ndarray, detector: vision.FaceDetector) -> list[FaceBox]:
    frame_height, frame_width = frame.shape[:2]
    detection_width = min(DETECTION_WIDTH, frame_width)
    detection_height = max(1, round(frame_height * detection_width / frame_width))
    detection_frame = (
        frame
        if (detection_width, detection_height) == (frame_width, frame_height)
        else cv2.resize(
            frame,
            (detection_width, detection_height),
            interpolation=cv2.INTER_AREA,
        )
    )
    rgb = cv2.cvtColor(detection_frame, cv2.COLOR_BGR2RGB)
    results = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    scale_x = frame_width / detection_width
    scale_y = frame_height / detection_height
    faces: list[FaceBox] = []
    for detection in results.detections:
        box = detection.bounding_box
        x1 = clamp(box.origin_x * scale_x, 0.0, float(frame_width))
        y1 = clamp(box.origin_y * scale_y, 0.0, float(frame_height))
        x2 = clamp((box.origin_x + box.width) * scale_x, 0.0, float(frame_width))
        y2 = clamp((box.origin_y + box.height) * scale_y, 0.0, float(frame_height))
        if x2 - x1 >= 1.0 and y2 - y1 >= 1.0:
            faces.append(FaceBox(x1, y1, x2 - x1, y2 - y1))
    return faces


def create_person_segmenter(path: Path) -> vision.ImageSegmenter:
    def options(delegate: python.BaseOptions.Delegate) -> vision.ImageSegmenterOptions:
        return vision.ImageSegmenterOptions(
            base_options=python.BaseOptions(model_asset_path=str(path), delegate=delegate),
            running_mode=vision.RunningMode.IMAGE,
            output_confidence_masks=True,
            output_category_mask=False,
        )

    try:
        return vision.ImageSegmenter.create_from_options(options(python.BaseOptions.Delegate.GPU))
    except (RuntimeError, ValueError):
        return vision.ImageSegmenter.create_from_options(options(python.BaseOptions.Delegate.CPU))


def _mask_from_segmentation_result(result: Any, output_width: int, output_height: int) -> np.ndarray:
    if not result.confidence_masks:
        return np.ones((output_height, output_width), dtype=np.float32)
    mask = np.asarray(result.confidence_masks[0].numpy_view(), dtype=np.float32).squeeze()
    if mask.shape != (output_height, output_width):
        mask = cv2.resize(mask, (output_width, output_height), interpolation=cv2.INTER_LINEAR)
    mask = cv2.GaussianBlur(np.clip(mask, 0.0, 1.0), (0, 0), 1.35)
    return np.clip(mask, 0.0, 1.0)


class LivePersonSegmenter:
    """Non-blocking MediaPipe LIVE_STREAM wrapper that retains only the newest mask."""

    def __init__(self, path: Path, output_width: int, output_height: int):
        self.output_width = output_width
        self.output_height = output_height
        self._lock = threading.Lock()
        self._latest_mask: np.ndarray | None = None
        self._latest_timestamp = -1
        self._last_submit_timestamp = -1
        self._submitted_at: dict[int, float] = {}
        self.latency_ms = 0.0
        self.gpu_enabled = True
        try:
            self._segmenter = self._create(path, python.BaseOptions.Delegate.GPU)
        except (RuntimeError, ValueError):
            self.gpu_enabled = False
            self._segmenter = self._create(path, python.BaseOptions.Delegate.CPU)

    def _create(self, path: Path, delegate: python.BaseOptions.Delegate) -> vision.ImageSegmenter:
        options = vision.ImageSegmenterOptions(
            base_options=python.BaseOptions(model_asset_path=str(path), delegate=delegate),
            running_mode=vision.RunningMode.LIVE_STREAM,
            output_confidence_masks=True,
            output_category_mask=False,
            result_callback=self._on_result,
        )
        return vision.ImageSegmenter.create_from_options(options)

    def _on_result(self, result: Any, _image: mp.Image, timestamp_ms: int) -> None:
        mask = _mask_from_segmentation_result(result, self.output_width, self.output_height)
        completed_at = time.monotonic()
        with self._lock:
            submitted_at = self._submitted_at.pop(timestamp_ms, completed_at)
            self._submitted_at = {
                timestamp: started for timestamp, started in self._submitted_at.items() if timestamp > timestamp_ms
            }
            measured_latency = max(0.0, (completed_at - submitted_at) * 1000.0)
            self.latency_ms = measured_latency if self.latency_ms == 0 else self.latency_ms * 0.8 + measured_latency * 0.2
            self._latest_mask = mask
            self._latest_timestamp = timestamp_ms

    def submit(self, frame: np.ndarray, timestamp_ms: int) -> None:
        input_width = min(SEGMENTATION_INPUT_WIDTH, frame.shape[1])
        input_height = max(1, round(frame.shape[0] * input_width / frame.shape[1]))
        inference_frame = cv2.resize(frame, (input_width, input_height), interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(cv2.cvtColor(inference_frame, cv2.COLOR_BGR2RGB))
        with self._lock:
            timestamp_ms = max(timestamp_ms, self._last_submit_timestamp + 1)
            self._last_submit_timestamp = timestamp_ms
            self._submitted_at[timestamp_ms] = time.monotonic()
        self._segmenter.segment_async(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp_ms
        )

    def latest_after(self, timestamp_ms: int) -> tuple[np.ndarray | None, int]:
        with self._lock:
            if self._latest_timestamp <= timestamp_ms or self._latest_mask is None:
                return None, timestamp_ms
            return self._latest_mask.copy(), self._latest_timestamp

    def close(self) -> None:
        self._segmenter.close()


def segment_person(frame: np.ndarray, segmenter: vision.ImageSegmenter) -> np.ndarray:
    input_width = min(SEGMENTATION_INPUT_WIDTH, frame.shape[1])
    input_height = max(1, round(frame.shape[0] * input_width / frame.shape[1]))
    inference_frame = cv2.resize(frame, (input_width, input_height), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(inference_frame, cv2.COLOR_BGR2RGB)
    result = segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    output_width = min(EFFECTS_WIDTH, frame.shape[1])
    output_height = max(1, round(frame.shape[0] * output_width / frame.shape[1]))
    return _mask_from_segmentation_result(result, output_width, output_height)


def _apply_natural_studio_light(
    frame: np.ndarray,
    alpha: np.ndarray,
    amount: float,
    ambient: AmbientLightState,
) -> np.ndarray:
    """Relight luminance with a face-aware soft key while preserving chroma."""
    height, width = alpha.shape
    x, y, subject_width, subject_height = _subject_bounds(alpha)
    center_x = x + subject_width * (0.5 + 0.10 * ambient.horizontal_bias)
    center_y = y + subject_height * 0.23
    radius_x = max(18.0, subject_width * 0.48)
    radius_y = max(20.0, subject_height * 0.38)
    yy, xx = np.ogrid[:height, :width]
    distance = ((xx - center_x) / radius_x) ** 2 + ((yy - center_y) / radius_y) ** 2
    key_light = np.exp(-1.45 * distance).astype(np.float32)
    subject_weight = alpha * (0.22 + 0.78 * key_light)

    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    luminance = ycrcb[:, :, 0].astype(np.float32) / 255.0
    desired_subject = 0.57
    exposure_need = float(np.clip(desired_subject - ambient.subject_luma, -0.08, 0.30))
    highlight_guard = np.clip((0.96 - luminance) / 0.28, 0.0, 1.0)
    highlight_guard *= max(0.35, 1.0 - ambient.highlight_ratio * 4.0)
    shadow_weight = np.power(np.clip(1.0 - luminance, 0.0, 1.0), 1.35)
    lift = amount * (0.025 + max(0.0, exposure_need) * 0.62)
    subject_delta = lift * shadow_weight * highlight_guard * subject_weight
    if exposure_need < 0:
        subject_delta += amount * exposure_need * 0.18 * subject_weight

    background_room = float(np.clip(ambient.background_luma - 0.16, 0.0, 0.55))
    background_dim = amount * (0.012 + background_room * 0.13)
    far_from_key = np.clip(1.0 - key_light, 0.0, 1.0)
    background_delta = background_dim * (1.0 - alpha) * (0.42 + 0.58 * far_from_key)
    adjusted = np.clip(luminance + subject_delta - background_delta, 0.0, 1.0)
    ycrcb[:, :, 0] = np.rint(adjusted * 255.0).astype(np.uint8)
    return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)


def apply_camera_effects(
    frame: np.ndarray,
    person_mask: np.ndarray,
    *,
    portrait_enabled: bool,
    portrait_strength: int,
    studio_light_enabled: bool,
    studio_light_strength: int,
    ambient_light: AmbientLightState | None = None,
) -> np.ndarray:
    """Composite adjustable background blur and virtual foreground lighting."""
    original_size = (frame.shape[1], frame.shape[0])
    work_width = min(EFFECTS_WIDTH, frame.shape[1])
    work_height = max(1, round(frame.shape[0] * work_width / frame.shape[1]))
    output = cv2.resize(frame, (work_width, work_height), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(person_mask, (work_width, work_height), interpolation=cv2.INTER_LINEAR)
    alpha = np.clip(alpha, 0.0, 1.0).astype(np.float32)
    if portrait_enabled and portrait_strength > 0:
        amount = max(0.0, min(1.0, portrait_strength / 100.0))
        blurred = cv2.GaussianBlur(output, (0, 0), 1.5 + amount * 8.0)
        background_weight = (1.0 - alpha) * amount
        output = cv2.blendLinear(output, blurred, 1.0 - background_weight, background_weight)
    if studio_light_enabled and studio_light_strength > 0:
        amount = max(0.0, min(1.0, studio_light_strength / 100.0))
        output = _apply_natural_studio_light(
            output,
            alpha,
            amount,
            ambient_light or AmbientLightState(),
        )
    return cv2.resize(output, original_size, interpolation=cv2.INTER_LINEAR)


def _status_fields(
    camera: CameraDevice,
    mode: InputCaps,
    measured_fps: float,
    tracking: str,
    zoom: float,
    *,
    center_stage_enabled: bool,
    pipewire_visible: bool,
    portrait_enabled: bool = False,
    portrait_strength: int = 50,
    studio_light_enabled: bool = False,
    studio_light_strength: int = 50,
    effects_quality: str = "off",
    ambient_light: AmbientLightState | None = None,
    segmentation_latency_ms: float = 0.0,
    effects_cpu_cores: float = 0.0,
    output_format: str = OUTPUT_FORMAT,
) -> dict[str, Any]:
    fields = {
        "capture_backend": "pipewire",
        "output_backend": "v4l2",
        "input": camera.path,
        "input_name": camera.name,
        "input_width": mode.width,
        "input_height": mode.height,
        "input_fps": round(mode.fps, 2),
        "input_format": mode.format_name,
        "output": OUTPUT_DEVICE,
        "output_width": OUTPUT_WIDTH,
        "output_height": OUTPUT_HEIGHT,
        "output_fps": OUTPUT_FPS,
        "output_format": output_format,
        "outputs": {"v4l2": True, "pipewire": pipewire_visible},
        "width": OUTPUT_WIDTH,
        "height": OUTPUT_HEIGHT,
        "fps": round(measured_fps, 1),
        "requested_fps": OUTPUT_FPS,
        "format": output_format,
        "tracking": tracking,
        "zoom": round(zoom, 2),
        "center_stage_enabled": center_stage_enabled,
        "portrait_enabled": portrait_enabled,
        "portrait_strength": portrait_strength,
        "studio_light_enabled": studio_light_enabled,
        "studio_light_strength": studio_light_strength,
        "effects_quality": effects_quality,
        "segmentation_latency_ms": round(segmentation_latency_ms, 1),
        "effects_cpu_cores": round(effects_cpu_cores, 2),
    }
    if ambient_light is not None:
        fields["ambient_light"] = {
            "subject_luma": round(ambient_light.subject_luma, 3),
            "background_luma": round(ambient_light.background_luma, 3),
            "highlight_ratio": round(ambient_light.highlight_ratio, 3),
            "shadow_ratio": round(ambient_light.shadow_ratio, 3),
            "horizontal_bias": round(ambient_light.horizontal_bias, 3),
            "noise": round(ambient_light.noise, 4),
        }
    return fields


def run_pipeline(
    config: dict[str, Any],
    camera: CameraDevice,
    *,
    stop_event: threading.Event | None = None,
    preview: bool = False,
    debug: bool = False,
    manage_pipewire: bool = True,
    publish_status: bool = True,
    output_format: str = OUTPUT_FORMAT,
) -> None:
    del debug, manage_pipewire
    if preview:
        raise EngineError("preview_unavailable", "Use the GTK desktop app for preview")
    stopper = stop_event or threading.Event()
    output_device = OUTPUT_DEVICE
    if os.path.realpath(camera.path) == os.path.realpath(output_device):
        raise EngineError("same_input_output", "Input and output cameras are identical")

    controls = camera_control_values(config, camera.camera_id)
    apply_saved_controls(camera, controls)
    center_stage_enabled = bool(config.get("center_stage_enabled", True))
    mirror_output = bool(config.get("mirror_output", False))
    portrait_enabled = bool(config.get("portrait_enabled", False))
    portrait_strength = int(config.get("portrait_strength", 50))
    studio_light_enabled = bool(config.get("studio_light_enabled", False))
    studio_light_strength = int(config.get("studio_light_strength", 50))
    if publish_status:
        write_status(
            "starting",
            "opening_camera",
            capture_backend="pipewire",
            output_backend="v4l2",
            input=camera.path,
            output=output_device,
            output_width=OUTPUT_WIDTH,
            output_height=OUTPUT_HEIGHT,
            output_fps=OUTPUT_FPS,
            tracking="SEARCHING" if center_stage_enabled else "OFF",
            center_stage_enabled=center_stage_enabled,
            portrait_enabled=portrait_enabled,
            portrait_strength=portrait_strength,
            studio_light_enabled=studio_light_enabled,
            studio_light_strength=studio_light_strength,
        )

    capture = PipeWireCameraCapture(camera)
    output = V4L2VirtualOutput(output_device, output_format=output_format)
    detector = None
    segmenter: LivePersonSegmenter | None = None
    try:
        frame, mode = capture.start()
        detector = create_face_detector(model_path())
        framer = AutoFramer(
            mode.width,
            mode.height,
            center_stage_enabled=center_stage_enabled,
        )
        fps_counter = FpsCounter()
        frame_index = 0
        now = time.monotonic()
        faces = detect_faces(frame, detector) if center_stage_enabled else ()
        snapshot = framer.update(faces, center_stage_enabled, now)
        clean_output = crop_and_resize(frame, snapshot.crop, OUTPUT_WIDTH, OUTPUT_HEIGHT)
        mask_stabilizer = TemporalMaskStabilizer()
        ambient_estimator = AmbientLightEstimator()
        person_mask = mask_stabilizer.update(clean_output, None)
        ambient_light = ambient_estimator.state
        quality_governor: AdaptiveQualityGovernor | None = None
        last_segment_timestamp = -1
        if portrait_enabled or studio_light_enabled:
            segmenter = LivePersonSegmenter(
                segmentation_model_path(), person_mask.shape[1], person_mask.shape[0]
            )
            quality_governor = AdaptiveQualityGovernor(
                DeviceCapabilityProfile(mode.width, mode.height, mode.fps, segmenter.gpu_enabled)
            )
            segmenter.submit(clean_output, int(now * 1000))
            ambient_light = ambient_estimator.update(clean_output, person_mask)
            clean_output = apply_camera_effects(
                clean_output,
                person_mask,
                portrait_enabled=portrait_enabled,
                portrait_strength=portrait_strength,
                studio_light_enabled=studio_light_enabled,
                studio_light_strength=studio_light_strength,
                ambient_light=ambient_light,
            )
        latest_output = cv2.flip(clean_output, 1) if mirror_output else clean_output
        output.start(latest_output)
        measured_fps = fps_counter.tick(now)
        if publish_status:
            write_status(
                "running",
                "ready",
                **_status_fields(
                    camera,
                    mode,
                    measured_fps,
                    snapshot.status,
                    snapshot.zoom,
                    center_stage_enabled=center_stage_enabled,
                    pipewire_visible=output.refresh_pipewire_visibility(),
                    portrait_enabled=portrait_enabled,
                    portrait_strength=portrait_strength,
                    studio_light_enabled=studio_light_enabled,
                    studio_light_strength=studio_light_strength,
                    effects_quality=(quality_governor.level if quality_governor and (portrait_enabled or studio_light_enabled) else "off"),
                    ambient_light=ambient_light,
                    segmentation_latency_ms=segmenter.latency_ms if segmenter else 0.0,
                    effects_cpu_cores=quality_governor.cpu_cores if quality_governor else 0.0,
                    output_format=output.output_format,
                ),
            )

        last_camera_frame = now
        last_status_update = 0.0
        last_config_check = now
        effects_active = portrait_enabled or studio_light_enabled
        next_output_at = time.monotonic() + 1.0 / OUTPUT_FPS
        while not stopper.is_set():
            new_frame = capture.read(0)
            now = time.monotonic()
            if new_frame is not None:
                frame = new_frame
                last_camera_frame = now
                if publish_status and now - last_config_check >= CONFIG_POLL_INTERVAL:
                    live_config = load_config()
                    requested_center_stage = bool(
                        live_config.get("center_stage_enabled", True)
                    )
                    if requested_center_stage != center_stage_enabled:
                        center_stage_enabled = requested_center_stage
                        framer = AutoFramer(
                            mode.width,
                            mode.height,
                            center_stage_enabled=center_stage_enabled,
                        )
                    mirror_output = bool(live_config.get("mirror_output", False))
                    portrait_enabled = bool(live_config.get("portrait_enabled", False))
                    portrait_strength = int(live_config.get("portrait_strength", 50))
                    studio_light_enabled = bool(live_config.get("studio_light_enabled", False))
                    studio_light_strength = int(live_config.get("studio_light_strength", 50))
                    if (portrait_enabled or studio_light_enabled) and segmenter is None:
                        segmenter = LivePersonSegmenter(
                            segmentation_model_path(), person_mask.shape[1], person_mask.shape[0]
                        )
                        quality_governor = AdaptiveQualityGovernor(
                            DeviceCapabilityProfile(mode.width, mode.height, mode.fps, segmenter.gpu_enabled)
                        )
                        last_segment_timestamp = -1
                        segmenter.submit(clean_output, int(now * 1000))
                    last_config_check = now
                detection_ran = (
                    center_stage_enabled and frame_index % DETECTION_INTERVAL == 0
                )
                faces = detect_faces(frame, detector) if detection_ran else ()
                snapshot = framer.update(faces, detection_ran, now)
                clean_output = crop_and_resize(frame, snapshot.crop, OUTPUT_WIDTH, OUTPUT_HEIGHT)
                effects_active = portrait_enabled or studio_light_enabled
                if effects_active and segmenter is not None:
                    interval = quality_governor.segmentation_interval if quality_governor else SEGMENTATION_INTERVAL
                    if frame_index % interval == 1:
                        segmenter.submit(clean_output, int(now * 1000))
                    next_mask, last_segment_timestamp = segmenter.latest_after(last_segment_timestamp)
                    person_mask = mask_stabilizer.update(clean_output, next_mask)
                    ambient_light = ambient_estimator.update(clean_output, person_mask)
                    clean_output = apply_camera_effects(
                        clean_output,
                        person_mask,
                        portrait_enabled=portrait_enabled,
                        portrait_strength=portrait_strength,
                        studio_light_enabled=studio_light_enabled,
                        studio_light_strength=studio_light_strength,
                        ambient_light=ambient_light,
                    )
                latest_output = (
                    cv2.flip(clean_output, 1)
                    if mirror_output
                    else clean_output
                )
                frame_index += 1
            elif now - last_camera_frame > CAMERA_STALL_SECONDS:
                raise EngineError("camera_disconnected", "Camera stopped returning frames")

            output.send(latest_output)
            measured_fps = fps_counter.tick(now)
            if effects_active and quality_governor is not None and segmenter is not None:
                quality_governor.update(measured_fps, segmenter.latency_ms, now)
            if publish_status and now - last_status_update >= 0.5:
                write_status(
                    "running",
                    "ready",
                    **_status_fields(
                        camera,
                        mode,
                        measured_fps,
                        snapshot.status,
                        snapshot.zoom,
                        center_stage_enabled=center_stage_enabled,
                        pipewire_visible=output.refresh_pipewire_visibility(),
                        portrait_enabled=portrait_enabled,
                        portrait_strength=portrait_strength,
                        studio_light_enabled=studio_light_enabled,
                        studio_light_strength=studio_light_strength,
                        effects_quality=(quality_governor.level if quality_governor and effects_active else "off"),
                        ambient_light=ambient_light,
                        segmentation_latency_ms=segmenter.latency_ms if segmenter else 0.0,
                        effects_cpu_cores=quality_governor.cpu_cores if quality_governor else 0.0,
                        output_format=output.output_format,
                    ),
                )
                last_status_update = now

            delay = next_output_at - time.monotonic()
            next_output_at += 1.0 / OUTPUT_FPS
            if delay > 0:
                stopper.wait(delay)
            elif delay < -0.25:
                next_output_at = time.monotonic() + 1.0 / OUTPUT_FPS
    finally:
        output.close()
        if detector is not None:
            detector.close()
        if segmenter is not None:
            segmenter.close()
        capture.close()


def acquire_engine_lock():
    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "engine.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise EngineError("already_running", "Center Stage is already running") from error
    return handle


def run_service() -> int:
    stop_event = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    lock = None
    try:
        lock = acquire_engine_lock()
        config = load_config()
        camera = find_camera(config.get("selected_camera_id"))
        if camera is None:
            raise EngineError("camera_missing", "The selected camera is not connected")
        run_pipeline(config, camera, stop_event=stop_event)
        write_status("stopped", "stopped", details={})
        return 0
    except EngineError as error:
        write_status("error", error.code, details={"reason": str(error)})
        print(f"Center Stage error [{error.code}]: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        write_status("error", "internal_error", details={"reason": str(error)})
        print(f"Center Stage internal error: {error}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Center Stage background engine")
    parser.add_argument("--mark-stopped", action="store_true")
    args = parser.parse_args(argv)
    if args.mark_stopped:
        write_status("stopped", "stopped", details={})
        return 0
    return run_service()


if __name__ == "__main__":
    raise SystemExit(main())
