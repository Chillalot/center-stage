# Center Stage V4.4.0

Center Stage turns a physical webcam into a face-tracking virtual camera on
Linux. The V4 desktop app provides a lightweight GTK3 interface in English and
Vietnamese while the camera engine runs independently as a systemd user
service.

## Desktop behavior

- Open **Center Stage** from the application menu. The preferred camera connects
  automatically and the virtual camera appears as **Center Stage** in video apps.
- The minimal Photo Booth window captures photos, 2x2 four-up collages, and
  H.264/AAC MP4 video with a selectable microphone.
- Select the camera and turn it on or off from the window. Center Stage,
  microphone, mute, mirror, image controls, language, and the media folder live
  in the Cinnamon status menu.
- Portrait applies an adjustable person-aware background blur. Studio Light
  adapts a face-aware virtual key light to the camera and room illumination,
  protects skin color and highlights, and subtly separates the background.
  Each effect has its own tray toggle and opens a compact adjustment popup when
  enabled. Both update live without restarting `/dev/video10`.
- Live-stream segmentation, motion-aware mask release, and an adaptive
  High/Balanced/Eco quality governor reduce latency and trailing edges while
  maintaining the 1080p/30fps virtual output.
- Closing the window hides it to the status icon and leaves the camera engine
  running. **Quit Center Stage** stops the camera before exiting.
- Reactions and Background items marked **Preview** are interface previews and
  do not alter video in this release.
- The engine does not start automatically after login.

English is used on first launch. Vietnamese can be selected from the status
menu without restarting the window or camera. Media defaults to
`~/Pictures/Center Stage` and can be changed from the same menu.

The on-screen preview is transported over a private shared-memory socket. It
does not open `/dev/video10`, leaving the V4L2 webcam available to Chrome,
Vesktop, and other video-call applications while the controller is open.
Current v4l2loopback releases allow one direct V4L2 reader at a time; concurrent
video applications must share the camera through PipeWire rather than opening
`/dev/video10` directly more than once.

## Build the local Debian package

The build needs Python 3.12, pip, GNU gettext, and internet access for the
private MediaPipe/OpenCV runtime:

```bash
chmod +x packaging/build_deb.sh
./packaging/build_deb.sh
```

The result is:

```text
dist/center-stage_4.4.0_amd64.deb
```

Install it with:

```bash
sudo apt install ./dist/center-stage_4.4.0_amd64.deb
```

The package expects a working `v4l2loopback` kernel module. It configures the
module to create `/dev/video10` with the label `Center Stage`, exclusive capture
capabilities, and enough buffers for concurrent consumers on future boots.
If the module was already loaded with different options, reboot once after
installation.

## Development

Create a virtual environment and install dependencies:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps mediapipe==0.10.35
```

Run the developer CLI:

```bash
.venv/bin/python main.py \
  --input /dev/v4l/by-id/<camera>-video-index0
```

Run tests:

```bash
.venv/bin/python -m unittest discover -s tests -v
msgfmt --check --check-format -o /dev/null po/vi.po
```

The GStreamer integration and physical-camera sharing suites are opt-in:

```bash
CENTER_STAGE_INTEGRATION_TESTS=1 .venv/bin/python -m unittest tests.test_integration_gstreamer -v
CENTER_STAGE_HARDWARE_TESTS=1 .venv/bin/python -m unittest tests.test_integration_gstreamer -v
```

The development-only Chrome harness enumerates `Center Stage`, captures 90
frames, and checks resolution, cadence, black frames, frozen frames, and
timestamp gaps. It is never copied into the Debian package:

```bash
CENTER_STAGE_BROWSER_TESTS=1 .venv/bin/python -m unittest tests.test_browser_compat -v
```

The GTK controller uses system Python because Linux Mint already provides
PyGObject:

```bash
PYTHONPATH=. python3 -m center_stage.gui
```

Starting the background engine from the development GUI requires the packaged
systemd user unit. The CLI can be used before installing the package.

## Files and runtime state

- User configuration: `~/.config/center-stage/config.json`
- Runtime status: `$XDG_RUNTIME_DIR/center-stage/status.json`
- Virtual camera: `/dev/video10`
- User service: `center-stage-engine.service`
- GUI log: `~/.local/state/center-stage/gui.log`
- Default media: `~/Pictures/Center Stage`

The application stores image-control values separately for each stable camera
ID. Camera product names remain unchanged; only the integrated/USB description
is translated.

## Troubleshooting

### Camera is busy

Center Stage captures physical cameras through PipeWire, so PipeWire-aware apps
can use the physical camera and Center Stage concurrently. Legacy apps that open
`/dev/video*` directly may still require the physical camera to be released.

### Center Stage does not appear in a video app

Open Center Stage first, then reopen the video app's camera selector. The
standard V4L2 webcam appears while the engine is writing and stops being usable
after **Disconnect Camera** or **Quit Center Stage**. PipeWire may also discover that V4L2 webcam, but Center Stage does not
create a PipeWire node itself. It never restarts PipeWire, WirePlumber, or the
audio service.

### `/dev/video10` is missing

Check the module and reboot once after package installation:

```bash
modinfo v4l2loopback
v4l2-ctl --list-devices
```

Secure Boot may prevent an unsigned DKMS module from loading.
