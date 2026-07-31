"""GTK3 workspace and status controller for the Center Stage engine."""

from __future__ import annotations

import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import (  # noqa: E402
    AyatanaAppIndicator3,
    Gio,
    GLib,
    Gst,
    Gtk,
    Pango,
)

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
from .devices import (
    CameraDevice,
    discover_cameras,
    preferred_camera,
    query_controls,
    set_normalized_control,
)
from .i18n import translator

SERVICE_NAME = "center-stage-engine.service"
N_ = lambda message: message

STATUS_MESSAGES = {
    "stopped": N_("Camera is disconnected"),
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

CONTROL_LABELS = {
    "brightness": N_("Brightness"),
    "contrast": N_("Contrast"),
    "saturation": N_("Saturation"),
    "hue": N_("Hue"),
}

PREVIEW_FEATURES = (
    ("portrait", N_("Portrait")),
    ("studio_light", N_("Studio Light")),
    ("reactions", N_("Reactions")),
    ("background", N_("Background")),
)


class CenterStageWindow(Gtk.ApplicationWindow):
    """One camera workspace backed by a persistent systemd user service."""

    def __init__(self, application: "CenterStageApplication"):
        self.config = load_config()
        self._ = translator(str(self.config.get("language", "en")))
        super().__init__(application=application, title="Center Stage")
        self.set_default_size(1040, 680)
        self.set_size_request(760, 520)
        self.set_icon_name("center-stage")
        self.connect("delete-event", self._on_delete_event)
        self.connect("destroy", self._on_window_destroyed)

        self.cameras: list[CameraDevice] = []
        self.camera_by_id: dict[str, CameraDevice] = {}
        self.available_controls: dict[str, Any] = {}
        self.control_scales: dict[str, Gtk.Scale] = {}
        self.control_timers: dict[str, int] = {}
        self.preview_feature_buttons: dict[str, Gtk.ToggleButton] = {}
        self.preview_feature_menu_items: dict[str, Gtk.CheckMenuItem] = {}
        self.camera_menu_items: dict[str, Gtk.RadioMenuItem] = {}
        self.pipeline: Gst.Pipeline | None = None
        self.preview_widget: Gtk.Widget | None = None
        self.preview_running = False
        self.engine_state = "stopped"
        self._building = True
        self._destroyed = False
        self._quitting = False
        self._syncing_controls = False
        self._status_timer = 0
        self._device_timer = 0
        self._preview_retry_timer = 0
        self._preview_retry_count = 0
        self._service_action_pending = False
        self._transition_until = 0.0
        self._device_generation = 0
        self._control_generation = 0
        self._device_refresh_pending = False
        self._initial_autostart_done = False
        self._camera_requested = True
        self._last_status_code = "stopped"
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="center-stage-gui")

        Gst.init(None)
        self._install_css()
        self._build_ui()
        self._build_indicator()
        self._building = False
        self.show_all()
        self.manual_framing_revealer.set_reveal_child(
            not bool(self.config.get("center_stage_enabled", True))
        )
        self.image_settings_revealer.set_reveal_child(False)
        self.workspace_stack.set_visible_child_name("camera")
        self._schedule_camera_refresh(force=True)
        self._status_timer = GLib.timeout_add(500, self._poll_status)
        self._device_timer = GLib.timeout_add_seconds(2, self._periodic_device_refresh)

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            .camera-stage { background: #151515; }
            .workspace-toolbar { padding: 8px 12px; }
            .status-chip { padding: 5px 10px; border-radius: 999px; background: alpha(@theme_fg_color, 0.08); }
            .effect-dock { padding: 10px; }
            .effect-button { padding: 8px 12px; min-width: 112px; transition: 180ms ease-in-out; }
            .effect-button:checked { background: @theme_selected_bg_color; color: @theme_selected_fg_color; }
            .preview-feature:checked { background: alpha(@theme_selected_bg_color, 0.35); }
            .preview-label { font-size: 0.8em; opacity: 0.72; }
            .coming-soon { opacity: 0.72; }
            .settings-panel { padding: 12px; }
            .empty-stage { padding: 32px; }
            """
        )
        screen = self.get_screen()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.get_style_context().add_class("window-root")
        self.add(root)

        self.workspace_stack = Gtk.Stack()
        self.workspace_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.workspace_stack.set_transition_duration(180)
        self.workspace_stack.set_hexpand(True)
        self.workspace_stack.set_vexpand(True)

        self.workspace_stack.add_titled(self._build_camera_page(), "camera", self._("Camera"))
        self.workspace_stack.add_titled(self._build_studio_page(), "studio", self._("Studio"))
        self.workspace_stack.add_titled(self._build_calls_page(), "calls", self._("Calls"))
        root.pack_start(self.workspace_stack, True, True, 0)

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Center Stage"
        header.props.subtitle = f"V{__version__}"
        self.set_titlebar(header)

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.workspace_stack)
        switcher.set_halign(Gtk.Align.CENTER)
        header.set_custom_title(switcher)

        self.settings_button = Gtk.Button()
        self.settings_button.set_image(
            Gtk.Image.new_from_icon_name("emblem-system-symbolic", Gtk.IconSize.BUTTON)
        )
        self.settings_button.set_tooltip_text(self._("Image Adjustments"))
        self.settings_button.connect("clicked", self._on_settings_clicked)
        header.pack_end(self.settings_button)

    def _build_camera_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.get_style_context().add_class("workspace-toolbar")
        self.camera_name_label = Gtk.Label(label=self._("No camera connected"), xalign=0)
        self.camera_name_label.set_ellipsize(Pango.EllipsizeMode.END)
        toolbar.pack_start(self.camera_name_label, True, True, 0)
        self.fps_label = Gtk.Label(label="")
        toolbar.pack_end(self.fps_label, False, False, 0)
        self.status_label = Gtk.Label(label=self._("Camera is disconnected"))
        self.status_label.get_style_context().add_class("status-chip")
        toolbar.pack_end(self.status_label, False, False, 0)
        page.pack_start(toolbar, False, False, 0)

        self.preview_stack = Gtk.Stack()
        self.preview_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.preview_stack.set_transition_duration(180)
        self.preview_stack.set_hexpand(True)
        self.preview_stack.set_vexpand(True)
        self.preview_stack.get_style_context().add_class("camera-stage")

        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        placeholder.set_halign(Gtk.Align.CENTER)
        placeholder.set_valign(Gtk.Align.CENTER)
        placeholder.get_style_context().add_class("empty-stage")
        placeholder.pack_start(
            Gtk.Image.new_from_icon_name("camera-web-symbolic", Gtk.IconSize.DIALOG),
            False,
            False,
            0,
        )
        self.placeholder_label = Gtk.Label(
            label=self._("Connect a camera to make Center Stage available to Meet and Vesktop.")
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
        page.pack_start(self.preview_stack, True, True, 0)

        self.manual_framing_revealer = Gtk.Revealer()
        self.manual_framing_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.manual_framing_revealer.set_transition_duration(180)
        manual = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        manual.set_border_width(8)
        manual.pack_start(Gtk.Label(label=self._("Manual Framing")), False, False, 0)
        preview = Gtk.Label(label=self._("Preview"))
        preview.get_style_context().add_class("preview-label")
        manual.pack_start(preview, False, False, 0)
        zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 2.0, 0.1)
        zoom.set_value(1.0)
        zoom.set_draw_value(True)
        zoom.set_size_request(220, -1)
        manual.pack_start(zoom, True, True, 0)
        recenter = Gtk.Button.new_with_label(self._("Recenter"))
        recenter.connect("clicked", lambda _button: zoom.set_value(1.0))
        manual.pack_end(recenter, False, False, 0)
        self.manual_framing_revealer.add(manual)
        page.pack_start(self.manual_framing_revealer, False, False, 0)

        dock = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dock.set_halign(Gtk.Align.CENTER)
        dock.get_style_context().add_class("effect-dock")
        self.center_stage_button = self._effect_button(self._("Center Stage"), preview=False)
        self.center_stage_button.set_active(
            bool(self.config.get("center_stage_enabled", True))
        )
        self.center_stage_button.connect("toggled", self._on_center_stage_toggled)
        dock.pack_start(self.center_stage_button, False, False, 0)
        for feature_id, label in PREVIEW_FEATURES:
            button = self._effect_button(self._(label), preview=True)
            button.connect("toggled", self._on_preview_feature_toggled, feature_id)
            self.preview_feature_buttons[feature_id] = button
            dock.pack_start(button, False, False, 0)
        page.pack_start(dock, False, False, 0)

        self.image_settings_revealer = Gtk.Revealer()
        self.image_settings_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.image_settings_revealer.set_transition_duration(180)
        self.image_settings_revealer.add(self._build_image_settings_panel())
        page.pack_end(self.image_settings_revealer, False, False, 0)
        return page

    def _effect_button(self, label: str, *, preview: bool) -> Gtk.ToggleButton:
        text = f"{label}\n{self._('Preview')}" if preview else label
        button = Gtk.ToggleButton.new_with_label(text)
        button.get_style_context().add_class("effect-button")
        if preview:
            button.get_style_context().add_class("preview-feature")
            button.set_tooltip_text(
                self._("Interface preview only — this effect does not change the video yet.")
            )
        return button

    def _build_image_settings_panel(self) -> Gtk.Widget:
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.get_style_context().add_class("settings-panel")

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label()
        title.set_markup(f"<b>{GLib.markup_escape_text(self._('Image Adjustments'))}</b>")
        title.set_xalign(0)
        heading.pack_start(title, True, True, 0)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda _button: self.image_settings_revealer.set_reveal_child(False))
        heading.pack_end(close, False, False, 0)
        panel.pack_start(heading, False, False, 0)

        grid = Gtk.Grid(column_spacing=18, row_spacing=6)
        for index, name in enumerate(CONTROL_NAMES):
            column = index % 2
            row = (index // 2) * 2
            label = Gtk.Label(label=self._(CONTROL_LABELS[name]), xalign=0)
            grid.attach(label, column, row, 1, 1)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -100, 100, 1)
            scale.set_hexpand(True)
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
            scale.connect("value-changed", self._on_control_changed, name)
            self.control_scales[name] = scale
            grid.attach(scale, column, row + 1, 1, 1)
        panel.pack_start(grid, False, False, 0)

        options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        options.pack_start(Gtk.Label(label=self._("Mirror Output")), False, False, 0)
        self.mirror_switch = Gtk.Switch(active=bool(self.config.get("mirror_output", False)))
        self.mirror_switch.connect("notify::active", self._on_mirror_changed)
        options.pack_start(self.mirror_switch, False, False, 0)
        options.pack_start(Gtk.Label(label=self._("Language")), False, False, 0)
        self.language_combo = Gtk.ComboBoxText()
        self.language_combo.append("en", "English")
        self.language_combo.append("vi", "Tiếng Việt")
        self.language_combo.set_active_id(str(self.config.get("language", "en")))
        self.language_combo.connect("changed", self._on_language_changed)
        options.pack_start(self.language_combo, False, False, 0)
        reset = Gtk.Button.new_with_label(self._("Reset Image Settings"))
        reset.connect("clicked", self._on_reset_controls)
        options.pack_end(reset, False, False, 0)
        panel.pack_start(options, False, False, 0)
        frame.add(panel)
        return frame

    def _build_studio_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(16)
        page.get_style_context().add_class("coming-soon")

        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        modes.set_halign(Gtk.Align.CENTER)
        first: Gtk.RadioButton | None = None
        for label in (self._("Photo"), self._("4-up"), self._("Video")):
            button = Gtk.RadioButton.new_with_label_from_widget(first, label)
            first = first or button
            button.set_sensitive(False)
            modes.pack_start(button, False, False, 0)
        page.pack_start(modes, False, False, 0)

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        stage.set_valign(Gtk.Align.CENTER)
        stage.set_halign(Gtk.Align.CENTER)
        stage.pack_start(
            Gtk.Image.new_from_icon_name("camera-photo-symbolic", Gtk.IconSize.DIALOG),
            False,
            False,
            0,
        )
        title = Gtk.Label()
        studio_title = self._("Studio is coming soon")
        title.set_markup(f"<big><b>{GLib.markup_escape_text(studio_title)}</b></big>")
        stage.pack_start(title, False, False, 0)
        stage.pack_start(
            Gtk.Label(label=self._("Photo, 4-up, video, and the filmstrip will use the same camera stage.")),
            False,
            False,
            0,
        )
        shutter = Gtk.Button.new_with_label(self._("Capture"))
        shutter.set_sensitive(False)
        shutter.set_size_request(140, 48)
        stage.pack_start(shutter, False, False, 4)
        page.pack_start(stage, True, True, 0)

        filmstrip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filmstrip.set_size_request(-1, 72)
        filmstrip.pack_start(Gtk.Label(label=self._("Filmstrip")), False, False, 0)
        page.pack_end(filmstrip, False, False, 0)
        return page

    def _build_calls_page(self) -> Gtk.Widget:
        page = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        page.set_position(260)
        page.get_style_context().add_class("coming-soon")

        contacts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        contacts.set_border_width(14)
        title = Gtk.Label()
        recent_calls = self._("Recent Calls")
        title.set_markup(f"<b>{GLib.markup_escape_text(recent_calls)}</b>")
        title.set_xalign(0)
        contacts.pack_start(title, False, False, 0)
        contacts.pack_start(Gtk.SearchEntry(), False, False, 0)
        empty = Gtk.Label(label=self._("No recent calls"), xalign=0)
        contacts.pack_start(empty, False, False, 12)
        page.pack1(contacts, resize=False, shrink=False)

        call_stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        call_stage.set_halign(Gtk.Align.CENTER)
        call_stage.set_valign(Gtk.Align.CENTER)
        call_stage.pack_start(
            Gtk.Image.new_from_icon_name("camera-web-symbolic", Gtk.IconSize.DIALOG),
            False,
            False,
            0,
        )
        heading = Gtk.Label()
        calls_title = self._("Calls are coming soon")
        heading.set_markup(f"<big><b>{GLib.markup_escape_text(calls_title)}</b></big>")
        call_stage.pack_start(heading, False, False, 0)
        call_stage.pack_start(
            Gtk.Label(label=self._("Start one-to-one and group calls from this workspace.")),
            False,
            False,
            0,
        )
        new_call = Gtk.Button.new_with_label(self._("New Call"))
        new_call.set_sensitive(False)
        call_stage.pack_start(new_call, False, False, 4)
        page.pack2(call_stage, resize=True, shrink=False)
        return page

    def _build_indicator(self) -> None:
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "center-stage-status",
            "center-stage-idle",
            AyatanaAppIndicator3.IndicatorCategory.HARDWARE,
        )
        self.indicator.set_title("Center Stage")
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        self.indicator_menu = Gtk.Menu()
        self.tray_status_item = Gtk.MenuItem.new_with_label(self._("Camera is disconnected"))
        self.tray_status_item.set_sensitive(False)
        self.indicator_menu.append(self.tray_status_item)
        self.indicator_menu.append(Gtk.SeparatorMenuItem())

        self.tray_center_stage_item = Gtk.CheckMenuItem.new_with_label(self._("Center Stage"))
        self.tray_center_stage_item.set_active(
            bool(self.config.get("center_stage_enabled", True))
        )
        self.tray_center_stage_item.connect("toggled", self._on_tray_center_stage_toggled)
        self.indicator_menu.append(self.tray_center_stage_item)

        for feature_id, label in PREVIEW_FEATURES:
            item = Gtk.CheckMenuItem.new_with_label(f"{self._(label)} · {self._('Preview')}")
            item.connect("toggled", self._on_tray_preview_toggled, feature_id)
            self.preview_feature_menu_items[feature_id] = item
            self.indicator_menu.append(item)

        self.indicator_menu.append(Gtk.SeparatorMenuItem())
        self.camera_root_item = Gtk.MenuItem.new_with_label(self._("Camera"))
        self.camera_submenu = Gtk.Menu()
        self.camera_root_item.set_submenu(self.camera_submenu)
        self.indicator_menu.append(self.camera_root_item)

        adjustments = Gtk.MenuItem.new_with_label(self._("Image Adjustments…"))
        adjustments.connect("activate", self._on_tray_adjustments)
        self.indicator_menu.append(adjustments)
        show = Gtk.MenuItem.new_with_label(self._("Show Center Stage"))
        show.connect("activate", lambda _item: self.present_workspace("camera"))
        self.indicator_menu.append(show)

        self.indicator_menu.append(Gtk.SeparatorMenuItem())
        self.connect_item = Gtk.MenuItem.new_with_label(self._("Disconnect Camera"))
        self.connect_item.connect("activate", self._on_connect_disconnect)
        self.indicator_menu.append(self.connect_item)
        quit_item = Gtk.MenuItem.new_with_label(self._("Quit Center Stage"))
        quit_item.connect("activate", self._on_quit_requested)
        self.indicator_menu.append(quit_item)

        self.indicator_menu.show_all()
        self.indicator.set_menu(self.indicator_menu)

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

        previous_id = str(self.config.get("selected_camera_id") or "")
        self.cameras = discovered
        self.camera_by_id = {camera.camera_id: camera for camera in discovered}
        selected = self.camera_by_id.get(previous_id)
        fallback_changed = False
        if selected is None and discovered:
            selected = preferred_camera(discovered)
            assert selected is not None
            self.config["selected_camera_id"] = selected.camera_id
            save_config(self.config)
            fallback_changed = bool(previous_id and previous_id != selected.camera_id)

        self._rebuild_camera_menu()
        self._load_camera_controls()
        self._update_camera_name()

        if fallback_changed and selected is not None:
            self._notify(
                self._("Camera changed"),
                self._("Switched to {camera} because the previous camera disconnected.").format(
                    camera=selected.name
                ),
            )
            if self._camera_requested:
                self._service_action(
                    "restart" if self.engine_state == "running" else "start"
                )

        if selected is not None and self._camera_requested and not self._initial_autostart_done:
            self._initial_autostart_done = True
            self._service_action("start")
        return False

    def _periodic_device_refresh(self) -> bool:
        self._schedule_camera_refresh()
        return True

    def _selected_camera(self) -> CameraDevice | None:
        return self.camera_by_id.get(str(self.config.get("selected_camera_id") or ""))

    def _rebuild_camera_menu(self) -> None:
        for child in self.camera_submenu.get_children():
            self.camera_submenu.remove(child)
        self.camera_menu_items.clear()
        first: Gtk.RadioMenuItem | None = None
        active_id = str(self.config.get("selected_camera_id") or "")
        if not self.cameras:
            empty = Gtk.MenuItem.new_with_label(self._("No camera connected"))
            empty.set_sensitive(False)
            self.camera_submenu.append(empty)
        for camera in self.cameras:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(first, self._camera_label(camera))
            first = first or item
            item.set_active(camera.camera_id == active_id)
            item.connect("toggled", self._on_tray_camera_selected, camera.camera_id)
            self.camera_menu_items[camera.camera_id] = item
            self.camera_submenu.append(item)
        self.camera_submenu.append(Gtk.SeparatorMenuItem())
        refresh = Gtk.MenuItem.new_with_label(self._("Refresh Cameras"))
        refresh.connect("activate", lambda _item: self._schedule_camera_refresh(force=True))
        self.camera_submenu.append(refresh)
        self.camera_submenu.show_all()

    def _update_camera_name(self) -> None:
        camera = self._selected_camera()
        self.camera_name_label.set_text(
            self._camera_label(camera) if camera else self._("No camera connected")
        )

    def _on_tray_camera_selected(self, item: Gtk.RadioMenuItem, camera_id: str) -> None:
        if self._syncing_controls or not item.get_active():
            return
        camera = self.camera_by_id.get(camera_id)
        if camera is None or camera_id == self.config.get("selected_camera_id"):
            return
        self.config["selected_camera_id"] = camera_id
        save_config(self.config)
        self._load_camera_controls()
        self._update_camera_name()
        if self._camera_requested:
            self._service_action("restart" if self.engine_state == "running" else "start")

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
                self._finish_camera_controls, generation, camera.camera_id, completed
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
            or self.config.get("selected_camera_id") != camera_id
        ):
            return False
        try:
            self.available_controls = future.result()
        except Exception:
            self.available_controls = {}
        for name, scale in self.control_scales.items():
            supported = name in self.available_controls
            scale.set_sensitive(supported)
            scale.set_tooltip_text(None if supported else self._("Not supported by this camera"))
        return False

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

    def _on_center_stage_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._building or self._syncing_controls:
            return
        enabled = button.get_active()
        self.config["center_stage_enabled"] = enabled
        save_config(self.config)
        self.manual_framing_revealer.set_reveal_child(not enabled)
        self._syncing_controls = True
        self.tray_center_stage_item.set_active(enabled)
        self._syncing_controls = False

    def _on_tray_center_stage_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._syncing_controls:
            return
        self._syncing_controls = True
        self.center_stage_button.set_active(item.get_active())
        self._syncing_controls = False
        self.config["center_stage_enabled"] = item.get_active()
        save_config(self.config)
        self.manual_framing_revealer.set_reveal_child(not item.get_active())

    def _on_preview_feature_toggled(
        self, button: Gtk.ToggleButton, feature_id: str
    ) -> None:
        if self._syncing_controls:
            return
        item = self.preview_feature_menu_items.get(feature_id)
        if item is not None:
            self._syncing_controls = True
            item.set_active(button.get_active())
            self._syncing_controls = False

    def _on_tray_preview_toggled(
        self, item: Gtk.CheckMenuItem, feature_id: str
    ) -> None:
        if self._syncing_controls:
            return
        button = self.preview_feature_buttons.get(feature_id)
        if button is not None:
            self._syncing_controls = True
            button.set_active(item.get_active())
            self._syncing_controls = False

    def _on_mirror_changed(self, switch: Gtk.Switch, _property: Any) -> None:
        if self._building:
            return
        self.config["mirror_output"] = switch.get_active()
        save_config(self.config)
        if self.preview_running:
            self._start_preview()

    def _on_language_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._building:
            return
        language = combo.get_active_id()
        if language not in {"en", "vi"} or language == self.config.get("language"):
            return
        self.config["language"] = language
        save_config(self.config)
        application = self.get_application()
        if isinstance(application, CenterStageApplication):
            GLib.idle_add(application.reload_window)

    def _on_settings_clicked(self, _button: Gtk.Button) -> None:
        self.present_workspace("camera")
        self.image_settings_revealer.set_reveal_child(
            not self.image_settings_revealer.get_reveal_child()
        )

    def _on_tray_adjustments(self, _item: Gtk.MenuItem) -> None:
        self.present_workspace("camera")
        self.image_settings_revealer.set_reveal_child(True)

    def present_workspace(self, name: str) -> None:
        self.workspace_stack.set_visible_child_name(name)
        self.show_all()
        self.present()

    def _on_delete_event(self, _window: Gtk.Window, _event: Any) -> bool:
        if self._quitting:
            return False
        self.hide()
        return True

    def _on_connect_disconnect(self, _item: Gtk.MenuItem) -> None:
        if self._camera_requested and self.engine_state in {"running", "starting", "restarting"}:
            self._camera_requested = False
            self._service_action("stop")
        else:
            self._camera_requested = True
            self._initial_autostart_done = True
            if self._selected_camera() is None:
                self._schedule_camera_refresh(force=True)
                self._set_visible_status("camera_missing", "error")
            else:
                self._service_action("start")

    def _service_action(self, action: str, callback: Any | None = None) -> None:
        if self._service_action_pending and action != "stop":
            return
        self._service_action_pending = True
        if action == "restart":
            self.engine_state = "restarting"
        elif action == "start":
            self.engine_state = "starting"
        else:
            self.engine_state = "stopped"
        self._transition_until = time.monotonic() + 6.0
        if action == "start":
            self._run_systemctl_async(
                "reset-failed",
                lambda _ok: self._run_systemctl_async(
                    action, lambda ok: self._finish_service_action(ok, callback)
                ),
            )
        else:
            self._run_systemctl_async(
                action, lambda ok: self._finish_service_action(ok, callback)
            )

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

    def _finish_service_action(self, succeeded: bool, callback: Any | None) -> None:
        if self._destroyed:
            return
        self._service_action_pending = False
        if not succeeded:
            self._transition_until = 0.0
            self.engine_state = "stopped"
            self._set_visible_status("service_unavailable", "error")
        if callback is not None:
            callback(succeeded)

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
        self._last_status_code = code
        self._set_visible_status(code, state)

        if state == "running":
            fps = float(status.get("fps", 0.0) or 0.0)
            tracking = str(status.get("tracking", ""))
            self.fps_label.set_text(f"{tracking} · {fps:.1f} FPS" if fps else tracking)
            if not self.preview_running:
                self._start_preview()
        else:
            self.fps_label.set_text("")
            if self.preview_running:
                self._stop_preview()
        return True

    def _set_visible_status(self, code: str, state: str) -> None:
        message = self._(STATUS_MESSAGES.get(code, "Camera is disconnected"))
        self.status_label.set_text(message)
        self.tray_status_item.set_label(message)
        active = state in {"running", "starting", "restarting"}
        self.connect_item.set_label(
            self._("Disconnect Camera") if active else self._("Connect Camera")
        )
        self.tray_center_stage_item.set_sensitive(self._camera_requested)
        if state == "running":
            icon = "center-stage-active"
        elif state in {"error", "starting", "restarting"}:
            icon = "center-stage-warning"
        else:
            icon = "center-stage-idle"
        self.indicator.set_icon_full(icon, message)

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

    def _notify(self, title: str, body: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        application = self.get_application()
        if isinstance(application, Gio.Application):
            application.send_notification(None, notification)

    def _on_quit_requested(self, _item: Gtk.MenuItem) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._camera_requested = False
        self._service_action("stop", self._finish_quit)

    def _finish_quit(self, _succeeded: bool) -> None:
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        application = self.get_application()
        self.destroy()
        if isinstance(application, CenterStageApplication):
            application.finish_quit()

    def prepare_for_reload(self) -> None:
        self._quitting = True
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.destroy()

    def _on_window_destroyed(self, _window: Gtk.Window) -> None:
        self._destroyed = True
        for source_id in (
            self._status_timer,
            self._device_timer,
            self._preview_retry_timer,
        ):
            if source_id:
                GLib.source_remove(source_id)
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
        self._held = False

    def do_activate(self) -> None:
        if not self._held:
            self.hold()
            self._held = True
        if self.window is None or self.window._destroyed:
            self.window = CenterStageWindow(self)
        self.window.present_workspace("camera")

    def reload_window(self) -> bool:
        old = self.window
        self.window = CenterStageWindow(self)
        self.window.present_workspace("camera")
        if old is not None:
            old.prepare_for_reload()
        return False

    def finish_quit(self) -> None:
        self.window = None
        if self._held:
            self.release()
            self._held = False
        self.quit()


def main() -> int:
    return CenterStageApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
