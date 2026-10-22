"""Windows camera engine using Media Foundation, MediaPipe, and OBS."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import cv2
import numpy as np

from .config import camera_control_values, load_config, write_status
from .devices import CameraDevice, apply_saved_controls, find_camera
from .engine import (
    CAMERA_STALL_SECONDS,
    CONFIG_POLL_INTERVAL,
    DETECTION_INTERVAL,
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    SEGMENTATION_INTERVAL,
    AdaptiveQualityGovernor,
    AmbientLightEstimator,
    AutoFramer,
    DeviceCapabilityProfile,
    FpsCounter,
    InputCaps,
    LivePersonSegmenter,
    TemporalMaskStabilizer,
    acquire_engine_lock,
    apply_camera_effects,
    create_face_detector,
    crop_and_resize,
    detect_faces,
    model_path,
    segmentation_model_path,
)
from .errors import EngineError
from .media_win import WindowsRecorder

VIRTUAL_CAMERA_NAME = "OBS Virtual Camera"
CAPTURE_BACKEND = "msmf"
OUTPUT_BACKEND = "obs"


class WindowsCameraCapture:
    """Capture a DirectShow/Media Foundation camera through OpenCV."""

    def __init__(self, camera: CameraDevice):
        self.camera = camera
        self.capture: cv2.VideoCapture | None = None
        self.mode: InputCaps | None = None

    def _index(self) -> int:
        raw = str(self.camera.path or self.camera.resolved_path or "0")
        digits = "".join(character for character in raw if character.isdigit())
        return int(digits or 0)

    def start(self) -> tuple[np.ndarray, InputCaps]:
        index = self._index()
        for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW):
            capture = cv2.VideoCapture(index, backend)
            if capture.isOpened():
                self.capture = capture
                break
            capture.release()
        if self.capture is None:
            raise EngineError(
                "camera_unavailable",
                f"Cannot open camera {index}. Close applications that are using it and try again.",
            )

        capture = self.capture
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, OUTPUT_WIDTH)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, OUTPUT_HEIGHT)
        capture.set(cv2.CAP_PROP_FPS, OUTPUT_FPS)
        try:
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except cv2.error:
            pass
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        first_frame: np.ndarray | None = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            ok, frame = capture.read()
            if ok and frame is not None and frame.size:
                first_frame = frame
                break
            time.sleep(0.05)
        if first_frame is None:
            self.close()
            raise EngineError(
                "camera_unavailable",
                "The camera opened but did not return a frame. Check Windows camera privacy settings.",
            )

        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or first_frame.shape[1])
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or first_frame.shape[0])
        fps = float(capture.get(cv2.CAP_PROP_FPS) or OUTPUT_FPS)
        if fps <= 1.0 or fps > 240.0:
            fps = float(OUTPUT_FPS)
        self.mode = InputCaps("video/x-raw", width, height, round(fps), 1)
        return first_frame, self.mode

    def read(self, timeout_ns: int = 0) -> np.ndarray | None:
        del timeout_ns
        if self.capture is None:
            return None
        ok, frame = self.capture.read()
        if not ok or frame is None or frame.size == 0:
            return None
        return frame

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
        self.capture = None


class ObsVirtualOutput:
    """Publish processed frames to the signed OBS Virtual Camera driver."""

    def __init__(self) -> None:
        self.camera: Any = None

    def start(self, first_frame: np.ndarray) -> None:
        del first_frame
        try:
            import pyvirtualcam
        except ImportError as error:
            raise EngineError(
                "virtual_camera_missing",
                "pyvirtualcam is not installed. Reinstall Center Stage.",
            ) from error

        errors: list[str] = []
        for backend in ("obs", None):
            try:
                self.camera = pyvirtualcam.Camera(
                    width=OUTPUT_WIDTH,
                    height=OUTPUT_HEIGHT,
                    fps=OUTPUT_FPS,
                    fmt=pyvirtualcam.PixelFormat.BGR,
                    backend=backend,
                    print_fps=False,
                )
                return
            except Exception as error:  # pyvirtualcam raises backend-specific errors
                errors.append(str(error))
        raise EngineError(
            "virtual_camera_missing",
            "OBS Virtual Camera is not available. Install OBS Studio 28 or newer, "
            "then start its Virtual Camera once. " + " | ".join(errors[-2:]),
        )

    def send(self, frame: np.ndarray) -> None:
        if self.camera is None:
            raise EngineError("virtual_camera_missing", "OBS Virtual Camera is not running")
        self.camera.send(frame)

    def close(self) -> None:
        if self.camera is not None:
            try:
                self.camera.close()
            finally:
                self.camera = None


def _status_fields(
    camera: CameraDevice,
    mode: InputCaps,
    measured_fps: float,
    tracking: str,
    zoom: float,
    *,
    center_stage_enabled: bool,
    virtual_camera_visible: bool,
    portrait_enabled: bool,
    portrait_strength: int,
    studio_light_enabled: bool,
    studio_light_strength: int,
    effects_quality: str,
    segmentation_latency_ms: float,
    effects_cpu_cores: float,
) -> dict[str, Any]:
    return {
        "capture_backend": CAPTURE_BACKEND,
        "output_backend": OUTPUT_BACKEND,
        "input": camera.path,
        "input_name": camera.name,
        "input_width": mode.width,
        "input_height": mode.height,
        "input_fps": round(mode.fps, 2),
        "input_format": mode.format_name,
        "output": VIRTUAL_CAMERA_NAME,
        "output_width": OUTPUT_WIDTH,
        "output_height": OUTPUT_HEIGHT,
        "output_fps": OUTPUT_FPS,
        "output_format": "BGR",
        "outputs": {"v4l2": False, "obs": virtual_camera_visible, "pipewire": False},
        "width": OUTPUT_WIDTH,
        "height": OUTPUT_HEIGHT,
        "fps": round(measured_fps, 1),
        "requested_fps": OUTPUT_FPS,
        "format": "BGR",
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


class WindowsPipeline:
    """Process one camera feed and publish preview, status, and optional recording."""

    def __init__(
        self,
        config: dict[str, Any],
        camera: CameraDevice,
        *,
        on_preview: Callable[[np.ndarray], None] | None = None,
        on_status: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        publish_status: bool = True,
    ) -> None:
        self.config = config
        self.camera = camera
        self.on_preview = on_preview
        self.on_status = on_status
        self.on_error = on_error
        self.publish_status = publish_status
        self.stop_event = threading.Event()
        self._latest_lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._recorder: WindowsRecorder | None = None
        self._recorder_lock = threading.Lock()

    def latest_frame(self) -> np.ndarray | None:
        with self._latest_lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def start_recording(self, destination: Path, microphone_id: str | None, muted: bool) -> None:
        with self._recorder_lock:
            if self._recorder is not None:
                raise RuntimeError("recording already active")
            recorder = WindowsRecorder(OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS)
            recorder.start(destination, microphone_id, muted)
            self._recorder = recorder

    def stop_recording(self) -> Path | None:
        with self._recorder_lock:
            recorder = self._recorder
            self._recorder = None
        if recorder is None:
            return None
        return recorder.stop()

    def _write_recording(self, frame: np.ndarray) -> None:
        with self._recorder_lock:
            recorder = self._recorder
        if recorder is not None:
            try:
                recorder.write(frame)
            except Exception as error:
                self._report("recording_failed", f"Recording stopped: {error}")
                self.stop_recording()

    def _report(self, code: str, message: str) -> None:
        if self.on_error is not None:
            self.on_error(code, message)

    def run(self) -> None:
        controls = camera_control_values(self.config, self.camera.camera_id)
        apply_saved_controls(self.camera, controls)
        center_stage_enabled = bool(self.config.get("center_stage_enabled", True))
        mirror_output = bool(self.config.get("mirror_output", False))
        portrait_enabled = bool(self.config.get("portrait_enabled", False))
        portrait_strength = int(self.config.get("portrait_strength", 50))
        studio_light_enabled = bool(self.config.get("studio_light_enabled", False))
        studio_light_strength = int(self.config.get("studio_light_strength", 50))

        capture: WindowsCameraCapture | None = None
        output: ObsVirtualOutput | None = None
        detector = None
        segmenter: LivePersonSegmenter | None = None
        try:
            capture = WindowsCameraCapture(self.camera)
            frame, mode = capture.start()
            detector = create_face_detector(model_path())
            framer = AutoFramer(mode.width, mode.height, center_stage_enabled=center_stage_enabled)
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
            output = ObsVirtualOutput()
            virtual_camera_visible = False
            try:
                output.start(latest_output)
                virtual_camera_visible = True
            except EngineError as error:
                output = None
                self._report(error.code, str(error))

            with self._latest_lock:
                self._latest_frame = latest_output.copy()
            if self.on_preview is not None:
                self.on_preview(_preview_frame(latest_output))
            measured_fps = fps_counter.tick(now)
            status = _status_fields(
                self.camera,
                mode,
                measured_fps,
                snapshot.status,
                snapshot.zoom,
                center_stage_enabled=center_stage_enabled,
                virtual_camera_visible=virtual_camera_visible,
                portrait_enabled=portrait_enabled,
                portrait_strength=portrait_strength,
                studio_light_enabled=studio_light_enabled,
                studio_light_strength=studio_light_strength,
                effects_quality=(quality_governor.level if quality_governor else "off"),
                segmentation_latency_ms=segmenter.latency_ms if segmenter else 0.0,
                effects_cpu_cores=quality_governor.cpu_cores if quality_governor else 0.0,
            )
            self._emit_status(status)
            if self.publish_status:
                write_status("running", "ready", **status)

            last_camera_frame = now
            last_status_update = 0.0
            last_config_check = now
            effects_active = portrait_enabled or studio_light_enabled
            next_output_at = time.monotonic() + 1.0 / OUTPUT_FPS

            while not self.stop_event.is_set():
                new_frame = capture.read()
                now = time.monotonic()
                if new_frame is not None:
                    frame = new_frame
                    last_camera_frame = now
                    if now - last_config_check >= CONFIG_POLL_INTERVAL:
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
                                segmentation_model_path(),
                                person_mask.shape[1],
                                person_mask.shape[0],
                            )
                            quality_governor = AdaptiveQualityGovernor(
                                DeviceCapabilityProfile(
                                    mode.width, mode.height, mode.fps, segmenter.gpu_enabled
                                )
                            )
                            last_segment_timestamp = -1
                            segmenter.submit(clean_output, int(now * 1000))
                        last_config_check = now

                    detection_ran = center_stage_enabled and frame_index % DETECTION_INTERVAL == 0
                    faces = detect_faces(frame, detector) if detection_ran else ()
                    snapshot = framer.update(faces, detection_ran, now)
                    clean_output = crop_and_resize(frame, snapshot.crop, OUTPUT_WIDTH, OUTPUT_HEIGHT)
                    effects_active = portrait_enabled or studio_light_enabled
                    if effects_active and segmenter is not None:
                        interval = (
                            quality_governor.segmentation_interval
                            if quality_governor
                            else SEGMENTATION_INTERVAL
                        )
                        if frame_index % interval == 1:
                            segmenter.submit(clean_output, int(now * 1000))
                        next_mask, last_segment_timestamp = segmenter.latest_after(
                            last_segment_timestamp
                        )
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
                    latest_output = cv2.flip(clean_output, 1) if mirror_output else clean_output
                    with self._latest_lock:
                        self._latest_frame = latest_output.copy()
                    frame_index += 1
                elif now - last_camera_frame > CAMERA_STALL_SECONDS:
                    raise EngineError("camera_disconnected", "Camera stopped returning frames")

                self._write_recording(latest_output)
                if output is not None:
                    try:
                        output.send(latest_output)
                    except EngineError as error:
                        self._report(error.code, str(error))
                        output.close()
                        output = None
                        virtual_camera_visible = False
                if self.on_preview is not None and frame_index % 2 == 0:
                    self.on_preview(_preview_frame(latest_output))
                measured_fps = fps_counter.tick(now)
                if effects_active and quality_governor is not None and segmenter is not None:
                    quality_governor.update(measured_fps, segmenter.latency_ms, now)
                if now - last_status_update >= 0.5:
                    status = _status_fields(
                        self.camera,
                        mode,
                        measured_fps,
                        snapshot.status,
                        snapshot.zoom,
                        center_stage_enabled=center_stage_enabled,
                        virtual_camera_visible=virtual_camera_visible,
                        portrait_enabled=portrait_enabled,
                        portrait_strength=portrait_strength,
                        studio_light_enabled=studio_light_enabled,
                        studio_light_strength=studio_light_strength,
                        effects_quality=(quality_governor.level if quality_governor and effects_active else "off"),
                        segmentation_latency_ms=segmenter.latency_ms if segmenter else 0.0,
                        effects_cpu_cores=quality_governor.cpu_cores if quality_governor else 0.0,
                    )
                    self._emit_status(status)
                    if self.publish_status:
                        write_status("running", "ready", **status)
                    last_status_update = now

                delay = next_output_at - time.monotonic()
                next_output_at += 1.0 / OUTPUT_FPS
                if delay > 0:
                    self.stop_event.wait(delay)
                elif delay < -0.25:
                    next_output_at = time.monotonic() + 1.0 / OUTPUT_FPS
        except EngineError as error:
            self._report(error.code, str(error))
            if self.publish_status:
                write_status("error", error.code, details={"reason": str(error)})
        except Exception as error:
            self._report("internal_error", f"Center Stage stopped: {error}")
            if self.publish_status:
                write_status("error", "internal_error", details={"reason": str(error)})
        finally:
            try:
                self.stop_recording()
            except Exception:
                pass
            if output is not None:
                output.close()
            if detector is not None:
                detector.close()
            if segmenter is not None:
                segmenter.close()
            if capture is not None:
                capture.close()
            self._emit_status({"state": "stopped", "code": "stopped", "details": {}})
            if self.publish_status:
                write_status("stopped", "stopped", details={})

    def _emit_status(self, status: dict[str, Any]) -> None:
        if self.on_status is not None:
            self.on_status(status)

    def stop(self) -> None:
        self.stop_event.set()


def _preview_frame(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    target_width = min(960, width)
    target_height = max(1, round(height * target_width / width))
    return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)


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
        pipeline = WindowsPipeline(config, camera)
        thread = threading.Thread(target=pipeline.run, name="center-stage-engine")
        thread.start()
        while thread.is_alive() and not stop_event.is_set():
            stop_event.wait(0.25)
        pipeline.stop()
        thread.join(timeout=5)
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
    parser = argparse.ArgumentParser(description="Center Stage Windows engine")
    parser.add_argument("--mark-stopped", action="store_true")
    args = parser.parse_args(argv)
    if args.mark_stopped:
        write_status("stopped", "stopped", details={})
        return 0
    return run_service()


if __name__ == "__main__":
    raise SystemExit(main())


def run_pipeline(
    config: dict[str, Any],
    camera: CameraDevice,
    *,
    preview: bool = True,
    publish_status: bool = True,
) -> None:
    """Run the Windows pipeline with the CLI's blocking contract."""
    pipeline = WindowsPipeline(
        config,
        camera,
        publish_status=publish_status,
    )
    del preview
    pipeline.run()
