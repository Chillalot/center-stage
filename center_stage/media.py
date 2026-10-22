"""Photo Booth media storage, thumbnails, and GStreamer recording."""

from __future__ import annotations

import errno
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402

    GST_AVAILABLE = True
except (ImportError, AttributeError, ValueError):
    Gst = None  # type: ignore[assignment]
    GST_AVAILABLE = False


MEDIA_SUFFIXES = {".jpg", ".jpeg", ".mp4"}
LOGGER = logging.getLogger("center-stage.media")


def unique_media_path(folder: Path, prefix: str, suffix: str, now: datetime | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    candidate = folder / f"{prefix}_{stamp}{suffix}"
    index = 2
    while candidate.exists():
        candidate = folder / f"{prefix}_{stamp}_{index}{suffix}"
        index += 1
    return candidate


def save_jpeg(frame: np.ndarray, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, payload = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise OSError("JPEG encoding failed")
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(payload.tobytes())
    os.replace(temporary, destination)
    return destination


def four_up(frames: list[np.ndarray]) -> np.ndarray:
    if len(frames) != 4:
        raise ValueError("four-up requires exactly four frames")
    height = min(frame.shape[0] for frame in frames)
    width = min(frame.shape[1] for frame in frames)
    cells = [cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA) for frame in frames]
    return np.vstack((np.hstack(cells[:2]), np.hstack(cells[2:])))


def list_media(folder: Path, limit: int = 12) -> list[Path]:
    try:
        media = [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() in MEDIA_SUFFIXES]
    except OSError:
        return []
    return sorted(media, key=lambda item: item.stat().st_mtime, reverse=True)[:limit]


def list_media_folders(folders: tuple[Path, ...], limit: int = 12) -> list[Path]:
    media: dict[Path, Path] = {}
    for folder in folders:
        for path in list_media(folder, limit=limit):
            media[path.resolve()] = path
    return sorted(media.values(), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]


@dataclass(frozen=True)
class MediaPreview:
    frame: np.ndarray | None
    duration_seconds: int | None = None


def media_preview(path: Path, size: tuple[int, int] = (112, 64)) -> MediaPreview:
    if path.suffix.lower() == ".mp4":
        capture = cv2.VideoCapture(str(path))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = int(round(frame_count / fps)) if fps > 0 and frame_count > 0 else None
        seek_ms = min(2000.0, max(350.0, (duration or 0) * 100.0))
        capture.set(cv2.CAP_PROP_POS_MSEC, seek_ms)
        ok, frame = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
        capture.release()
        if not ok:
            frame = np.full((size[1], size[0], 3), 32, dtype=np.uint8)
    else:
        frame = cv2.imread(str(path))
        if frame is None:
            return MediaPreview(None)
        duration = None
    return MediaPreview(
        cv2.resize(frame, size, interpolation=cv2.INTER_AREA),
        duration,
    )


def thumbnail(path: Path, size: tuple[int, int] = (112, 64)) -> np.ndarray | None:
    return media_preview(path, size).frame


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def recording_encoder_candidates() -> list[tuple[str, str]]:
    """Return quality-oriented encoders in preferred runtime order."""
    if Gst is None:
        return []
    Gst.init(None)
    candidates: list[tuple[str, str]] = []
    if Gst.ElementFactory.find("vaapih264enc") is not None:
        candidates.append(
            (
                "vaapi",
                "videoconvert n-threads=2 ! video/x-raw,format=NV12 ! "
                "vaapih264enc tune=low-power rate-control=cqp init-qp=24 "
                "keyframe-period=60 cabac=true dct8x8=true",
            )
        )
    if Gst.ElementFactory.find("x264enc") is not None:
        candidates.append(
            (
                "x264",
                "videoconvert n-threads=2 ! video/x-raw,format=I420 ! "
                "x264enc speed-preset=veryfast bitrate=16000 key-int-max=60 "
                "bframes=2 ref=2 cabac=true noise-reduction=200 threads=0",
            )
        )
    if Gst.ElementFactory.find("openh264enc") is not None:
        candidates.append(
            (
                "openh264",
                "videoconvert n-threads=2 ! video/x-raw,format=I420 ! "
                "openh264enc complexity=low bitrate=16000000 max-bitrate=24000000 "
                "rate-control=bitrate enable-denoise=true enable-frame-skip=false "
                "gop-size=60 multi-thread=0 usage-type=camera",
            )
        )
    return candidates


@dataclass
class VideoRecorder:
    """Records the processed shared-memory feed with live mic/silence selection."""

    preview_socket: Path
    recovery_folder: Path
    on_message: Callable[..., None]
    pipeline: Gst.Pipeline | None = None
    destination: Path | None = None
    recovery_path: Path | None = None
    encoder_name: str = ""

    def start(self, destination: Path, microphone_id: str | None, muted: bool) -> None:
        if self.pipeline is not None:
            raise RuntimeError("recording already active")
        if Gst is None:
            raise RuntimeError("GStreamer recording is not available on this platform")
        Gst.init(None)
        self.recovery_folder.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="center-stage-", suffix=".mp4.part", dir=self.recovery_folder)
        os.close(fd)
        self.destination = destination
        self.recovery_path = Path(temporary)
        escaped_socket = str(self.preview_socket).replace('"', '\\"')
        escaped_file = temporary.replace('"', '\\"')
        video_source = (
            f'shmsrc socket-path="{escaped_socket}" is-live=true do-timestamp=true ! '
            'video/x-raw,format=YUY2,width=1920,height=1080,framerate=30/1 ! '
            'queue leaky=downstream max-size-buffers=4 max-size-bytes=0 max-size-time=0 ! '
            'videorate ! video/x-raw,framerate=30/1 ! '
        )
        microphone = ""
        if microphone_id:
            escaped_mic = microphone_id.replace('"', '\\"')
            microphone = (
                f'pulsesrc device="{escaped_mic}" do-timestamp=true ! '
                'audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=2 ! '
                'volume name=mic_volume ! voaacenc bitrate=192000 ! aacparse ! queue ! mux.'
            )
        audio = ""
        if not muted and microphone_id:
            audio = microphone
        mux = (
            f'mp4mux name=mux faststart=true presentation-time=true interleave-time=500000000 '
            f'! filesink location="{escaped_file}" '
        )
        failures: list[str] = []
        candidates = recording_encoder_candidates()
        if not candidates:
            self.recovery_path.unlink(missing_ok=True)
            self.recovery_path = None
            self.destination = None
            raise RuntimeError("no H.264 encoder is installed")
        for encoder_name, encoder in candidates:
            description = (
                f'{mux}{video_source}{encoder} ! h264parse config-interval=-1 ! '
                f'queue max-size-buffers=8 max-size-bytes=0 max-size-time=0 ! mux. {audio}'
            )
            pipeline: Gst.Pipeline | None = None
            bus: Gst.Bus | None = None
            try:
                pipeline = Gst.parse_launch(description)
                bus = pipeline.get_bus()
                state_result = pipeline.set_state(Gst.State.PLAYING)
                settled, _current, _pending = pipeline.get_state(750 * Gst.MSECOND)
                error_message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR)
                if state_result == Gst.StateChangeReturn.FAILURE or settled == Gst.StateChangeReturn.FAILURE:
                    raise RuntimeError("encoder failed to enter PLAYING state")
                if error_message is not None:
                    error, _debug = error_message.parse_error()
                    raise RuntimeError(error.message)
                bus.add_signal_watch()
                bus.connect("message", self.on_message)
                self.pipeline = pipeline
                self.encoder_name = encoder_name
                LOGGER.info("Recording encoder selected name=%s", encoder_name)
                return
            except Exception as error:
                failures.append(f"{encoder_name}: {error}")
                if pipeline is not None:
                    pipeline.set_state(Gst.State.NULL)
                LOGGER.warning("Recording encoder unavailable name=%s error=%s", encoder_name, error)
        self.encoder_name = ""
        if self.recovery_path is not None:
            self.recovery_path.unlink(missing_ok=True)
        self.recovery_path = None
        self.destination = None
        raise RuntimeError("recording pipeline failed (" + "; ".join(failures) + ")")

    def set_muted(self, muted: bool) -> None:
        if self.pipeline is None:
            return
        volume = self.pipeline.get_by_name("mic_volume")
        if volume is not None:
            volume.set_property("mute", muted)

    def stop(self) -> Path | None:
        pipeline = self.pipeline
        if pipeline is None:
            return None
        pipeline.send_event(Gst.Event.new_eos())
        bus = pipeline.get_bus()
        bus.timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS)
        bus.remove_signal_watch()
        pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.encoder_name = ""
        recovery = self.recovery_path
        destination = self.destination
        self.recovery_path = None
        self.destination = None
        if recovery is None or not recovery.exists() or recovery.stat().st_size == 0:
            return None
        if destination is None:
            return recovery
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(recovery, destination)
            return destination
        except OSError as error:
            if error.errno != errno.EXDEV:
                return recovery
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            with recovery.open("rb") as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
            recovery.unlink()
            return destination
        except OSError:
            temporary.unlink(missing_ok=True)
            return recovery
