#!/usr/bin/env bash
# Live object detection on a USB webcam using the Hailo-10H.
# The rpicam-* apps only drive CSI ribbon cameras, so this replaces
# "rpicam-hello --post-process-file .../hailo_yolov6_inference.json".
#
# Usage: ./hailo-webcam-detect.sh [/dev/videoN] [model.hef]
set -euo pipefail

DEV="${1:-/dev/video0}"
HEF="${2:-/usr/share/hailo-models/yolov11m_h10.hef}"
PP=/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp_post.so
W=1280; H=720; FPS=30

exec gst-launch-1.0 \
  v4l2src device="$DEV" ! image/jpeg,width=$W,height=$H,framerate=$FPS/1 ! jpegdec \
  ! videoconvert ! videoscale add-borders=true ! video/x-raw,format=RGB,width=640,height=640 \
  ! queue leaky=downstream max-size-buffers=3 \
  ! hailonet hef-path="$HEF" \
  ! queue leaky=downstream max-size-buffers=3 \
  ! hailofilter so-path="$PP" function-name=filter_letterbox \
  ! hailooverlay ! videoconvert ! fpsdisplaysink video-sink=autovideosink sync=false text-overlay=false
