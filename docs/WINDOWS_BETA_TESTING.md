# Windows Beta testing guide

Center Stage 5.0.0 includes a native Windows app that uses Media Foundation for
capture, MediaPipe for tracking and segmentation, and OBS Virtual Camera for
output. The code and packaging are covered by Windows CI, but the virtual camera
and capture paths still need validation on real Windows hardware before the
build leaves Beta.

## What to test

Use a Windows 10 22H2 or Windows 11 x64 machine with at least one webcam and,
if possible, a microphone.

### 1. Install

- Download `CenterStage-Setup.exe` and `SHA256SUMS-windows.txt` from the latest
  GitHub Release.
- Verify the installer hash with `Get-FileHash`.
- Install OBS Studio once so the OBS Virtual Camera driver is present. OBS does
  not need to be running.
- Run the installer as the current user. No administrator access should be
  required.

### 2. Capture and preview

- Launch Center Stage from the Start menu or desktop shortcut.
- Confirm the correct camera is selected automatically.
- Confirm the preview is live, color-correct, and not mirrored or upside down.
- Move left, right, closer, and farther away. Confirm tracking keeps the face
  framed without obvious jumps.
- Toggle Center Stage off and on and confirm the output returns to the full
  frame.
- Switch between light and dark themes.
- Try English and Vietnamese from the language menu.

### 3. Photo and four-up capture

- Take a photo. Confirm a JPEG appears in the album and in the configured
  Pictures folder.
- Take a four-up collage. Confirm four distinct frames form a 2×2 image and
  only one file is created.
- Test the album thumbnail, open, and delete actions.
- Collapse and expand the album and restart the app to confirm the setting is
  remembered.

### 4. Video and audio

- Record a 10–20 second video with the microphone enabled. Confirm the MP4 plays
  in Windows Media Player or VLC with audio and video in sync.
- Mute the microphone and record again. Confirm the file is video-only.
- Stop a recording while the camera is moving and confirm the file finalizes.
- Start and stop three recordings in a row.
- If a microphone is unplugged during recording, note whether the app continues
  without audio or reports a clear error.

### 5. OBS Virtual Camera

- Open Zoom, Teams, Google Meet, Discord, or OBS and select **OBS Virtual
  Camera**.
- Confirm the Center Stage output appears at 1920×1080 and 30fps.
- Move around and toggle portrait blur and studio light; confirm changes are
  visible in the receiving app without restarting it.
- Close Center Stage to the tray and confirm the virtual camera keeps running.
- Choose **Quit Center Stage** and confirm the virtual camera stops and the
  camera is released.
- Open a second app and confirm whether OBS Virtual Camera can be shared by both
  apps. Record the result.

### 6. Settings and lifecycle

- Change camera and microphone selections and confirm the engine restarts with
  the new device.
- Change the pictures and videos folders and capture to each.
- Enable **Start Center Stage when I sign in**, sign out/in, and confirm the
  app starts hidden to the tray.
- Close the window and restore it from the tray.
- Use **Check for updates** on both Stable and Beta channels.
- Confirm `%APPDATA%\center-stage\config.json` and
  `%LOCALAPPDATA%\center-stage\gui.log` are created.

### 7. Installer and uninstall

- Install to the default per-user location.
- Uninstall from Settings → Apps and confirm no running process remains.
- Reinstall and confirm settings are preserved unless the user removes the
  config directory.
- Test the portable ZIP on a clean folder without installing.

## Report

Open a bug report with:

- Windows version and build (`winver`).
- Center Stage version.
- Installer or portable ZIP.
- Camera and microphone models.
- OBS Studio version and whether OBS was running.
- Receiving app and version for virtual-camera tests.
- Exact steps, expected behavior, actual behavior, and whether it reproduces.
- Relevant section of `%LOCALAPPDATA%\center-stage\gui.log` with personal data
  removed.

If the app does not start, include any Windows Event Viewer entry and confirm
the SHA-256 hash of the downloaded installer.

## Known Beta limitations

- The build is not code-signed, so SmartScreen may warn.
- Hardware validation and OBS Virtual Camera compatibility are pending.
- Automatic background installation of Windows updates is not implemented; the
  in-app checker opens the verified release download.
- OBS Virtual Camera must be installed separately with OBS Studio.
- Only Windows 10 22H2+ and Windows 11 x64 are targeted.

Do not report security problems in a public testing issue; use
[SECURITY.md](../SECURITY.md).
