"""Face-driven framing primitives shared by the CLI and desktop engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import cv2

BASE_ZOOM = 1.15
MAX_ZOOM = 1.5
DESIRED_FACE_RATIO = 0.16
ZOOM_RATIO_LOW = 0.145
ZOOM_RATIO_HIGH = 0.175
FACE_OUTPUT_Y = 0.42

DEAD_ZONE_X = 0.08
DEAD_ZONE_Y = 0.06
FACE_SMOOTH_TAU = 0.12
PAN_SMOOTH_TAU = 0.45
RECENTER_SMOOTH_TAU = 0.85
ZOOM_SMOOTH_TAU = 0.90
MAX_PAN_SPEED_RATIO = 0.65
LOST_HOLD_SECONDS = 1.25


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def smoothing_alpha(dt: float, time_constant: float) -> float:
    if time_constant <= 0.0:
        return 1.0
    return 1.0 - math.exp(-max(0.0, dt) / time_constant)


@dataclass(frozen=True)
class FaceBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0

    @property
    def area(self) -> float:
        return self.width * self.height

    def smoothed(self, target: "FaceBox", alpha: float) -> "FaceBox":
        return FaceBox(
            x=self.x + (target.x - self.x) * alpha,
            y=self.y + (target.y - self.y) * alpha,
            width=self.width + (target.width - self.width) * alpha,
            height=self.height + (target.height - self.height) * alpha,
        )


@dataclass(frozen=True)
class CropWindow:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class FramingSnapshot:
    status: str
    face_box: FaceBox | None
    crop: CropWindow
    zoom: float


def select_primary_face(
    faces: Sequence[FaceBox], previous_face: FaceBox | None
) -> FaceBox | None:
    if not faces:
        return None
    if previous_face is None:
        return max(faces, key=lambda face: face.area)
    return min(
        faces,
        key=lambda face: (
            (face.center_x - previous_face.center_x) ** 2
            + (face.center_y - previous_face.center_y) ** 2
        ),
    )


class AutoFramer:
    def __init__(self, frame_width: int, frame_height: int, auto_zoom: bool):
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError("Frame dimensions must be positive")
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.auto_zoom = auto_zoom
        self.camera_x = frame_width / 2.0
        self.camera_y = frame_height / 2.0
        self.current_zoom = BASE_ZOOM
        self.target_zoom = BASE_ZOOM
        self.smoothed_face: FaceBox | None = None
        self.last_face_seen: float | None = None
        self.last_detection_time: float | None = None
        self.last_detection_had_face = False
        self.last_update_time: float | None = None

    def update(
        self, faces: Sequence[FaceBox], detection_ran: bool, now: float
    ) -> FramingSnapshot:
        dt = self._frame_dt(now)
        if detection_ran:
            self._update_detection(faces, now)

        if self.last_face_seen is None:
            status = "SEARCHING"
            self._recenter(dt)
        else:
            face_age = max(0.0, now - self.last_face_seen)
            if self.last_detection_had_face and self.smoothed_face is not None:
                status = "TRACKING"
                self._track_face(self.smoothed_face, dt)
            elif face_age <= LOST_HOLD_SECONDS:
                status = "HOLD"
            else:
                status = "RECENTERING"
                self._recenter(dt)
                self.smoothed_face = None

        if not self.auto_zoom:
            self.target_zoom = BASE_ZOOM
            self.current_zoom = BASE_ZOOM
        self._clamp_camera_to_frame()
        return FramingSnapshot(
            status=status,
            face_box=self.smoothed_face,
            crop=self.crop_window(),
            zoom=self.current_zoom,
        )

    def crop_window(self) -> CropWindow:
        crop_height = max(
            1, min(int(round(self.frame_height / self.current_zoom)), self.frame_height)
        )
        crop_width = max(
            1,
            min(
                int(round(crop_height * self.frame_width / self.frame_height)),
                self.frame_width,
            ),
        )
        x = int(round(self.camera_x - crop_width / 2.0))
        y = int(round(self.camera_y - crop_height / 2.0))
        return CropWindow(
            x=max(0, min(x, self.frame_width - crop_width)),
            y=max(0, min(y, self.frame_height - crop_height)),
            width=crop_width,
            height=crop_height,
        )

    def _frame_dt(self, now: float) -> float:
        dt = (
            1.0 / 30.0
            if self.last_update_time is None
            else clamp(now - self.last_update_time, 1.0 / 240.0, 0.1)
        )
        self.last_update_time = now
        return dt

    def _update_detection(self, faces: Sequence[FaceBox], now: float) -> None:
        selected = select_primary_face(faces, self.smoothed_face)
        if selected is None:
            self.last_detection_had_face = False
            self.last_detection_time = now
            return
        if self.smoothed_face is None or self.last_detection_time is None:
            self.smoothed_face = selected
        else:
            detection_dt = clamp(now - self.last_detection_time, 0.0, 0.25)
            self.smoothed_face = self.smoothed_face.smoothed(
                selected, smoothing_alpha(detection_dt, FACE_SMOOTH_TAU)
            )
        self.last_face_seen = now
        self.last_detection_time = now
        self.last_detection_had_face = True

    def _track_face(self, face: FaceBox, dt: float) -> None:
        if self.auto_zoom:
            source_face_ratio = face.height / self.frame_height
            framed_face_ratio = source_face_ratio * self.current_zoom
            if framed_face_ratio < ZOOM_RATIO_LOW or framed_face_ratio > ZOOM_RATIO_HIGH:
                self.target_zoom = clamp(
                    DESIRED_FACE_RATIO / max(source_face_ratio, 1e-6),
                    BASE_ZOOM,
                    MAX_ZOOM,
                )
            self.current_zoom += (
                self.target_zoom - self.current_zoom
            ) * smoothing_alpha(dt, ZOOM_SMOOTH_TAU)
            self.current_zoom = clamp(self.current_zoom, BASE_ZOOM, MAX_ZOOM)

        crop_width, crop_height = self._floating_crop_size()
        desired_x = face.center_x
        desired_y = face.center_y + (0.5 - FACE_OUTPUT_Y) * crop_height
        error_x = desired_x - self.camera_x
        error_y = desired_y - self.camera_y
        target_x = self.camera_x + self._outside_dead_zone(
            error_x, crop_width * DEAD_ZONE_X
        )
        target_y = self.camera_y + self._outside_dead_zone(
            error_y, crop_height * DEAD_ZONE_Y
        )
        self._move_camera(target_x, target_y, dt, PAN_SMOOTH_TAU)

    def _recenter(self, dt: float) -> None:
        self.target_zoom = BASE_ZOOM
        self.current_zoom += (BASE_ZOOM - self.current_zoom) * smoothing_alpha(
            dt, RECENTER_SMOOTH_TAU
        )
        self.current_zoom = clamp(self.current_zoom, BASE_ZOOM, MAX_ZOOM)
        self._move_camera(
            self.frame_width / 2.0,
            self.frame_height / 2.0,
            dt,
            RECENTER_SMOOTH_TAU,
        )

    def _move_camera(
        self, target_x: float, target_y: float, dt: float, time_constant: float
    ) -> None:
        alpha = smoothing_alpha(dt, time_constant)
        move_x = (target_x - self.camera_x) * alpha
        move_y = (target_y - self.camera_y) * alpha
        max_x = self.frame_width * MAX_PAN_SPEED_RATIO * dt
        max_y = self.frame_height * MAX_PAN_SPEED_RATIO * dt
        self.camera_x += clamp(move_x, -max_x, max_x)
        self.camera_y += clamp(move_y, -max_y, max_y)

    def _floating_crop_size(self) -> tuple[float, float]:
        crop_height = self.frame_height / self.current_zoom
        return crop_height * self.frame_width / self.frame_height, crop_height

    def _clamp_camera_to_frame(self) -> None:
        width, height = self._floating_crop_size()
        self.camera_x = clamp(self.camera_x, width / 2.0, self.frame_width - width / 2.0)
        self.camera_y = clamp(
            self.camera_y, height / 2.0, self.frame_height - height / 2.0
        )

    @staticmethod
    def _outside_dead_zone(error: float, margin: float) -> float:
        if abs(error) <= margin:
            return 0.0
        return math.copysign(abs(error) - margin, error)


def crop_and_resize(
    frame,
    crop: CropWindow,
    output_width: int | None = None,
    output_height: int | None = None,
):
    cropped = frame[crop.y : crop.y + crop.height, crop.x : crop.x + crop.width]
    if cropped.size == 0:
        raise RuntimeError(f"Calculated an empty crop: {crop}")
    frame_height, frame_width = frame.shape[:2]
    target_width = output_width or frame_width
    target_height = output_height or frame_height
    if (
        crop.width == frame_width
        and crop.height == frame_height
        and target_width == frame_width
        and target_height == frame_height
    ):
        return cropped.copy()
    interpolation = (
        cv2.INTER_AREA
        if target_width < crop.width or target_height < crop.height
        else cv2.INTER_LINEAR
    )
    return cv2.resize(cropped, (target_width, target_height), interpolation=interpolation)


def face_box_in_output(
    face: FaceBox,
    crop: CropWindow,
    output_width: int,
    output_height: int,
    mirrored: bool,
) -> tuple[int, int, int, int]:
    scale_x = output_width / crop.width
    scale_y = output_height / crop.height
    x = int(round((face.x - crop.x) * scale_x))
    y = int(round((face.y - crop.y) * scale_y))
    width = int(round(face.width * scale_x))
    height = int(round(face.height * scale_y))
    if mirrored:
        x = output_width - x - width
    return x, y, width, height


def make_debug_preview(frame, snapshot: FramingSnapshot, fps: float):
    preview = cv2.flip(frame, 1)
    height, width = preview.shape[:2]
    cv2.rectangle(
        preview,
        (
            int(round(width * (0.5 - DEAD_ZONE_X))),
            int(round(height * (FACE_OUTPUT_Y - DEAD_ZONE_Y))),
        ),
        (
            int(round(width * (0.5 + DEAD_ZONE_X))),
            int(round(height * (FACE_OUTPUT_Y + DEAD_ZONE_Y))),
        ),
        (255, 180, 0),
        1,
    )
    if snapshot.face_box is not None:
        x, y, box_width, box_height = face_box_in_output(
            snapshot.face_box, snapshot.crop, width, height, mirrored=True
        )
        color = (0, 255, 0) if snapshot.status == "TRACKING" else (0, 210, 255)
        cv2.rectangle(preview, (x, y), (x + box_width, y + box_height), color, 2)
    text = f"{snapshot.status}  zoom {snapshot.zoom:.2f}x  fps {fps:.1f}"
    cv2.putText(preview, text, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 4)
    cv2.putText(preview, text, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
    return preview
