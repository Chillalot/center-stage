import errno
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from center_stage.media import (
    GST_AVAILABLE,
    format_duration,
    four_up,
    list_media,
    list_media_folders,
    media_preview,
    recording_encoder_candidates,
    save_jpeg,
    unique_media_path,
    VideoRecorder,
)


class MediaTests(unittest.TestCase):
    def test_unique_names_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            now = datetime(2026, 8, 3, 12, 0, 0)
            first = unique_media_path(folder, "Photo", ".jpg", now)
            first.touch()
            second = unique_media_path(folder, "Photo", ".jpg", now)
        self.assertEqual(first.name, "Photo_2026-08-03_12-00-00.jpg")
        self.assertEqual(second.name, "Photo_2026-08-03_12-00-00_2.jpg")

    def test_four_up_layout_and_atomic_jpeg(self):
        frames = [np.full((20, 30, 3), value, np.uint8) for value in (10, 20, 30, 40)]
        collage = four_up(frames)
        self.assertEqual(collage.shape, (40, 60, 3))
        with tempfile.TemporaryDirectory() as directory:
            path = save_jpeg(collage, Path(directory) / "FourUp.jpg")
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(list_media(path.parent), [path])

    def test_four_up_rejects_incomplete_capture(self):
        with self.assertRaises(ValueError):
            four_up([np.zeros((2, 2, 3), np.uint8)])

    def test_video_preview_reports_duration_and_seeks_past_the_first_frame(self):
        capture = MagicMock()
        capture.get.side_effect = lambda prop: {
            5: 30.0,  # cv2.CAP_PROP_FPS
            7: 1950.0,  # cv2.CAP_PROP_FRAME_COUNT
        }.get(prop, 0.0)
        capture.read.return_value = (True, np.full((90, 160, 3), 80, np.uint8))
        with patch("center_stage.media.cv2.VideoCapture", return_value=capture):
            preview = media_preview(Path("clip.mp4"))
        self.assertEqual(preview.duration_seconds, 65)
        self.assertEqual(preview.frame.shape, (64, 112, 3))
        capture.set.assert_any_call(0, 2000.0)  # cv2.CAP_PROP_POS_MSEC
        capture.release.assert_called_once()

    def test_corrupt_video_still_has_a_visible_thumbnail_card(self):
        capture = MagicMock()
        capture.get.return_value = 0.0
        capture.read.return_value = (False, None)
        with patch("center_stage.media.cv2.VideoCapture", return_value=capture):
            preview = media_preview(Path("broken.mp4"))
        self.assertEqual(preview.frame.shape, (64, 112, 3))
        self.assertIsNone(preview.duration_seconds)

    @unittest.skipUnless(GST_AVAILABLE, "GStreamer is not installed")
    def test_duration_format_and_recording_quality_contract(self):
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3661), "1:01:01")
        candidates = dict(recording_encoder_candidates())
        self.assertTrue(candidates)
        source = " ".join(candidates.values())
        self.assertNotIn("tune=zerolatency", source)
        self.assertNotIn("bitrate=8000", source)
        self.assertTrue("init-qp=24" in source or "bitrate=16000" in source)

    def test_media_from_picture_video_and_legacy_folders_is_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folders = tuple(root / name for name in ("Pictures", "Videos", "Legacy"))
            for index, folder in enumerate(folders):
                folder.mkdir()
                path = folder / ("clip.mp4" if index == 1 else "photo.jpg")
                path.write_bytes(b"media")
                os.utime(path, (index + 1, index + 1))
            media = list_media_folders(folders)
        self.assertEqual([path.parent.name for path in media], ["Legacy", "Videos", "Pictures"])

    def test_recording_moves_across_filesystems_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / "recovery.mp4.part"
            destination = root / "Videos" / "Video.mp4"
            recovery.write_bytes(b"complete-video")
            pipeline = MagicMock()
            bus = pipeline.get_bus.return_value
            recorder = VideoRecorder(root / "preview.sock", root, lambda *_args: None)
            recorder.pipeline = pipeline
            recorder.recovery_path = recovery
            recorder.destination = destination
            real_replace = os.replace

            def replace(source, target):
                if Path(source) == recovery:
                    raise OSError(errno.EXDEV, "cross-device link")
                return real_replace(source, target)

            with patch("center_stage.media.os.replace", side_effect=replace):
                result = recorder.stop()
            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), b"complete-video")
            self.assertFalse(recovery.exists())


if __name__ == "__main__":
    unittest.main()
