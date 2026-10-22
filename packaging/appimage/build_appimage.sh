#!/bin/sh
set -eu

# Center Stage AppImage builder.
#
# The AppImage intentionally does not bundle Python, GTK, GStreamer, or
# PipeWire. It runs on the system Python 3.12 stack from Linux Mint 22.x /
# Ubuntu 24.04 and bundles only the Python packages that are not part of that
# stack (OpenCV, NumPy, MediaPipe, and their dependencies).

unset CDPATH
ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
VERSION=$(tr -d '[:space:]' <"$ROOT/VERSION")
ARCH=${ARCH:-x86_64}
BUILD_PYTHON=${PYTHON:-python3}
BUILD_DIR=${BUILD_DIR:-"$ROOT/build/appimage"}
OUTPUT_DIR=${OUTPUT_DIR:-"$ROOT/dist"}
SKIP_DEPS=0
SKIP_ZSYNC=0

for argument in "$@"; do
    case "$argument" in
        --skip-deps) SKIP_DEPS=1 ;;
        --no-zsync) SKIP_ZSYNC=1 ;;
        --output-dir=*) OUTPUT_DIR=${argument#*=} ;;
        --build-dir=*) BUILD_DIR=${argument#*=} ;;
        -h|--help)
            cat <<'EOF'
Usage: packaging/appimage/build_appimage.sh [options]
  --skip-deps           reuse an existing vendor directory
  --no-zsync            do not create a .zsync file
  --output-dir=DIR      write the AppImage to DIR
  --build-dir=DIR       use DIR for the AppDir and downloaded tools
EOF
            exit 0
            ;;
        *)
            echo "Unknown argument: $argument" >&2
            exit 2
            ;;
    esac
done

APP_ROOT="$BUILD_DIR/AppDir/usr/lib/center-stage"
VENDOR="$APP_ROOT/vendor"
APP_DIR="$APP_ROOT/app"
APPDIR="$BUILD_DIR/AppDir"
mkdir -p "$APP_DIR" "$VENDOR" "$APPDIR/usr/bin" \
    "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps" \
    "$APPDIR/usr/share/metainfo" \
    "$APPDIR/usr/share/doc/center-stage" \
    "$OUTPUT_DIR"

if [ "$SKIP_DEPS" -eq 0 ]; then
    echo "Installing vendored Python runtime into $VENDOR"
    "$BUILD_PYTHON" -m pip install --upgrade --no-compile --target "$VENDOR" \
        -r "$ROOT/packaging/appimage/requirements.txt"
    "$BUILD_PYTHON" -m pip install --upgrade --no-compile --no-deps --target "$VENDOR" \
        "mediapipe==0.10.35"
fi

rm -rf "$APP_DIR/center_stage"
cp -a "$ROOT/center_stage" "$APP_DIR/center_stage"
cp "$ROOT/VERSION" "$APP_DIR/VERSION"
cp "$ROOT/blaze_face_short_range.tflite" "$APP_ROOT/"
cp "$ROOT/selfie_segmenter_landscape.tflite" "$APP_ROOT/"
mkdir -p "$APP_ROOT/locale/vi/LC_MESSAGES"
msgfmt --check --check-format -o "$APP_ROOT/locale/vi/LC_MESSAGES/center-stage.mo" "$ROOT/po/vi.po"

install -m 0755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -m 0644 "$ROOT/packaging/appimage/center-stage.desktop" \
    "$APPDIR/center-stage.desktop"
install -m 0644 "$ROOT/packaging/appimage/center-stage.desktop" \
    "$APPDIR/usr/share/applications/center-stage.desktop"
install -m 0644 "$ROOT/packaging/appimage/center-stage.appdata.xml" \
    "$APPDIR/usr/share/metainfo/center-stage.appdata.xml"
install -m 0644 "$ROOT/packaging/center-stage.svg" \
    "$APPDIR/center-stage.svg"
install -m 0644 "$ROOT/packaging/center-stage.svg" \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps/center-stage.svg"
install -m 0644 "$ROOT/LICENSE" "$APPDIR/usr/share/doc/center-stage/LICENSE"
install -m 0644 "$ROOT/THIRD_PARTY_NOTICES.md" \
    "$APPDIR/usr/share/doc/center-stage/THIRD_PARTY_NOTICES.md"
cat >"$APPDIR/usr/bin/center-stage" <<'EOF'
#!/bin/sh
APPDIR=$(cd -- "$(dirname -- "$0")/../.." && pwd)
exec "$APPDIR/AppRun" "$@"
EOF
chmod 0755 "$APPDIR/usr/bin/center-stage"

# Keep the image slim without removing runtime libraries or licenses.
find "$VENDOR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$APP_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "${VENDOR:?}/bin"
rm -rf "$VENDOR/matplotlib/mpl-data/sample_data" \
    "$VENDOR/matplotlib/mpl-data/images" \
    "$VENDOR/matplotlib/tests" \
    "$VENDOR/matplotlib/sphinxext" \
    "$VENDOR/numpy/tests" \
    "$VENDOR/numpy/f2py/tests" \
    "$VENDOR/opencv" 2>/dev/null || true
find "$VENDOR" -type f \( -name '*.pyc' -o -name '*.pyi' -o -name '*.h' -o -name '*.hpp' \) -delete
find "$VENDOR" -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true

echo "AppDir: $APPDIR"
du -sh "$APPDIR" "$VENDOR"

if command -v appimagetool >/dev/null 2>&1; then
    APPIMAGETOOL=$(command -v appimagetool)
else
    APPIMAGETOOL="$BUILD_DIR/appimagetool-$ARCH.AppImage"
    if [ ! -x "$APPIMAGETOOL" ]; then
        TOOL_URL=${APPIMAGETOOL_URL:-"https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"}
        echo "Downloading appimagetool from $TOOL_URL"
        curl -fL --retry 3 -o "$APPIMAGETOOL" "$TOOL_URL"
        chmod +x "$APPIMAGETOOL"
    fi
fi

OUTPUT="$OUTPUT_DIR/CenterStage-$VERSION-$ARCH.AppImage"
rm -f "$OUTPUT"
ARCH="$ARCH" "$APPIMAGETOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUTPUT"
chmod +x "$OUTPUT"
echo "AppImage: $OUTPUT"
sha256sum "$OUTPUT" >"$OUTPUT.sha256"

if [ "$SKIP_ZSYNC" -eq 0 ] && command -v zsyncmake >/dev/null 2>&1; then
    (cd "$OUTPUT_DIR" && zsyncmake -u \
        "https://github.com/Chillalot/center-stage/releases/latest/download/CenterStage-$ARCH.AppImage" \
        -o "$(basename "$OUTPUT").zsync" "$(basename "$OUTPUT")")
    echo "zsync: $OUTPUT.zsync"
fi
