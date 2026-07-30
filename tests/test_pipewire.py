import json
import unittest

from center_stage.engine import (
    pipewire_dump_has_camera,
    pipewire_dump_has_node,
    pipewire_node_count,
    pipewire_v4l2_node_ids,
)


class PipeWireTests(unittest.TestCase):
    def test_requires_device_and_video_source_on_same_object(self):
        payload = json.dumps(
            [
                {
                    "id": 41,
                    "info": {
                        "props": {
                            "api.v4l2.path": "/dev/video10",
                            "media.class": "Video/Device",
                        }
                    }
                },
                {
                    "id": 42,
                    "info": {
                        "props": {
                            "api.v4l2.path": "/dev/video2",
                            "media.class": "Video/Source",
                        }
                    }
                },
            ]
        )
        self.assertFalse(pipewire_dump_has_camera(payload, "/dev/video10"))

    def test_accepts_matching_video_source(self):
        payload = json.dumps(
            [
                {
                    "id": 75,
                    "info": {
                        "props": {
                            "api.v4l2.path": "/dev/video10",
                            "media.class": "Video/Source",
                        }
                    }
                }
            ]
        )
        self.assertTrue(pipewire_dump_has_camera(payload, "/dev/video10"))

    def test_invalid_dump_is_not_a_match(self):
        self.assertFalse(pipewire_dump_has_camera("not json", "/dev/video10"))

    def test_matches_only_the_v4l2_backed_center_stage_source(self):
        payload = json.dumps(
            [
                {
                    "id": 10,
                    "info": {"props": {"media.class": "Video/Source", "api.v4l2.path": "/dev/video2"}},
                },
                {
                    "id": 11,
                    "info": {"props": {"media.class": "Video/Device", "api.v4l2.path": "/dev/video10"}},
                },
                {
                    "id": 12,
                    "info": {"props": {"media.class": "Video/Source", "api.v4l2.path": "/dev/video10"}},
                },
            ]
        )
        self.assertTrue(pipewire_dump_has_node(payload))
        self.assertEqual(pipewire_node_count(payload), 1)
        self.assertEqual(pipewire_v4l2_node_ids(payload), [12])


if __name__ == "__main__":
    unittest.main()
