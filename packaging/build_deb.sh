#!/bin/bash
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PACKAGE_VERSION=4.5.1
PACKAGE_ARCH=amd64
PACKAGE_NAME=center-stage
OUTPUT_DIR="$PROJECT_DIR/dist"
BUILD_ROOT=$(mktemp -d)
PACKAGE_ROOT="$BUILD_ROOT/${PACKAGE_NAME}_${PACKAGE_VERSION}_${PACKAGE_ARCH}"
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    BUILD_PYTHON="$PROJECT_DIR/.venv/bin/python"
else
    BUILD_PYTHON=python3
fi

cleanup() {
    rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT

mkdir -p \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/opt/center-stage/app" \
    "$PACKAGE_ROOT/opt/center-stage/vendor" \
    "$PACKAGE_ROOT/opt/center-stage/share" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/lib/systemd/user" \
    "$PACKAGE_ROOT/usr/share/applications" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$PACKAGE_ROOT/usr/share/locale/vi/LC_MESSAGES" \
    "$PACKAGE_ROOT/etc/modules-load.d" \
    "$PACKAGE_ROOT/etc/modprobe.d"

cp -a "$PROJECT_DIR/center_stage" "$PACKAGE_ROOT/opt/center-stage/app/"
find "$PACKAGE_ROOT/opt/center-stage/app" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$PACKAGE_ROOT/opt/center-stage/app" -type f -name '*.pyc' -delete
find "$PACKAGE_ROOT/opt/center-stage/app" -type d -exec chmod 0755 {} +
find "$PACKAGE_ROOT/opt/center-stage/app" -type f -exec chmod 0644 {} +
cp "$PROJECT_DIR/blaze_face_short_range.tflite" "$PACKAGE_ROOT/opt/center-stage/share/"
cp "$PROJECT_DIR/selfie_segmenter_landscape.tflite" "$PACKAGE_ROOT/opt/center-stage/share/"

"$BUILD_PYTHON" -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --target "$PACKAGE_ROOT/opt/center-stage/vendor" \
    -r "$PROJECT_DIR/packaging/runtime-requirements.txt"

"$BUILD_PYTHON" -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --no-deps \
    --target "$PACKAGE_ROOT/opt/center-stage/vendor" \
    matplotlib==3.11.1 mediapipe==0.10.35

OPENCV_COUNT=$(find "$PACKAGE_ROOT/opt/center-stage/vendor" -maxdepth 1 -type d -name 'opencv_*python*.dist-info' | wc -l)
if [ "$OPENCV_COUNT" -ne 1 ] || ! find "$PACKAGE_ROOT/opt/center-stage/vendor" -maxdepth 1 -type d -name 'opencv_python_headless-*.dist-info' | grep -q .; then
    echo "Center Stage requires exactly one opencv-python-headless distribution." >&2
    exit 1
fi
if [ -e "$PACKAGE_ROOT/opt/center-stage/vendor/PIL" ]; then
    echo "Bundled Pillow is forbidden; the package uses Linux Mint's python3-pil." >&2
    exit 1
fi

find "$PACKAGE_ROOT/opt/center-stage/vendor" -type d \( -name __pycache__ -o -name tests -o -name test \) -prune -exec rm -rf {} +
find "$PACKAGE_ROOT/opt/center-stage/vendor" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
rm -rf "$PACKAGE_ROOT/opt/center-stage/vendor/bin"
rm -rf \
    "$PACKAGE_ROOT/opt/center-stage/vendor/matplotlib/mpl-data/sample_data" \
    "$PACKAGE_ROOT/opt/center-stage/vendor/matplotlib/mpl-data/images" \
    "$PACKAGE_ROOT/opt/center-stage/vendor/matplotlib/mpl-data/plot_directive" \
    "$PACKAGE_ROOT/opt/center-stage/vendor/matplotlib/sphinxext" \
    "$PACKAGE_ROOT/opt/center-stage/vendor/matplotlib/testing"

PYTHONPATH="$PACKAGE_ROOT/opt/center-stage/app:$PACKAGE_ROOT/opt/center-stage/vendor:/usr/lib/python3/dist-packages" \
MODEL_PATH="$PACKAGE_ROOT/opt/center-stage/share/blaze_face_short_range.tflite" \
SEGMENTATION_MODEL_PATH="$PACKAGE_ROOT/opt/center-stage/share/selfie_segmenter_landscape.tflite" \
/usr/bin/python3 - <<'PY'
import os
import cv2
import numpy as np
from center_stage.engine import OUTPUT_FORMAT, create_face_detector, create_person_segmenter, detect_faces, raw_bgr_payload

frame = np.zeros((720, 1280, 3), dtype=np.uint8)
resized = cv2.resize(frame, (1920, 1080))
mirrored = cv2.flip(resized, 1)
payload = raw_bgr_payload(mirrored)
detector = create_face_detector(os.environ["MODEL_PATH"])
segmenter = create_person_segmenter(os.environ["SEGMENTATION_MODEL_PATH"])
try:
    detect_faces(frame, detector)
finally:
    detector.close()
    segmenter.close()
assert resized.shape == (1080, 1920, 3)
assert OUTPUT_FORMAT == "YUY2"
assert len(payload) == 1080 * 1920 * 3
PY

msgfmt --check --output-file \
    "$PACKAGE_ROOT/usr/share/locale/vi/LC_MESSAGES/center-stage.mo" \
    "$PROJECT_DIR/po/vi.po"

install -m 0755 "$PROJECT_DIR/packaging/center-stage-launcher" "$PACKAGE_ROOT/usr/bin/center-stage"
install -m 0644 "$PROJECT_DIR/packaging/center-stage-engine.service" "$PACKAGE_ROOT/usr/lib/systemd/user/"
install -m 0644 "$PROJECT_DIR/packaging/center-stage.desktop" "$PACKAGE_ROOT/usr/share/applications/"
install -m 0644 "$PROJECT_DIR/packaging/center-stage.svg" "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$PROJECT_DIR/packaging/center-stage-idle.svg" "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$PROJECT_DIR/packaging/center-stage-active.svg" "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$PROJECT_DIR/packaging/center-stage-warning.svg" "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$PROJECT_DIR/packaging/modules-load.conf" "$PACKAGE_ROOT/etc/modules-load.d/center-stage.conf"
install -m 0644 "$PROJECT_DIR/packaging/modprobe.conf" "$PACKAGE_ROOT/etc/modprobe.d/center-stage.conf"
install -m 0755 "$PROJECT_DIR/packaging/postinst" "$PACKAGE_ROOT/DEBIAN/postinst"
install -m 0755 "$PROJECT_DIR/packaging/preinst" "$PACKAGE_ROOT/DEBIAN/preinst"
install -m 0755 "$PROJECT_DIR/packaging/prerm" "$PACKAGE_ROOT/DEBIAN/prerm"
install -m 0755 "$PROJECT_DIR/packaging/postrm" "$PACKAGE_ROOT/DEBIAN/postrm"

INSTALLED_SIZE=$(du -sk "$PACKAGE_ROOT" | cut -f1)
cat >"$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $PACKAGE_VERSION
Section: video
Priority: optional
Architecture: $PACKAGE_ARCH
Maintainer: Center Stage Local <local@center-stage.invalid>
Depends: python3 (>= 3.12), python3-pil, python3-gi, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-gtk3, gstreamer1.0-pipewire, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad, gstreamer1.0-plugins-ugly, pulseaudio-utils, v4l-utils, pipewire, wireplumber
Suggests: v4l2loopback-dkms, v4l2loopback-utils
Installed-Size: $INSTALLED_SIZE
Description: Bilingual Photo Booth and auto-framing virtual camera
 Center Stage turns a physical webcam into a face-tracking virtual camera.
 The GTK3 controller supports English and Vietnamese.
EOF

cat >"$PACKAGE_ROOT/DEBIAN/conffiles" <<EOF
/etc/modules-load.d/center-stage.conf
/etc/modprobe.d/center-stage.conf
EOF

find "$PACKAGE_ROOT" -type d -exec chmod 0755 {} +
mkdir -p "$OUTPUT_DIR"
PACKAGE_FILE="$OUTPUT_DIR/${PACKAGE_NAME}_${PACKAGE_VERSION}_${PACKAGE_ARCH}.deb"
MAX_INSTALLED_SIZE_KB=330000
MAX_PACKAGE_SIZE_BYTES=$((75 * 1024 * 1024))
if [ "$INSTALLED_SIZE" -gt "$MAX_INSTALLED_SIZE_KB" ]; then
    echo "Installed size ${INSTALLED_SIZE} KiB exceeds ${MAX_INSTALLED_SIZE_KB} KiB." >&2
    exit 1
fi

for maintainer_script in preinst postinst prerm postrm; do
    sh -n "$PACKAGE_ROOT/DEBIAN/$maintainer_script"
done
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$PROJECT_DIR"/packaging/*.sh "$PROJECT_DIR"/packaging/preinst "$PROJECT_DIR"/packaging/postinst "$PROJECT_DIR"/packaging/prerm "$PROJECT_DIR"/packaging/postrm
fi

dpkg-deb --root-owner-group -Zxz -z9 --threads-max=2 --build "$PACKAGE_ROOT" "$PACKAGE_FILE"
PACKAGE_SIZE=$(stat -c %s "$PACKAGE_FILE")
if [ "$PACKAGE_SIZE" -gt "$MAX_PACKAGE_SIZE_BYTES" ]; then
    echo "Package size ${PACKAGE_SIZE} bytes exceeds the 75 MiB release budget." >&2
    exit 1
fi
dpkg-deb --info "$PACKAGE_FILE" >/dev/null
if command -v lintian >/dev/null 2>&1; then
    lintian "$PACKAGE_FILE"
fi
echo "$PACKAGE_FILE"
