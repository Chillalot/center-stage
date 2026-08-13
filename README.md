# Center Stage V4.5.1

Center Stage turns a physical webcam into a face-tracking virtual camera on
Linux. The V4 desktop app provides a lightweight GTK3 interface in English and
Vietnamese while the camera engine runs independently as a systemd user
service.

## Desktop behavior

- Open **Center Stage** from the application menu. The preferred camera connects
  automatically and the virtual camera appears as **Center Stage** in video apps.
- The slim Photo Booth toolbar switches between photos, 2x2 four-up collages,
  and H.264/AAC MP4 video with compact rounded controls. The media strip sits
  above the toolbar, can be collapsed, and has a dedicated horizontal scrollbar.
- Video recording uses a three-second countdown, shows elapsed time while live,
  and adds a representative frame plus duration to each album thumbnail. The
  recorder prefers hardware H.264 with bounded queues and falls back to tuned
  software encoders when hardware acceleration is unavailable.
- Choose the bright white or dark theme from the window header or from the
  existing status-icon menu; no additional status icon is created.
- Select the camera and turn it on or off from the window. Center Stage,
  microphone, mute, mirror, image controls, language, and media configuration
  live in the Cinnamon status menu. **Configure…** provides separate editable
  destinations and folder pickers for pictures and videos.
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
menu without restarting the window or camera. Pictures default to `~/Pictures`
and videos default to `~/Videos`; existing legacy media remains visible in the
album after upgrading.

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
dist/center-stage_4.5.1_amd64.deb
```

Install it with:

```bash
sudo apt install ./dist/center-stage_4.5.1_amd64.deb
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
- Default pictures: `~/Pictures`
- Default videos: `~/Videos`

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

## Contributing

Center Stage is organized around a headless camera engine and a thin GTK
controller. Keep changes focused on one layer when possible:

| Area | Primary files | Contribution focus |
| --- | --- | --- |
| Camera engine | `center_stage/engine.py`, `devices.py`, `framing.py` | PipeWire capture, face tracking, effects, V4L2 output |
| Desktop UI | `center_stage/gui.py` | GTK layout, tray actions, capture workflow, accessibility |
| Media | `center_stage/media.py` | Recording pipelines, thumbnails, folder handling |
| Configuration | `center_stage/config.py` | Backward-compatible settings and atomic persistence |
| Localization | `po/vi.po`, `po/center-stage.pot` | English/Vietnamese strings and gettext validation |
| Packaging | `packaging/`, `tests/test_package.py` | Debian package, systemd unit, install lifecycle |

Before opening a pull request:

1. Branch from `main` with a focused name such as `fix/config-validation` or
   `feat/media-export`.
2. Keep the patch scoped; do not commit `dist/`, local recordings, recovery
   files, virtual environments, or personal configuration.
3. Add or update a regression test for behavior changes.
4. Run the default suite and gettext validation:

   ```bash
   .venv/bin/python -m unittest discover -s tests -v
   msgfmt --check --check-format -o /dev/null po/vi.po
   ```

5. Run integration, hardware, or browser suites only when the required device
   and services are available, and document skipped opt-in checks in the PR.
6. Describe the user impact, failure mode, validation performed, and any
   migration or packaging implications in the PR body.

## Collaboration plan

The project uses small, reviewable workstreams. A contributor can own one
workstream while a collaborator reviews its boundary with adjacent modules:

| Phase | Goal | Exit criteria |
| --- | --- | --- |
| 1. Stabilize | Validate config inputs, cancel pending GTK timers, and avoid blocking UI paths | Corrupt-config and lifecycle regressions are covered by tests |
| 2. Harden media | Make recording paths/parser inputs robust and keep EOS handling off the GTK main loop | Paths with unusual names work; stop/recovery behavior is tested |
| 3. Smooth UI | Move album thumbnail/decode work off the main loop and preserve tray/window state | Album refresh remains responsive with large folders |
| 4. Verify hardware | Exercise PipeWire sharing, V4L2 output, VA-API fallback, microphones, and browser capture | Opt-in integration suites pass on supported hardware |
| 5. Release | Update version, translations, README, package metadata, and release notes together | Tests pass, `.deb` metadata matches, and the GitHub release includes the artifact |

For parallel work, agree on file ownership before editing. Prefer one
integration PR per phase, rebase only when needed, and have a collaborator
review cross-cutting changes to `config.py`, `engine.py`, or `media.py` before
merge. The maintainer makes the final release/version decision; contributors
should not publish packages or alter the default branch directly.

## Maintainer release checklist

- Confirm the version is identical in `center_stage/__init__.py`,
  `packaging/build_deb.sh`, README examples, translations, and package tests.
- Run the default test suite, `msgfmt`, and any available opt-in checks.
- Build the Debian package and verify its metadata, contents, size, and SHA-256.
- Create a version tag and GitHub Release with the `.deb` attached.
- Record known limitations and skipped hardware checks in the release notes.
