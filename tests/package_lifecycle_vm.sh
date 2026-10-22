#!/bin/sh
set -eu

package=${1:-"dist/center-stage_$(tr -d '[:space:]' <VERSION)_amd64.deb"}
old_package=${2:-}

if [ ! -f "$package" ]; then
    echo "Package not found: $package" >&2
    exit 1
fi

sudo apt-get install -y v4l2loopback-dkms v4l2loopback-utils
if [ -n "$old_package" ]; then
    sudo apt-get install -y "$old_package"
fi
sudo apt-get install -y "$package"

test "$(dpkg-query -W -f='${Version}' center-stage)" = "$(tr -d '[:space:]' <VERSION)"
systemctl --user daemon-reload
systemctl --user start center-stage-engine.service
sleep 5
test "$(systemctl --user is-active center-stage-engine.service)" = "active"
systemctl --user stop center-stage-engine.service
test "$(systemctl --user is-active center-stage-engine.service)" = "inactive"

sudo apt-get purge -y center-stage
if systemctl --user is-active --quiet center-stage-engine.service; then
    echo "Center Stage service remained active after purge." >&2
    exit 1
fi
