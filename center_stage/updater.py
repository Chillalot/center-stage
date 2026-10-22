"""GitHub Releases update checks and verified downloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__

DEFAULT_REPOSITORY = "Chillalot/center-stage"
GITHUB_API = "https://api.github.com"
USER_AGENT = f"CenterStage/{__version__}"
CHUNK_SIZE = 1024 * 1024


class UpdateError(RuntimeError):
    """A network, release parsing, or checksum failure."""


@dataclass(frozen=True)
class Version:
    core: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()

    def __lt__(self, other: "Version") -> bool:
        if self.core != other.core:
            return self.core < other.core
        if not self.prerelease and not other.prerelease:
            return False
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        return self.prerelease < other.prerelease

    def __str__(self) -> str:
        base = ".".join(str(part) for part in self.core)
        return f"{base}-{'.'.join(self.prerelease)}" if self.prerelease else base


def parse_version(value: str) -> Version | None:
    cleaned = value.strip().lstrip("vV")
    match = re.fullmatch(
        r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
        cleaned,
    )
    if match is None:
        return None
    core = (
        int(match.group(1)),
        int(match.group(2) or 0),
        int(match.group(3) or 0),
    )
    prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
    return Version(core, prerelease)


def is_newer(candidate: str, current: str) -> bool:
    latest = parse_version(candidate)
    installed = parse_version(current)
    if latest is None or installed is None:
        return False
    return installed < latest


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int = 0
    sha256: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    page_url: str
    published_at: str
    prerelease: bool
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    version: str
    tag: str
    notes: str
    page_url: str
    asset: ReleaseAsset
    checksum_url: str | None
    checksum_name: str | None
    is_appimage: bool
    is_windows: bool

    @property
    def download_url(self) -> str:
        return self.asset.url

    @property
    def asset_name(self) -> str:
        return self.asset.name


Opener = Callable[[urllib.request.Request, float], Any]


def _request(url: str, *, token: str | None = None) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _fetch_json(
    url: str,
    *,
    opener: Opener | None = None,
    timeout: float = 12.0,
    token: str | None = None,
) -> Any:
    request = _request(url, token=token)
    try:
        with (opener or urllib.request.urlopen)(request, timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateError(f"GitHub returned HTTP {error.code} for {url}") from error
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise UpdateError(f"Cannot reach GitHub Releases: {error}") from error
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError("GitHub returned an invalid release response") from error


def _release_from_payload(payload: dict[str, Any]) -> ReleaseInfo | None:
    tag = str(payload.get("tag_name", "")).strip()
    version = parse_version(tag)
    if version is None:
        return None
    assets: list[ReleaseAsset] = []
    for raw in payload.get("assets", []):
        name = str(raw.get("name", ""))
        url = str(raw.get("browser_download_url", ""))
        if not name or not url:
            continue
        digest = str(raw.get("digest") or "")
        sha256 = digest.removeprefix("sha256:") if digest.startswith("sha256:") else None
        assets.append(
            ReleaseAsset(
                name=name,
                url=url,
                size=int(raw.get("size") or 0),
                sha256=sha256 or None,
            )
        )
    return ReleaseInfo(
        version=str(version),
        tag=tag,
        notes=str(payload.get("body") or ""),
        page_url=str(payload.get("html_url") or ""),
        published_at=str(payload.get("published_at") or ""),
        prerelease=bool(payload.get("prerelease")),
        assets=tuple(assets),
    )


def _api_url(repository: str, channel: str) -> str:
    if channel == "stable":
        return f"{GITHUB_API}/repos/{repository}/releases/latest"
    return f"{GITHUB_API}/repos/{repository}/releases?per_page=20"


def fetch_release(
    *,
    repository: str | None = None,
    channel: str = "stable",
    opener: Opener | None = None,
    timeout: float = 12.0,
    token: str | None = None,
) -> ReleaseInfo | None:
    repository = repository or os.environ.get("CENTER_STAGE_REPO", DEFAULT_REPOSITORY)
    channel = "beta" if channel == "beta" else "stable"
    token = token or os.environ.get("GITHUB_TOKEN")
    payload = _fetch_json(
        _api_url(repository, channel), opener=opener, timeout=timeout, token=token
    )
    if payload is None:
        return None
    if channel == "stable":
        return _release_from_payload(payload) if isinstance(payload, dict) else None
    if not isinstance(payload, list):
        return None
    releases: list[ReleaseInfo] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        release = _release_from_payload(item)
        if release is not None:
            releases.append(release)
    if not releases:
        return None
    return max(releases, key=lambda release: parse_version(release.version) or Version((0, 0, 0)))


def _is_windows(system: str | None = None) -> bool:
    system = system or sys.platform
    return system.startswith("win")


def _is_linux(system: str | None = None) -> bool:
    system = system or sys.platform
    return system.startswith("linux")


def build_asset_candidates(version: str, system: str, prefer_appimage: bool) -> list[str]:
    if _is_windows(system):
        return [
            "CenterStage-Setup.exe",
            f"CenterStage-Setup-{version}.exe",
            f"CenterStage-{version}-win64.zip",
            "CenterStage-win64.zip",
        ]
    if _is_linux(system):
        candidates: list[str] = []
        if prefer_appimage:
            candidates.extend(
                [
                    "CenterStage-x86_64.AppImage",
                    f"CenterStage-{version}-x86_64.AppImage",
                ]
            )
        candidates.extend(
            [
                f"center-stage_{version}_amd64.deb",
                "center-stage_amd64.deb",
            ]
        )
        return candidates
    return []


def select_asset(
    release: ReleaseInfo,
    *,
    system: str | None = None,
    prefer_appimage: bool | None = None,
) -> ReleaseAsset | None:
    system = system or sys.platform
    if prefer_appimage is None:
        prefer_appimage = bool(os.environ.get("APPIMAGE"))
    by_name = {asset.name: asset for asset in release.assets}
    for name in build_asset_candidates(release.version, system, prefer_appimage):
        if name in by_name:
            return by_name[name]
    return None


def _checksum_candidates(system: str) -> tuple[str, ...]:
    if _is_windows(system):
        return ("SHA256SUMS-windows.txt", "SHA256SUMS.txt")
    if _is_linux(system):
        return ("SHA256SUMS-linux.txt", "SHA256SUMS.txt")
    return ("SHA256SUMS.txt",)


def _checksum_asset(release: ReleaseInfo, system: str) -> ReleaseAsset | None:
    by_name = {asset.name: asset for asset in release.assets}
    for name in _checksum_candidates(system):
        if name in by_name:
            return by_name[name]
    return None


def check_for_updates(
    *,
    current_version: str | None = None,
    repository: str | None = None,
    channel: str | None = None,
    system: str | None = None,
    opener: Opener | None = None,
    timeout: float = 12.0,
    token: str | None = None,
) -> UpdateInfo | None:
    current_version = current_version or __version__
    channel = channel or os.environ.get("CENTER_STAGE_UPDATE_CHANNEL", "stable")
    release = fetch_release(
        repository=repository,
        channel=channel,
        opener=opener,
        timeout=timeout,
        token=token,
    )
    if release is None or not is_newer(release.version, current_version):
        return None
    system = system or sys.platform
    asset = select_asset(release, system=system)
    if asset is None:
        raise UpdateError(
            f"Release {release.version} does not contain an asset for this platform"
        )
    checksum = _checksum_asset(release, system)
    return UpdateInfo(
        current_version=current_version,
        version=release.version,
        tag=release.tag,
        notes=release.notes,
        page_url=release.page_url,
        asset=asset,
        checksum_url=checksum.url if checksum else None,
        checksum_name=checksum.name if checksum else None,
        is_appimage=asset.name.endswith(".AppImage"),
        is_windows=_is_windows(system),
    )


def parse_checksums(payload: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in payload.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, name = parts
        name = name.lstrip("*").strip()
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            checksums[name] = digest.lower()
    return checksums


def _fetch_checksums(
    update: UpdateInfo, *, opener: Opener | None = None, timeout: float = 12.0
) -> dict[str, str]:
    if not update.checksum_url:
        return {}
    request = _request(update.checksum_url)
    try:
        with (opener or urllib.request.urlopen)(request, timeout) as response:
            return parse_checksums(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise UpdateError(f"Cannot download the checksum file: {error}") from error


def expected_checksum(
    update: UpdateInfo, *, opener: Opener | None = None, timeout: float = 12.0
) -> str:
    if update.asset.sha256:
        return update.asset.sha256.lower()
    checksums = _fetch_checksums(update, opener=opener, timeout=timeout)
    digest = checksums.get(update.asset.name)
    if not digest:
        raise UpdateError(f"No SHA-256 checksum found for {update.asset.name}")
    return digest


def download_update(
    update: UpdateInfo,
    destination: Path,
    *,
    opener: Opener | None = None,
    timeout: float = 30.0,
    progress: Callable[[int, int], None] | None = None,
    expected_sha256: str | None = None,
) -> Path:
    digest = expected_sha256 or expected_checksum(update, opener=opener, timeout=timeout)
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = _request(update.asset.url)
    hasher = hashlib.sha256()
    downloaded = 0
    total = update.asset.size
    try:
        with (opener or urllib.request.urlopen)(request, timeout) as response:
            content_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
            if content_length and int(content_length) > 0:
                total = int(content_length)
            with temporary.open("wb") as handle:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total)
        actual = hasher.hexdigest()
        if actual != digest.lower():
            raise UpdateError(
                f"SHA-256 mismatch for {update.asset.name}: expected {digest}, got {actual}"
            )
        os.replace(temporary, destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def download_and_install_appimage(
    update: UpdateInfo,
    target: Path,
    *,
    opener: Opener | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download a verified AppImage next to the running one.

    Replacement is intentionally left to the user or to AppImageUpdate because
    a running AppImage cannot safely overwrite itself on every filesystem.
    """
    destination = target.expanduser()
    result = download_update(update, destination, opener=opener, progress=progress)
    result.chmod(result.stat().st_mode | 0o111)
    return result


def install_windows_update(installer: Path, *, silent: bool = True) -> None:
    if not _is_windows():
        raise UpdateError("Windows update installation is only available on Windows")
    command = [str(installer)]
    if silent:
        command.extend(["/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS"])
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, creationflags=creation_flags)


def describe_update(update: UpdateInfo) -> str:
    notes = update.notes.strip()
    if len(notes) > 1800:
        notes = notes[:1800].rstrip() + "…"
    return (
        f"Center Stage {update.version} is available (installed: {update.current_version}).\n\n"
        f"{notes or 'See the release page for details.'}\n\n"
        f"Download: {update.asset.name}\n"
        f"Release page: {update.page_url}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Center Stage releases")
    parser.add_argument("--check", action="store_true", help="check for an update")
    parser.add_argument("--download", metavar="DIRECTORY", help="download the selected asset")
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--repository", default=None)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        update = check_for_updates(
            repository=args.repository,
            channel=args.channel,
        )
    except UpdateError as error:
        print(f"Update check failed: {error}", file=sys.stderr)
        return 1
    if update is None:
        if args.json:
            print(json.dumps({"update_available": False, "version": __version__}))
        else:
            print(f"Center Stage {__version__} is up to date.")
        return 0
    if args.json:
        print(
            json.dumps(
                {
                    "update_available": True,
                    "version": update.version,
                    "current_version": update.current_version,
                    "asset": update.asset_name,
                    "url": update.download_url,
                    "page_url": update.page_url,
                    "checksum_url": update.checksum_url,
                },
                indent=2,
            )
        )
    else:
        print(describe_update(update))
    if args.download:
        try:
            destination = download_update(update, Path(args.download) / update.asset_name)
        except UpdateError as error:
            print(f"Download failed: {error}", file=sys.stderr)
            return 1
        print(f"Saved {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
