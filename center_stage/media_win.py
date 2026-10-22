"""FFmpeg-backed Windows recording with optional microphone audio."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any


def ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise RuntimeError(
            "FFmpeg is missing. Reinstall Center Stage or install FFmpeg and add it to PATH."
        )


class WindowsRecorder:
    """Pipe processed BGR frames to FFmpeg and mux microphone audio on stop."""

    def __init__(self, width: int, height: int, fps: int):
        self.width = width
        self.height = height
        self.fps = fps
        self._process: subprocess.Popen[bytes] | None = None
        self._destination: Path | None = None
        self._temp_dir: Path | None = None
        self._video_path: Path | None = None
        self._audio_path: Path | None = None
        self._audio_stream: Any = None
        self._audio_file: wave.Wave_write | None = None
        self._audio_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.last_warning = ""

    def start(self, destination: Path, microphone_id: str | None, muted: bool) -> None:
        if self._process is not None:
            raise RuntimeError("recording already active")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._temp_dir = Path(tempfile.mkdtemp(prefix=".center-stage-", dir=destination.parent))
        except OSError:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="center-stage-"))
        self._destination = destination
        self._video_path = self._temp_dir / "video.mp4"
        ffmpeg = ffmpeg_executable()
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            str(self.fps),
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(self._video_path),
        ]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creation_flags,
        )
        if not muted and microphone_id:
            try:
                self._start_audio(microphone_id)
            except Exception as error:
                self.last_warning = f"Microphone unavailable: {error}"
                self._stop_audio()

    def _start_audio(self, microphone_id: str) -> None:
        import sounddevice as sd

        device: int | None = None
        if str(microphone_id).isdigit():
            device = int(microphone_id)
        else:
            for index, item in enumerate(sd.query_devices()):
                if item.get("max_input_channels", 0) > 0 and item.get("name") == microphone_id:
                    device = index
                    break
        self._audio_path = self._temp_dir / "audio.wav" if self._temp_dir else None
        if self._audio_path is None:
            return
        self._audio_file = wave.open(str(self._audio_path), "wb")
        self._audio_file.setnchannels(1)
        self._audio_file.setsampwidth(2)
        self._audio_file.setframerate(48000)

        def callback(indata: Any, _frames: int, _time: Any, status: Any) -> None:
            if status:
                return
            with self._audio_lock:
                if self._audio_file is not None:
                    self._audio_file.writeframes(indata.tobytes())

        self._audio_stream = sd.InputStream(
            samplerate=48000,
            channels=1,
            dtype="int16",
            device=device,
            callback=callback,
        )
        self._audio_stream.start()

    def write(self, frame: Any) -> None:
        with self._write_lock:
            process = self._process
            if process is None or process.stdin is None:
                raise RuntimeError("recording is not active")
            if process.poll() is not None:
                detail = self._stderr_text(process)
                raise RuntimeError(f"FFmpeg stopped unexpectedly: {detail}")
            process.stdin.write(frame.tobytes())

    def stop(self) -> Path | None:
        try:
            with self._write_lock:
                process = self._process
                self._process = None
                if process is not None and process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                if process is not None:
                    try:
                        return_code = process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        return_code = -1
                    if return_code != 0:
                        detail = self._stderr_text(process)
                        raise RuntimeError(
                            f"FFmpeg could not finalize the video: {detail}"
                        )

            self._stop_audio()
            video = self._video_path
            destination = self._destination
            if video is None or not video.exists() or video.stat().st_size == 0:
                self._cleanup()
                return None
            if destination is None:
                path = video
                self._cleanup(keep=video)
                return path

            muxed = self._mux_audio(video)
            try:
                os.replace(muxed, destination)
                result = destination
            except OSError:
                result = muxed
            self._cleanup(keep=result)
            return result
        except Exception:
            self._stop_audio()
            self._cleanup()
            raise

    def _mux_audio(self, video: Path) -> Path:
        audio = self._audio_path
        if audio is None or not audio.exists() or audio.stat().st_size <= 44:
            return video
        ffmpeg = ffmpeg_executable()
        output = video.with_name("muxed.mp4")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            creationflags=creation_flags,
        )
        if result.returncode != 0 or not output.exists():
            self.last_warning = "Audio could not be muxed; the video was kept without audio."
            return video
        video.unlink(missing_ok=True)
        return output

    def _stop_audio(self) -> None:
        stream = self._audio_stream
        self._audio_stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._audio_lock:
            audio_file = self._audio_file
            self._audio_file = None
            if audio_file is not None:
                try:
                    audio_file.close()
                except Exception:
                    pass

    def _stderr_text(self, process: subprocess.Popen[bytes] | None = None) -> str:
        process = process or self._process
        if process is None or process.stderr is None:
            return "no details"
        try:
            return (
                process.stderr.read().decode("utf-8", "replace").strip()[-500:]
                or "no details"
            )
        except Exception:
            return "no details"
        finally:
            try:
                process.stderr.close()
            except Exception:
                pass

    def _cleanup(self, keep: Path | None = None) -> None:
        temp_dir = self._temp_dir
        self._temp_dir = None
        self._video_path = None
        self._audio_path = None
        if temp_dir is None:
            return
        if keep is not None and keep.parent == temp_dir:
            return
        shutil.rmtree(temp_dir, ignore_errors=True)
