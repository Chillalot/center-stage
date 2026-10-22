<div align="center">

<img src="packaging/icons/center-stage-256.png" width="112" alt="Center Stage application icon">

# Center Stage

**A private, local-first virtual camera that keeps you centered.**

[![CI](https://github.com/Chillalot/center-stage/actions/workflows/ci.yml/badge.svg)](https://github.com/Chillalot/center-stage/actions/workflows/ci.yml)
[![Release](https://github.com/Chillalot/center-stage/actions/workflows/release.yml/badge.svg)](https://github.com/Chillalot/center-stage/actions/workflows/release.yml)
[![Latest release](https://img.shields.io/github/v/release/Chillalot/center-stage?display_name=tag)](https://github.com/Chillalot/center-stage/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-blue.svg)](#downloads)

</div>

Center Stage turns a physical webcam into a face-tracking virtual camera. It
keeps your face framed while you move, adds optional portrait blur and studio
lighting, and can capture photos, 2×2 four-up collages, and H.264/AAC videos.
Everything runs locally; there is no cloud processing, account, or telemetry.

| Platform | Status | Virtual camera output | UI |
| --- | --- | --- | --- |
| Linux Mint 22.x / Ubuntu 24.04 | Stable | v4l2loopback (`/dev/video10`) | GTK 3 |
| Windows 10 22H2 / 11 (x64) | **Beta** | OBS Virtual Camera | PySide6 |

The Windows build is marked **Beta** until it has completed hardware validation
and OBS Virtual Camera testing on a physical Windows machine. See the
[Windows beta testing guide](docs/WINDOWS_BETA_TESTING.md) if you can help.

## Downloads

All release assets are published on the
[latest release](https://github.com/Chillalot/center-stage/releases/latest).

| Asset | Platform | Notes |
| --- | --- | --- |
| [`center-stage_amd64.deb`](https://github.com/Chillalot/center-stage/releases/latest/download/center-stage_amd64.deb) | Linux | Recommended. Installs the engine service, v4l2loopback configuration, desktop entry, and icons. |
| [`CenterStage-x86_64.AppImage`](https://github.com/Chillalot/center-stage/releases/latest/download/CenterStage-x86_64.AppImage) | Linux | Slim portable build. Uses the system Python 3.12, GTK 3, GStreamer, and PipeWire stack. |
| [`CenterStage-Setup.exe`](https://github.com/Chillalot/center-stage/releases/latest/download/CenterStage-Setup.exe) | Windows | **Beta.** Per-user installer; OBS Virtual Camera driver required. |
| [`CenterStage-win64.zip`](https://github.com/Chillalot/center-stage/releases/latest/download/CenterStage-win64.zip) | Windows | **Beta.** Portable ZIP; extract and run `CenterStage.exe`. |
| [`SHA256SUMS.txt`](https://github.com/Chillalot/center-stage/releases/latest/download/SHA256SUMS.txt) | All | SHA-256 checksums for the release binaries. |

### Verify your download

Linux:

```bash
curl -LO https://github.com/Chillalot/center-stage/releases/latest/download/SHA256SUMS-linux.txt
sha256sum -c SHA256SUMS-linux.txt
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://github.com/Chillalot/center-stage/releases/latest/download/SHA256SUMS-windows.txt -OutFile SHA256SUMS-windows.txt
Get-FileHash .\CenterStage-Setup.exe -Algorithm SHA256
# Compare the hash with the matching line in SHA256SUMS-windows.txt.
```

The in-app update checker uses the GitHub Releases API. Use the published
checksums to verify a download; the updater module also verifies a downloaded
asset against the release checksum before installation.

## Install

### Linux — Debian package (recommended)

The package targets Linux Mint 22.x and Ubuntu 24.04 on x86-64. It installs the
GTK controller, the headless engine user service, the desktop entry, icons, and
the v4l2loopback module configuration.

```bash
curl -LO https://github.com/Chillalot/center-stage/releases/latest/download/center-stage_amd64.deb
sudo apt install ./center-stage_amd64.deb
```

If `v4l2loopback` was already loaded with different options, reboot once after
installation so `/dev/video10` is created with the Center Stage label. Then open
**Center Stage** from the application menu and choose **Center Stage** as the
camera in Zoom, Teams, Meet, Discord, OBS, or another video application.

### Linux — Slim AppImage

The AppImage does not bundle Python, GTK, GStreamer, or PipeWire. Install the
system dependencies once:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gstreamer-1.0 \
  gir1.2-gst-plugins-base-1.0 gir1.2-ayatanaappindicator3-0.1 \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
  gstreamer1.0-pipewire gstreamer1.0-libav \
  v4l2loopback-dkms v4l2loopback-utils
```

Download and run it:

```bash
curl -LO https://github.com/Chillalot/center-stage/releases/latest/download/CenterStage-x86_64.AppImage
chmod +x CenterStage-x86_64.AppImage
./CenterStage-x86_64.AppImage
```

The AppImage runs the engine directly when a systemd user unit is not installed.
The v4l2loopback kernel module must be available. If `/dev/video10` is missing:

```bash
sudo modprobe v4l2loopback video_nr=10 card_label="Center Stage" exclusive_caps=1
```

For a managed installation with the module loaded at boot and a systemd user
service, use the `.deb` package instead.

### Windows — Beta

Requirements:

- Windows 10 22H2 or Windows 11, x64.
- A webcam and optionally a microphone.
- [OBS Studio](https://obsproject.com/) installed once so the **OBS Virtual
  Camera** driver is available. OBS itself does not need to be running.
- The Microsoft Visual C++ 2015–2022 Redistributable if Windows reports a
  missing runtime DLL.

Run `CenterStage-Setup.exe` and follow the per-user installer. The build is not
code-signed yet, so Windows SmartScreen may show a warning: choose **More info**
→ **Run anyway** only if you downloaded the file from this repository and the
SHA-256 checksum matches.

Launch **Center Stage**, allow camera and microphone access, then select **OBS
Virtual Camera** in your video application. The portable ZIP works without an
installer; extract it and run `CenterStage.exe`.

## Use

### Linux

- The window controls camera power, photos, four-up capture, video recording,
  themes, and language.
- Use the Cinnamon status icon for camera/microphone selection, mirror,
  portrait blur, studio light, image controls, folders, and quit.
- Closing the window hides it to the status icon and keeps the virtual camera
  running.
- Choose **Center Stage** in a video app. The virtual camera is available while
  the engine is running.
- Pictures default to `~/Pictures`; videos default to `~/Videos`.

### Windows

- The camera starts automatically. Use the preview, mode tabs, shutter button,
  and album on the main window.
- The system tray menu contains camera power, microphone mute, mirror, portrait
  blur, studio light, settings, update checks, and quit.
- Photo mode captures a JPEG. Four-up mode captures four frames and creates a
  2×2 collage. Video mode records H.264/AAC MP4 with an optional microphone.
- Settings selects the camera, microphone, pictures/videos folders, update
  channel, automatic update checks, and sign-in startup.
- Closing the window hides it to the tray. **Quit Center Stage** stops the
  camera and exits.

### Effects

- **Center Stage** tracks the largest face and smoothly reframes the output to
  1080p/30fps.
- **Portrait** applies a person-aware background blur.
- **Studio Light** adds a face-aware virtual key light while protecting skin
  tones and highlights.
- The adaptive quality governor switches between High, Balanced, and Eco
  segmentation cadence based on measured headroom.

## Privacy

Center Stage processes video and audio on your device. It does not upload
frames, recordings, or microphone audio. The optional update check contacts the
GitHub Releases API and sends only a standard User-Agent string; it does not
include a hardware ID or usage data. Release notes and update checks can be
disabled in Settings.

## Requirements

Linux:

- Linux Mint 22.x or Ubuntu 24.04, x86-64.
- Python 3.12, PyGObject, GTK 3, GStreamer 1.0, PipeWire, and
  Ayatana AppIndicator 3.
- `v4l2loopback` for virtual camera output.
- Optional: VA-API drivers for hardware H.264 encoding.

Windows:

- Windows 10 22H2 or Windows 11, x64.
- OBS Virtual Camera driver.
- A DirectShow/Media Foundation camera.

## Build from source

### Linux

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps mediapipe==0.10.35

# Run the test suite.
.venv/bin/python -m unittest discover -s tests -v
msgfmt --check --check-format -o /dev/null po/vi.po

# Build the Debian package and slim AppImage.
packaging/build_deb.sh
PYTHON=.venv/bin/python packaging/appimage/build_appimage.sh
```

Both packages are written to `dist/`. The AppImage builder needs `appimagetool`;
it downloads it automatically if it is not already on `PATH`. Install `zsync`
to generate the `.zsync` update file.

### Windows

```powershell
python -m pip install -r packaging/windows/requirements.txt
python -m pip install -r packaging/windows/build-requirements.txt
python -m pip install --no-deps mediapipe==0.10.35
python packaging/compile_translations.py
python -m unittest discover -s tests -v
python -m center_stage.gui_qt --selftest
.\packaging\windows\build.ps1 -SkipDeps
```

The Windows build installs PyInstaller, MediaPipe, and FFmpeg into the frozen
app. Inno Setup 6 is required for `CenterStage-Setup-<version>.exe`; the
portable ZIP is produced without it. See
[packaging/windows](packaging/windows) for the installer and PyInstaller spec.

### Test matrix

```bash
# Default suite
.venv/bin/python -m unittest discover -s tests -v

# GStreamer integration and physical-camera sharing (opt-in)
CENTER_STAGE_INTEGRATION_TESTS=1 .venv/bin/python -m unittest tests.test_integration_gstreamer -v
CENTER_STAGE_HARDWARE_TESTS=1 .venv/bin/python -m unittest tests.test_integration_gstreamer -v

# Browser compatibility harness (opt-in)
CENTER_STAGE_BROWSER_TESTS=1 .venv/bin/python -m unittest tests.test_browser_compat -v
```

The Windows CI job runs the unit tests, an offscreen Qt selftest, PyInstaller,
and the Inno Setup installer. Hardware and OBS Virtual Camera validation is
tracked separately because hosted runners do not have a camera.

## Architecture

```text
Physical camera
      │
      ├── Linux: PipeWire capture ──┐
      └── Windows: Media Foundation ┤
                                    ▼
                       face tracking + auto-framing
                       portrait blur + studio light
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
    Linux: v4l2loopback /dev/video10          Windows: OBS Virtual Camera
```

| Area | Primary files |
| --- | --- |
| Shared framing/effects | `center_stage/engine.py`, `framing.py`, `effects.py` |
| Linux capture/output | `center_stage/engine.py`, `virtual_output.py`, `pipewire.py` |
| Windows capture/output | `center_stage/engine_win.py`, `media_win.py` |
| Linux GTK app | `center_stage/gui.py` |
| Windows PySide6 app | `center_stage/gui_qt.py` |
| Configuration and paths | `center_stage/config.py`, `platform_paths.py` |
| Updates | `center_stage/updater.py` |
| Packaging | `packaging/`, `.github/workflows/` |

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before
opening a pull request. In short:

- Open an issue for a bug or a focused feature proposal.
- Branch from `main`; keep one logical change per pull request.
- Add a regression test for behavior changes.
- Run the default suite and `msgfmt` validation.
- Do not commit `dist/`, recordings, virtual environments, credentials, or
  personal configuration.
- Pull requests are squash-merged after review and green CI. The maintainer
  makes the final release and version decision.

By participating, you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report
security issues privately according to [SECURITY.md](SECURITY.md), not in a
public issue.

## Troubleshooting

### The camera is busy

Linux captures the physical camera through PipeWire, so PipeWire-aware apps can
share it with Center Stage. Legacy apps that open `/dev/video*` directly may
need to release the camera first.

### Center Stage does not appear in a video app

Start Center Stage first and wait for the engine to report ready. On Linux,
confirm `/dev/video10` exists with `v4l2-ctl --list-devices`. On Windows, confirm
the OBS Virtual Camera driver is installed and select **OBS Virtual Camera** in
the app.

### `/dev/video10` is missing (Linux)

```bash
modinfo v4l2loopback
v4l2-ctl --list-devices
```

Secure Boot may prevent an unsigned DKMS module from loading. If the module was
loaded with different options, reboot once after installing the package.

### The Windows app shows a missing DLL

Install the Microsoft Visual C++ 2015–2022 Redistributable and restart the app.
If the problem persists, open an issue with the Windows version, camera model,
OBS Studio version, and the log at `%LOCALAPPDATA%\center-stage\gui.log`.

### Logs and state

Linux:

- Config: `~/.config/center-stage/config.json`
- Runtime status: `$XDG_RUNTIME_DIR/center-stage/status.json`
- GUI log: `~/.local/state/center-stage/gui.log`

Windows:

- Config: `%APPDATA%\center-stage\config.json`
- Runtime status: `%LOCALAPPDATA%\center-stage\run\status.json`
- GUI log: `%LOCALAPPDATA%\center-stage\gui.log`

## License

Center Stage is released under the [MIT License](LICENSE). The Windows binary
also bundles GPL-licensed pyvirtualcam and a GPL FFmpeg build; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component licenses and
redistribution notes.
