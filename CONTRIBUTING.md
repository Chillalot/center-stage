# Contributing to Center Stage

Thanks for helping improve Center Stage. The project is a small, local-first
desktop app: a camera engine, a thin GTK controller, a native Windows PySide6
controller, and the packaging that ships them.

By participating, you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Search existing issues and pull requests before opening a new one.
- For a bug, include the platform, version, installation method, camera model,
  reproduction steps, and the relevant log section.
- For a large feature or a change to the engine/output contract, open an issue
  first so the design can be agreed before implementation.
- For a security problem, follow [SECURITY.md](SECURITY.md) instead of opening a
  public issue.

## Development setup

### Linux

Use the system Python 3.12 stack because PyGObject and GStreamer are provided by
the distribution:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps mediapipe==0.10.35
```

Run the GTK app from the source tree:

```bash
PYTHONPATH=. python3 -m center_stage.gui
```

The packaged engine uses a systemd user service. For a source-tree engine, use
the CLI (and make sure v4l2loopback is loaded):

```bash
.venv/bin/python main.py --input /dev/v4l/by-id/<camera>-video-index0
```

### Windows

```powershell
python -m pip install -r packaging/windows/requirements.txt
python -m pip install -r packaging/windows/build-requirements.txt
python -m pip install --no-deps mediapipe==0.10.35
python packaging/compile_translations.py
python -m center_stage.gui_qt
```

The Windows build is **Beta** until hardware/OBS testing is complete. Changes
that affect Media Foundation capture, OBS Virtual Camera output, or FFmpeg
recording should be tested on real Windows hardware and called out in the pull
request.

## Tests and checks

Run the default suite before every pull request:

```bash
.venv/bin/python -m unittest discover -s tests -v
msgfmt --check --check-format -o /dev/null po/vi.po
```

On Windows CI the same suite runs with `QT_QPA_PLATFORM=offscreen`; the Qt
selftest is:

```powershell
python -m center_stage.gui_qt --selftest
```

Opt-in suites require a camera or a graphical session:

```bash
CENTER_STAGE_INTEGRATION_TESTS=1 .venv/bin/python -m unittest tests.test_integration_gstreamer -v
CENTER_STAGE_HARDWARE_TESTS=1 .venv/bin/python -m unittest tests.test_integration_gstreamer -v
CENTER_STAGE_BROWSER_TESTS=1 .venv/bin/python -m unittest tests.test_browser_compat -v
```

Never mark an opt-in hardware check as passed unless it actually ran on the
required hardware. Report skipped checks in the pull request.

### Packaging checks

```bash
packaging/build_deb.sh
PYTHON=.venv/bin/python packaging/appimage/build_appimage.sh
```

On Windows:

```powershell
.\packaging\windows\build.ps1 -SkipDeps
```

The AppImage builder downloads `appimagetool` when it is not installed and
creates a `.zsync` file when `zsyncmake` is available. Do not commit generated
directories (`build/`, `dist/`) or installers.

## Code style

- Keep changes consistent with the surrounding code.
- Use type hints on public functions and dataclasses where practical.
- Prefer small, composable functions over new global state.
- Do not import GTK, GStreamer, or other Linux-only modules at import time in
  shared modules. Import them inside functions or guard them so Windows remains
  import-safe.
- Keep the engine output contract at 1080p/30fps BGR processing and YUY2 for the
  Linux virtual camera.
- Use the existing translator wrapper for user-visible strings. Add new strings
  to both `po/center-stage.pot` and `po/vi.po`.
- Do not add telemetry, cloud calls, or bundled credentials.

## Pull request rules

1. Branch from `main` with a focused name such as `fix/config-validation` or
   `feat/media-export`.
2. Keep one logical change per pull request. Split unrelated refactors out.
3. Add or update a regression test for behavior changes. A bug fix should fail
   before the fix and pass after it.
4. Update documentation when user-visible behavior, installation, or the update
   contract changes.
5. Run the default tests and gettext validation.
6. Describe the user impact, failure mode, validation performed, skipped checks,
   and any migration or packaging implications.
7. Do not force-push a branch while a reviewer is actively reading it; use
   normal fix-up commits, then squash at merge time.
8. Pull requests are squash-merged after review and green CI. The maintainer
   makes the final release and version decision.

### Commit messages

Use a short imperative subject, optionally with a `feat:`, `fix:`, `docs:`,
`test:`, `ci:`, or `chore:` prefix. Keep the subject under about 72 characters
and put context in the body.

## Translations

The app ships English and Vietnamese. When adding a string:

```bash
# Extract/update the template if xgettext is available, or add the entry
# manually with the correct source reference.
msgfmt --check --check-format -o /dev/null po/vi.po
```

Keep placeholders such as `{version}` unchanged and preserve accelerator
characters. If you are not confident in a translation, add the English string
and ask a Vietnamese speaker to review it in the pull request.

## Architecture ownership

| Area | Primary files | Review focus |
| --- | --- | --- |
| Shared engine | `center_stage/engine.py`, `framing.py`, `effects.py` | Capture/processing/output contract, frame timing |
| Linux engine | `virtual_output.py`, `pipewire.py` | v4l2loopback caps, PipeWire sharing, cleanup |
| Windows engine | `engine_win.py`, `media_win.py` | Media Foundation, OBS output, FFmpeg, audio |
| GTK app | `center_stage/gui.py` | Tray actions, service lifecycle, accessibility |
| Qt app | `center_stage/gui_qt.py` | Threading, tray behavior, Windows UX |
| Configuration | `config.py`, `platform_paths.py` | Backward compatibility, atomic writes, platform paths |
| Packaging | `packaging/`, `tests/test_package.py` | Deb/AppImage/Windows artifacts, versions, checksums |
| Updates | `center_stage/updater.py` | Asset selection, checksum verification, channels |

For parallel work, agree on file ownership before editing. Avoid editing
unrelated generated files such as `po/center-stage.pot` by hand unless the
change is part of the translation update.

## Release process

The maintainer follows this sequence:

1. Confirm `VERSION` and runtime version agree.
2. Run the default tests, `msgfmt`, and any available hardware/opt-in checks.
3. Build the `.deb`, AppImage, Windows installer, and portable ZIP.
4. Verify the assets, checksums, and stable download names.
5. Merge all release pull requests with squash merges.
6. Tag `v<version>` and let the release workflow publish the GitHub Release.
7. Mark the Windows build Beta until hardware/OBS validation is recorded.

Contributors should not publish packages, push tags, or alter `main` directly.

## Questions

Open a discussion or issue with the `question` label. For conduct or security
matters, contact the maintainer privately through GitHub.
