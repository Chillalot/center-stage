"""GTK3 desktop controller for the Center Stage background engine."""

from __future__ import annotations

import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst, Gtk, Pango  # noqa: E402

from . import __version__
from .config import (
    CONTROL_NAMES,
    camera_control_values,
    load_config,
    preview_socket_path,
    read_status,
    save_config,
    set_camera_control_value,
)
from .devices import CameraDevice, discover_cameras, query_controls, set_normalized_control
from .i18n import translator

SERVICE_NAME = "center-stage-engine.service"
N_ = lambda message: message

STATUS_MESSAGES = {
    "stopped": N_("Center Stage is off"),
    "opening_camera": N_("Opening camera…"),
    "ready": N_("Center Stage is ready"),
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

CONTROL_LABELS = {
    "brightness": N_("Brightness"),
    "contrast": N_("Contrast"),
    "saturation": N_("Saturation"),
    "hue": N_("Hue"),
}


class CenterStageWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        self.config = load_config()
        self._ = translator(str(self.config.get("language", "en")))
        super().__init__(application=application, title="Center Stage")
        self.set_default_size(960, 650)
        self.set_size_request(680, 480)
        self.set_icon_name("center-stage")
        self.connect("destroy", self._on_window_destroyed)

        self.cameras: list[CameraDevice] = []
        self.camera_by_id: dict[str, CameraDevice] = {}
        self.available_controls: dict[str, Any] = {}
        self.control_scales: dict[str, Gtk.Scale] = {}
        self.control_timers: dict[str, int] = {}
        self.pipeline: Gst.Pipeline | None = None
        self.preview_widget: Gtk.Widget | None = None
        self.preview_running = False
        self.engine_state = "stopped"
        self._building = True
        self._destroyed = False
        self._language_reload_pending = False
        self._status_timer = 0
        self._device_timer = 0
        self._restart_timer = 0
        self._preview_retry_timer = 0
        self._preview_retry_count = 0
        self._service_action_pending = False
        self._transition_until = 0.0
        self._device_generation = 0
        self._control_generation = 0
        self._device_refresh_pending = False
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="center-stage-gui")

        Gst.init(None)
        self._build_ui()
        self._building = False
        self.show_all()
        self._schedule_camera_refresh(force=True)

        self._status_timer = GLib.timeout_add(500, self._poll_status)
        self._device_timer = GLib.timeout_add_seconds(2, self._periodic_device_refresh)

    def _build_ui(self) -> None:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Center Stage"
        header.props.subtitle = f"V{__version__}"
        self.set_titlebar(header)

        self.camera_combo = Gtk.ComboBoxText()
        self.camera_combo.set_tooltip_text(self._("Input camera"))
        self.camera_combo.connect("changed", self._on_camera_changed)
        header.pack_start(self.camera_combo)

        self.settings_button = Gtk.Button()
        self.settings_button.set_image(
            Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        )
        self.settings_button.set_tooltip_text(self._("Settings"))
        self.settings_popover = self._build_settings_popover()
        self.settings_popover.set_relative_to(self.settings_button)
        self.settings_popover.set_position(Gtk.PositionType.BOTTOM)
        self.settings_button.connect("clicked", self._on_settings_clicked)
        header.pack_end(self.settings_button)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        self.preview_stack = Gtk.Stack()
        self.preview_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.preview_stack.set_hexpand(True)
        self.preview_stack.set_vexpand(True)
        self.preview_stack.get_style_context().add_class("view")

        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        placeholder.set_halign(Gtk.Align.CENTER)
        placeholder.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("camera-web-symbolic", Gtk.IconSize.DIALOG)
        placeholder.pack_start(icon, False, False, 0)
        self.placeholder_label = Gtk.Label(
            label=self._("Choose a camera, then select Start to create your virtual camera.")
        )
        self.placeholder_label.set_line_wrap(True)
        self.placeholder_label.set_justify(Gtk.Justification.CENTER)
        placeholder.pack_start(self.placeholder_label, False, False, 0)
        self.preview_stack.add_named(placeholder, "placeholder")

        self.preview_holder = Gtk.Box()
        self.preview_holder.set_hexpand(True)
        self.preview_holder.set_vexpand(True)
        self.preview_stack.add_named(self.preview_holder, "preview")
        self.preview_stack.set_visible_child_name("placeholder")
        root.pack_start(self.preview_stack, True, True, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom.set_border_width(12)
        self.status_label = Gtk.Label(label=self._("Center Stage is off"))
        self.status_label.set_xalign(0.0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        bottom.pack_start(self.status_label, True, True, 0)
        self.fps_label = Gtk.Label(label="")
        bottom.pack_start(self.fps_label, False, False, 0)
        self.start_button = Gtk.Button.new_with_label(self._("Start"))
        self.start_button.set_size_request(130, -1)
        self.start_button.get_style_context().add_class("suggested-action")
        self.start_button.connect("clicked", self._on_start_stop)
        bottom.pack_end(self.start_button, False, False, 0)
        root.pack_end(bottom, False, False, 0)

    def _build_settings_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(14)
        box.set_size_request(360, -1)

        title = Gtk.Label()
        title.set_markup(f"<b>{GLib.markup_escape_text(self._('Settings'))}</b>")
        title.set_xalign(0)
        box.pack_start(title, False, False, 0)

        self.auto_zoom_switch = self._switch_row(
            box, self._("Auto Zoom"), bool(self.config.get("auto_zoom", True))
        )
        self.auto_zoom_switch.connect("notify::active", self._on_auto_zoom_changed)
        self.mirror_switch = self._switch_row(
            box, self._("Mirror Output"), bool(self.config.get("mirror_output", False))
        )
        self.mirror_switch.connect("notify::active", self._on_mirror_changed)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.pack_start(separator, False, False, 2)

        language_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        language_row.pack_start(Gtk.Label(label=self._("Language"), xalign=0), True, True, 0)
        self.language_combo = Gtk.ComboBoxText()
        self.language_combo.append("en", "English")
        self.language_combo.append("vi", "Tiếng Việt")
        self.language_combo.set_active_id(str(self.config.get("language", "en")))
        self.language_combo.connect("changed", self._on_language_changed)
        language_row.pack_end(self.language_combo, False, False, 0)
        box.pack_start(language_row, False, False, 0)

        image_title = Gtk.Label()
        image_settings_text = self._("Image Settings")
        image_title.set_markup(f"<b>{GLib.markup_escape_text(image_settings_text)}</b>")
        image_title.set_xalign(0)
        box.pack_start(image_title, False, False, 4)

        for name in CONTROL_NAMES:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label = Gtk.Label(label=self._(CONTROL_LABELS[name]), xalign=0)
            row.pack_start(label, False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -100, 100, 1)
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
            scale.connect("value-changed", self._on_control_changed, name)
            self.control_scales[name] = scale
            row.pack_start(scale, False, False, 0)
            box.pack_start(row, False, False, 0)

        reset = Gtk.Button.new_with_label(self._("Reset Image Settings"))
        reset.connect("clicked", self._on_reset_controls)
        box.pack_start(reset, False, False, 2)
        popover.add(box)
        box.show_all()
        return popover

    def _on_settings_clicked(self, _button: Gtk.Button) -> None:
        if self.settings_popover.get_visible():
            self.settings_popover.popdown()
        else:
            self.settings_popover.popup()

    @staticmethod
    def _switch_row(parent: Gtk.Box, label: str, active: bool) -> Gtk.Switch:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
        switch = Gtk.Switch(active=active)
        row.pack_end(switch, False, False, 0)
        parent.pack_start(row, False, False, 0)
        return switch

    def _camera_label(self, camera: CameraDevice) -> str:
        kind = self._("Integrated camera") if camera.kind == "integrated" else self._("External USB camera")
        return f"{camera.name} — {kind}"

    def _schedule_camera_refresh(self, force: bool = False) -> None:
        if self._destroyed or (self._device_refresh_pending and not force):
            return
        self._device_generation += 1
        generation = self._device_generation
        self._device_refresh_pending = True
        future = self.worker.submit(discover_cameras)
        future.add_done_callback(
            lambda completed: GLib.idle_add(
                self._finish_camera_refresh, generation, force, completed
            )
        )

    def _finish_camera_refresh(
        self, generation: int, force: bool, future: Future[list[CameraDevice]]
    ) -> bool:
        if self._destroyed or generation != self._device_generation:
            return False
        self._device_refresh_pending = False
        try:
            discovered = future.result()
        except Exception:
            return False
        signature = [(camera.camera_id, camera.path) for camera in discovered]
        previous_signature = [(camera.camera_id, camera.path) for camera in self.cameras]
        if not force and signature == previous_signature:
            return False
        active_id = self.config.get("selected_camera_id")
        self.cameras = discovered
        self.camera_by_id = {camera.camera_id: camera for camera in discovered}
        self._building = True
        self.camera_combo.remove_all()
        for camera in discovered:
            self.camera_combo.append(camera.camera_id, self._camera_label(camera))
        if active_id in self.camera_by_id:
            self.camera_combo.set_active_id(active_id)
        elif active_id is None and discovered:
            preferred = next((camera for camera in discovered if camera.kind == "usb"), discovered[0])
            self.config["selected_camera_id"] = preferred.camera_id
            self.camera_combo.set_active_id(preferred.camera_id)
            save_config(self.config)
        self._building = False
        self._load_camera_controls()
        return False

    def _periodic_device_refresh(self) -> bool:
        self._schedule_camera_refresh()
        return True

    def _selected_camera(self) -> CameraDevice | None:
        return self.camera_by_id.get(self.camera_combo.get_active_id() or "")

    def _load_camera_controls(self) -> None:
        camera = self._selected_camera()
        self._control_generation += 1
        generation = self._control_generation
        self._building = True
        values = camera_control_values(self.config, camera.camera_id) if camera else {}
        for name, scale in self.control_scales.items():
            scale.set_sensitive(False)
            scale.set_tooltip_text(self._("Checking camera controls…") if camera else None)
            scale.set_value(values.get(name, 0))
        self._building = False
        if camera is None:
            self.available_controls = {}
            return
        future = self.worker.submit(query_controls, camera.path)
        future.add_done_callback(
            lambda completed: GLib.idle_add(
                self._finish_camera_controls,
                generation,
                camera.camera_id,
                completed,
            )
        )

    def _finish_camera_controls(
        self,
        generation: int,
        camera_id: str,
        future: Future[dict[str, Any]],
    ) -> bool:
        if (
            self._destroyed
            or generation != self._control_generation
            or self.camera_combo.get_active_id() != camera_id
        ):
            return False
        try:
            self.available_controls = future.result()
        except Exception:
            self.available_controls = {}
        self._building = True
        for name, scale in self.control_scales.items():
            supported = name in self.available_controls
            scale.set_sensitive(supported)
            scale.set_tooltip_text(
                None if supported else self._("Not supported by this camera")
            )
        self._building = False
        return False

    def _on_camera_changed(self, _combo: Gtk.ComboBoxText) -> None:
        if self._building:
            return
        camera = self._selected_camera()
        if camera is None:
            return
        self.config["selected_camera_id"] = camera.camera_id
        save_config(self.config)
        self._load_camera_controls()
        if self.engine_state == "running":
            self._schedule_restart()

    def _on_control_changed(self, scale: Gtk.Scale, name: str) -> None:
        if self._building:
            return
        camera = self._selected_camera()
        if camera is None or name not in self.available_controls:
            return
        value = int(round(scale.get_value()))
        set_camera_control_value(self.config, camera.camera_id, name, value)
        save_config(self.config)
        old_timer = self.control_timers.pop(name, None)
        if old_timer:
            GLib.source_remove(old_timer)
        self.control_timers[name] = GLib.timeout_add(
            120, self._apply_control, camera.camera_id, name, value
        )

    def _apply_control(self, camera_id: str, name: str, value: int) -> bool:
        self.control_timers.pop(name, None)
        camera = self.camera_by_id.get(camera_id)
        control = self.available_controls.get(name)
        if camera and control:
            self.worker.submit(set_normalized_control, camera.path, control, value)
        return False

    def _on_reset_controls(self, _button: Gtk.Button) -> None:
        for scale in self.control_scales.values():
            if scale.get_sensitive():
                scale.set_value(0)

    def _on_auto_zoom_changed(self, switch: Gtk.Switch, _property: Any) -> None:
        if self._building:
            return
        self.config["auto_zoom"] = switch.get_active()
        save_config(self.config)
        if self.engine_state == "running":
            self._schedule_restart()

    def _on_mirror_changed(self, switch: Gtk.Switch, _property: Any) -> None:
        if self._building:
            return
        self.config["mirror_output"] = switch.get_active()
        save_config(self.config)
        if self.engine_state == "running":
            self._schedule_restart()

    def _on_language_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._building:
            return
        language = combo.get_active_id()
        if language not in {"en", "vi"} or language == self.config.get("language"):
            return
        self.config["language"] = language
        save_config(self.config)
        if not self._language_reload_pending:
            self._language_reload_pending = True
            GLib.idle_add(self._reload_gui)

    def _reload_gui(self) -> bool:
        """Rebuild one window in-process so language changes cannot orphan a GUI."""
        if self._destroyed:
            return False
        application = self.get_application()
        if application is None:
            return False
        replacement = CenterStageWindow(application)
        if hasattr(application, "window"):
            application.window = replacement
        replacement.present()
        self.destroy()
        return False

    def _on_start_stop(self, _button: Gtk.Button) -> None:
        if self._service_action_pending:
            return
        if self.engine_state in {"running", "starting", "restarting"}:
            self._service_action("stop")
        else:
            if self._selected_camera() is None:
                self.status_label.set_text(self._("Select a connected camera first."))
                return
            self._service_action("start")

    def _schedule_restart(self) -> None:
        if self._restart_timer:
            GLib.source_remove(self._restart_timer)
        self._restart_timer = GLib.timeout_add(250, self._run_scheduled_restart)

    def _run_scheduled_restart(self) -> bool:
        self._restart_timer = 0
        if self._destroyed or self.engine_state != "running":
            return False
        if self._service_action_pending:
            self._restart_timer = GLib.timeout_add(250, self._run_scheduled_restart)
            return False
        self._service_action("restart")
        return False

    def _service_action(self, action: str) -> None:
        self._service_action_pending = True
        self.start_button.set_sensitive(False)
        self.engine_state = "restarting" if action == "restart" else (
            "starting" if action == "start" else "stopped"
        )
        self._transition_until = time.monotonic() + 6.0
        self.status_label.set_text(
            self._("Restarting…") if action == "restart" else (
                self._("Starting…") if action == "start" else self._("Center Stage is off")
            )
        )
        if action == "start":
            self._run_systemctl_async(
                "reset-failed", lambda _ok: self._run_systemctl_async(action, self._finish_service_action)
            )
        else:
            self._run_systemctl_async(action, self._finish_service_action)

    def _run_systemctl_async(self, action: str, callback: Any) -> None:
        try:
            process = Gio.Subprocess.new(
                ["systemctl", "--user", action, SERVICE_NAME],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
            process.wait_check_async(None, self._on_systemctl_finished, callback)
        except GLib.Error:
            callback(False)

    def _on_systemctl_finished(
        self, process: Gio.Subprocess, result: Gio.AsyncResult, callback: Any
    ) -> None:
        try:
            succeeded = process.wait_check_finish(result)
        except GLib.Error:
            succeeded = False
        callback(bool(succeeded))

    def _finish_service_action(self, succeeded: bool) -> None:
        if self._destroyed:
            return
        self._service_action_pending = False
        self.start_button.set_sensitive(True)
        if not succeeded:
            self._transition_until = 0.0
            self.engine_state = "stopped"
            self.status_label.set_text(self._(STATUS_MESSAGES["service_unavailable"]))

    def _poll_status(self) -> bool:
        status = read_status()
        state = str(status.get("state", "stopped"))
        updated_at = float(status.get("updated_at", 0.0) or 0.0)
        if state in {"running", "starting", "restarting"} and time.time() - updated_at > 3.0:
            state = "stopped"
        if (
            time.monotonic() < self._transition_until
            and self.engine_state in {"starting", "restarting"}
            and state == "stopped"
        ):
            state = self.engine_state
        elif state in {"running", "error", "stopped"}:
            self._transition_until = 0.0
        self.engine_state = state
        code = str(status.get("code", state))
        message = self._(STATUS_MESSAGES.get(code, "Center Stage is off"))
        self.status_label.set_text(message)

        if state == "running":
            fps = float(status.get("fps", 0.0) or 0.0)
            tracking = str(status.get("tracking", ""))
            self.fps_label.set_text(f"{tracking} · {fps:.1f} FPS" if fps else tracking)
            self.start_button.set_label(self._("Stop"))
            self.start_button.get_style_context().remove_class("suggested-action")
            self.start_button.get_style_context().add_class("destructive-action")
            if not self.preview_running:
                self._start_preview()
        else:
            self.fps_label.set_text("")
            self.start_button.set_label(self._("Start"))
            self.start_button.get_style_context().remove_class("destructive-action")
            self.start_button.get_style_context().add_class("suggested-action")
            if self.preview_running:
                self._stop_preview()
        return True

    def _start_preview(self) -> None:
        self._stop_preview()
        flip = "" if bool(self.config.get("mirror_output", False)) else "videoflip method=horizontal-flip ! "
        try:
            pipeline = Gst.parse_launch(
                f"shmsrc socket-path={preview_socket_path()} is-live=true do-timestamp=true ! "
                "video/x-raw,format=YUY2,width=1920,height=1080,framerate=30/1 ! "
                "queue leaky=downstream max-size-buffers=1 ! "
                f"videoconvert ! {flip}gtksink name=preview_sink sync=false"
            )
            sink = pipeline.get_by_name("preview_sink")
            widget = sink.get_property("widget")
            self.preview_holder.pack_start(widget, True, True, 0)
            widget.show()
            pipeline.set_state(Gst.State.PLAYING)
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_preview_message)
            self.pipeline = pipeline
            self.preview_widget = widget
            self.preview_running = True
            self._preview_retry_count = 0
            self.preview_stack.set_visible_child_name("preview")
        except Exception:
            self.preview_stack.set_visible_child_name("placeholder")

    def _on_preview_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        if message.type not in {Gst.MessageType.ERROR, Gst.MessageType.EOS}:
            return
        self._stop_preview()
        if (
            not self._destroyed
            and self.engine_state == "running"
            and self._preview_retry_count < 3
            and not self._preview_retry_timer
        ):
            self._preview_retry_count += 1
            self._preview_retry_timer = GLib.timeout_add(750, self._retry_preview)

    def _retry_preview(self) -> bool:
        self._preview_retry_timer = 0
        if not self._destroyed and self.engine_state == "running":
            self._start_preview()
        return False

    def _stop_preview(self) -> None:
        if self.pipeline is not None:
            bus = self.pipeline.get_bus()
            bus.remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self.preview_widget is not None:
            self.preview_holder.remove(self.preview_widget)
            self.preview_widget = None
        self.preview_running = False
        self.preview_stack.set_visible_child_name("placeholder")

    def _on_window_destroyed(self, _window: Gtk.Window) -> None:
        self._destroyed = True
        for source_id in (
            self._status_timer,
            self._device_timer,
            self._restart_timer,
            self._preview_retry_timer,
        ):
            if source_id:
                GLib.source_remove(source_id)
        self._status_timer = 0
        self._device_timer = 0
        self._restart_timer = 0
        self._preview_retry_timer = 0
        for source_id in self.control_timers.values():
            if source_id:
                GLib.source_remove(source_id)
        self.control_timers.clear()
        self._stop_preview()
        self.worker.shutdown(wait=False, cancel_futures=True)


class CenterStageApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.centerstage.Desktop", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: CenterStageWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = CenterStageWindow(self)
        self.window.present()


def main() -> int:
    return CenterStageApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
