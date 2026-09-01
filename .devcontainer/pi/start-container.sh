#!/usr/bin/env bash
# Start the mybot ROS 2 container on the Pi 5 (Raspberry Pi OS Trixie).
#
# Devices are added through add(), which WARNS on a missing device instead of
# failing. devcontainer.json lists the same set statically and Docker refuses
# to start when any entry is absent -- prefer this script when something is
# unplugged.
#
# The camera is a Logitech C920 (USB/UVC), which needs exactly one node. The
# CSI machinery this script used to carry -- dma_heap buffers, the rp1-cfe and
# pispbe media/video nodes, the v4l-subdevs, the /run/udev mount -- was all
# for libcamera driving the IMX500. That camera is gone and so is all of it.
set -euo pipefail

NAME=${NAME:-mybot-pi}
IMAGE=${IMAGE:-ghcr.io/mjwoolley/ros2-humble-dev:0.2.0}
REPO=${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}

DEV=()
add() { [ -e "$1" ] && DEV+=(--device="$1") || echo "WARN: missing $1" >&2; }

# --- serial -------------------------------------------------------------
for d in /dev/arduino /dev/motor /dev/rplidar /dev/ttyUSB0 /dev/ttyUSB1; do add "$d"; done

# --- camera: the C920 capture node --------------------------------------
# /dev/webcam is a udev symlink (see udev/99-webcam-c920.rules). The C920
# claims two video nodes and only index 0 captures; index 1 is metadata-only
# and pointing a camera node at it fails. Fall back to the bare node if the
# rule has not been installed yet.
if [ -e /dev/webcam ]; then
  add /dev/webcam
else
  echo "WARN: /dev/webcam missing -- install udev/99-webcam-c920.rules" >&2
  add /dev/video0
fi

# --- Hailo-10H NPU ------------------------------------------------------
# The kernel driver stays on the host (hailo1x_pci, DKMS, apt-managed from
# archive.raspberrypi.com). Only the character device crosses into the
# container. HailoRT userspace in the image must match the host driver
# version exactly.
add /dev/hailo0

echo "Passing ${#DEV[@]} devices."

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  --network=host --ipc=host \
  --init \
  "${DEV[@]}" \
  --group-add=dialout --group-add=video \
  -v "$REPO":/workspace \
  -v "$HOME/.local/share/claude":/opt/claude:ro \
  -v "$HOME/.claude":/home/ros/.claude \
  -v "$HOME/.local/share/mybot/claude-shim":/usr/local/bin/claude:ro \
  -w /workspace -u ros \
  "$IMAGE" sleep infinity
