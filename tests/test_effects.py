import unittest
import time
from pathlib import Path

import numpy as np

from center_stage.engine import (
    AdaptiveQualityGovernor,
    AmbientLightEstimator,
    AmbientLightState,
    DeviceCapabilityProfile,
    LivePersonSegmenter,
    TemporalMaskStabilizer,
    apply_camera_effects,
    create_person_segmenter,
    segment_person,
)


class EffectTests(unittest.TestCase):
    def test_portrait_blurs_only_background(self):
        checker = (np.indices((80, 120)).sum(axis=0) % 2 * 255).astype(np.uint8)
        frame = np.repeat(checker[..., None], 3, axis=2)
        mask = np.zeros((80, 120), np.float32)
        mask[:, :60] = 1.0
        output = apply_camera_effects(
            frame,
            mask,
            portrait_enabled=True,
            portrait_strength=100,
            studio_light_enabled=False,
            studio_light_strength=0,
        )
        np.testing.assert_array_equal(output[:, :60], frame[:, :60])
        self.assertLess(output[:, 60:].std(), frame[:, 60:].std() * 0.2)

    def test_studio_light_brightens_person_and_dims_background(self):
        frame = np.full((20, 40, 3), 100, np.uint8)
        mask = np.zeros((20, 40), np.float32)
        mask[:, :20] = 1.0
        output = apply_camera_effects(
            frame,
            mask,
            portrait_enabled=False,
            portrait_strength=0,
            studio_light_enabled=True,
            studio_light_strength=100,
        )
        self.assertGreater(float(output[:, :20].mean()), 100)
        self.assertLess(float(output[:, 20:].mean()), 100)

    def test_off_is_pixel_identical(self):
        frame = np.random.default_rng(7).integers(0, 256, (20, 30, 3), np.uint8)
        output = apply_camera_effects(
            frame,
            np.zeros((20, 30), np.float32),
            portrait_enabled=False,
            portrait_strength=100,
            studio_light_enabled=False,
            studio_light_strength=100,
        )
        np.testing.assert_array_equal(output, frame)

    def test_official_segmenter_returns_person_mask_shape(self):
        model = Path(__file__).resolve().parent.parent / "selfie_segmenter_landscape.tflite"
        segmenter = create_person_segmenter(model)
        try:
            mask = segment_person(np.zeros((72, 128, 3), np.uint8), segmenter)
        finally:
            segmenter.close()
        self.assertEqual(mask.shape, (72, 128))
        self.assertTrue(np.all((mask >= 0) & (mask <= 1)))

    def test_segmenter_caps_large_mask_at_compositor_resolution(self):
        model = Path(__file__).resolve().parent.parent / "selfie_segmenter_landscape.tflite"
        segmenter = create_person_segmenter(model)
        try:
            mask = segment_person(np.zeros((1080, 1920, 3), np.uint8), segmenter)
        finally:
            segmenter.close()
        self.assertEqual(mask.shape, (360, 640))

    def test_studio_light_adapts_lift_to_ambient_subject_level(self):
        frame = np.full((80, 120, 3), 90, np.uint8)
        mask = np.zeros((80, 120), np.float32)
        mask[8:74, 30:90] = 1.0
        dark = apply_camera_effects(
            frame,
            mask,
            portrait_enabled=False,
            portrait_strength=0,
            studio_light_enabled=True,
            studio_light_strength=70,
            ambient_light=AmbientLightState(subject_luma=0.18, background_luma=0.35),
        )
        bright = apply_camera_effects(
            frame,
            mask,
            portrait_enabled=False,
            portrait_strength=0,
            studio_light_enabled=True,
            studio_light_strength=70,
            ambient_light=AmbientLightState(subject_luma=0.72, background_luma=0.35),
        )
        self.assertGreater(float(dark[20:55, 42:78].mean()), float(bright[20:55, 42:78].mean()))

    def test_studio_light_preserves_chroma(self):
        frame = np.full((60, 80, 3), (65, 105, 165), np.uint8)
        output = apply_camera_effects(
            frame,
            np.ones((60, 80), np.float32),
            portrait_enabled=False,
            portrait_strength=0,
            studio_light_enabled=True,
            studio_light_strength=80,
            ambient_light=AmbientLightState(subject_luma=0.25),
        )
        import cv2

        source_lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.int16)
        output_lab = cv2.cvtColor(output, cv2.COLOR_BGR2LAB).astype(np.int16)
        self.assertLess(float(np.abs(source_lab[:, :, 1:] - output_lab[:, :, 1:]).mean()), 3.0)

    def test_temporal_mask_releases_departed_subject_quickly(self):
        stabilizer = TemporalMaskStabilizer()
        frame = np.zeros((90, 160, 3), np.uint8)
        first = np.zeros((90, 160), np.float32)
        first[:, :80] = 1.0
        stabilizer.update(frame, first)
        moved = np.zeros_like(first)
        moved[:, 80:] = 1.0
        result = stabilizer.update(frame, moved)
        self.assertLess(float(result[:, :80].mean()), 0.3)
        self.assertGreater(float(result[:, 80:].mean()), 0.45)

    def test_ambient_estimator_separates_subject_and_background(self):
        estimator = AmbientLightEstimator()
        frame = np.full((90, 160, 3), 40, np.uint8)
        frame[10:80, 50:110] = 180
        mask = np.zeros((90, 160), np.float32)
        mask[10:80, 50:110] = 1.0
        state = estimator.update(frame, mask)
        self.assertGreater(state.subject_luma, state.background_luma + 0.3)

    def test_quality_governor_starts_from_device_capability(self):
        self.assertEqual(
            AdaptiveQualityGovernor(DeviceCapabilityProfile(1920, 1080, 30, True)).level,
            "balanced",
        )
        self.assertEqual(
            AdaptiveQualityGovernor(DeviceCapabilityProfile(1280, 720, 20, False)).level,
            "eco",
        )

    def test_live_segmenter_returns_latest_mask_without_blocking(self):
        model = Path(__file__).resolve().parent.parent / "selfie_segmenter_landscape.tflite"
        segmenter = LivePersonSegmenter(model, 160, 90)
        try:
            started = time.monotonic()
            segmenter.submit(np.zeros((360, 640, 3), np.uint8), 1)
            self.assertLess(time.monotonic() - started, 0.1)
            mask = None
            timestamp = -1
            deadline = time.monotonic() + 2.0
            while mask is None and time.monotonic() < deadline:
                mask, timestamp = segmenter.latest_after(timestamp)
                time.sleep(0.02)
            self.assertIsNotNone(mask)
            self.assertEqual(mask.shape, (90, 160))
        finally:
            segmenter.close()


if __name__ == "__main__":
    unittest.main()
