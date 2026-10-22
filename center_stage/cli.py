"""Developer CLI for the Center Stage engine."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import load_config
from .devices import CameraDevice, discover_cameras, find_camera


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Center Stage auto-framing virtual camera")
    parser.add_argument("--input", help="camera ID or V4L2 path (default: saved camera)")
    parser.add_argument("--no-center-stage", "--no-auto-zoom", action="store_true")
    parser.add_argument("--mirror-output", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def resolve_camera(value: str | None, selected_id: str | None) -> CameraDevice:
    devices = discover_cameras()
    if value:
        matched = next(
            (
                camera
                for camera in devices
                if value in {camera.camera_id, camera.path, camera.resolved_path}
                or os.path.realpath(value) == camera.resolved_path
            ),
            None,
        )
        if matched:
            return matched
        if value.startswith("/dev/") and Path(value).exists():
            resolved = os.path.realpath(value)
            return CameraDevice(
                camera_id=f"path-{Path(resolved).name}",
                path=value,
                resolved_path=resolved,
                name=Path(resolved).name,
                serial=Path(resolved).name,
                kind="usb",
            )
        raise EngineError("camera_missing", f"Camera not found: {value}")
    saved = find_camera(selected_id)
    if saved:
        return saved
    if not devices:
        raise EngineError("camera_missing", "No capture camera is connected")
    return devices[0]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if os.name == "nt":
        try:
            from .engine_win import EngineError, acquire_engine_lock, run_pipeline
        except ImportError:
            print(
                "Center Stage error [backend_missing]: the Windows backend is not installed",
                file=sys.stderr,
            )
            return 1
    else:
        from .engine import EngineError, acquire_engine_lock, run_pipeline

    lock = None
    try:
        lock = acquire_engine_lock()
        config = load_config()
        camera = resolve_camera(args.input, config.get("selected_camera_id"))
        config.update(
            {
                "selected_camera_id": camera.camera_id,
                "center_stage_enabled": not args.no_center_stage,
                "mirror_output": args.mirror_output,
            }
        )
        run_pipeline(
            config,
            camera,
            preview=False,
            publish_status=False,
        )
        return 0
    except KeyboardInterrupt:
        return 0
    except EngineError as error:
        print(f"Center Stage error [{error.code}]: {error}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
