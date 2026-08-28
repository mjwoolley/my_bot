#!/usr/bin/env bash
# Start the mybot ROS 2 container on the Pi 5 (Raspberry Pi OS Trixie).
#
# Device list is GENERATED, not hardcoded: the runbook placeholder
# (--device=/dev/video0 --device=/dev/media0) is wrong on a Pi 5 -- media0 is
# the ISP, not the camera, and libcamera needs the whole rp1-cfe + pispbe set
# or it enumerates and then fails on first capture.
set -euo pipefail

NAME=${NAME:-mybot-pi}
IMAGE=${IMAGE:-ghcr.io/mjwoolley/ros2-humble-dev:0.2.0}
REPO=${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}

DEV=()
add() { [ -e "$1" ] && DEV+=(--device="$1") || echo "WARN: missing $1" >&2; }

# --- serial -------------------------------------------------------------
for d in /dev/arduino /dev/motor /dev/rplidar /dev/ttyUSB0 /dev/ttyUSB1; do add "$d"; done

# --- dma_heap (libcamera frame buffers; camera fails on capture without) --
add "/dev/dma_heap/linux,cma"
add "/dev/dma_heap/system"

# --- camera: media devices for pispbe (ISP) + rp1-cfe (CSI), skip hevc ---
for m in /dev/media*; do
  case "$(media-ctl -d "$m" -p 2>/dev/null | awk "/model/{print \$2; exit}")" in
    pispbe|rp1-cfe) add "$m" ;;
  esac
done

# --- camera: v4l2 nodes belonging to rp1-cfe / pispbe --------------------
for v in /sys/class/video4linux/video*; do
  case "$(cat "$v/name" 2>/dev/null)" in
    rp1-cfe*|pispbe*) add "/dev/$(basename "$v")" ;;
  esac
done

# --- camera: subdevs (csi2, pisp-fe, imx500) -----------------------------
for v in /sys/class/video4linux/v4l-subdev*; do add "/dev/$(basename "$v")"; done

echo "Passing ${#DEV[@]} devices."

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  --network=host --ipc=host \
  --init \
  "${DEV[@]}" \
  --group-add=dialout --group-add=video \
  -v /run/udev:/run/udev:ro \
  -v "$REPO":/workspace \
  -v "$HOME/.local/share/claude":/opt/claude:ro \
  -v "$HOME/.claude":/home/ros/.claude \
  -v "$HOME/.local/share/mybot/claude-shim":/usr/local/bin/claude:ro \
  -w /workspace -u ros \
  "$IMAGE" sleep infinity
