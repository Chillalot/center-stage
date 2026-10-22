# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the Windows desktop app."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

datas = [
    (str(ROOT / "blaze_face_short_range.tflite"), "."),
    (str(ROOT / "selfie_segmenter_landscape.tflite"), "."),
    (str(ROOT / "VERSION"), "."),
    (str(ROOT / "build" / "locale"), "locale"),
    (str(ROOT / "packaging" / "icons"), "icons"),
    (str(ROOT / "packaging" / "center-stage.svg"), "icons"),
]
binaries = []

for package in ("mediapipe", "pyvirtualcam", "sounddevice", "imageio_ffmpeg"):
    datas += collect_data_files(package)
    binaries += collect_dynamic_libs(package)

hiddenimports = [
    "cv2_enumerate_cameras",
    "imageio_ffmpeg",
    "pyvirtualcam",
    "sounddevice",
]
hiddenimports += collect_submodules("mediapipe")
hiddenimports += collect_submodules("cv2_enumerate_cameras")

a = Analysis(
    [str(ROOT / "main_win.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["gi", "Gtk", "Gst", "tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CenterStage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "icons" / "center-stage.ico"),
    version=str(ROOT / "packaging" / "windows" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CenterStage",
)
