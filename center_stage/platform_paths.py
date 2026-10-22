"""Platform-specific user directories and defaults.

The Linux build follows the XDG base directory specification. The Windows
build uses the Known Folder API so OneDrive-redirected Pictures and Videos
folders keep working.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return os.name == "nt"


def _windows_known_folder(guid: str, fallback: Path) -> Path:
    if not is_windows():
        return fallback
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

            def __init__(self, value: str) -> None:
                super().__init__()
                parts = value.strip("{}").split("-")
                self.Data1 = int(parts[0], 16)
                self.Data2 = int(parts[1], 16)
                self.Data3 = int(parts[2], 16)
                tail = bytes.fromhex(parts[3] + parts[4])
                for index, byte in enumerate(tail):
                    self.Data4[index] = byte

        folder_id = GUID(guid)
        output = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(output)
        )
        if result == 0 and output.value:
            try:
                return Path(output.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(output)
    except (AttributeError, OSError, ValueError):
        pass
    return fallback


def config_dir(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    if is_windows():
        base = values.get("APPDATA")
        if base:
            return Path(base) / "center-stage"
        return _windows_known_folder(
            "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}",
            Path.home() / "AppData" / "Roaming",
        ) / "center-stage"
    override = values.get("CENTER_STAGE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(values.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "center-stage"


def runtime_dir(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    if is_windows():
        base = values.get("LOCALAPPDATA")
        if base:
            return Path(base) / "center-stage" / "run"
        return _windows_known_folder(
            "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}",
            Path.home() / "AppData" / "Local",
        ) / "center-stage" / "run"
    override = values.get("CENTER_STAGE_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    base = values.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "center-stage"
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(f"/run/user/{uid}") / "center-stage"


def state_dir(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    if is_windows():
        base = values.get("LOCALAPPDATA")
        if base:
            return Path(base) / "center-stage"
        return runtime_dir(values).parent
    override = values.get("CENTER_STAGE_STATE_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(values.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "center-stage"


def pictures_dir(env: dict[str, str] | None = None) -> Path:
    if is_windows():
        return _windows_known_folder(
            "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
            Path.home() / "Pictures",
        )
    return Path.home() / "Pictures"


def videos_dir(env: dict[str, str] | None = None) -> Path:
    if is_windows():
        return _windows_known_folder(
            "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
            Path.home() / "Videos",
        )
    return Path.home() / "Videos"


def default_output_device() -> str:
    return "obs" if is_windows() else "/dev/video10"


def bundled_roots() -> tuple[Path, ...]:
    """Return directories that can contain models and translations."""
    roots: list[Path] = []
    override = os.environ.get("CENTER_STAGE_SHARE_DIR")
    if override:
        roots.append(Path(override).expanduser())
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
        roots.append(Path(sys.executable).parent)
    package_root = Path(__file__).resolve().parent.parent
    roots.append(package_root)
    roots.append(Path("/opt/center-stage/share"))
    return tuple(roots)
