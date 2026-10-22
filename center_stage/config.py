"""Persistent user configuration and transient engine status."""

from __future__ import annotations

import json
import os
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .platform_paths import (
    config_dir,
    default_output_device,
    pictures_dir,
    runtime_dir as platform_runtime_dir,
    videos_dir,
)

CONTROL_NAMES = ("brightness", "contrast", "saturation", "hue")

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 4,
    "language": "en",
    "theme": "light",
    "capture_mode": "photo",
    "gallery_collapsed": False,
    "selected_camera_id": None,
    "center_stage_enabled": True,
    "mirror_output": False,
    "portrait_enabled": False,
    "portrait_strength": 50,
    "studio_light_enabled": False,
    "studio_light_strength": 50,
    "selected_microphone_id": None,
    "microphone_muted": False,
    "check_for_updates": True,
    "update_channel": "stable",
    "autostart": False,
    "pictures_folder": "",
    "videos_folder": "",
    "media_folder": "",
    "output_device": default_output_device(),
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "camera_controls": {},
}


def config_path() -> Path:
    return config_dir() / "config.json"


def runtime_dir() -> Path:
    return platform_runtime_dir()


def status_path() -> Path:
    return runtime_dir() / "status.json"


def preview_socket_path() -> Path:
    return runtime_dir() / "preview.sock"


def load_config(path: Path | None = None) -> dict[str, Any]:
    result = deepcopy(DEFAULT_CONFIG)
    target = path or config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return result
    if not isinstance(raw, dict):
        return result
    for key in DEFAULT_CONFIG:
        if key in raw:
            result[key] = raw[key]
    if "center_stage_enabled" not in raw and "auto_zoom" in raw:
        result["center_stage_enabled"] = bool(raw["auto_zoom"])
    result["version"] = DEFAULT_CONFIG["version"]
    if result["language"] not in {"en", "vi"}:
        result["language"] = "en"
    if result["theme"] not in {"light", "dark"}:
        result["theme"] = "light"
    if result["capture_mode"] not in {"photo", "four_up", "video"}:
        result["capture_mode"] = "photo"
    if not isinstance(result["camera_controls"], dict):
        result["camera_controls"] = {}
    result["center_stage_enabled"] = bool(result["center_stage_enabled"])
    result["mirror_output"] = bool(result["mirror_output"])
    result["portrait_enabled"] = bool(result["portrait_enabled"])
    result["studio_light_enabled"] = bool(result["studio_light_enabled"])
    result["portrait_strength"] = max(0, min(100, int(result["portrait_strength"])))
    result["studio_light_strength"] = max(0, min(100, int(result["studio_light_strength"])))
    result["microphone_muted"] = bool(result["microphone_muted"])
    result["check_for_updates"] = bool(result["check_for_updates"])
    result["autostart"] = bool(result["autostart"])
    if result["update_channel"] not in {"stable", "beta"}:
        result["update_channel"] = "stable"
    result["gallery_collapsed"] = bool(result["gallery_collapsed"])
    for folder_key in ("pictures_folder", "videos_folder", "media_folder"):
        if not isinstance(result[folder_key], str):
            result[folder_key] = ""
    return result


def pictures_folder(config: dict[str, Any]) -> Path:
    configured = str(config.get("pictures_folder") or "").strip()
    return Path(configured).expanduser() if configured else pictures_dir()


def videos_folder(config: dict[str, Any]) -> Path:
    configured = str(config.get("videos_folder") or "").strip()
    return Path(configured).expanduser() if configured else videos_dir()


def media_folders(config: dict[str, Any]) -> tuple[Path, ...]:
    folders = [pictures_folder(config), videos_folder(config)]
    legacy = str(config.get("media_folder") or "").strip()
    if legacy:
        legacy_folder = Path(legacy).expanduser()
        if legacy_folder not in folders:
            folders.append(legacy_folder)
    return tuple(folders)


def media_folder(config: dict[str, Any]) -> Path:
    """Compatibility alias for callers that previously stored all media together."""
    return pictures_folder(config)


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {key: config.get(key, deepcopy(value)) for key, value in DEFAULT_CONFIG.items()}
    if data["language"] not in {"en", "vi"}:
        data["language"] = "en"
    if data["theme"] not in {"light", "dark"}:
        data["theme"] = "light"
    if data["capture_mode"] not in {"photo", "four_up", "video"}:
        data["capture_mode"] = "photo"
    if data["update_channel"] not in {"stable", "beta"}:
        data["update_channel"] = "stable"
    data["check_for_updates"] = bool(data["check_for_updates"])
    data["autostart"] = bool(data["autostart"])
    for folder_key in ("pictures_folder", "videos_folder", "media_folder"):
        if not isinstance(data[folder_key], str):
            data[folder_key] = ""
    _atomic_json_write(target, data, mode=0o600)


def read_status(path: Path | None = None) -> dict[str, Any]:
    target = path or status_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"state": "stopped", "code": "stopped", "details": {}}
    if not isinstance(data, dict):
        return {"state": "stopped", "code": "stopped", "details": {}}
    return data


def write_status(
    state: str,
    code: str,
    *,
    details: dict[str, Any] | None = None,
    path: Path | None = None,
    **fields: Any,
) -> None:
    target = path or status_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "code": code,
        "details": details or {},
        "updated_at": time.time(),
        **fields,
    }
    _atomic_json_write(target, payload, mode=0o600)


def camera_control_values(config: dict[str, Any], camera_id: str) -> dict[str, int]:
    raw = config.get("camera_controls", {}).get(camera_id, {})
    return {
        name: max(-100, min(100, int(raw.get(name, 0))))
        for name in CONTROL_NAMES
    }


def set_camera_control_value(
    config: dict[str, Any], camera_id: str, name: str, value: int
) -> None:
    if name not in CONTROL_NAMES:
        raise ValueError(f"Unsupported camera control: {name}")
    controls = config.setdefault("camera_controls", {})
    per_camera = controls.setdefault(camera_id, {})
    per_camera[name] = max(-100, min(100, int(value)))


def _atomic_json_write(path: Path, payload: dict[str, Any], mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
