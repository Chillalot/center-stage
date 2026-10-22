"""PySide6 desktop app for Center Stage on Windows."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSystemTrayIcon,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import (
    load_config,
    media_folders,
    pictures_folder,
    save_config,
    videos_folder,
)
from .devices import (
    CameraDevice,
    discover_cameras,
    discover_microphones,
    preferred_camera,
    preferred_microphone,
)
from .engine_win import WindowsPipeline
from .i18n import translator
from .media import (
    four_up,
    list_media_folders,
    media_preview,
    save_jpeg,
    unique_media_path,
)
from .platform_paths import state_dir

APP_NAME = "Center Stage"
MODE_LABELS = (("photo", "Photo"), ("four_up", "4-up"), ("video", "Video"))


def _configure_logging() -> logging.Logger:
    log_path = state_dir() / "gui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("center-stage.gui")
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = _configure_logging()


def _icon_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    candidates = (
        root / "packaging" / "icons" / name,
        root / "icons" / name,
        root / "packaging" / name,
        Path(__file__).resolve().parent.parent / "packaging" / "icons" / name,
        Path(__file__).resolve().parent.parent / "packaging" / name,
        Path("/opt/center-stage/share/icons") / name,
        Path("/usr/share/icons/hicolor/scalable/apps") / name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _qimage(frame: np.ndarray) -> QImage:
    contiguous = np.ascontiguousarray(frame)
    height, width = contiguous.shape[:2]
    image = QImage(
        contiguous.data,
        width,
        height,
        contiguous.strides[0],
        QImage.Format_BGR888,
    )
    return image.copy()


def _thumbnail_pixmap(frame: np.ndarray | None, size: QSize) -> QPixmap:
    if frame is None:
        pixmap = QPixmap(size)
        pixmap.fill(Qt.darkGray)
        return pixmap
    return QPixmap.fromImage(_qimage(frame)).scaled(
        size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


def _set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                executable = Path(sys.executable)
                if getattr(sys, "frozen", False):
                    command = f'"{executable}" --hidden'
                else:
                    command = f'"{executable}" -m center_stage.app --hidden'
                winreg.SetValueEx(key, "CenterStage", 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, "CenterStage")
                except FileNotFoundError:
                    pass
    except OSError as error:
        LOGGER.warning("Cannot update Windows autostart: %s", error)


class EngineThread(QThread):
    preview_ready = Signal(QImage)
    status_changed = Signal(dict)
    error_raised = Signal(str, str)

    def __init__(self, config: dict[str, Any], camera: CameraDevice, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.camera = camera
        self.pipeline = WindowsPipeline(
            config,
            camera,
            on_preview=self._preview,
            on_status=self.status_changed.emit,
            on_error=self.error_raised.emit,
        )

    def _preview(self, frame: np.ndarray) -> None:
        self.preview_ready.emit(_qimage(frame))

    def run(self) -> None:
        self.pipeline.run()

    def snapshot(self) -> np.ndarray | None:
        return self.pipeline.latest_frame()

    def start_recording(self, destination: Path, microphone_id: str | None, muted: bool) -> None:
        self.pipeline.start_recording(destination, microphone_id, muted)

    def stop_recording(self) -> Path | None:
        return self.pipeline.stop_recording()

    def stop_pipeline(self) -> None:
        self.pipeline.stop()
        self.wait(6000)


class SettingsDialog(QDialog):
    def __init__(self, window: "CenterStageWindow"):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(window._("Settings"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel(window._("Camera")))
        self.camera_combo = QComboBox()
        self.camera_combo.addItem(window._("Automatic"), None)
        for camera in window.cameras:
            self.camera_combo.addItem(camera.name, camera.camera_id)
        selected = window.config.get("selected_camera_id")
        index = self.camera_combo.findData(selected)
        self.camera_combo.setCurrentIndex(max(0, index))
        camera_row.addWidget(self.camera_combo, 1)
        layout.addLayout(camera_row)

        microphone_row = QHBoxLayout()
        microphone_row.addWidget(QLabel(window._("Microphone")))
        self.microphone_combo = QComboBox()
        self.microphone_combo.addItem(window._("Default microphone"), None)
        for microphone in window.microphones:
            self.microphone_combo.addItem(microphone.name, microphone.microphone_id)
        index = self.microphone_combo.findData(window.config.get("selected_microphone_id"))
        self.microphone_combo.setCurrentIndex(max(0, index))
        microphone_row.addWidget(self.microphone_combo, 1)
        layout.addLayout(microphone_row)

        self.pictures_edit = QLineEdit(str(pictures_folder(window.config)))
        layout.addLayout(window._folder_row(window._("Pictures folder"), self.pictures_edit))
        self.videos_edit = QLineEdit(str(videos_folder(window.config)))
        layout.addLayout(window._folder_row(window._("Videos folder"), self.videos_edit))

        self.check_updates = QCheckBox(window._("Check for updates automatically"))
        self.check_updates.setChecked(bool(window.config.get("check_for_updates", True)))
        layout.addWidget(self.check_updates)
        self.autostart = QCheckBox(window._("Start Center Stage when I sign in"))
        self.autostart.setChecked(bool(window.config.get("autostart", False)))
        layout.addWidget(self.autostart)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        config = self.window.config
        config["selected_camera_id"] = self.camera_combo.currentData()
        config["selected_microphone_id"] = self.microphone_combo.currentData()
        config["pictures_folder"] = self.pictures_edit.text().strip()
        config["videos_folder"] = self.videos_edit.text().strip()
        config["check_for_updates"] = self.check_updates.isChecked()
        config["autostart"] = self.autostart.isChecked()
        save_config(config)
        _set_autostart(config["autostart"])
        self.window.refresh_album()
        self.window.restart_camera_if_needed()


class CenterStageWindow(QMainWindow):
    update_available = Signal(str)

    def __init__(self, *, start_engine: bool = True):
        super().__init__()
        self.config = load_config()
        self._ = translator(str(self.config.get("language", "en")))
        self.cameras: list[CameraDevice] = []
        self.microphones = []
        self.engine: EngineThread | None = None
        self.latest_frame: np.ndarray | None = None
        self.latest_image = QImage()
        self.recording = False
        self.recording_started = 0.0
        self.pending_action: str | None = None
        self.countdown = 0
        self.quitting = False
        self.last_error = ""
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._tick)
        self._status_timer.start(500)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setWindowIcon(QIcon(str(_icon_path("center-stage-256.png"))))
        self.resize(1100, 760)
        self._build_ui()
        self._build_tray()
        self.update_available.connect(self._show_update_available)
        self._apply_theme()
        self._refresh_devices()
        if start_engine:
            QTimer.singleShot(150, self.start_camera)
        if self.config.get("check_for_updates", True):
            QTimer.singleShot(2500, self.check_updates_silent)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Center Stage")
        title.setObjectName("title")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.DemiBold))
        header.addWidget(title)
        header.addSpacing(14)

        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(230)
        self.camera_combo.currentIndexChanged.connect(self._camera_changed)
        header.addWidget(self.camera_combo)
        self.camera_button = QPushButton()
        self.camera_button.clicked.connect(self.toggle_camera)
        header.addWidget(self.camera_button)
        header.addStretch(1)

        self.language_button = QPushButton()
        self.language_button.clicked.connect(self._language_menu)
        header.addWidget(self.language_button)
        self.theme_button = QPushButton()
        self.theme_button.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_button)
        self.settings_button = QPushButton()
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)
        root.addLayout(header)

        self.preview = QLabel()
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(640, 360)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setText(self._("Camera is off"))
        root.addWidget(self.preview, 1)

        self.countdown_label = QLabel("")
        self.countdown_label.setObjectName("countdown")
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.hide()
        root.addWidget(self.countdown_label)

        self.status_label = QLabel(self._("Camera is off"))
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)

        controls = QFrame()
        controls.setObjectName("controls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(10, 8, 10, 8)
        controls_layout.setSpacing(12)

        self.mode_tabs = QTabBar()
        self.mode_tabs.setExpanding(False)
        for mode, label in MODE_LABELS:
            self.mode_tabs.addTab(self._(label))
        saved_mode = str(self.config.get("capture_mode", "photo"))
        self.mode_tabs.setCurrentIndex(
            next(
                (index for index, (mode, _label) in enumerate(MODE_LABELS) if mode == saved_mode),
                0,
            )
        )
        self.mode_tabs.currentChanged.connect(self._mode_changed)
        controls_layout.addWidget(self.mode_tabs)
        controls_layout.addStretch(1)

        self.mirror_check = QCheckBox(self._("Mirror"))
        self.mirror_check.setChecked(bool(self.config.get("mirror_output", False)))
        self.mirror_check.toggled.connect(self._mirror_changed)
        controls_layout.addWidget(self.mirror_check)

        self.portrait_check = QCheckBox(self._("Portrait"))
        self.portrait_check.setChecked(bool(self.config.get("portrait_enabled", False)))
        self.portrait_check.toggled.connect(self._effects_changed)
        controls_layout.addWidget(self.portrait_check)
        self.portrait_slider = QSlider(Qt.Horizontal)
        self.portrait_slider.setRange(0, 100)
        self.portrait_slider.setValue(int(self.config.get("portrait_strength", 50)))
        self.portrait_slider.setFixedWidth(90)
        self.portrait_slider.valueChanged.connect(self._effects_changed)
        controls_layout.addWidget(self.portrait_slider)

        self.studio_check = QCheckBox(self._("Studio Light"))
        self.studio_check.setChecked(bool(self.config.get("studio_light_enabled", False)))
        self.studio_check.toggled.connect(self._effects_changed)
        controls_layout.addWidget(self.studio_check)
        self.studio_slider = QSlider(Qt.Horizontal)
        self.studio_slider.setRange(0, 100)
        self.studio_slider.setValue(int(self.config.get("studio_light_strength", 50)))
        self.studio_slider.setFixedWidth(90)
        self.studio_slider.valueChanged.connect(self._effects_changed)
        controls_layout.addWidget(self.studio_slider)

        controls_layout.addStretch(1)
        self.shutter_button = QPushButton()
        self.shutter_button.setObjectName("shutter")
        self.shutter_button.setMinimumSize(120, 44)
        self.shutter_button.clicked.connect(self.shutter_pressed)
        controls_layout.addWidget(self.shutter_button)

        self.album_button = QPushButton()
        self.album_button.clicked.connect(self.toggle_album)
        controls_layout.addWidget(self.album_button)
        root.addWidget(controls)

        self.album = QListWidget()
        self.album.setViewMode(QListWidget.IconMode)
        self.album.setIconSize(QSize(150, 88))
        self.album.setGridSize(QSize(166, 128))
        self.album.setResizeMode(QListWidget.Adjust)
        self.album.setMovement(QListWidget.Static)
        self.album.setSpacing(4)
        self.album.itemDoubleClicked.connect(self._open_album_item)
        self.album.setMaximumHeight(150)
        root.addWidget(self.album)
        self.album.setVisible(not bool(self.config.get("gallery_collapsed", False)))

        self._retranslate()
        self._retranslate_album_button()
        self._update_shutter_label()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(str(_icon_path("center-stage-idle.svg"))), self)
        self.tray.setToolTip(f"{APP_NAME} {__version__}")
        menu = QMenu(self)
        self.tray_show = QAction(self._("Show Center Stage"), self)
        self.tray_show.triggered.connect(self.show_window)
        menu.addAction(self.tray_show)
        self.tray_camera = QAction(self._("Camera on"), self)
        self.tray_camera.triggered.connect(self.toggle_camera)
        menu.addAction(self.tray_camera)
        self.tray_photo = QAction(self._("Take photo"), self)
        self.tray_photo.triggered.connect(self.take_photo)
        menu.addAction(self.tray_photo)
        self.tray_video = QAction(self._("Record video"), self)
        self.tray_video.triggered.connect(self.toggle_video)
        menu.addAction(self.tray_video)
        menu.addSeparator()
        self.tray_settings = QAction(self._("Settings…"), self)
        self.tray_settings.triggered.connect(self.open_settings)
        menu.addAction(self.tray_settings)
        self.tray_updates = QAction(self._("Check for updates…"), self)
        self.tray_updates.triggered.connect(self.check_updates)
        menu.addAction(self.tray_updates)
        menu.addSeparator()
        self.tray_quit = QAction(self._("Quit Center Stage"), self)
        self.tray_quit.triggered.connect(self.quit_app)
        menu.addAction(self.tray_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _retranslate(self) -> None:
        self.camera_button.setText(self._("Camera off") if self.engine else self._("Camera on"))
        self.language_button.setText("VI" if self.config.get("language") == "vi" else "EN")
        self.theme_button.setText(self._("Dark") if self.config.get("theme") == "light" else self._("Light"))
        self.settings_button.setText(self._("Settings"))
        self.shutter_button.setText(self._("Take photo") if self._mode() == "photo" else self._("Record"))
        self.album_button.setText(
            self._("Hide album") if not self.album.isHidden() else self._("Show album")
        )
        self.mode_tabs.setTabText(0, self._("Photo"))
        self.mode_tabs.setTabText(1, self._("4-up"))
        self.mode_tabs.setTabText(2, self._("Video"))
        if getattr(self, "tray", None):
            self.tray_show.setText(self._("Show Center Stage"))
            self.tray_camera.setText(self._("Camera off") if self.engine else self._("Camera on"))
            self.tray_photo.setText(self._("Take photo"))
            self.tray_video.setText(self._("Record video"))
            self.tray_settings.setText(self._("Settings…"))
            self.tray_updates.setText(self._("Check for updates…"))
            self.tray_quit.setText(self._("Quit Center Stage"))

    def _retranslate_album_button(self) -> None:
        self.album_button.setText(
            self._("Hide album") if not self.album.isHidden() else self._("Show album")
        )

    # ------------------------------------------------------------- devices

    def _refresh_devices(self) -> None:
        selected = self.config.get("selected_camera_id")
        self.cameras = discover_cameras()
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        if not self.cameras:
            self.camera_combo.addItem(self._("No camera found"), None)
        else:
            for camera in self.cameras:
                self.camera_combo.addItem(camera.name, camera.camera_id)
            preferred = preferred_camera(self.cameras)
            target = next((c for c in self.cameras if c.camera_id == selected), preferred)
            if target is not None:
                self.camera_combo.setCurrentIndex(self.cameras.index(target))
        self.camera_combo.blockSignals(False)
        self.microphones = discover_microphones()

    def current_camera(self) -> CameraDevice | None:
        camera_id = self.camera_combo.currentData()
        return next((camera for camera in self.cameras if camera.camera_id == camera_id), None)

    def current_microphone(self) -> str | None:
        microphone = preferred_microphone(
            self.microphones, self.config.get("selected_microphone_id")
        )
        return microphone.microphone_id if microphone else None

    def _camera_changed(self) -> None:
        camera = self.current_camera()
        self.config["selected_camera_id"] = camera.camera_id if camera else None
        save_config(self.config)
        if self.engine is not None:
            self.start_camera()

    # --------------------------------------------------------------- engine

    def start_camera(self) -> None:
        camera = self.current_camera()
        if camera is None:
            self._set_status(self._("No camera found"), error=True)
            return
        self.stop_camera()
        self.config["selected_camera_id"] = camera.camera_id
        save_config(self.config)
        self.engine = EngineThread(self.config, camera, self)
        self.engine.preview_ready.connect(self._preview_ready)
        self.engine.status_changed.connect(self._engine_status)
        self.engine.error_raised.connect(self._engine_error)
        self.engine.finished.connect(
            lambda engine=self.engine: self._engine_finished(engine)
        )
        self.engine.start()
        self.camera_button.setText(self._("Camera off"))
        self.tray_camera.setText(self._("Camera off"))
        self._set_status(self._("Connecting camera…"))

    def stop_camera(self) -> None:
        if self.engine is None:
            return
        engine = self.engine
        self.engine = None
        engine.stop_pipeline()
        self.latest_frame = None
        self.latest_image = QImage()
        self.preview.setText(self._("Camera is off"))
        self.camera_button.setText(self._("Camera on"))
        self.tray_camera.setText(self._("Camera on"))
        self._set_status(self._("Camera is off"))

    def toggle_camera(self) -> None:
        if self.engine is None:
            self.start_camera()
        else:
            self.stop_camera()

    def restart_camera_if_needed(self) -> None:
        if self.engine is not None:
            self.start_camera()

    def _engine_finished(self, engine: EngineThread) -> None:
        engine.deleteLater()
        if self.engine is not engine:
            return
        self.engine = None
        self.latest_frame = None
        self.latest_image = QImage()
        self.preview.setText(self._("Camera is off"))
        self.camera_button.setText(self._("Camera on"))
        self.tray_camera.setText(self._("Camera on"))
        self.tray.setIcon(QIcon(str(_icon_path("center-stage-idle.svg"))))
        self._set_status(self._("Camera is off"))

    def _preview_ready(self, image: QImage) -> None:
        self.latest_image = image
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def _engine_status(self, status: dict[str, Any]) -> None:
        if status.get("state") == "stopped":
            self._set_status(self._("Camera is off"))
            self.tray.setIcon(QIcon(str(_icon_path("center-stage-idle.svg"))))
            return
        tracking = str(status.get("tracking", "")).lower()
        if tracking in {"tracking", "searching"}:
            text = self._("Camera is ready") if tracking == "tracking" else self._("Looking for a face…")
        else:
            text = self._("Camera is ready")
        if not status.get("outputs", {}).get("obs", True):
            text += " · " + self._("OBS Virtual Camera is not available")
        self._set_status(text)
        self.tray.setIcon(
            QIcon(str(_icon_path("center-stage-active.svg" if tracking == "tracking" else "center-stage-idle.svg")))
        )
        self.tray.setToolTip(f"{APP_NAME} · {text}")

    def _engine_error(self, code: str, message: str) -> None:
        self.last_error = f"{code}: {message}"
        LOGGER.warning("Engine error %s: %s", code, message)
        self._set_status(message, error=True)
        if code in {"virtual_camera_missing", "camera_unavailable", "camera_missing"}:
            self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning, 5000)

    # -------------------------------------------------------------- actions

    def _mode(self) -> str:
        return MODE_LABELS[max(0, self.mode_tabs.currentIndex())][0]

    def _mode_changed(self, index: int) -> None:
        self.config["capture_mode"] = MODE_LABELS[max(0, index)][0]
        save_config(self.config)
        self._update_shutter_label()

    def _update_shutter_label(self) -> None:
        mode = self._mode()
        if self.recording:
            self.shutter_button.setText(self._("Stop"))
            return
        self.shutter_button.setText(
            self._("Take photo")
            if mode == "photo"
            else self._("Take 4 photos")
            if mode == "four_up"
            else self._("Record")
        )

    def _mirror_changed(self, checked: bool) -> None:
        self.config["mirror_output"] = checked
        save_config(self.config)

    def _effects_changed(self) -> None:
        self.config["portrait_enabled"] = self.portrait_check.isChecked()
        self.config["portrait_strength"] = self.portrait_slider.value()
        self.config["studio_light_enabled"] = self.studio_check.isChecked()
        self.config["studio_light_strength"] = self.studio_slider.value()
        save_config(self.config)

    def shutter_pressed(self) -> None:
        if self.recording:
            self.stop_video()
            return
        if self.engine is None:
            self.start_camera()
            return
        mode = self._mode()
        if mode == "photo":
            self.take_photo()
        elif mode == "four_up":
            self.take_four_up()
        else:
            self.start_video()

    def take_photo(self) -> None:
        if self.engine is None:
            return
        frame = self.engine.snapshot()
        if frame is None:
            self._set_status(self._("The camera is not ready yet"), error=True)
            return
        destination = unique_media_path(pictures_folder(self.config), "photo", ".jpg")
        try:
            save_jpeg(frame, destination)
        except OSError as error:
            self._set_status(str(error), error=True)
            return
        self._set_status(self._("Saved {name}").format(name=destination.name))
        self.refresh_album()

    def take_four_up(self) -> None:
        if self.engine is None:
            return
        self._start_countdown(lambda: self._capture_four_up())

    def _capture_four_up(self) -> None:
        frames: list[np.ndarray] = []
        for index in range(4):
            if self.engine is None:
                return
            frame = self.engine.snapshot()
            if frame is not None:
                frames.append(frame)
            if index < 3:
                time.sleep(0.45)
        if len(frames) != 4:
            self._set_status(self._("The camera is not ready yet"), error=True)
            return
        destination = unique_media_path(pictures_folder(self.config), "four_up", ".jpg")
        try:
            save_jpeg(four_up(frames), destination)
        except OSError as error:
            self._set_status(str(error), error=True)
            return
        self._set_status(self._("Saved {name}").format(name=destination.name))
        self.refresh_album()

    def start_video(self) -> None:
        if self.engine is None:
            return
        self._start_countdown(self._begin_recording)

    def _begin_recording(self) -> None:
        if self.engine is None:
            return
        destination = unique_media_path(videos_folder(self.config), "video", ".mp4")
        try:
            self.engine.start_recording(
                destination,
                self.current_microphone(),
                bool(self.config.get("microphone_muted", False)),
            )
        except Exception as error:
            self._set_status(str(error), error=True)
            return
        self.recording = True
        self.recording_started = time.monotonic()
        self._update_shutter_label()
        self._set_status(self._("Recording…"))

    def stop_video(self) -> None:
        if self.engine is None or not self.recording:
            return
        self.recording = False
        self._update_shutter_label()
        try:
            path = self.engine.stop_recording()
        except Exception as error:
            self._set_status(str(error), error=True)
            return
        if path is None:
            self._set_status(self._("Recording could not be saved"), error=True)
        else:
            self._set_status(self._("Saved {name}").format(name=path.name))
        self.refresh_album()

    def toggle_video(self) -> None:
        if self.recording:
            self.stop_video()
        else:
            self.start_video()

    def _start_countdown(self, action) -> None:
        self.pending_action = action
        self.countdown = 3
        self.countdown_label.setText(str(self.countdown))
        self.countdown_label.show()
        self._countdown_timer.start(1000)

    def _countdown_tick(self) -> None:
        self.countdown -= 1
        if self.countdown <= 0:
            self._countdown_timer.stop()
            self.countdown_label.hide()
            action = self.pending_action
            self.pending_action = None
            if action is not None:
                action()
            return
        self.countdown_label.setText(str(self.countdown))

    def _tick(self) -> None:
        if self.recording:
            elapsed = int(time.monotonic() - self.recording_started)
            self.status_label.setText(f"{self._('Recording…')} {elapsed // 60:02d}:{elapsed % 60:02d}")
        if self.engine is not None:
            self.tray_camera.setText(self._("Camera off"))
        else:
            self.tray_camera.setText(self._("Camera on"))

    # ---------------------------------------------------------------- album

    def refresh_album(self) -> None:
        current = self.album.currentItem()
        selected = current.data(Qt.UserRole) if current else None
        self.album.clear()
        for path in list_media_folders(media_folders(self.config), limit=12):
            preview = media_preview(path, (150, 84))
            item = QListWidgetItem()
            item.setIcon(QIcon(_thumbnail_pixmap(preview.frame, QSize(150, 84))))
            label = path.name
            if preview.duration_seconds is not None:
                label += f"\n{preview.duration_seconds // 60}:{preview.duration_seconds % 60:02d}"
            item.setText(label)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.album.addItem(item)
            if selected and str(path) == selected:
                self.album.setCurrentItem(item)

    def toggle_album(self) -> None:
        visible = self.album.isHidden()
        self.album.setVisible(visible)
        self.config["gallery_collapsed"] = not visible
        save_config(self.config)
        self._retranslate_album_button()

    def _open_album_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------- settings

    def _folder_row(self, label: str, line_edit: QLineEdit) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addWidget(line_edit, 1)
        button = QPushButton(self._("Browse…"))
        button.clicked.connect(lambda: self._browse_folder(line_edit))
        row.addWidget(button)
        return row

    def _browse_folder(self, line_edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._("Choose folder"), line_edit.text())
        if folder:
            line_edit.setText(folder)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply()
            self._retranslate()
            self.refresh_album()

    def toggle_theme(self) -> None:
        self.config["theme"] = "dark" if self.config.get("theme", "light") == "light" else "light"
        save_config(self.config)
        self._apply_theme()
        self._retranslate()

    def _apply_theme(self) -> None:
        dark = self.config.get("theme", "light") == "dark"
        if dark:
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #15181d; color: #eef2f7; }
                QLabel#title { color: #ffffff; }
                QLabel#preview { background: #090b0e; border-radius: 14px; color: #7f8a99; }
                QLabel#status { color: #9fb0c5; }
                QFrame#controls { background: #20252d; border-radius: 12px; }
                QPushButton { background: #2c333d; border: 1px solid #3b4552; border-radius: 8px; padding: 7px 12px; }
                QPushButton:hover { background: #374150; }
                QPushButton#shutter { background: #2f7cf6; border: none; color: white; font-weight: 600; }
                QPushButton#shutter:hover { background: #4a90ff; }
                QComboBox, QLineEdit { background: #20252d; border: 1px solid #3b4552; border-radius: 7px; padding: 5px 8px; }
                QListWidget { background: #1a1e24; border: 1px solid #2b323c; border-radius: 10px; }
                QTabBar::tab { background: #2c333d; padding: 7px 16px; margin-right: 4px; border-radius: 7px; }
                QTabBar::tab:selected { background: #2f7cf6; color: white; }
                """
            )
        else:
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #f5f7fb; color: #202733; }
                QLabel#title { color: #162033; }
                QLabel#preview { background: #11151b; border-radius: 14px; color: #9aa7b8; }
                QLabel#status { color: #5d6b7d; }
                QFrame#controls { background: #ffffff; border: 1px solid #e1e6ee; border-radius: 12px; }
                QPushButton { background: #ffffff; border: 1px solid #d7dee9; border-radius: 8px; padding: 7px 12px; }
                QPushButton:hover { background: #eef3fa; }
                QPushButton#shutter { background: #2f7cf6; border: none; color: white; font-weight: 600; }
                QPushButton#shutter:hover { background: #4a90ff; }
                QComboBox, QLineEdit { background: #ffffff; border: 1px solid #d7dee9; border-radius: 7px; padding: 5px 8px; }
                QListWidget { background: #ffffff; border: 1px solid #e1e6ee; border-radius: 10px; }
                QTabBar::tab { background: #ffffff; padding: 7px 16px; margin-right: 4px; border-radius: 7px; }
                QTabBar::tab:selected { background: #2f7cf6; color: white; }
                """
            )

    # ------------------------------------------------------------- language

    def _language_menu(self) -> None:
        menu = QMenu(self)
        english = menu.addAction("English")
        vietnamese = menu.addAction("Tiếng Việt")
        english.setCheckable(True)
        vietnamese.setCheckable(True)
        english.setChecked(self.config.get("language") != "vi")
        vietnamese.setChecked(self.config.get("language") == "vi")
        chosen = menu.exec(self.language_button.mapToGlobal(self.language_button.rect().bottomLeft()))
        if chosen is english:
            self.config["language"] = "en"
        elif chosen is vietnamese:
            self.config["language"] = "vi"
        else:
            return
        save_config(self.config)
        self._ = translator(str(self.config["language"]))
        self._retranslate()
        self.refresh_album()

    # ------------------------------------------------------------- updates

    def check_updates(self) -> None:
        try:
            from .updater import check_for_updates, describe_update
        except ImportError:
            QMessageBox.information(
                self, APP_NAME, self._("Update checking is not available in this build.")
            )
            return
        self._set_status(self._("Checking for updates…"))
        try:
            result = check_for_updates()
        except Exception as error:
            self._set_status(str(error), error=True)
            return
        if result is None:
            self._set_status(self._("Center Stage is up to date"))
            QMessageBox.information(self, APP_NAME, self._("Center Stage is up to date."))
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            describe_update(result),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(result.download_url))

    def check_updates_silent(self) -> None:
        try:
            from .updater import check_for_updates
        except ImportError:
            return
        thread = threading.Thread(target=self._silent_update_check, args=(check_for_updates,), daemon=True)
        thread.start()

    def _silent_update_check(self, check) -> None:
        try:
            result = check()
        except Exception:
            return
        if result is not None:
            self.update_available.emit(result.version)

    def _show_update_available(self, version: str) -> None:
        self.tray.showMessage(
            APP_NAME,
            self._("Center Stage {version} is available.").format(version=version),
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    # --------------------------------------------------------------- status

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #d9534f;" if error else "")

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def closeEvent(self, event) -> None:
        if not self.quitting and self.tray.isVisible():
            event.ignore()
            self.hide()
            return
        self.stop_camera()
        event.accept()

    def quit_app(self) -> None:
        self.quitting = True
        self.stop_camera()
        self.tray.hide()
        QApplication.quit()

    def current_status_text(self) -> str:
        return self.status_label.text()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Center Stage desktop app")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--hidden", action="store_true", help="start minimized to the tray")
    parser.add_argument("--selftest", action="store_true", help="verify imports and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv[:1] + ([] if not args.selftest else ["-platform", "offscreen"]))
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(_icon_path("center-stage-256.png"))))
    if args.selftest:
        window = CenterStageWindow(start_engine=False)
        window.refresh_album()
        print(f"Center Stage {__version__} Qt selftest OK ({len(window.cameras)} cameras)")
        window.quit_app()
        return 0
    window = CenterStageWindow()
    if not args.hidden:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
