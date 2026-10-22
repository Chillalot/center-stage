import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from center_stage.updater import (
    ReleaseAsset,
    ReleaseInfo,
    UpdateError,
    UpdateInfo,
    build_asset_candidates,
    check_for_updates,
    download_update,
    is_newer,
    parse_checksums,
    parse_version,
    select_asset,
)


class FakeResponse:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None):
        self.payload = payload
        self.headers = headers or {}
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def release_payload() -> dict:
    return {
        "tag_name": "v5.1.0",
        "html_url": "https://example.test/releases/v5.1.0",
        "body": "Release notes",
        "published_at": "2026-10-20T00:00:00Z",
        "prerelease": False,
        "assets": [
            {
                "name": "CenterStage-x86_64.AppImage",
                "browser_download_url": "https://example.test/CenterStage-x86_64.AppImage",
                "size": 12,
                "digest": "sha256:" + hashlib.sha256(b"appimage-data").hexdigest(),
            },
            {
                "name": "CenterStage-Setup.exe",
                "browser_download_url": "https://example.test/CenterStage-Setup.exe",
                "size": 12,
            },
            {
                "name": "SHA256SUMS-linux.txt",
                "browser_download_url": "https://example.test/SHA256SUMS-linux.txt",
                "size": 100,
            },
        ],
    }


class UpdaterTests(unittest.TestCase):
    def test_version_parsing_and_comparison(self):
        self.assertEqual(str(parse_version("v5.1.0")), "5.1.0")
        self.assertTrue(is_newer("5.1.0", "5.0.0"))
        self.assertFalse(is_newer("5.0.0", "5.1.0"))
        self.assertFalse(is_newer("5.0.0", "5.0.0"))
        self.assertFalse(is_newer("5.0.0-beta.1", "5.0.0"))
        self.assertTrue(is_newer("5.0.0", "5.0.0-beta.1"))

    def test_selects_stable_asset_names_per_platform(self):
        release = ReleaseInfo(
            "5.1.0",
            "v5.1.0",
            "",
            "",
            "",
            False,
            (
                ReleaseAsset("CenterStage-x86_64.AppImage", "https://example.test/a"),
                ReleaseAsset("CenterStage-Setup.exe", "https://example.test/b"),
                ReleaseAsset("center-stage_5.1.0_amd64.deb", "https://example.test/c"),
            ),
        )
        self.assertEqual(
            select_asset(release, system="linux", prefer_appimage=True).name,
            "CenterStage-x86_64.AppImage",
        )
        self.assertEqual(
            select_asset(release, system="linux", prefer_appimage=False).name,
            "center-stage_5.1.0_amd64.deb",
        )
        self.assertEqual(
            select_asset(release, system="win32").name,
            "CenterStage-Setup.exe",
        )

    def test_build_asset_candidates_include_versioned_fallbacks(self):
        candidates = build_asset_candidates("5.1.0", "linux", True)
        self.assertIn("CenterStage-x86_64.AppImage", candidates)
        self.assertIn("CenterStage-5.1.0-x86_64.AppImage", candidates)
        self.assertIn("center-stage_5.1.0_amd64.deb", candidates)

    def test_check_returns_update_with_checksum(self):
        def opener(request, _timeout):
            self.assertIn("/releases/latest", request.full_url)
            return FakeResponse(json.dumps(release_payload()).encode())

        with mock.patch.dict(os.environ, {"APPIMAGE": "/tmp/CenterStage.AppImage"}):
            update = check_for_updates(
                current_version="5.0.0",
                system="linux",
                opener=opener,
            )
        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.version, "5.1.0")
        self.assertEqual(update.asset.name, "CenterStage-x86_64.AppImage")
        self.assertEqual(update.checksum_name, "SHA256SUMS-linux.txt")
        self.assertTrue(update.is_appimage)

    def test_check_returns_none_when_current(self):
        with mock.patch.dict(os.environ, {"APPIMAGE": "/tmp/CenterStage.AppImage"}):
            update = check_for_updates(
                current_version="5.2.0",
                system="linux",
                opener=lambda _request, _timeout: FakeResponse(
                    json.dumps(release_payload()).encode()
                ),
            )
        self.assertIsNone(update)

    def test_beta_channel_picks_highest_release(self):
        payload = [
            release_payload(),
            {
                **release_payload(),
                "tag_name": "v5.1.0-beta.2",
                "prerelease": True,
            },
        ]

        def opener(_request, _timeout):
            return FakeResponse(json.dumps(payload).encode())

        update = check_for_updates(
            current_version="5.0.0",
            channel="beta",
            system="win32",
            opener=opener,
        )
        assert update is not None
        self.assertEqual(update.version, "5.1.0")
        self.assertEqual(update.asset.name, "CenterStage-Setup.exe")

    def test_checksum_parser_accepts_gnu_and_bsd_formats(self):
        digest = "a" * 64
        parsed = parse_checksums(f"{digest}  file-one\n{digest} *file-two\n")
        self.assertEqual(parsed["file-one"], digest)
        self.assertEqual(parsed["file-two"], digest)

    def test_download_verifies_sha256_and_replaces_atomically(self):
        payload = b"verified-update"
        update = UpdateInfo(
            current_version="5.0.0",
            version="5.1.0",
            tag="v5.1.0",
            notes="",
            page_url="",
            asset=ReleaseAsset(
                "CenterStage-Setup.exe",
                "https://example.test/setup.exe",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
            checksum_url=None,
            checksum_name=None,
            is_appimage=False,
            is_windows=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "setup.exe"
            result = download_update(
                update,
                destination,
                opener=lambda _request, _timeout: FakeResponse(payload),
            )
            self.assertEqual(result.read_bytes(), payload)
            self.assertFalse(destination.with_suffix(".exe.part").exists())

    def test_download_rejects_bad_sha256(self):
        update = UpdateInfo(
            "5.0.0",
            "5.1.0",
            "v5.1.0",
            "",
            "",
            ReleaseAsset("setup.exe", "https://example.test/setup.exe", 4, "0" * 64),
            None,
            None,
            False,
            True,
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UpdateError):
                download_update(
                    update,
                    Path(directory) / "setup.exe",
                    opener=lambda _request, _timeout: FakeResponse(b"nope"),
                )


if __name__ == "__main__":
    unittest.main()
