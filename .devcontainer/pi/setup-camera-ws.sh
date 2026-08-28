#!/usr/bin/env bash
# Build libcamera (Raspberry Pi fork) + camera_ros for the Pi 5 camera.
#
# Run INSIDE the Pi devcontainer, after a rebuild. Everything apt installs
# here lives in the container's writable overlay and is lost on the next
# rebuild -- only /workspace/camera_ws survives. Once this is proven, move
# the apt block into the ros2-humble-dev image (see README-camera.md).
#
#   bash .devcontainer/pi/setup-camera-ws.sh
#
# Does not need the camera to be attached; that is only required to run
# camera_node afterwards.

set -euo pipefail

WS=/workspace/camera_ws

if [[ -z "${ROS_DISTRO:-}" ]]; then
    echo "ROS_DISTRO unset -- source /opt/ros/humble/setup.bash first" >&2
    exit 1
fi

echo "==> apt dependencies"
# meson is deliberately NOT in this list: Jammy ships 0.61.2 and libcamera
# needs >= 0.63. It comes from pip below.
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    libboost-dev libgnutls28-dev openssl libtiff-dev pybind11-dev \
    qtbase5-dev libqt5core5a cmake python3-yaml python3-ply python3-jinja2 \
    libglib2.0-dev libgstreamer-plugins-base1.0-dev \
    python3-colcon-meson ninja-build python3-pip git

echo "==> meson from pip (Jammy's apt version is too old)"
pip3 install --user --upgrade "meson>=1.0"
export PATH="$HOME/.local/bin:$PATH"
meson --version

echo "==> sources in $WS/src"
mkdir -p "$WS/src"
cd "$WS/src"
[[ -d libcamera  ]] || git clone https://github.com/raspberrypi/libcamera.git
[[ -d camera_ros ]] || git clone https://github.com/christianrauch/camera_ros.git

echo "==> rosdep"
cd "$WS"
# --skip-keys=libcamera is essential: without it rosdep installs Ubuntu's
# libcamera (a June 2020 snapshot, no Pi 5 / PiSP support) and camera_ros
# links against the wrong one.
rosdep install -y --from-paths src --ignore-src \
    --rosdistro "$ROS_DISTRO" --skip-keys=libcamera

echo "==> build (20-40 min on a Pi 5)"
colcon build --symlink-install

cat <<'DONE'

Built. To use:

    source /workspace/camera_ws/install/setup.bash
    ros2 run camera_ros camera_node

If camera_node reports no cameras, the host is not detecting the sensor --
that is a host-side problem, see README-camera.md step 1.
DONE
