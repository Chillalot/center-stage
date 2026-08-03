"""GTK3 Photo Booth window and advanced status controller."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import numpy as np

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import (  # noqa: E402
    AyatanaAppIndicator3,
    Gdk,
    GdkPixbuf,
    Gio,
    GLib,
    Gst,
    Gtk,
)

from . import __version__
from .config import (
    CONTROL_NAMES,
    camera_control_values,
    load_config,
    media_folder,
    preview_socket_path,
    read_status,
    save_config,
    set_camera_control_value,
)
from .devices import (
    CameraDevice,
    MicrophoneDevice,
    discover_cameras,
    discover_microphones,
    preferred_camera,
    preferred_microphone,
    query_controls,
    set_normalized_control,
)
from .i18n import translator
from .media import VideoRecorder, four_up, list_media, save_jpeg, thumbnail, unique_media_path


SERVICE_NAME = "center-stage-engine.service"
N_ = lambda message: message
CAPTURE_MODES = (("photo", N_("Photo")), ("four_up", N_("4-up")), ("video", N_("Video")))
PREVIEW_FEATURES = (
    ("reactions", N_("Reactions")),
    ("background", N_("Background")),
)
CONTROL_LABELS = {
    "brightness": N_("Brightness"),
    "contrast": N_("Contrast"),
    "saturation": N_("Saturation"),
    "hue": N_("Hue"),
}
STATUS_MESSAGES = {
    "stopped": N_("Turn camera on"),
    "opening_camera": N_("Connecting camera…"),
    "ready": N_("Camera is ready"),
    "camera_busy": N_("The camera is being used by another application."),
    "camera_missing": N_("The selected camera is not connected."),
    "camera_unavailable": N_("The camera could not be opened."),
    "pipewire_camera_unavailable": N_("PipeWire cannot share the selected camera."),
    "camera_disconnected": N_("The camera was disconnected."),
    "virtual_camera_missing": N_("The Center Stage virtual camera is unavailable."),
    "pipewire_unavailable": N_("Video is running, but PipeWire cannot see the virtual camera."),
    "pipewire_output_unavailable": N_("The Center Stage PipeWire camera is unavailable."),
    "output_pipeline_failed": N_("The virtual camera output could not be started."),
    "driver_missing": N_("The v4l2loopback driver is not installed."),
    "output_device_conflict": N_("Another device is using /dev/video10."),
    "model_missing": N_("The face detection model is missing."),
    "same_input_output": N_("Input and output cameras cannot be the same."),
    "already_running": N_("Center Stage is already running."),
    "resolution_changed": N_("The camera resolution changed unexpectedly."),
    "internal_error": N_("Center Stage stopped because of an internal error."),
    "service_unavailable": N_("The Center Stage background service is not installed."),
}


def _configure_logging() -> logging.Logger:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    log_path = state_home / "center-stage" / "gui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("center-stage.gui")
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = _configure_logging()


class TraySliderPopup(Gtk.Window):
    """Compact tray-adjacent control surface for sliders unsupported by DBusMenu."""

    def __init__(self, title: str):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title(title)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_size_request(292, -1)
        self.connect("focus-out-event", lambda *_args: self.hide())
        self.connect("key-press-event", self._on_key_press)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.body.set_border_width(14)
        self.body.get_style_context().add_class("tray-control-popup")
        heading = Gtk.Label(label=title, xalign=0)
        heading.get_style_context().add_class("tray-control-title")
        self.body.pack_start(heading, False, False, 0)
        self.add(self.body)

    def add_scale(
        self,
        label: str,
        minimum: int,
        maximum: int,
        value: int,
        callback: Any,
        *callback_args: Any,
    ) -> Gtk.Scale:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, 1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_value(value)
        scale.connect("value-changed", callback, *callback_args)
        row.pack_start(scale, False, False, 0)
        self.body.pack_start(row, False, False, 0)
        return scale

    def present_at_pointer(self) -> None:
        self.show_all()
        self.realize()
        display = Gdk.Display.get_default()
        pointer = display.get_default_seat().get_pointer() if display else None
        _screen, pointer_x, pointer_y = pointer.get_position() if pointer else (None, 0, 0)
        width, height = self.get_preferred_size()[1].width, self.get_preferred_size()[1].height
        monitor = display.get_monitor_at_point(pointer_x, pointer_y) if display else None
        geometry = monitor.get_workarea() if monitor else Gdk.Rectangle(x=0, y=0, width=1920, height=1080)
        x = min(max(pointer_x - width - 12, geometry.x + 8), geometry.x + geometry.width - width - 8)
        y = min(max(pointer_y + 8, geometry.y + 8), geometry.y + geometry.height - height - 8)
        self.move(x, y)
        self.present()

    def _on_key_press(self, _window: Gtk.Window, event: Any) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False


class MediaLibrary:
    """Storage facade used by the Photo Booth and filmstrip."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @property
    def folder(self) -> Path:
        return media_folder(self.config)

    def photo_path(self) -> Path:
        return unique_media_path(self.folder, "Photo", ".jpg")

    def four_up_path(self) -> Path:
        return unique_media_path(self.folder, "FourUp", ".jpg")

    def video_path(self) -> Path:
        return unique_media_path(self.folder, "Video", ".mp4")


class PhotoBoothWindow(Gtk.ApplicationWindow):
    """Minimal capture window backed by the persistent virtual-camera engine."""

    def __init__(self, application: "AppShell"):
        self.config = load_config()
        self._ = translator(str(self.config.get("language", "en")))
        super().__init__(application=application, title="Center Stage")
        self.set_default_size(960, 700)
        self.set_size_request(720, 520)
        self.set_icon_name("center-stage")
        self.connect("delete-event", self._on_delete_event)
        self.connect("destroy", self._on_window_destroyed)

        self.cameras: list[CameraDevice] = []
        self.microphones: list[MicrophoneDevice] = []
        self.camera_by_id: dict[str, CameraDevice] = {}
        self.microphone_by_id: dict[str, MicrophoneDevice] = {}
        self.camera_menu_items: dict[str, Gtk.RadioMenuItem] = {}
        self.microphone_menu_items: dict[str, Gtk.RadioMenuItem] = {}
        self.preview_feature_items: dict[str, Gtk.CheckMenuItem] = {}
        self.preview_feature_state = {feature_id: False for feature_id, _label in PREVIEW_FEATURES}
        self.available_controls: dict[str, Any] = {}
        self.control_scales: dict[str, Gtk.Scale] = {}
        self.control_timers: dict[str, int] = {}
        self.effect_timers: dict[str, int] = {}
        self.effect_adjust_items: dict[str, Gtk.MenuItem] = {}
        self.control_popup: TraySliderPopup | None = None
        self.control_popup_kind = ""
        self.worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="center-stage-gui")
        self.media_library = MediaLibrary(self.config)
        recovery = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "center-stage" / "recovery"
        self.recorder = VideoRecorder(preview_socket_path(), recovery, self._on_recording_message)

        self.preview_pipeline: Gst.Pipeline | None = None
        self.preview_widget: Gtk.Widget | None = None
        self.preview_running = False
        self.latest_frame: np.ndarray | None = None
        self.frame_lock = threading.Lock()
        self.engine_state = "stopped"
        self.capture_mode = "photo"
        self.capturing = False
        self.recording = False
        self.four_up_frames: list[np.ndarray] = []
        self._destroyed = False
        self._quitting = False
        self._building = True
        self._syncing = False
        self._camera_requested = True
        self._initial_autostart_done = False
        self._service_action_pending = False
        self._transition_until = 0.0
        self._device_generation = 0
        self._control_generation = 0
        self._device_refresh_pending = False

        Gst.init(None)
        self._install_css()
        self._build_ui()
        application.ensure_indicator(self)
        self._rebuild_indicator_menu()
        self._building = False
        self.show_all()
        self.countdown_label.hide()
        self.flash_overlay.hide()
        self._refresh_filmstrip()
        self._schedule_device_refresh(force=True)
        self._status_timer = GLib.timeout_add(500, self._poll_status)
        self._device_timer = GLib.timeout_add_seconds(2, self._periodic_device_refresh)
        LOGGER.info("PhotoBoothWindow started version=%s", __version__)

    @property
    def app_shell(self) -> "AppShell":
        application = self.get_application()
        assert isinstance(application, AppShell)
        return application

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
            .camera-stage { background: #111; }
            .camera-toolbar { padding: 8px 12px; }
            .camera-power { padding: 6px 12px; border-radius: 999px; }
            .capture-controls { padding: 10px 16px; }
            .shutter { min-width: 72px; min-height: 52px; border-radius: 999px; font-weight: bold; }
            .shutter.recording { background: #d33; color: white; }
            .countdown { font-size: 64px; font-weight: bold; color: white; }
            .flash { background: white; }
            .filmstrip { padding: 8px 12px; background: alpha(@theme_fg_color, 0.06); }
            .thumbnail { padding: 2px; }
            .tray-control-popup { background: @theme_bg_color; border: 1px solid alpha(@theme_fg_color, 0.2); }
            .tray-control-title { font-weight: bold; font-size: 1.08em; }
        """)
        screen = self.get_screen()
        if screen:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        header = Gtk.HeaderBar(title="Center Stage", subtitle=f"V{__version__}")
        header.set_show_close_button(True)
        self.set_titlebar(header)

        self.camera_combo = Gtk.ComboBoxText()
        self.camera_combo.set_size_request(260, -1)
        self.camera_combo.connect("changed", self._on_window_camera_changed)
        header.pack_start(self.camera_combo)
        self.camera_power_button = Gtk.Button.new_with_label(self._("Turn camera on"))
        self.camera_power_button.get_style_context().add_class("camera-power")
        self.camera_power_button.connect("clicked", self._on_connect_disconnect)
        header.pack_end(self.camera_power_button)

        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        root.pack_start(overlay, True, True, 0)
        self.preview_stack = Gtk.Stack()
        self.preview_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.preview_stack.set_transition_duration(180)
        self.preview_stack.get_style_context().add_class("camera-stage")
        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        placeholder.set_halign(Gtk.Align.CENTER)
        placeholder.set_valign(Gtk.Align.CENTER)
        placeholder.pack_start(Gtk.Image.new_from_icon_name("camera-web-symbolic", Gtk.IconSize.DIALOG), False, False, 0)
        self.placeholder_label = Gtk.Label(label=self._("Turn on a camera to take photos or record video."))
        placeholder.pack_start(self.placeholder_label, False, False, 0)
        self.preview_stack.add_named(placeholder, "placeholder")
        self.preview_holder = Gtk.Box()
        self.preview_stack.add_named(self.preview_holder, "preview")
        overlay.add(self.preview_stack)
        self.countdown_label = Gtk.Label()
        self.countdown_label.get_style_context().add_class("countdown")
        self.countdown_label.set_halign(Gtk.Align.CENTER)
        self.countdown_label.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(self.countdown_label)
        self.flash_overlay = Gtk.EventBox()
        self.flash_overlay.get_style_context().add_class("flash")
        self.flash_overlay.set_hexpand(True)
        self.flash_overlay.set_vexpand(True)
        overlay.add_overlay(self.flash_overlay)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        controls.set_halign(Gtk.Align.CENTER)
        controls.get_style_context().add_class("capture-controls")
        first: Gtk.RadioButton | None = None
        self.mode_buttons: dict[str, Gtk.RadioButton] = {}
        for mode, label in CAPTURE_MODES:
            button = Gtk.RadioButton.new_with_label_from_widget(first, self._(label))
            first = first or button
            self.mode_buttons[mode] = button
            controls.pack_start(button, False, False, 0)
        self.shutter_button = Gtk.Button.new_with_label(self._("Capture"))
        self.shutter_button.get_style_context().add_class("shutter")
        self.shutter_button.connect("clicked", self._on_shutter_clicked)
        controls.pack_start(self.shutter_button, False, False, 12)
        for mode, button in self.mode_buttons.items():
            button.connect("toggled", self._on_mode_changed, mode)
        self.mode_buttons.get(self.capture_mode, self.mode_buttons["photo"]).set_active(True)
        self.shutter_button.set_label(self._("Record") if self.capture_mode == "video" else self._("Capture"))
        root.pack_start(controls, False, False, 0)

        filmstrip_frame = Gtk.ScrolledWindow()
        filmstrip_frame.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        filmstrip_frame.set_size_request(-1, 92)
        filmstrip_frame.get_style_context().add_class("filmstrip")
        self.filmstrip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filmstrip_viewport = Gtk.Viewport()
        filmstrip_viewport.add(self.filmstrip)
        filmstrip_frame.add(filmstrip_viewport)
        root.pack_end(filmstrip_frame, False, False, 0)

    def _rebuild_indicator_menu(self) -> None:
        indicator = self.app_shell.indicator
        assert indicator is not None
        if self.control_popup is not None:
            self.control_popup.destroy()
            self.control_popup = None
            self.control_popup_kind = ""
            self.control_scales.clear()
        menu = Gtk.Menu()
        self.tray_status_item = Gtk.MenuItem.new_with_label(self._("Turn camera on"))
        self.tray_status_item.set_sensitive(False)
        menu.append(self.tray_status_item)
        menu.append(Gtk.SeparatorMenuItem())
        self.tray_center_stage_item = Gtk.CheckMenuItem.new_with_label(self._("Center Stage"))
        self.tray_center_stage_item.set_active(bool(self.config.get("center_stage_enabled", True)))
        self.tray_center_stage_item.connect("toggled", self._on_center_stage_toggled)
        menu.append(self.tray_center_stage_item)
        self.effect_adjust_items.clear()
        self._append_effect_controls(
            menu, self._("Portrait"), "portrait_enabled", "portrait_strength", self._("Background Blur")
        )
        self._append_effect_controls(
            menu, self._("Studio Light"), "studio_light_enabled", "studio_light_strength", self._("Virtual Light")
        )
        self.preview_feature_items.clear()
        for feature_id, label in PREVIEW_FEATURES:
            item = Gtk.CheckMenuItem.new_with_label(f"{self._(label)} · {self._('Preview')}")
            item.set_active(self.preview_feature_state[feature_id])
            item.connect("toggled", self._on_preview_feature_toggled, feature_id)
            self.preview_feature_items[feature_id] = item
            menu.append(item)
        self.tray_mirror_item = Gtk.CheckMenuItem.new_with_label(self._("Mirror Camera"))
        self.tray_mirror_item.set_active(bool(self.config.get("mirror_output", False)))
        self.tray_mirror_item.connect("toggled", self._on_mirror_toggled)
        menu.append(self.tray_mirror_item)
        menu.append(Gtk.SeparatorMenuItem())
        self.camera_root_item = Gtk.MenuItem.new_with_label(self._("Camera"))
        self.camera_submenu = Gtk.Menu()
        self.camera_root_item.set_submenu(self.camera_submenu)
        menu.append(self.camera_root_item)
        self.microphone_root_item = Gtk.MenuItem.new_with_label(self._("Microphone"))
        self.microphone_submenu = Gtk.Menu()
        self.microphone_root_item.set_submenu(self.microphone_submenu)
        menu.append(self.microphone_root_item)
        self.tray_mute_item = Gtk.CheckMenuItem.new_with_label(self._("Mute Microphone"))
        self.tray_mute_item.set_active(bool(self.config.get("microphone_muted", False)))
        self.tray_mute_item.connect("toggled", self._on_mute_toggled)
        menu.append(self.tray_mute_item)
        adjustments = Gtk.MenuItem.new_with_label(self._("Image Adjustments"))
        adjustments.connect("activate", self._show_image_adjustments_popup)
        menu.append(adjustments)
        folder = Gtk.MenuItem.new_with_label(self._("Media Folder…"))
        folder.connect("activate", self._choose_media_folder)
        menu.append(folder)
        language = Gtk.MenuItem.new_with_label(self._("Language"))
        language_menu = Gtk.Menu()
        group: Gtk.RadioMenuItem | None = None
        for code, label in (("en", "English"), ("vi", "Tiếng Việt")):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = group or item
            item.set_active(code == self.config.get("language"))
            item.connect("toggled", self._on_language_selected, code)
            language_menu.append(item)
        language.set_submenu(language_menu)
        menu.append(language)
        menu.append(Gtk.SeparatorMenuItem())
        show = Gtk.MenuItem.new_with_label(self._("Show Center Stage"))
        show.connect("activate", lambda _item: self.present_window())
        menu.append(show)
        self.connect_item = Gtk.MenuItem.new_with_label(self._("Disconnect Camera"))
        self.connect_item.connect("activate", self._on_connect_disconnect)
        menu.append(self.connect_item)
        quit_item = Gtk.MenuItem.new_with_label(self._("Quit Center Stage"))
        quit_item.connect("activate", self._on_quit_requested)
        menu.append(quit_item)
        menu.show_all()
        indicator.set_menu(menu)
        self.indicator_menu = menu
        self._rebuild_camera_selectors()
        self._rebuild_microphone_menu()
        self._set_visible_status(self._last_status_code() if hasattr(self, "tray_status_item") else "stopped", self.engine_state)

    def _append_effect_controls(
        self,
        menu: Gtk.Menu,
        label: str,
        enabled_key: str,
        strength_key: str,
        strength_label: str,
    ) -> None:
        toggle = Gtk.CheckMenuItem.new_with_label(label)
        toggle.set_active(bool(self.config.get(enabled_key, False)))
        toggle.connect(
            "toggled", self._on_effect_toggled, enabled_key, strength_key, label, strength_label
        )
        menu.append(toggle)
        adjust = Gtk.MenuItem.new_with_label(f"   {strength_label}…")
        adjust.set_sensitive(toggle.get_active())
        adjust.connect("activate", self._show_effect_popup, strength_key, label, strength_label)
        self.effect_adjust_items[enabled_key] = adjust
        menu.append(adjust)

    def _replace_control_popup(self, title: str, kind: str) -> TraySliderPopup:
        if self.control_popup is not None:
            self.control_popup.destroy()
        self.control_popup = TraySliderPopup(title)
        self.control_popup_kind = kind
        return self.control_popup

    def _show_effect_popup(
        self,
        _item: Gtk.MenuItem | None,
        strength_key: str,
        effect_label: str,
        strength_label: str,
    ) -> None:
        popup = self._replace_control_popup(effect_label, strength_key)
        popup.add_scale(
            strength_label, 0, 100, int(self.config.get(strength_key, 50)), self._on_effect_strength_changed, strength_key
        )
        popup.present_at_pointer()

    def _show_image_adjustments_popup(self, _item: Gtk.MenuItem) -> None:
        popup = self._replace_control_popup(self._("Image Adjustments"), "image-adjustments")
        camera = self._selected_camera()
        values = camera_control_values(self.config, camera.camera_id) if camera else {}
        self.control_scales.clear()
        for name in CONTROL_NAMES:
            scale = popup.add_scale(
                self._(CONTROL_LABELS[name]), -100, 100, values.get(name, 0), self._on_control_changed, name
            )
            scale.set_sensitive(name in self.available_controls)
            self.control_scales[name] = scale
        reset = Gtk.Button.new_with_label(self._("Reset Image Settings"))
        reset.connect("clicked", self._on_reset_image_adjustments)
        reset.set_halign(Gtk.Align.END)
        popup.body.pack_start(reset, False, False, 0)
        popup.present_at_pointer()

    def _schedule_device_refresh(self, force: bool = False) -> None:
        if self._destroyed or (self._device_refresh_pending and not force):
            return
        self._device_generation += 1
        generation = self._device_generation
        self._device_refresh_pending = True
        future = self.worker.submit(lambda: (discover_cameras(), discover_microphones()))
        future.add_done_callback(lambda completed: GLib.idle_add(self._finish_device_refresh, generation, force, completed))

    def _finish_device_refresh(self, generation: int, force: bool, future: Future[Any]) -> bool:
        if self._destroyed or generation != self._device_generation:
            return False
        self._device_refresh_pending = False
        try:
            cameras, microphones = future.result()
        except Exception:
            LOGGER.exception("Device discovery failed")
            return False
        old_camera_ids = [item.camera_id for item in self.cameras]
        old_microphone_ids = [item.microphone_id for item in self.microphones]
        if not force and old_camera_ids == [item.camera_id for item in cameras] and old_microphone_ids == [item.microphone_id for item in microphones]:
            return False
        previous_camera_id = str(self.config.get("selected_camera_id") or "")
        previous_microphone_id = str(self.config.get("selected_microphone_id") or "")
        self.cameras = cameras
        self.microphones = microphones
        self.camera_by_id = {item.camera_id: item for item in cameras}
        self.microphone_by_id = {item.microphone_id: item for item in microphones}
        selected_camera = self.camera_by_id.get(previous_camera_id)
        if selected_camera is None and cameras:
            selected_camera = preferred_camera(cameras)
            self.config["selected_camera_id"] = selected_camera.camera_id if selected_camera else None
        selected_microphone = preferred_microphone(microphones, previous_microphone_id)
        self.config["selected_microphone_id"] = selected_microphone.microphone_id if selected_microphone else None
        save_config(self.config)
        self._rebuild_camera_selectors()
        self._rebuild_microphone_menu()
        self._load_camera_controls()
        if previous_camera_id and selected_camera and selected_camera.camera_id != previous_camera_id:
            self._notify(self._("Camera changed"), self._("Switched to {camera} because the previous camera disconnected.").format(camera=selected_camera.name))
            if self._camera_requested:
                self._service_action("restart" if self.engine_state == "running" else "start")
        if self.recording and previous_microphone_id and previous_microphone_id not in self.microphone_by_id:
            self.config["microphone_muted"] = True
            save_config(self.config)
            self.recorder.set_muted(True)
            self.tray_mute_item.set_active(True)
            self._notify(self._("Microphone disconnected"), self._("Video recording continues without sound."))
        if selected_camera and self._camera_requested and not self._initial_autostart_done:
            self._initial_autostart_done = True
            self._service_action("start")
        LOGGER.info("Devices refreshed cameras=%d microphones=%d", len(cameras), len(microphones))
        return False

    def _periodic_device_refresh(self) -> bool:
        self._schedule_device_refresh()
        return not self._destroyed

    def _selected_camera(self) -> CameraDevice | None:
        return self.camera_by_id.get(str(self.config.get("selected_camera_id") or ""))

    def _rebuild_camera_selectors(self) -> None:
        self._syncing = True
        self.camera_combo.remove_all()
        active_id = str(self.config.get("selected_camera_id") or "")
        for camera in self.cameras:
            self.camera_combo.append(camera.camera_id, camera.name)
        self.camera_combo.set_active_id(active_id)
        self._syncing = False
        if not hasattr(self, "camera_submenu"):
            return
        for child in self.camera_submenu.get_children():
            self.camera_submenu.remove(child)
        self.camera_menu_items.clear()
        first: Gtk.RadioMenuItem | None = None
        if not self.cameras:
            empty = Gtk.MenuItem.new_with_label(self._("No camera connected"))
            empty.set_sensitive(False)
            self.camera_submenu.append(empty)
        for camera in self.cameras:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(first, camera.name)
            first = first or item
            item.set_active(camera.camera_id == active_id)
            item.connect("toggled", self._on_tray_camera_selected, camera.camera_id)
            self.camera_menu_items[camera.camera_id] = item
            self.camera_submenu.append(item)
        refresh = Gtk.MenuItem.new_with_label(self._("Refresh Devices"))
        refresh.connect("activate", lambda _item: self._schedule_device_refresh(force=True))
        self.camera_submenu.append(Gtk.SeparatorMenuItem())
        self.camera_submenu.append(refresh)
        self.camera_submenu.show_all()

    def _rebuild_microphone_menu(self) -> None:
        if not hasattr(self, "microphone_submenu"):
            return
        for child in self.microphone_submenu.get_children():
            self.microphone_submenu.remove(child)
        self.microphone_menu_items.clear()
        active_id = str(self.config.get("selected_microphone_id") or "")
        first: Gtk.RadioMenuItem | None = None
        if not self.microphones:
            empty = Gtk.MenuItem.new_with_label(self._("No microphone connected"))
            empty.set_sensitive(False)
            self.microphone_submenu.append(empty)
        for microphone in self.microphones:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(first, microphone.name)
            first = first or item
            item.set_active(microphone.microphone_id == active_id)
            item.connect("toggled", self._on_microphone_selected, microphone.microphone_id)
            self.microphone_menu_items[microphone.microphone_id] = item
            self.microphone_submenu.append(item)
        self.microphone_submenu.show_all()

    def _on_window_camera_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._building or self._syncing:
            return
        camera_id = combo.get_active_id()
        if camera_id:
            self._select_camera(camera_id)

    def _on_tray_camera_selected(self, item: Gtk.RadioMenuItem, camera_id: str) -> None:
        if not self._syncing and item.get_active():
            self._select_camera(camera_id)

    def _select_camera(self, camera_id: str) -> None:
        if camera_id not in self.camera_by_id or camera_id == self.config.get("selected_camera_id"):
            return
        self.config["selected_camera_id"] = camera_id
        save_config(self.config)
        self._rebuild_camera_selectors()
        self._load_camera_controls()
        LOGGER.info("Camera selected id=%s", camera_id)
        if self._camera_requested:
            self._service_action("restart" if self.engine_state == "running" else "start")

    def _on_microphone_selected(self, item: Gtk.RadioMenuItem, microphone_id: str) -> None:
        if self._syncing or not item.get_active() or microphone_id == self.config.get("selected_microphone_id"):
            return
        self.config["selected_microphone_id"] = microphone_id
        save_config(self.config)
        LOGGER.info("Microphone selected id=%s", microphone_id)
        if self.recording:
            self._notify(self._("Microphone changed"), self._("The new microphone will be used for the next video."))

    def _load_camera_controls(self) -> None:
        camera = self._selected_camera()
        self._control_generation += 1
        generation = self._control_generation
        values = camera_control_values(self.config, camera.camera_id) if camera else {}
        self._syncing = True
        for name, scale in self.control_scales.items():
            scale.set_sensitive(False)
            scale.set_value(values.get(name, 0))
        self._syncing = False
        if camera is None:
            self.available_controls = {}
            return
        future = self.worker.submit(query_controls, camera.path)
        future.add_done_callback(lambda completed: GLib.idle_add(self._finish_camera_controls, generation, camera.camera_id, completed))

    def _finish_camera_controls(self, generation: int, camera_id: str, future: Future[Any]) -> bool:
        if self._destroyed or generation != self._control_generation or camera_id != self.config.get("selected_camera_id"):
            return False
        try:
            self.available_controls = future.result()
        except Exception:
            self.available_controls = {}
        for name, scale in self.control_scales.items():
            scale.set_sensitive(name in self.available_controls)
        return False

    def _on_control_changed(self, scale: Gtk.Scale, name: str) -> None:
        if self._syncing:
            return
        camera = self._selected_camera()
        control = self.available_controls.get(name)
        if camera is None or control is None:
            return
        value = int(round(scale.get_value()))
        set_camera_control_value(self.config, camera.camera_id, name, value)
        save_config(self.config)
        timer = self.control_timers.pop(name, 0)
        if timer:
            GLib.source_remove(timer)
        self.control_timers[name] = GLib.timeout_add(120, self._apply_control, camera.camera_id, name, value)

    def _apply_control(self, camera_id: str, name: str, value: int) -> bool:
        self.control_timers.pop(name, None)
        camera = self.camera_by_id.get(camera_id)
        control = self.available_controls.get(name)
        if camera and control:
            self.worker.submit(set_normalized_control, camera.path, control, value)
        return False

    def _on_reset_image_adjustments(self, _item: Gtk.MenuItem) -> None:
        for scale in self.control_scales.values():
            if scale.get_sensitive():
                scale.set_value(0)

    def _on_center_stage_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._syncing:
            return
        self.config["center_stage_enabled"] = item.get_active()
        save_config(self.config)

    def _on_preview_feature_toggled(self, item: Gtk.CheckMenuItem, feature_id: str) -> None:
        self.preview_feature_state[feature_id] = item.get_active()

    def _on_effect_toggled(
        self,
        item: Gtk.CheckMenuItem,
        config_key: str,
        strength_key: str,
        effect_label: str,
        strength_label: str,
    ) -> None:
        enabled = item.get_active()
        self.config[config_key] = enabled
        save_config(self.config)
        adjust = self.effect_adjust_items.get(config_key)
        if adjust is not None:
            adjust.set_sensitive(enabled)
        if enabled:
            GLib.timeout_add(
                140,
                self._show_effect_popup_after_toggle,
                strength_key,
                effect_label,
                strength_label,
            )
        elif self.control_popup_kind == strength_key and self.control_popup is not None:
            self.control_popup.hide()
        LOGGER.info("Effect toggled key=%s enabled=%s", config_key, enabled)

    def _show_effect_popup_after_toggle(self, strength_key: str, effect_label: str, strength_label: str) -> bool:
        self._show_effect_popup(None, strength_key, effect_label, strength_label)
        return False

    def _on_effect_strength_changed(self, scale: Gtk.Scale, config_key: str) -> None:
        self.config[config_key] = int(round(scale.get_value()))
        timer = self.effect_timers.pop(config_key, 0)
        if timer:
            GLib.source_remove(timer)
        self.effect_timers[config_key] = GLib.timeout_add(80, self._save_effect_strength, config_key)

    def _save_effect_strength(self, config_key: str) -> bool:
        self.effect_timers.pop(config_key, None)
        save_config(self.config)
        return False

    def _on_mirror_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._syncing:
            return
        self.config["mirror_output"] = item.get_active()
        save_config(self.config)
        LOGGER.info("Mirror changed enabled=%s", item.get_active())

    def _on_mute_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._syncing:
            return
        muted = item.get_active()
        self.config["microphone_muted"] = muted
        save_config(self.config)
        self.recorder.set_muted(muted)

    def _on_language_selected(self, item: Gtk.RadioMenuItem, language: str) -> None:
        if self._syncing or not item.get_active() or language == self.config.get("language"):
            return
        self.config["language"] = language
        save_config(self.config)
        self._ = translator(language)
        self._retranslate_in_place()
        LOGGER.info("Language changed language=%s", language)

    def _retranslate_in_place(self) -> None:
        """Rebuild child widgets and menu without replacing the window or indicator."""
        was_previewing = self.preview_running
        if was_previewing:
            self._stop_preview()
        child = self.get_child()
        if child is not None:
            self.remove(child)
        self._build_ui()
        self._rebuild_indicator_menu()
        self.show_all()
        self.countdown_label.hide()
        self.flash_overlay.hide()
        self._refresh_filmstrip()
        if was_previewing and self.engine_state == "running":
            self._start_preview()

    def _choose_media_folder(self, _item: Gtk.MenuItem) -> None:
        chooser = Gtk.FileChooserDialog(title=self._("Choose Media Folder"), transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        chooser.add_buttons(self._("Cancel"), Gtk.ResponseType.CANCEL, self._("Select"), Gtk.ResponseType.OK)
        chooser.set_current_folder(str(self.media_library.folder))
        if chooser.run() == Gtk.ResponseType.OK:
            selected = chooser.get_filename()
            if selected:
                self.config["media_folder"] = selected
                save_config(self.config)
                self._refresh_filmstrip()
        chooser.destroy()

    def _on_mode_changed(self, button: Gtk.RadioButton, mode: str) -> None:
        if button.get_active() and not self.recording:
            self.capture_mode = mode
            self.shutter_button.set_label(self._("Record") if mode == "video" else self._("Capture"))

    def _on_shutter_clicked(self, _button: Gtk.Button) -> None:
        if self.recording:
            self._stop_recording()
            return
        if self.capturing or self.engine_state != "running" or self.latest_frame is None:
            return
        if self.capture_mode == "video":
            self._start_recording()
        else:
            self.capturing = True
            self.four_up_frames = []
            self._countdown(3, self._capture_photo if self.capture_mode == "photo" else self._capture_four_up_frame)

    def _countdown(self, value: int, callback: Any) -> None:
        if value <= 0:
            self.countdown_label.hide()
            callback()
            return
        self.countdown_label.set_text(str(value))
        self.countdown_label.show()
        GLib.timeout_add_seconds(1, self._countdown_tick, value - 1, callback)

    def _countdown_tick(self, value: int, callback: Any) -> bool:
        self._countdown(value, callback)
        return False

    def _frame_copy(self) -> np.ndarray | None:
        with self.frame_lock:
            return None if self.latest_frame is None else self.latest_frame.copy()

    def _flash(self) -> None:
        self.flash_overlay.show()
        GLib.timeout_add(180, lambda: (self.flash_overlay.hide(), False)[1])

    def _capture_photo(self) -> None:
        frame = self._frame_copy()
        try:
            if frame is None:
                raise OSError("No camera frame")
            path = save_jpeg(frame, self.media_library.photo_path())
            self._flash()
            self._capture_finished(path)
        except OSError as error:
            self._media_error(error)

    def _capture_four_up_frame(self) -> None:
        frame = self._frame_copy()
        if frame is None:
            self._media_error(OSError("No camera frame"))
            return
        self.four_up_frames.append(frame)
        self._flash()
        if len(self.four_up_frames) < 4:
            GLib.timeout_add_seconds(1, lambda: (self._capture_four_up_frame(), False)[1])
            return
        try:
            path = save_jpeg(four_up(self.four_up_frames), self.media_library.four_up_path())
            self._capture_finished(path)
        except OSError as error:
            self._media_error(error)

    def _capture_finished(self, path: Path) -> None:
        self.capturing = False
        self.four_up_frames = []
        LOGGER.info("Media saved kind=%s path=%s", self.capture_mode, path)
        self._refresh_filmstrip()

    def _start_recording(self) -> None:
        try:
            self.recorder.start(
                self.media_library.video_path(),
                str(self.config.get("selected_microphone_id") or "") or None,
                bool(self.config.get("microphone_muted", False)),
            )
        except Exception as error:
            LOGGER.exception("Recording failed to start")
            self._media_error(error)
            return
        self.recording = True
        self.shutter_button.set_label(self._("Stop"))
        self.shutter_button.get_style_context().add_class("recording")
        for button in self.mode_buttons.values():
            button.set_sensitive(False)
        LOGGER.info("Recording started microphone=%s muted=%s", self.config.get("selected_microphone_id"), self.config.get("microphone_muted"))

    def _stop_recording(self) -> None:
        if not self.recording:
            return
        path = self.recorder.stop()
        self.recording = False
        self.shutter_button.set_label(self._("Record"))
        self.shutter_button.get_style_context().remove_class("recording")
        for button in self.mode_buttons.values():
            button.set_sensitive(True)
        if path is None:
            self._media_error(OSError("Video could not be finalized"))
            return
        LOGGER.info("Recording saved path=%s", path)
        if path.parent == self.recorder.recovery_folder:
            self._notify(self._("Video recovered"), self._("The selected folder was unavailable; the video was kept in the recovery folder."))
        self._refresh_filmstrip()

    def _on_recording_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        if message.type != Gst.MessageType.ERROR:
            return
        source_name = message.src.get_name() if message.src else ""
        if "pulse" in source_name:
            GLib.idle_add(self._handle_microphone_error)
        else:
            GLib.idle_add(self._handle_recording_error)

    def _handle_microphone_error(self) -> bool:
        self.config["microphone_muted"] = True
        save_config(self.config)
        self.recorder.set_muted(True)
        self._syncing = True
        self.tray_mute_item.set_active(True)
        self._syncing = False
        self._notify(self._("Microphone unavailable"), self._("Video recording continues without sound."))
        return False

    def _handle_recording_error(self) -> bool:
        if self.recording:
            self._stop_recording()
        return False

    def _media_error(self, error: Exception) -> None:
        self.capturing = False
        LOGGER.error("Media operation failed: %s", error)
        dialog = Gtk.MessageDialog(transient_for=self, flags=Gtk.DialogFlags.MODAL, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE, text=self._("Media could not be saved"))
        dialog.format_secondary_text(str(error))
        dialog.run()
        dialog.destroy()

    def _refresh_filmstrip(self) -> None:
        for child in self.filmstrip.get_children():
            self.filmstrip.remove(child)
        media = list_media(self.media_library.folder)
        if not media:
            label = Gtk.Label(label=self._("Your photos and videos will appear here."))
            self.filmstrip.pack_start(label, False, False, 8)
        for path in media:
            frame = thumbnail(path)
            if frame is None:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb.shape
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgb.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8, width, height, width * channels)
            button = Gtk.Button(image=Gtk.Image.new_from_pixbuf(pixbuf))
            button.get_style_context().add_class("thumbnail")
            button.set_tooltip_text(path.name)
            button.connect("clicked", lambda _button, item=path: self._open_path(item))
            button.connect("button-press-event", self._on_media_button_press, path)
            self.filmstrip.pack_start(button, False, False, 0)
        self.filmstrip.show_all()

    def _on_media_button_press(self, _button: Gtk.Button, event: Any, path: Path) -> bool:
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        open_folder = Gtk.MenuItem.new_with_label(self._("Open Folder"))
        open_folder.connect("activate", lambda _item: self._open_path(path.parent))
        delete = Gtk.MenuItem.new_with_label(self._("Delete"))
        delete.connect("activate", self._delete_media, path)
        menu.append(open_folder)
        menu.append(delete)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _open_path(self, path: Path) -> None:
        Gio.AppInfo.launch_default_for_uri(path.resolve().as_uri(), None)

    def _delete_media(self, _item: Gtk.MenuItem, path: Path) -> None:
        dialog = Gtk.MessageDialog(transient_for=self, flags=Gtk.DialogFlags.MODAL, message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.CANCEL, text=self._("Delete this media file?"))
        dialog.add_button(self._("Delete"), Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                path.unlink()
            except OSError as error:
                self._media_error(error)
            self._refresh_filmstrip()
        dialog.destroy()

    def _start_preview(self) -> None:
        self._stop_preview()
        try:
            pipeline = Gst.parse_launch(
                f'shmsrc socket-path="{preview_socket_path()}" is-live=true do-timestamp=true ! '
                'video/x-raw,format=YUY2,width=1920,height=1080,framerate=30/1 ! tee name=t '
                't. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! gtksink name=preview_sink sync=false '
                't. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! video/x-raw,format=BGR ! '
                'appsink name=frame_sink emit-signals=true max-buffers=1 drop=true sync=false'
            )
            sink = pipeline.get_by_name("preview_sink")
            frame_sink = pipeline.get_by_name("frame_sink")
            frame_sink.connect("new-sample", self._on_preview_sample)
            widget = sink.get_property("widget")
            self.preview_holder.pack_start(widget, True, True, 0)
            widget.show()
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_preview_message)
            pipeline.set_state(Gst.State.PLAYING)
            self.preview_pipeline = pipeline
            self.preview_widget = widget
            self.preview_running = True
            self.preview_stack.set_visible_child_name("preview")
        except Exception:
            LOGGER.exception("Preview failed to start")
            self.preview_stack.set_visible_child_name("placeholder")

    def _on_preview_sample(self, sink: Any) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            frame = np.frombuffer(info.data, dtype=np.uint8).reshape((height, width, 3)).copy()
        finally:
            buffer.unmap(info)
        with self.frame_lock:
            self.latest_frame = frame
        return Gst.FlowReturn.OK

    def _on_preview_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        if message.type in {Gst.MessageType.ERROR, Gst.MessageType.EOS}:
            GLib.idle_add(self._stop_preview)

    def _stop_preview(self) -> bool:
        if self.preview_pipeline is not None:
            bus = self.preview_pipeline.get_bus()
            bus.remove_signal_watch()
            self.preview_pipeline.set_state(Gst.State.NULL)
            self.preview_pipeline = None
        if self.preview_widget is not None and self.preview_widget.get_parent() is self.preview_holder:
            self.preview_holder.remove(self.preview_widget)
        self.preview_widget = None
        self.preview_running = False
        with self.frame_lock:
            self.latest_frame = None
        if hasattr(self, "preview_stack"):
            self.preview_stack.set_visible_child_name("placeholder")
        return False

    def _on_connect_disconnect(self, _item: Any) -> None:
        if self._camera_requested and self.engine_state in {"running", "starting", "restarting"}:
            if self.recording:
                self._stop_recording()
            self._camera_requested = False
            self._service_action("stop")
        else:
            self._camera_requested = True
            self._initial_autostart_done = True
            if self._selected_camera() is None:
                self._schedule_device_refresh(force=True)
                self._set_visible_status("camera_missing", "error")
            else:
                self._service_action("start")

    def _service_action(self, action: str, callback: Any | None = None) -> None:
        if self._service_action_pending and action != "stop":
            return
        self._service_action_pending = True
        self.engine_state = "restarting" if action == "restart" else "starting" if action == "start" else "stopped"
        self._transition_until = time.monotonic() + 6.0
        LOGGER.info("Engine action=%s", action)
        if action == "start":
            self._run_systemctl("reset-failed", lambda _ok: self._run_systemctl(action, lambda ok: self._finish_service_action(ok, callback)))
        else:
            self._run_systemctl(action, lambda ok: self._finish_service_action(ok, callback))

    def _run_systemctl(self, action: str, callback: Any) -> None:
        try:
            process = Gio.Subprocess.new(["systemctl", "--user", action, SERVICE_NAME], Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
            process.wait_check_async(None, self._on_systemctl_finished, callback)
        except GLib.Error:
            callback(False)

    def _on_systemctl_finished(self, process: Gio.Subprocess, result: Gio.AsyncResult, callback: Any) -> None:
        try:
            succeeded = process.wait_check_finish(result)
        except GLib.Error:
            succeeded = False
        callback(bool(succeeded))

    def _finish_service_action(self, succeeded: bool, callback: Any | None) -> None:
        if self._destroyed:
            return
        self._service_action_pending = False
        if not succeeded:
            self._transition_until = 0
            self.engine_state = "stopped"
            self._set_visible_status("service_unavailable", "error")
        if callback:
            callback(succeeded)

    def _last_status_code(self) -> str:
        return str(read_status().get("code", "stopped"))

    def _poll_status(self) -> bool:
        if self._destroyed:
            return False
        status = read_status()
        state = str(status.get("state", "stopped"))
        if state in {"running", "starting", "restarting"} and time.time() - float(status.get("updated_at", 0) or 0) > 3:
            state = "stopped"
        if time.monotonic() < self._transition_until and self.engine_state in {"starting", "restarting"} and state == "stopped":
            state = self.engine_state
        elif state in {"running", "error", "stopped"}:
            self._transition_until = 0
        self.engine_state = state
        self._set_visible_status(str(status.get("code", state)), state)
        if state == "running" and not self.preview_running:
            self._start_preview()
        elif state != "running" and self.preview_running:
            self._stop_preview()
        return True

    def _set_visible_status(self, code: str, state: str) -> None:
        message = self._(STATUS_MESSAGES.get(code, "Turn camera on"))
        active = state in {"running", "starting", "restarting"}
        self.camera_power_button.set_label(message if state != "running" else self._("Camera is ready"))
        self.camera_power_button.set_sensitive(not self._service_action_pending)
        self.shutter_button.set_sensitive(state == "running" and not self.capturing)
        self.tray_status_item.set_label(message)
        self.connect_item.set_label(self._("Disconnect Camera") if active else self._("Connect Camera"))
        self.tray_center_stage_item.set_sensitive(self._camera_requested)
        icon = "center-stage-active" if state == "running" else "center-stage-warning" if state in {"error", "starting", "restarting"} else "center-stage-idle"
        indicator = self.app_shell.indicator
        if indicator:
            indicator.set_icon_full(icon, message)

    def _notify(self, title: str, body: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.app_shell.send_notification(None, notification)

    def present_window(self) -> None:
        self.show_all()
        self.countdown_label.set_visible(self.capturing)
        self.flash_overlay.hide()
        self.present()

    def _on_delete_event(self, _window: Gtk.Window, _event: Any) -> bool:
        if self._quitting:
            return False
        self.hide()
        LOGGER.info("Window hidden to status icon")
        return True

    def _on_quit_requested(self, _item: Any) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.recording:
            self._stop_recording()
        self._camera_requested = False
        self._service_action("stop", self._finish_quit)

    def _finish_quit(self, _succeeded: bool) -> None:
        self.app_shell.finish_quit()

    def _on_window_destroyed(self, _window: Gtk.Window) -> None:
        self._destroyed = True
        for source in (getattr(self, "_status_timer", 0), getattr(self, "_device_timer", 0)):
            if source:
                GLib.source_remove(source)
        for source in self.control_timers.values():
            if source:
                GLib.source_remove(source)
        for source in self.effect_timers.values():
            if source:
                GLib.source_remove(source)
        if self.control_popup is not None:
            self.control_popup.destroy()
        self._stop_preview()
        self.worker.shutdown(wait=False, cancel_futures=True)


class TrayController:
    """Stable AppIndicator owned by AppShell for the full process lifetime."""

    @staticmethod
    def create() -> AyatanaAppIndicator3.Indicator:
        indicator = AyatanaAppIndicator3.Indicator.new("center-stage-status", "center-stage-idle", AyatanaAppIndicator3.IndicatorCategory.HARDWARE)
        indicator.set_title("Center Stage")
        indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        return indicator


class AppShell(Gtk.Application):
    """Single-instance application lifecycle and persistent status icon."""

    def __init__(self) -> None:
        super().__init__(application_id="io.centerstage.Desktop", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: PhotoBoothWindow | None = None
        self.indicator: AyatanaAppIndicator3.Indicator | None = None
        self._held = False

    def ensure_indicator(self, _window: PhotoBoothWindow) -> None:
        if self.indicator is None:
            self.indicator = TrayController.create()

    def do_activate(self) -> None:
        if not self._held:
            self.hold()
            self._held = True
        if self.window is None or self.window._destroyed:
            self.window = PhotoBoothWindow(self)
        self.window.present_window()

    def finish_quit(self) -> None:
        if self.indicator:
            self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        window, self.window = self.window, None
        if window and not window._destroyed:
            window.destroy()
        if self._held:
            self.release()
            self._held = False
        LOGGER.info("Application quit")
        self.quit()


# Compatibility aliases for older launchers and downstream imports.
CenterStageApplication = AppShell
CenterStageWindow = PhotoBoothWindow


def main() -> int:
    return AppShell().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
