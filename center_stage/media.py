"""Photo Booth media storage, thumbnails, and GStreamer recording."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402


MEDIA_SUFFIXES = {".jpg", ".jpeg", ".mp4"}


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


def thumbnail(path: Path, size: tuple[int, int] = (112, 64)) -> np.ndarray | None:
    if path.suffix.lower() == ".mp4":
        capture = cv2.VideoCapture(str(path))
        ok, frame = capture.read()
        capture.release()
        if not ok:
            return None
    else:
        frame = cv2.imread(str(path))
        if frame is None:
            return None
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


@dataclass
class VideoRecorder:
    """Records the processed shared-memory feed with live mic/silence selection."""

    preview_socket: Path
    recovery_folder: Path
    on_message: Callable[[Gst.Message], None]
    pipeline: Gst.Pipeline | None = None
    destination: Path | None = None
    recovery_path: Path | None = None

    def start(self, destination: Path, microphone_id: str | None, muted: bool) -> None:
        if self.pipeline is not None:
            raise RuntimeError("recording already active")
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
            'queue ! videoconvert ! x264enc tune=zerolatency speed-preset=veryfast '
            'bitrate=8000 key-int-max=60 ! h264parse ! queue ! '
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
        if audio:
            description = (
                f'mp4mux name=mux faststart=true ! filesink location="{escaped_file}" '
                f'{video_source}mux. {audio}'
            )
        else:
            description = (
                f'{video_source}mp4mux faststart=true ! filesink location="{escaped_file}"'
            )
        pipeline = Gst.parse_launch(description)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            bus.remove_signal_watch()
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("recording pipeline failed")
        self.pipeline = pipeline

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
        except OSError:
            return recovery
