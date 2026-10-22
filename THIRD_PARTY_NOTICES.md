# Third-party notices

Center Stage is distributed under the MIT License. The application bundles or
depends on the following third-party components. Their licenses apply to those
components, not to the Center Stage source code.

## Runtime dependencies

| Component | License | Project |
| --- | --- | --- |
| Python | PSF-2.0 | https://www.python.org/ |
| MediaPipe | Apache-2.0 | https://github.com/google-ai-edge/mediapipe |
| OpenCV | Apache-2.0 | https://opencv.org/ |
| NumPy | BSD-3-Clause | https://numpy.org/ |
| GTK 3 / PyGObject | LGPL-2.1-or-later | https://www.gtk.org/ |
| GStreamer | LGPL-2.1-or-later | https://gstreamer.freedesktop.org/ |
| PipeWire | MIT | https://pipewire.org/ |
| v4l2loopback | GPL-2.0 | https://github.com/v4l2loopback/v4l2loopback |
| Ayatana AppIndicator | LGPL-3.0 | https://github.com/AyatanaIndicators/libayatana-appindicator |
| Qt / PySide6 | LGPL-3.0 (or commercial) | https://www.qt.io/ |
| sounddevice | MIT | https://python-sounddevice.readthedocs.io/ |
| pyvirtualcam | GPL-2.0-or-later | https://github.com/letmaik/pyvirtualcam |
| FFmpeg (via imageio-ffmpeg) | GPL-2.0-or-later (bundled Windows build) | https://ffmpeg.org/ |
| Pillow | HPND | https://python-pillow.org/ |
| Matplotlib | PSF-based | https://matplotlib.org/ |
| imageio-ffmpeg | BSD-2-Clause | https://github.com/imageio/imageio-ffmpeg |
| cv2-enumerate-cameras | MIT | https://github.com/letmaik/cv2-enumerate-cameras |
| polib (build only) | MIT | https://github.com/izimobil/polib |

## Distribution notes

- Qt/PySide6 is bundled dynamically. Users may replace the bundled Qt
  libraries with a compatible build; the application does not statically link
  Qt.
- FFmpeg is invoked as a separate executable. The Windows `imageio-ffmpeg`
  binary is a GPL build that enables libx264 so H.264 recording works out of the
  box. Its source and build configuration are available from the FFmpeg and
  imageio-ffmpeg projects.
- The Windows binary distribution includes pyvirtualcam and the GPL FFmpeg
  build. The Center Stage source remains MIT-licensed; redistributors of the
  Windows binary must comply with the GPL terms for those bundled components,
  including source availability. The Linux packages do not bundle pyvirtualcam.
- GStreamer, GTK, PipeWire, and v4l2loopback are system packages on Linux and
  are not relicensed by this project.
- MediaPipe and OpenCV wheels include their own license files in the packaged
  vendor directory.

If you redistribute Center Stage, keep this file and the license files that
accompany the bundled wheels and executables.
